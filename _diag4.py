"""Diag: verify addResource persists to project_resources table."""
import shutil, sys, sqlite3
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
print(f"created {p.id}")

r = mgr.addResource(
    projectId=p.id,
    resourceType="freq",
    title="Test resource",
    summary="",
    parameters={"k": 1},
    snapshotData={},
)
print(f"addResource returned: {r.id if r else None}")

import time
time.sleep(0.3)
conn = sqlite3.connect(str(DB))
print("projects:")
for row in conn.execute("SELECT id, name FROM projects").fetchall():
    print(" ", row)
print("project_resources:")
for row in conn.execute(
    "SELECT id, type, title FROM project_resources"
).fetchall():
    print(" ", row)
conn.close()

mgr.deleteProject(p.id)
