# coding: utf-8
"""研究项目数据模型（PRD-002 REQ-PROJ-001）

按 PRD-002 §4.2 F1 的实体定义裁剪为 MVP 必要字段。
- Project       研究项目（语料+配置+结果的容器）
- CorpusRef     项目对语料库的引用
- Resource      项目内的分析结果资源
- AiInsight     AI 解读归档

笔记编辑功能已下线(下放到 Word);Note 数据类与 Project.notes 字段同步移除。
旧 project.json 中残留的 notes 字段会在反序列化时自动忽略,无需数据迁移。

命名遵循 lowerCamelCase(变量/属性)/ UpperCamelCase(类)/ UPPER_SNAKE_CASE(常量)。
序列化字段全部用 ISO8601 字符串,避免 datetime 跨平台问题。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 常量(资源类型 / 状态)
# ---------------------------------------------------------------------------

# Resource.type 合法值(与 AiInsightService.TYPE_* 对齐)
RESOURCE_TYPE_FREQ = "freq"
RESOURCE_TYPE_NETWORK = "network"
RESOURCE_TYPE_KWIC = "kwic"
RESOURCE_TYPE_COLLOCATION = "collocation"
RESOURCE_TYPE_CONSTRUCTION = "construction"
RESOURCE_TYPE_DEPENDENCY = "dependency"
RESOURCE_TYPE_KEYWORD_LIST = "keyword_list"
RESOURCE_TYPE_NGRAM_CLUSTER = "ngram_cluster"
RESOURCE_TYPE_SENTIMENT = "sentiment"
RESOURCE_TYPE_WORD_CLOUD = "word_cloud"
RESOURCE_TYPE_WORD_ANALYSIS = "word_analysis"

# Resource.status 合法值(MVP 只用 "new",后续支持 candidate/selected/rejected)
RESOURCE_STATUS_NEW = "new"
RESOURCE_STATUS_CANDIDATE = "candidate"
RESOURCE_STATUS_SELECTED = "selected"
RESOURCE_STATUS_REJECTED = "rejected"
RESOURCE_STATUS_PENDING = "pending"

# Project.status 合法值
PROJECT_STATUS_ACTIVE = "active"
PROJECT_STATUS_PAUSED = "paused"
PROJECT_STATUS_ARCHIVED = "archived"

# CorpusRef.role 合法值
CORPUS_ROLE_TARGET = "target"
CORPUS_ROLE_REFERENCE = "reference"
CORPUS_ROLE_EXPLORATORY = "exploratory"

# 当前 schema 版本(后续字段升级时 +1)
CURRENT_SCHEMA_VERSION = 1

# 当前项目数据格式版本(写入 project.json)
CURRENT_PROJECT_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# 实体
# ---------------------------------------------------------------------------


@dataclass
class CorpusRef:
    """项目对语料库的引用(不复制语料数据,只记 id)"""

    corpusId: str
    role: str = CORPUS_ROLE_TARGET
    note: str = ""


@dataclass
class Resource:
    """项目内的单个分析结果资源"""

    id: str
    type: str  # 参见 RESOURCE_TYPE_*
    title: str
    summary: str = ""  # 200 字以内摘要
    parameters: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    status: str = RESOURCE_STATUS_NEW
    createdAt: str = ""  # ISO8601
    snapshotRelPath: str = ""  # 相对项目根,如 "resources/<uuid>.json"
    thumbnailRelPath: Optional[str] = None  # 缩略图相对路径(MVP 可为 None)


@dataclass
class AiInsight:
    """AI 解读归档(MVP 阶段保留数据类,UI 后续迭代)"""

    id: str
    analysisType: str
    content: str = ""
    citations: List[Dict[str, Any]] = field(default_factory=list)
    confidence: str = "medium"  # "high" | "medium" | "low"
    model: str = ""
    resourceId: Optional[str] = None
    createdAt: str = ""


@dataclass
class Project:
    """研究项目(语料+配置+结果的容器)"""

    id: str
    name: str
    description: str = ""
    tags: List[str] = field(default_factory=list)
    template: Optional[str] = None  # 来源模板名
    version: str = CURRENT_PROJECT_VERSION  # 项目数据格式版本
    schemaVersion: int = CURRENT_SCHEMA_VERSION
    status: str = PROJECT_STATUS_ACTIVE
    createdAt: str = ""
    updatedAt: str = ""
    corporaRefs: List[CorpusRef] = field(default_factory=list)
    resources: List[Resource] = field(default_factory=list)
    aiInsights: List[AiInsight] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def genId() -> str:
    """生成项目/资源/解读的统一 id(UUID4 字符串)"""
    return str(uuid.uuid4())


def projectToDict(project: Project) -> Dict[str, Any]:
    """Project → dict(供 SQLite / JSON 序列化)"""
    return asdict(project)


def projectFromDict(data: Dict[str, Any]) -> Project:
    """dict → Project(宽松模式,缺失字段用默认值)

    注:旧 project.json 中的 notes 字段会被静默忽略(数据模型已删除该字段)。
    """
    corporaRefs = [
        CorpusRef(**r) for r in data.get("corporaRefs", []) if isinstance(r, dict)
    ]
    resources = [
        Resource(**r) for r in data.get("resources", []) if isinstance(r, dict)
    ]
    aiInsights = [
        AiInsight(**a) for a in data.get("aiInsights", []) if isinstance(a, dict)
    ]
    return Project(
        id=data.get("id") or genId(),
        name=data.get("name", "未命名项目"),
        description=data.get("description", ""),
        tags=list(data.get("tags") or []),
        template=data.get("template"),
        version=data.get("version", CURRENT_PROJECT_VERSION),
        schemaVersion=int(data.get("schemaVersion", CURRENT_SCHEMA_VERSION)),
        status=data.get("status", PROJECT_STATUS_ACTIVE),
        createdAt=data.get("createdAt", ""),
        updatedAt=data.get("updatedAt", ""),
        corporaRefs=corporaRefs,
        resources=resources,
        aiInsights=aiInsights,
    )
