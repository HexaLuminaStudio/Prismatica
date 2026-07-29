# coding:utf-8
import os
import sys

from qfluentwidgets import (
    BoolValidator,
    ConfigItem,
    ConfigSerializer,
    FolderValidator,
    OptionsConfigItem,
    OptionsValidator,
    qconfig,
    QConfig,
    Theme,
)

from .setting import CONFIG_FILE, DOWNLOAD_FOLDER, INSTALL_DIR, DATA_FOLDER


def isWin11():
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22000


class Config(QConfig):
    """Config of application"""

    # 语料下载设置
    DownloadSavePath = ConfigItem(
        "CorpusDownload", "DownloadSavePath", str(DOWNLOAD_FOLDER), FolderValidator()
    )
    NumberPerDownloads = OptionsConfigItem(
        "CorpusDownload", "NumberPerDownloads", 100, OptionsValidator([10, 20, 50, 100])
    )
    ThreadPerDownloads = OptionsConfigItem(
        "CorpusDownload",
        "ThreadPerDownloads",
        3,
        OptionsValidator([1, 2, 3, 4, 5, 6]),
    )
    MaximumAttempts = OptionsConfigItem(
        "CorpusDownload",
        "MaximumAttempts",
        3,
        OptionsValidator([i for i in range(1, 11)]),
    )
    HSKLoginToken = ConfigItem(
        "CorpusDownload",
        "HSKLoginToken",
        os.getenv("HSK_LOGIN_TOKEN", ""),
    )
    GlobalLoginToken = ConfigItem(
        "CorpusDownload", "GlobalLoginToken", os.getenv("GLOBAL_LOGIN_TOKEN", "")
    )
    # HSK登录账号密码（通过环境变量注入）
    HSKLoginUsername = ConfigItem(
        "CorpusDownload", "HSKLoginUsername", os.getenv("HSK_LOGIN_USERNAME", "")
    )
    HSKLoginPassword = ConfigItem(
        "CorpusDownload", "HSKLoginPassword", os.getenv("HSK_LOGIN_PASSWORD", "")
    )
    # Global登录账号密码（通过环境变量注入）
    GlobalLoginUsername = ConfigItem(
        "CorpusDownload", "GlobalLoginUsername", os.getenv("GLOBAL_LOGIN_USERNAME", "")
    )
    GlobalLoginPassword = ConfigItem(
        "CorpusDownload", "GlobalLoginPassword", os.getenv("GLOBAL_LOGIN_PASSWORD", "")
    )

    MicaEnabled = ConfigItem("MainWindow", "MicaEnabled", isWin11(), BoolValidator())
    # 是否首次启动(用于决定是否弹出启动期引导窗口)
    FirstLaunch = ConfigItem("MainWindow", "FirstLaunch", True, BoolValidator())
    # 是否已在主窗口展示过「主界面引导遮罩」(Tour Overlay)
    # - 默认 True:首次进入主窗口自动弹出引导遮罩,介绍导航栏/工作区/项目切换器等
    # - 用户完成或跳过引导后写入 False,后续启动不再弹出
    # - 与 FirstLaunch 互不影响:即便清空 FirstLaunch,只要 MainTourShown=True 就跳过
    MainTourShown = ConfigItem("MainWindow", "MainTourShown", False, BoolValidator())
    DpiScale = OptionsConfigItem(
        "MainWindow",
        "DpiScale",
        "Auto",
        OptionsValidator([1.0, 1.25, 1.5, 1.75, 2.0, "Auto"]),
        restart=True,
    )

    # ============================================================
    # AI 聊天设置(参考 qfluentwidgetspro chat demo)
    # ApiKey / BaseUrl / Model 默认支持 DeepSeek,用户可在设置页切换
    # Chat 模型改为自由输入,支持任意 OpenAI 兼容服务的模型 ID
    # ============================================================
    AiApiKey = ConfigItem("AiChat", "AiApiKey", os.getenv("AI_API_KEY", ""))
    AiBaseUrl = ConfigItem(
        "AiChat", "AiBaseUrl", os.getenv("AI_BASE_URL", "https://api.deepseek.com")
    )
    AiModelChat = ConfigItem("AiChat", "AiModelChat", "deepseek-chat")
    # 系统提示词文件路径。运行时会读取该文件作为 system prompt;
    # 为空或读取失败则使用默认提示词。早期版本该字段存的是提示词正文,
    # 兼容策略:如果是多行文本则视为旧数据,运行时同样当作 prompt 直接使用。
    AiSystemPrompt = ConfigItem(
        "AiChat",
        "AiSystemPrompt",
        "",
    )
    AiMaxHistory = OptionsConfigItem(
        "AiChat",
        "AiMaxHistory",
        10,
        OptionsValidator([5, 10, 20, 50]),
    )

    # ============================================================
    # AI 解读（PRD-001 REQ-AI-001）专属配置
    # - 与 AI 聊天共用 AiApiKey / AiBaseUrl / AiModelChat（同一套 LLM）
    # - 仅 AiInsightStyle（Prompt 风格）是解读独有
    # ============================================================
    AiInsightStyle = OptionsConfigItem(
        "AiInsight",
        "AiInsightStyle",
        "学术",
        OptionsValidator(["学术", "通俗", "简洁"]),
    )

    # ============================================================
    # HSK 语料检索库（独立 SQLite + 全 NOCASE 索引）
    # - DbEnabled:    是否启用本模块
    # - DbPath:       SQLite 数据库绝对路径,默认 <DATA>/corpora/hsk_corpus.db
    # - ImportOnStartup: 启动期是否自动从 XlsxPath 导入
    # - XlsxPath:     Excel 数据源路径
    # - SearchLimit:  单次检索返回行数上限
    # ============================================================
    HskCorpusDbEnabled = ConfigItem(
        "HskCorpus",
        "DbEnabled",
        True,
        BoolValidator(),
    )
    HskCorpusDbPath = ConfigItem(
        "HskCorpus",
        "DbPath",
        str(DATA_FOLDER / "corpora" / "hsk_corpus.db"),
    )
    HskCorpusImportOnStartup = ConfigItem(
        "HskCorpus",
        "ImportOnStartup",
        False,
        BoolValidator(),
    )
    HskCorpusXlsxPath = ConfigItem(
        "HskCorpus",
        "XlsxPath",
        str(INSTALL_DIR / "test" / "hsk_corpus.xlsx"),
    )
    HskCorpusSearchLimit = OptionsConfigItem(
        "HskCorpus",
        "SearchLimit",
        500,
        OptionsValidator([100, 200, 500, 1000, 2000]),
    )


cfg = Config()
cfg.themeMode.value = Theme.AUTO
qconfig.load(str(CONFIG_FILE.absolute()), cfg)
