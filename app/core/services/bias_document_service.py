# coding: utf-8
"""偏误分析文件导入服务。

把 Excel、TXT 与 Word 文档统一转换为偏误分析可消费的 DataFrame。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable

import pandas as pd

from app.core.utils import logger


BIAS_TEXT_COLUMN = "文本"
SUPPORTED_BIAS_SOURCE_EXTENSIONS = frozenset({".xlsx", ".txt", ".docx", ".doc"})


class BiasDocumentLoadError(ValueError):
    """偏误分析文件无法读取。"""


class BiasDocumentService:
    """读取偏误分析支持的本地文件。"""

    def loadFile(self, filePath: str) -> tuple[pd.DataFrame, int]:
        """读取一个文件并返回统一表格与文本条数。"""
        path = Path(filePath)
        if not path.is_file():
            raise BiasDocumentLoadError("文件不存在或已被移动")

        extension = path.suffix.lower()
        if extension not in SUPPORTED_BIAS_SOURCE_EXTENSIONS:
            raise BiasDocumentLoadError(
                f"不支持 {extension or '无扩展名'} 文件；请选择 XLSX、TXT、DOCX 或 DOC"
            )

        try:
            if extension == ".xlsx":
                dataFrame = self._loadExcel(path)
            elif extension == ".txt":
                dataFrame = self._loadText(path)
            elif extension == ".docx":
                dataFrame = self._loadDocx(path)
            else:
                dataFrame = self._loadLegacyDoc(path)
        except BiasDocumentLoadError:
            raise
        except PermissionError as error:
            raise BiasDocumentLoadError("文件正在被占用或当前账户没有读取权限") from error
        except Exception as error:
            logger.exception(f"[BiasDocumentService] 文件解析失败: {path}")
            raise BiasDocumentLoadError(f"文件内容无法解析：{error}") from error

        if dataFrame.empty:
            raise BiasDocumentLoadError("文件中没有可分析的文本")
        return dataFrame, len(dataFrame)

    def _loadExcel(self, path: Path) -> pd.DataFrame:
        """读取 XLSX，并保留现有表头/列选择契约。"""
        try:
            dataFrame = pd.read_excel(
                path,
                engine="openpyxl",
                header=0,
                dtype=str,
                na_filter=False,
            )
        except Exception as firstError:
            logger.warning(f"[BiasDocumentService] pandas 读取失败，降级到 openpyxl: {firstError}")
            try:
                import openpyxl

                workbook = openpyxl.load_workbook(
                    path,
                    read_only=True,
                    data_only=True,
                )
                sheet = workbook.worksheets[0]
                rows = list(sheet.values)
                workbook.close()
            except Exception as error:
                raise BiasDocumentLoadError("Excel 文件损坏、加密或格式不正确") from error

            if not rows:
                return pd.DataFrame()
            dataFrame = pd.DataFrame(rows[1:], columns=rows[0])

        dataFrame.attrs["sourceKind"] = "excel"
        dataFrame.attrs["sourcePositions"] = list(range(2, len(dataFrame) + 2))
        return dataFrame

    def _loadText(self, path: Path) -> pd.DataFrame:
        """读取常见中文编码的 TXT，并以非空行作为分析单元。"""
        rawData = path.read_bytes()
        text = self._decodeText(rawData)
        units, positions = self._splitTextUnits(text)
        return self._buildDocumentFrame(units, positions, "txt")

    def _loadDocx(self, path: Path) -> pd.DataFrame:
        """按原始顺序读取 DOCX 段落与表格行。"""
        try:
            from docx import Document
            from docx.oxml.ns import qn

            document = Document(path)
        except Exception as error:
            raise BiasDocumentLoadError("Word 文档损坏、加密或并非有效的 DOCX 文件") from error

        units: list[str] = []
        positions: list[int] = []
        blockIndex = 0
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                blockIndex += 1
                text = "".join(
                    node.text or "" for node in child.iter(qn("w:t"))
                ).strip()
                if text:
                    units.append(text)
                    positions.append(blockIndex)
            elif child.tag == qn("w:tbl"):
                for row in child.iter(qn("w:tr")):
                    blockIndex += 1
                    cells = []
                    for cell in row.iter(qn("w:tc")):
                        cellText = "".join(
                            node.text or "" for node in cell.iter(qn("w:t"))
                        ).strip()
                        if cellText:
                            cells.append(cellText)
                    rowText = "\t".join(cells).strip()
                    if rowText:
                        units.append(rowText)
                        positions.append(blockIndex)

        return self._buildDocumentFrame(units, positions, "docx")

    def _loadLegacyDoc(self, path: Path) -> pd.DataFrame:
        """读取 OLE Compound File 格式的旧版 Word DOC。"""
        try:
            import olefile
        except ImportError as error:
            raise BiasDocumentLoadError("读取 DOC 文件需要安装 olefile") from error

        if not olefile.isOleFile(str(path)):
            raise BiasDocumentLoadError("该文件不是有效的旧版 Word DOC 文档")

        try:
            with olefile.OleFileIO(str(path)) as compoundFile:
                if not compoundFile.exists("WordDocument"):
                    raise BiasDocumentLoadError("DOC 文件缺少 WordDocument 数据流")
                wordStream = compoundFile.openstream("WordDocument").read()
                flags = self._readUInt16(wordStream, 10)
                tableStreamName = "1Table" if flags & 0x0200 else "0Table"
                if not compoundFile.exists(tableStreamName):
                    raise BiasDocumentLoadError(f"DOC 文件缺少 {tableStreamName} 数据流")
                tableStream = compoundFile.openstream(tableStreamName).read()
        except BiasDocumentLoadError:
            raise
        except Exception as error:
            raise BiasDocumentLoadError("DOC 文件损坏或无法读取") from error

        text = self._extractLegacyWordText(wordStream, tableStream)
        units, positions = self._splitTextUnits(text)
        return self._buildDocumentFrame(units, positions, "doc")

    def _extractLegacyWordText(self, wordStream: bytes, tableStream: bytes) -> str:
        """通过 FIB 与 Clx Piece Table 提取旧版 Word 主文档文本。"""
        if len(wordStream) < 32 or self._readUInt16(wordStream, 0) != 0xA5EC:
            raise BiasDocumentLoadError("DOC 文件头无效")

        flags = self._readUInt16(wordStream, 10)
        if flags & 0x0100:
            raise BiasDocumentLoadError("暂不支持加密的 DOC 文件，请先解除密码保护")

        cursor = 32
        csw = self._readUInt16(wordStream, cursor)
        cursor += 2 + csw * 2
        cslw = self._readUInt16(wordStream, cursor)
        cursor += 2
        fibRgLwStart = cursor
        if cslw < 4:
            raise BiasDocumentLoadError("DOC 文件缺少正文长度信息")
        characterCount = self._readUInt32(wordStream, fibRgLwStart + 12)
        cursor += cslw * 4

        pairCount = self._readUInt16(wordStream, cursor)
        cursor += 2
        clxPairIndex = 33
        if pairCount <= clxPairIndex:
            raise BiasDocumentLoadError("DOC 文件缺少文本位置表")
        clxOffset = self._readUInt32(wordStream, cursor + clxPairIndex * 8)
        clxLength = self._readUInt32(wordStream, cursor + clxPairIndex * 8 + 4)
        if clxLength <= 0 or clxOffset + clxLength > len(tableStream):
            raise BiasDocumentLoadError("DOC 文本位置表无效")

        clx = tableStream[clxOffset : clxOffset + clxLength]
        plcPcd = self._findPieceTable(clx)
        if len(plcPcd) < 16 or (len(plcPcd) - 4) % 12:
            raise BiasDocumentLoadError("DOC Piece Table 结构无效")

        pieceCount = (len(plcPcd) - 4) // 12
        cpArraySize = (pieceCount + 1) * 4
        textPieces: list[str] = []
        for pieceIndex in range(pieceCount):
            startCp = self._readUInt32(plcPcd, pieceIndex * 4)
            endCp = self._readUInt32(plcPcd, (pieceIndex + 1) * 4)
            if startCp >= characterCount:
                break
            pieceCharacterCount = min(endCp, characterCount) - startCp
            if pieceCharacterCount <= 0:
                continue

            pcdOffset = cpArraySize + pieceIndex * 8
            fcCompressed = self._readUInt32(plcPcd, pcdOffset + 2)
            isCompressed = bool(fcCompressed & 0x40000000)
            fileOffset = fcCompressed & 0x3FFFFFFF
            if isCompressed:
                fileOffset //= 2
                byteCount = pieceCharacterCount
                encoding = "cp1252"
            else:
                byteCount = pieceCharacterCount * 2
                encoding = "utf-16-le"

            endOffset = fileOffset + byteCount
            if fileOffset < 0 or endOffset > len(wordStream):
                raise BiasDocumentLoadError("DOC 正文位置超出文件范围")
            textPieces.append(
                wordStream[fileOffset:endOffset].decode(
                    encoding,
                    errors="replace",
                )
            )

        text = "".join(textPieces)
        text = text.translate(
            str.maketrans(
                {
                    "\r": "\n",
                    "\x07": "\n",
                    "\x0b": "\n",
                    "\x0c": "\n",
                    "\x13": "",
                    "\x14": "",
                    "\x15": "",
                }
            )
        )
        if not text.strip():
            raise BiasDocumentLoadError("DOC 文件中没有可提取的正文文本")
        return text

    def _findPieceTable(self, clx: bytes) -> bytes:
        """跳过可选 Prc，返回 Pcdt 中的 PlcPcd。"""
        cursor = 0
        while cursor < len(clx) and clx[cursor] == 0x01:
            if cursor + 3 > len(clx):
                raise BiasDocumentLoadError("DOC 属性表不完整")
            propertyLength = self._readUInt16(clx, cursor + 1)
            cursor += 3 + propertyLength

        if cursor + 5 > len(clx) or clx[cursor] != 0x02:
            raise BiasDocumentLoadError("DOC 文件缺少 Piece Table")
        pieceTableLength = self._readUInt32(clx, cursor + 1)
        start = cursor + 5
        end = start + pieceTableLength
        if pieceTableLength <= 0 or end > len(clx):
            raise BiasDocumentLoadError("DOC Piece Table 长度无效")
        return clx[start:end]

    def _decodeText(self, rawData: bytes) -> str:
        """以严格模式尝试常见 Unicode 与中文文本编码。"""
        if not rawData:
            return ""

        encodings = ["utf-8-sig"]
        if rawData.startswith((b"\xff\xfe", b"\xfe\xff")):
            encodings.insert(0, "utf-16")
        elif b"\x00" in rawData[:256]:
            encodings.extend(["utf-16-le", "utf-16-be"])
        encodings.extend(["gb18030", "big5"])
        for encoding in encodings:
            try:
                return rawData.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise BiasDocumentLoadError("无法识别 TXT 编码，请另存为 UTF-8、GB18030 或 Big5")

    def _splitTextUnits(self, text: str) -> tuple[list[str], list[int]]:
        """将文档按非空行拆成分析单元，并保留原始行号。"""
        units: list[str] = []
        positions: list[int] = []
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        for lineNumber, line in enumerate(normalized.split("\n"), start=1):
            value = line.strip()
            if value:
                units.append(value)
                positions.append(lineNumber)
        return units, positions

    def _buildDocumentFrame(
        self,
        units: Iterable[str],
        positions: Iterable[int],
        sourceKind: str,
    ) -> pd.DataFrame:
        """构造文档类来源的统一单列 DataFrame。"""
        textUnits = list(units)
        sourcePositions = list(positions)
        dataFrame = pd.DataFrame({BIAS_TEXT_COLUMN: textUnits}, dtype=str)
        dataFrame.attrs["sourceKind"] = sourceKind
        dataFrame.attrs["sourcePositions"] = sourcePositions
        return dataFrame

    @staticmethod
    def _readUInt16(data: bytes, offset: int) -> int:
        if offset < 0 or offset + 2 > len(data):
            raise BiasDocumentLoadError("DOC 结构意外结束")
        return struct.unpack_from("<H", data, offset)[0]

    @staticmethod
    def _readUInt32(data: bytes, offset: int) -> int:
        if offset < 0 or offset + 4 > len(data):
            raise BiasDocumentLoadError("DOC 结构意外结束")
        return struct.unpack_from("<I", data, offset)[0]


biasDocumentService = BiasDocumentService()


__all__ = [
    "BIAS_TEXT_COLUMN",
    "SUPPORTED_BIAS_SOURCE_EXTENSIONS",
    "BiasDocumentLoadError",
    "BiasDocumentService",
    "biasDocumentService",
]
