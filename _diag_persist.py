"""Diagnostic: write a row, then read it back through a fresh connection"""
import sqlite3
from pathlib import Path

DB_PATH = Path(r"E:\Prismatica\datas\projects.db")

conn = sqlite3.connect(str(DB_PATH))
tables = [
    r[0]
    for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
]
print("Tables in DB:", tables)

for t in tables:
    cnt = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {cnt} rows")

# Now check if ai_insights table exists
print()
print("--- ai_insights columns ---")
try:
    cols = conn.execute(
        "PRAGMA table_info(project_ai_insights)"
    ).fetchall()
    for c in cols:
        print(c)
    rows = conn.execute("SELECT * FROM project_ai_insights").fetchall()
    print(f"project_ai_insights rows: {len(rows)}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"error: {e}")
print()
print("--- project_resources columns ---")
try:
    cols = conn.execute(
        "PRAGMA table_info(project_resources)"
    ).fetchall()
    for c in cols:
        print(c)
    rows = conn.execute("SELECT * FROM project_resources").fetchall()
    print(f"project_resources rows: {len(rows)}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"error: {e}")
conn.close()
