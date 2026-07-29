# coding:utf-8
"""
HSK 语料 Excel → SQLite 一次性导入脚本
=====================================

把 e:\\Prismatica\\test\\hsk_corpus.xlsx(或指定路径)导入到
e:\\Prismatica\\datas\\corpora\\hsk_corpus.db,自动建表 + 建索引。

不依赖 PySide6,可独立运行。

用法:
    python scripts/build_hsk_corpus_db.py
    python scripts/build_hsk_corpus_db.py --xlsx D:\\data\\hsk.xlsx --db D:\\out\\hsk.db
    python scripts/build_hsk_corpus_db.py --force   # 覆盖已有 db

性能:
    11337 行 × 36 列 + 24 个 NOCASE 索引 ≈ 5-10 秒
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

# 把项目根加入 path,以便从 app.core.utils.data_paths 取默认路径
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# 列定义(必须与 hsk_corpus_service.py 完全一致,且与 Excel 列名一致)
# ---------------------------------------------------------------------------
# Excel 真实 12 列(只读 .xlsx 头部确认):
#   文本 5:作文题目 / 证书级别 / 国籍 / 作文母号 / 性别
#   整数 7:总词数 / 总字数 / 听力理解分数 / 阅读理解分数 / 综合表达考试分数
#         / 口试分数 / 作文分数
COLUMNS: list[tuple[str, str]] = [
    ("作文题目", "TEXT"),
    ("证书级别", "TEXT"),
    ("国籍", "TEXT"),
    ("总词数", "INTEGER"),
    ("总字数", "INTEGER"),
    ("听力理解分数", "INTEGER"),
    ("阅读理解分数", "INTEGER"),
    ("综合表达考试分数", "INTEGER"),
    ("口试分数", "INTEGER"),
    ("作文分数", "INTEGER"),
    ("作文母号", "TEXT"),
    ("性别", "TEXT"),
]

# 需要建 NOCASE 索引的文本列
TEXT_COLUMNS: list[str] = [name for name, t in COLUMNS if t == "TEXT"]


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
def buildSchemaSql() -> str:
    """构造建表 + 建索引的 DDL(SQLite IF NOT EXISTS,幂等)。"""
    colDefs = ",\n    ".join(f"{n} {t}" for n, t in COLUMNS)
    parts = [
        f"""
CREATE TABLE IF NOT EXISTS hsk_corpus (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    {colDefs},
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
    ]
    # 文本列建 NOCASE 索引(LIKE 不区分大小写)
    for col in TEXT_COLUMNS:
        parts.append(
            f"CREATE INDEX IF NOT EXISTS idx_hsk_{col} "
            f"ON hsk_corpus({col} COLLATE NOCASE);"
        )
    # 元数据表
    parts.append(
        """
CREATE TABLE IF NOT EXISTS hsk_corpus_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parseArgs() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HSK 语料 Excel → SQLite 导入")
    p.add_argument(
        "--xlsx", type=str, default="", help="源 Excel 文件路径,默认从 data_paths 取"
    )
    p.add_argument(
        "--db", type=str, default="", help="目标 SQLite 文件路径,默认从 data_paths 取"
    )
    p.add_argument("--force", action="store_true", help="删除已有 db 文件后重建")
    p.add_argument("--batch-size", type=int, default=500, help="批量插入条数,默认 500")
    return p.parse_args()


def main() -> int:
    args = parseArgs()

    # 路径解析:命令行 > data_paths > 默认
    try:
        from app.core.utils.data_paths import HSK_CORPUS_DB

        defaultDb = str(HSK_CORPUS_DB)
    except Exception:
        defaultDb = str(ROOT / "datas" / "corpora" / "hsk_corpus.db")
    defaultXlsx = str(ROOT / "test" / "hsk_corpus.xlsx")

    xlsxPath = Path(args.xlsx or defaultXlsx).resolve()
    dbPath = Path(args.db or defaultDb).resolve()

    if not xlsxPath.exists():
        print(f"[ERROR] Excel 文件不存在: {xlsxPath}", file=sys.stderr)
        return 1

    if args.force and dbPath.exists():
        print(f"[INFO] --force 已开启,删除 {dbPath}")
        for suffix in ("", "-shm", "-wal"):
            p = dbPath if not suffix else Path(str(dbPath) + suffix)
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    dbPath.parent.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] 源 Excel : {xlsxPath}")
    print(f"[INFO] 目标 db  : {dbPath}")
    print(f"[INFO] 文本列数 : {len(TEXT_COLUMNS)} (全部建 NOCASE 索引)")

    t0 = time.perf_counter()

    # ---- 1) 读 Excel ----
    try:
        import pandas as pd
    except ImportError:
        print("[ERROR] 缺少 pandas,执行: pip install pandas openpyxl", file=sys.stderr)
        return 1
    print(f"[INFO] 读取 Excel ...")
    df = pd.read_excel(xlsxPath, engine="openpyxl", dtype=str)
    df.columns = [str(c) for c in df.columns]
    print(f"[INFO] Excel 行数={len(df)}, 列数={len(df.columns)}")
    # 校验列名(允许 Excel 缺一些列,缺失填 NULL)
    excelCols = list(df.columns)
    extraCols = [c for c in excelCols if c not in {n for n, _ in COLUMNS}]
    if extraCols:
        print(f"[WARN] Excel 中以下列不在 schema 里,会被忽略: {extraCols}")

    # ---- 2) 建表 + 索引 ----
    conn = sqlite3.connect(str(dbPath), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(buildSchemaSql())
        conn.commit()
        print(f"[INFO] schema / 索引 创建完成")

        # ---- 3) 批量插入 ----
        colNames = [n for n, _ in COLUMNS]
        # DataFrame 列名对齐 schema,缺失列填 None
        aligned = df.reindex(columns=colNames)
        # 转 list[tuple],None 处理
        records: list[tuple] = []
        for _, row in aligned.iterrows():
            records.append(
                tuple(
                    (
                        None
                        if (v is None or (isinstance(v, float) and pd.isna(v)))
                        else str(v)
                    )
                    for v in row.tolist()
                )
            )

        placeholders = ",".join(["?"] * len(colNames))
        insertSql = (
            f"INSERT INTO hsk_corpus ({','.join(colNames)}) " f"VALUES ({placeholders})"
        )

        tIns = time.perf_counter()
        conn.execute("BEGIN")
        for i in range(0, len(records), args.batch_size):
            batch = records[i : i + args.batch_size]
            conn.executemany(insertSql, batch)
        conn.commit()
        insMs = (time.perf_counter() - tIns) * 1000
        print(f"[INFO] 插入 {len(records)} 行,耗时 {insMs:.0f} ms")

        # ---- 4) 元数据 ----
        conn.execute(
            "INSERT OR REPLACE INTO hsk_corpus_meta(key, value) "
            "VALUES('schema_version', ?)",
            ("1",),
        )
        conn.execute(
            "INSERT OR REPLACE INTO hsk_corpus_meta(key, value) "
            "VALUES('row_count', ?)",
            (str(len(records)),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO hsk_corpus_meta(key, value) "
            "VALUES('imported_at', ?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO hsk_corpus_meta(key, value) "
            "VALUES('source_xlsx', ?)",
            (str(xlsxPath),),
        )
        conn.commit()

        # ---- 5) ANALYZE 让 SQLite 收集统计信息,加速后续查询 ----
        conn.execute("ANALYZE")
        conn.commit()

        # ---- 6) 抽样验证 ----
        sample = conn.execute("SELECT * FROM hsk_corpus LIMIT 1").fetchone()
        if sample:
            print(f"[INFO] 抽样一行 keys={list(sample.keys())[:6]}...")
        cnt = conn.execute("SELECT COUNT(*) AS n FROM hsk_corpus").fetchone()["n"]
        print(f"[INFO] 入库后行数: {cnt}")

    finally:
        conn.close()

    totalMs = (time.perf_counter() - t0) * 1000
    print(f"\n[OK] 完成。总耗时 {totalMs:.0f} ms")
    print(f"     文件: {dbPath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
