# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**Prismatica（棱溯客户端）** —— 中文学术语料处理桌面应用。基于 PySide6 + qfluentwidgets（Pro 版），提供 HSK / 全球中介语语料库下载、偏误统计、词频 / KWIC / 共现网络 / 句法依存 / 词云 / 情感 / 词语分析等功能，对标 AntConc 的中文场景。Python 3.11（见 `.python-version`），打包工具为 PyInstaller / Nuitka。

完整命名规范（强制优先于 PEP 8）见 [.trae\rules\命名规则.md](.trae\rules\命名规则.md) —— **每次修改 Python 代码前必须重读**。提交信息规范见 [.trae/rules/git-commit-message.md](.trae/rules/git-commit-message.md)，目录职责见 [.trae/rules/代码存放规则.md](.trae/rules/代码存放规则.md)。每次给用户的回答必须是中文！

## 常用命令

### 运行
```bash
# 启动 GUI（项目根目录）
python main.py
# 或通过 VS Code 调试：F5，配置见 .vscode/launch.json（program = main.py）
```

### 依赖管理（uv）
项目使用 `uv`，`pyproject.toml` + `uv.lock` 是依赖权威来源。`pyside6-fluent-widgets-pro` 不在 PyPI 上，通过本地 whl 安装（路径已在 `[tool.uv.sources]` 配置）。清华源已配置为默认 index。
```bash
uv sync                          # 安装所有依赖
uv add <pkg>                     # 新增依赖
uv run python main.py            # 在 venv 中运行
```

### 测试 / Lint
**目前未配置 pytest、ruff、mypy 等工具**（无 `pytest.ini`、无 `tests/` 目录、无 `[tool.ruff]` 等配置）。`test/` 目录存放的是 BCC / HSK 示例语料与 PySide6 示例工程，不参与单元测试。修改代码前请确认是否需要顺带搭建这些工具。

### 资源编译
`app/resource/resource.py` 由 Qt 从 `app/resource/resource.qrc` 自动生成，**不要手动编辑**。新增图标 / 图片后需重新编译 .qrc 才能在代码中通过 `:app/icons/xxx` 引用（与 qrc 中 `<qresource prefix="/app">` 对应）。

### 打包
`app/core/utils/setting.py` 中 `DEBUG = "__compiled__" not in globals()` 用于区分开发模式与 PyInstaller / Nuitka 打包后的 exe 环境。打包前的 `MODE = "DEV"` 在 `app/core/utils/setting.py` 末尾，可切换为 `"TEST"` / `"RES"`。

## 架构

### 入口与生命周期
- [main.py](main.py) —— 入口脚本。设置 `qfluentwidgetspro` License、通过 `configureLogging(MODE)` 初始化统一日志、配置 DPI 缩放、创建 `QApplication`、实例化 `MainWindow`。
- `MainWindow` (`app/view/main_window.py`) 继承自 `MSFluentWindow`，通过 `qfluentwidgets` 的 `NavigationItemPosition` 注册 6 个子界面（Hsk / Global / Bias / FreqAnalyzer 顶部，Task / Setting 底部）。`closeEvent` 会拦截有未完成任务时的退出。

### 分层（视图只能调服务，服务可调 API + Models）
```
app/
├── core/
│   ├── api/        外部接口封装（HTTP、下载）
│   ├── models/     数据模型定义（目前空，预留 Pydantic/dataclass）
│   ├── services/   业务逻辑 + 多线程 Worker（HSK 下载、Global 下载、TaskManager）
│   │               + 云端 Gateway（2026-08-05）：
│   │                 - cloud_api.py       底层 httpx 客户端（11 端点、401 自动 refresh）
│   │                 - cloud_config.py    多环境配置（prod/dev/staging + qconfig 改写）
│   │                 - cloud_device.py    设备 ID 持久化
│   │                 - cloud_cache.py     本地只读快照（user.json/bills.json）
│   │                 - auth_gateway.py    鉴权云端编排（强云端，不再写本地 SQLite）
│   │                 - billing_gateway.py 计费云端编排（me/preauth/settle/refund/listBills）
│   │                 - auth_service.py    本地 license.enc 存储 + token 缓存（强云端后只承担这 2 件事）
│   │                 - billing_service.py 计费业务门面（被 @charged 装饰器调用，内部委托 billing_gateway）
│   └── utils/      通用工具（config、logger、license、encryption、setting、data_paths、signal_bus、constant、device_id、error_messages_cn）
├── view/
│   ├── *_interface.py     顶层子界面（每个导航项一个）
│   ├── main_window.py     主窗口
│   └── widgets/           可复用 QWidget 组件；每个功能模块一个子包
└── resource/      Qt 资源（icons/*.svg, images/*.png, resource.qrc, 自动生成的 resource.py）
```

### 云端鉴权/计费架构变化（2026-08-05）

**强云端决策已落定**：AuthService 不再降级到本地 SQLite，所有写操作走云端。
- `AuthService.redeemCode` → `AuthGateway.redeem` → `CloudApi.redeem`
- `BillingService.{preauth,settle,refund}` → `BillingGateway.{preauth,settle,refund}` → `CloudApi.*`
- 本地 `license.enc` 仅作 token 缓存（走设备指纹派生 AES-GCM）
- `account_db` 不再写 bill，所有 bill 由云端权威决定
- 单测：`tests/test_cloud_api.py` + `tests/test_auth_gateway.py`，12 个用例

**导入规则（强制）**：使用绝对导入，根包为 `app.core.*` / `app.view.*` / `app.resource.*`。视图层**只能**调用 `app.core.services`，禁止直接调 `app.core.api` 或操作 models。资源通过 Qt 资源系统引用，前缀为 `:app/...`（如 `:app/icons/Hsk.svg`、`:app/images/logo.png`），对应 `app/resource/resource.qrc` 中 `<qresource prefix="/app">` 的声明。

### 路径管理（数据文件唯一权威来源）
所有运行时路径常量定义在 [app/core/utils/setting.py](app/core/utils/setting.py) 和 [app/core/utils/data_paths.py](app/core/utils/data_paths.py)。**严禁在代码中硬编码路径**。安装目录由 `sys.frozen` 区分开发模式（项目根）与打包后（exe 所在目录）。

开发与打包目录结构一致：
```
<INSTALL_DIR>/
├── config/        配置文件（config.json，gitignored）
├── download/      用户下载的语料原始文件（gitignored）
├── logs/          应用日志（gitignored）
└── datas/
    ├── corpora_registry.db    语料库注册表（全局唯一）
    ├── corpus_state.json      当前/上次活动语料库记忆
    ├── corpora/*.db           各语料库独立 SQLite
    └── exports/{reports,charts,csv}/
```

`data_paths.py` 启动时会自动从旧路径 `<INSTALL_DIR>/app/data/` 迁移数据，新功能无需关心迁移。

### 词频分析子包（最大且最复杂的模块）
[app/view/widgets/freq_analyzer/](app/view/widgets/freq_analyzer/) 是核心分析模块，遵循 **「主面板 + 引擎 + 弹窗」** 拆分模式：
- `*_widget.py` —— 主面板（QWidget + 业务编排）
- `*_engine.py` —— 纯逻辑引擎（不依赖 Qt 控件，方便单测与复用）
- `dialogs.py` —— 弹窗集中地
- `corpus_store.py` + `corpus_manager.py` —— 语料存储与注册表
- `token_cache.py` —— 分词结果缓存（带模型版本号与文本哈希，模型升级自动失效）
- `clean_coordinator.py` —— 文本清洗调度
- `presets/` —— 预置文件（HSK 词表等 JSON）

子包公共 API 集中在 [`__init__.py`](app/view/widgets/freq_analyzer/__init__.py) 并通过 `__all__` 导出。新增 widget 时务必同步更新 `__all__` 和文件头注释。

### 跨层通信
- `signal_bus` (`app/core/utils/signal_bus.py`) —— 全局信号总线，用于解耦模块间事件。
- `TaskManager.taskManager` (`app/core/services/task_manager.py`) 全局单例，统一管理所有下载 / 业务任务的 pending / in_progress / done / failed 状态；`main_window.closeEvent` 会询问并 `stopAllTasks()`。

### 日志
使用 loguru（`app/core/utils/logger.py`），仅允许入口脚本通过 `configureLogging(MODE)` 初始化一次。运行日志统一写入 `<INSTALL_DIR>/logs/prismatica.log`，不再拆分 debug / error / audit / startup 文件。业务代码统一从 `app.core.utils` 导入 `log`，**不要直接 `from loguru import logger`**。模块自带 API key、Token、邮箱、手机号、身份证号等敏感信息过滤。

### 关键第三方依赖
- `PySide6` + `qfluentwidgetspro`（本地 whl）—— UI
- `hanlp-restful` —— 中文 NLP（分词 / 词性 / 依存）
- `jieba` —— 备用分词（fallback）
- `pandas`, `openpyxl`, `python-docx` —— 语料导入
- `matplotlib`, `networkx`, `wordcloud` —— 可视化
- `loguru` —— 日志
- `requests`, `bcrypt`, `cryptography`, `pyperclip`, `olefile`, `mlxtend`, `psutil` —— 杂项

## 开发注意点

- **命名规范优先于 PEP 8**：变量 / 函数 / 方法参数 / 类属性用 lowerCamelCase（`userName`），类名 UpperCamelCase，常量 `UPPER_SNAKE_CASE`，模块文件名 snake_case 但目录名小写无下划线。完整规则见上述链接。
- **中文提交信息**：格式为 `[需求编号] 动作：标题`（如 `[REQ-001] 完成：实现用户注册接口`），动作用中文（完成 / 修复 / 更新 / 部分实现 / 重构 / 文档 / 配置），标题 ≤ 72 字符。无需求关联时使用 `[chore]` / `[docs]` / `[style]` 前缀。需求编号默认从项目内的需求总结文档（如 `.trae/rules/` 下以 `需求总结` 命名的文件）解析；找不到时主动询问。完整规则见 [.trae/rules/git-commit-message.md](.trae/rules/git-commit-message.md)。
- **新功能流程**：在 `app/core/services/` 加业务服务 → 在 `app/view/widgets/` 加 UI 组件 → 在对应 `*_interface.py` 接入导航。视图层不要直接 import `app.core.api.*`。
- **保存路径**：用户数据全部在 `INSTALL_DIR` 下派生，开发模式下即项目根目录，`config/`、`download/`、`logs/`、`datas/` 均被 `.gitignore` 排除，提交前不会被误纳入。
- **路径常量**：所有数据文件路径统一从 `app.core.utils.data_paths` 或 `app.core.utils.setting` 取，不要在业务代码里写死。
