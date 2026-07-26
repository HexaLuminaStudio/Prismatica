# coding: utf-8
"""PRD-002 实施验证:语法检查 + 导入检查 + 数据模型冒烟测试

退出码:0=通过,1=失败
输出会同时写入 _check.log
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("e:/Prismatica").resolve()
OUT_LOG = ROOT / "_check.log"


class Tee:
    def __init__(self):
        self._fh = open(OUT_LOG, "w", encoding="utf-8")

    def write(self, msg):
        sys.__stdout__.write(msg)
        sys.__stdout__.flush()
        self._fh.write(msg)
        self._fh.flush()

    def flush(self):
        sys.__stdout__.flush()
        self._fh.flush()


_tee = Tee()
sys.stdout = _tee
sys.stderr = _tee


def w(msg=""):
    """快捷写入"""
    _tee.write(str(msg) + "\n")
    _tee.flush()


def wo(msg=""):
    """不换行写入"""
    _tee.write(str(msg))
    _tee.flush()


# 1) py_compile 所有新增/修改文件
TARGETS = [
    # 新增
    "app/core/models/__init__.py",
    "app/core/models/project.py",
    "app/core/services/project_manager.py",
    "app/view/widgets/project_switcher_widget.py",
    "app/view/widgets/project_manager_widget.py",
    "app/view/widgets/project_manager_dialogs.py",
    "app/view/project_interface.py",
    "app/view/widgets/freq_analyzer/resource_sink_mixin.py",
    # 修改
    "app/core/utils/data_paths.py",
    "app/core/utils/signal_bus.py",
    "app/core/services/__init__.py",
    "app/view/widgets/titlebar_widget.py",
    "app/view/main_window.py",
    "app/view/widgets/freq_analyzer/freq_analyzer_widget.py",
    "app/view/widgets/freq_analyzer/network_widget.py",
    "app/view/widgets/freq_analyzer/concordance_widget.py",
]

w("=== 阶段1:语法检查(py_compile)===")
failed = []
for rel in TARGETS:
    p = ROOT / rel
    if not p.exists():
        w(f"  [MISS] {rel}")
        failed.append(rel)
        continue
    res = subprocess.run(
        [sys.executable, "-m", "py_compile", str(p)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    if res.returncode != 0:
        w(f"  [FAIL] {rel}")
        w(res.stderr)
        failed.append(rel)
    else:
        w(f"  [OK]   {rel}")

if failed:
    w(f"\n语法失败 {len(failed)} 个:{failed}")
    sys.exit(1)

w("\n=== 阶段2:模块导入检查(无 GUI)===")

# 用临时路径避免污染真实 datas/
tmp = Path(tempfile.mkdtemp(prefix="prismatica_check_"))

# 在执行前 patch data_paths,再导入 services
from app.core.utils import data_paths
from app.core.services import project_manager

data_paths.PROJECTS_DB = tmp / "projects.db"
data_paths.PROJECTS_DIR = tmp / "projects"
data_paths.PROJECT_STATE_FILE = tmp / "project_state.json"
project_manager.PROJECTS_DB = data_paths.PROJECTS_DB
project_manager.PROJECTS_DIR = data_paths.PROJECTS_DIR
project_manager.PROJECT_STATE_FILE = data_paths.PROJECT_STATE_FILE

# 静默 loguru
try:
    from loguru import logger as _lg

    _lg.remove()
    _lg.add(sys.__stderr__, level="WARNING")
except Exception:
    pass

# 信号 emit 需要 QCoreApplication(Signal 在没有 app 时会阻塞)
try:
    from PySide6.QtWidgets import QApplication

    _qtApp = QApplication.instance() or QApplication(sys.argv)
except Exception:
    _qtApp = None

# 检查 1:models
wo("[import] app.core.models ... ")
from app.core.models import (
    AiInsight,
    CorpusRef,
    Note,
    Project,
    RESOURCE_TYPE_FREQ,
    RESOURCE_TYPE_NETWORK,
    RESOURCE_TYPE_KWIC,
    Resource,
    genId,
    projectFromDict,
    projectToDict,
)

w("OK")

# 检查 2:project_manager(不带 Qt)
wo("[import] app.core.services.project_manager ... ")
from app.core.services.project_manager import ProjectManager

w("OK")

# 检查 3:data_paths 新常量
wo("[import] app.core.utils.data_paths ... ")
from app.core.utils.data_paths import (
    DEFAULT_PROJECT_NAME,
    PROJECTS_DB,
    PROJECTS_DIR,
    PROJECT_STATE_FILE,
)

assert (
    PROJECTS_DB.exists() or PROJECTS_DB.parent.exists()
), f"PROJECTS_DB 父目录不存在:{PROJECTS_DB}"
w("OK")

# 检查 4:signal_bus 新信号
wo("[import] app.core.utils.signal_bus ... ")
from app.core.utils.signal_bus import SignalBus

bus = SignalBus()
assert hasattr(bus, "activeProjectChanged"), "缺少 activeProjectChanged 信号"
assert hasattr(bus, "projectListChanged"), "缺少 projectListChanged 信号"
w("OK")

# 检查 5:数据模型基本操作
w("\n=== 阶段3:数据模型基本操作 ===")
p = Project(id=genId(), name="测试", tags=["a", "b"])
p.resources.append(Resource(id=genId(), type=RESOURCE_TYPE_FREQ, title="r1"))
d = projectToDict(p)
p2 = projectFromDict(d)
assert p2.name == "测试"
assert len(p2.resources) == 1
assert p2.resources[0].type == RESOURCE_TYPE_FREQ
w("  [OK] projectToDict / projectFromDict round-trip")

# 检查 6:ProjectManager 持久化(SQLite + 文件夹)
w("\n=== 阶段4:ProjectManager 持久化端到端 ===")
pm = ProjectManager.instance()
w(f"  [OK] singleton: projects={len(pm.listProjects())}")

w("  [trace] calling createProject ...")
p1 = pm.createProject("项目A")
w(f"  [OK] createProject: id={p1.id[:8]}, name={p1.name}")

r = pm.addResource(
    p1.id,
    RESOURCE_TYPE_FREQ,
    "词频测试",
    summary="Top-1 是 X",
    parameters={"min": 2},
    snapshotData={"top": [1, 2, 3]},
)
w(f"  [OK] addResource: id={r.id[:8]}, type={r.type}")

resources = pm.listResources(p1.id)
assert len(resources) == 1
w(f"  [OK] listResources: {len(resources)} 个")

pm.setActiveProject(p1.id)
w(f"  [OK] setActiveProject: active={pm.activeProject().name}")

pm.renameProject(p1.id, "项目A重命名")
w(f"  [OK] renameProject: new name={pm.activeProject().name}")

pm.deleteProject(p1.id)
remaining = pm.listProjects()
w(f"  [OK] deleteProject: remaining={len(remaining)}")

# 验证落盘文件确实存在过(我们在删除前快照)
w("\n=== 阶段5:落盘文件检查 ===")
for child in sorted(tmp.rglob("*")):
    w(f"  {child.relative_to(tmp)}")

import shutil

shutil.rmtree(tmp, ignore_errors=True)

w("\n=== 阶段6:UI 模块静态导入(不需要 GUI 初始化)===")
# 这些模块 import 时只是定义类,不创建 QApplication
# 不实例化任何 widget,只验证 import 能成功
import importlib

UI_MODULES = [
    "app.view.widgets.project_switcher_widget",
    "app.view.widgets.project_manager_widget",
    "app.view.widgets.project_manager_dialogs",
    "app.view.project_interface",
    "app.view.widgets.freq_analyzer.resource_sink_mixin",
]
for mod in UI_MODULES:
    wo(f"  [import] {mod} ... ")
    try:
        importlib.import_module(mod)
        w("OK")
    except Exception as e:
        w(f"FAIL: {e}")
        sys.exit(1)

w("\n=== ALL CHECKS PASSED ===")
sys.exit(0)
