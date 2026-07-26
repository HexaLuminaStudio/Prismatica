"""Check file state + read after writes"""
import shutil, sys, sqlite3, time, os
from pathlib import Path

DB = Path(r"E:\Prismatica\datas\projects.db")
for sfx in ("", "-shm", "-wal"):
    p = Path(str(DB) + sfx)
    p.unlink(missing_ok=True)

for m in list(sys.modules):
    if m.startswith("app."):
        del sys.modules[m]

from app.core.services.project_manager import ProjectManager

ProjectManager._instance = None
mgr = ProjectManager.instance()
p = mgr.createProject(name="PROJ1")
r = mgr.addResource(
    projectId=p.id,
    resourceType="freq",
    title="test",
    summary="",
    parameters={"k": 1},
    snapshotData={},
)

time.sleep(0.5)

print("Files in datas/:")
for f in Path(r"E:\Prismatica\datas").iterdir():
    if "projects" in f.name:
        print(f"  {f.name}: {f.stat().st_size} bytes  mtime={os.path.getmtime(f):.3f}")

# Read-only connect
print()
print("Read-only connect (uri=True):")
try:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cnt = conn.execute(
        "SELECT COUNT(*) FROM project_resources"
    ).fetchone()[0]
    print(f"  count: {cnt}")
    for row in conn.execute(
        "SELECT id, title FROM project_resources"
    ).fetchall():
        print(f"  row: {row}")
    conn.close()
except Exception as e:
    print(f"  err: {e}")

# sqlite_master
print()
print("sqlite_master:")
conn = sqlite3.connect(str(DB))
for row in conn.execute(
    "SELECT type, name, length(sql) FROM sqlite_master"
).fetchall():
    print(f"  {row[0]:10s} {row[1]:30s} sql-len={row[2]}")
conn.close()

mgr.deleteProject(p.id)
