"""Diag: verify createProject persists to projects table."""
import shutil, sys, sqlite3
from pathlib import Path

DB = Path(r"E:\Prismatica\datas\projects.db")
# delete
for sfx in ("", "-shm", "-wal"):
    p = Path(str(DB) + sfx)
    p.unlink(missing_ok=True)

for m in list(sys.modules):
    if m.startswith("app."):
        del sys.modules[m]

from app.core.services.project_manager import ProjectManager

ProjectManager._instance = None
mgr = ProjectManager.instance()

print("Tables after init:")
conn = sqlite3.connect(str(DB))
for r in conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall():
    print(" ", r[0])
conn.close()

p = mgr.createProject(name="PROJ1")
print(f"created {p.id}")

conn = sqlite3.connect(str(DB))
print("after createProject:")
for r in conn.execute("SELECT id, name FROM projects").fetchall():
    print(" ", r)
conn.close()

mgr.deleteProject(p.id)
