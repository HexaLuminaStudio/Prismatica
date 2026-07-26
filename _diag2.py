"""More diagnostic - try the actual SQL queries that addResource/addAiInsight execute"""
import sqlite3
from pathlib import Path

DB_PATH = Path(r"E:\Prismatica\datas\projects.db")

conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
# Match the manager settings
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA foreign_keys=ON")

# Get the project id
project_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
print(f"project_id: {project_id}")

# Try to insert a resource manually
import json, uuid
rid = str(uuid.uuid4())
now = "2026-01-01T00:00:00+0800"

try:
    conn.execute(
        "INSERT OR REPLACE INTO project_resources "
        "(id, project_id, type, title, summary, parameters, "
        " tags, status, created_at, snapshot_rel_path, "
        " thumbnail_rel_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rid,
            project_id,
            "freq",
            "Manual Test",
            "",
            json.dumps({"x": 1}, ensure_ascii=False),
            json.dumps([]),
            "new",
            now,
            "resources/foo.json",
            None,
        ),
    )
    print(f"Resource inserted: {rid}")
except Exception as e:
    print(f"Resource insert failed: {e}")

cnt = conn.execute(
    "SELECT COUNT(*) FROM project_resources"
).fetchone()[0]
print(f"After insert, resources count: {cnt}")

# Try insight insert
iid = str(uuid.uuid4())
try:
    conn.execute(
        "INSERT OR REPLACE INTO project_ai_insights "
        "(id, project_id, analysis_type, content, citations, "
        " confidence, model, resource_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            iid,
            project_id,
            "research_report",
            "Manual test content",
            json.dumps([]),
            "medium",
            "deepseek-chat",
            None,
            now,
        ),
    )
    print(f"Insight inserted: {iid}")
except Exception as e:
    print(f"Insight insert failed: {e}")

cnt = conn.execute(
    "SELECT COUNT(*) FROM project_ai_insights"
).fetchone()[0]
print(f"After insert, insights count: {cnt}")

conn.close()

# Re-open to verify data visible
print()
print("Re-opening DB to verify:")
conn2 = sqlite3.connect(str(DB_PATH))
cnt_p = conn2.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
cnt_r = conn2.execute("SELECT COUNT(*) FROM project_resources").fetchone()[0]
cnt_i = conn2.execute(
    "SELECT COUNT(*) FROM project_ai_insights"
).fetchone()[0]
print(f"projects: {cnt_p}, resources: {cnt_r}, insights: {cnt_i}")
conn2.close()
