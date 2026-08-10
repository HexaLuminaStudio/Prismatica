# coding: utf-8
"""从用户提供的 PNG 原样生成安装器 PNG 与多尺寸 ICO。"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def prepareAssets(sourceLogo: Path, assetsDir: Path, licensePath: Path) -> None:
    """保存原始 Logo，并生成 Windows 多尺寸图标。"""
    if not sourceLogo.is_file():
        raise FileNotFoundError(f"Logo 文件不存在：{sourceLogo}")
    if not licensePath.is_file():
        raise FileNotFoundError(f"许可协议不存在：{licensePath}")

    assetsDir.mkdir(parents=True, exist_ok=True)
    targetPng = assetsDir / "installer_logo.png"
    targetIcon = assetsDir / "PrismaticaInstaller.ico"
    targetLicense = assetsDir / "LICENSE.txt"
    shutil.copy2(sourceLogo, targetPng)
    shutil.copy2(licensePath, targetLicense)

    with Image.open(sourceLogo) as sourceImage:
        rgbaImage = sourceImage.convert("RGBA")
        sideLength = max(rgbaImage.size)
        squareImage = Image.new("RGBA", (sideLength, sideLength), (0, 0, 0, 0))
        offset = (
            (sideLength - rgbaImage.width) // 2,
            (sideLength - rgbaImage.height) // 2,
        )
        squareImage.alpha_composite(rgbaImage, offset)
        squareImage.save(targetIcon, format="ICO", sizes=[(size, size) for size in ICON_SIZES])


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 Prismatica 安装器图标资源")
    parser.add_argument("--source-logo", dest="sourceLogo", type=Path, required=True)
    parser.add_argument("--assets-dir", dest="assetsDir", type=Path, required=True)
    parser.add_argument("--license", dest="licensePath", type=Path, required=True)
    arguments = parser.parse_args()
    prepareAssets(arguments.sourceLogo, arguments.assetsDir, arguments.licensePath)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
