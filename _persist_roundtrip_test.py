"""In-process round-trip test (simpler & faster):
- Drop and recreate schema via direct SQLite, then use ProjectManager
- Resources + AI insights should be visible after _loadAllProjectsFromDb()
"""

import shutil
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(r"E:\Prismatica\datas\projects.db")
DB_BACKUP = Path(r"E:\Prismatica\datas\projects.db.test_backup")

# Backup then drop existing DB
backup_exists = DB_PATH.exists()
if backup_exists:
    if DB_BACKUP.exists():
        DB_BACKUP.unlink()
    shutil.copy2(str(DB_PATH), str(DB_BACKUP))

# Wipe schema (test DB freshly so we can verify from-scratch bootstrap)
DB_PATH.unlink(missing_ok=True)
for suffix in ("-shm", "-wal"):
    p = Path(str(DB_PATH) + suffix)
    p.unlink(missing_ok=True)

try:
    # Force module reload — schema must be (re-)initialized
    for mod in list(sys.modules):
        if mod.startswith("app."):
            del sys.modules[mod]

    from app.core.services.project_manager import ProjectManager
    from app.core.models.project import RESOURCE_TYPE_FREQ

    # Reset the cached singleton too
    ProjectManager._instance = None

    print("Test 1: add resource + AI insight via real API")
    mgr = ProjectManager.instance()
    proj = mgr.createProject(name="测试项目1")
    print(f"  created project {proj.id}")
    res = mgr.addResource(
        projectId=proj.id,
        resourceType=RESOURCE_TYPE_FREQ,
        title="测试词频",
        summary="summary",
        parameters={"window": 5},
        snapshotData={"top": ["a", "b"]},
    )
    print(f"  added resource {res.id}")
    ins = mgr.addAiInsight(
        projectId=proj.id,
        content="测试 AI 解读",
        analysisType="research_report",
        model="deepseek",
    )
    print(f"  added insight {ins.id}")

    # Read directly from DB
    print()
    print("Direct DB read after writes:")
    conn = sqlite3.connect(str(DB_PATH))
    # Check WAL first
    try:
        wal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        print(f"  journal_mode: {wal_mode}")
    except Exception:
        pass
    cnt_p = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    cnt_r = conn.execute("SELECT COUNT(*) FROM project_resources").fetchone()[0]
    cnt_i = conn.execute("SELECT COUNT(*) FROM project_ai_insights").fetchone()[0]
    print(f"  projects: {cnt_p}, resources: {cnt_r}, insights: {cnt_i}")
    conn.close()

    assert cnt_r == 1, f"Resources not in DB (got {cnt_r})"
    assert cnt_i == 1, f"Insights not in DB (got {cnt_i})"

    # Test 2: simulate restart
    print()
    print("Test 2: simulate restart — clear cache + reload")
    mgr._memCache.clear()
    mgr._loadAllProjectsFromDb()
    p = mgr.getProject(proj.id)
    print(f"  resources: {len(p.resources)}, insights: {len(p.aiInsights)}")
    assert len(p.resources) == 1
    assert len(p.aiInsights) == 1

    # Test 3: delete
    print()
    print("Test 3: delete insight, reload, verify removal")
    mgr.deleteAiInsight(proj.id, ins.id)
    mgr._memCache.clear()
    mgr._loadAllProjectsFromDb()
    p = mgr.getProject(proj.id)
    print(f"  after delete insights: {len(p.aiInsights)}")
    assert len(p.aiInsights) == 0

    print()
    print("ALL TESTS PASSED")

    mgr.deleteProject(proj.id)

finally:
    if backup_exists:
        DB_PATH.unlink(missing_ok=True)
        shutil.copy2(str(DB_BACKUP), str(DB_PATH))
        DB_BACKUP.unlink(missing_ok=True)
        print("Restored backup")
