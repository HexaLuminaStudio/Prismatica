# 账户中心重写设计

**日期**:2026-08-06
**状态**:已批准(2026-08-06 用户确认)
**范围**:UI + Service 重写。PrismaticaAPI 后端不动。
**功能集**:基础信息 + 余额、账单 + 设备 + 操作(不新增会话管理 / 使用统计)

---

## 一、目标与边界

### 1.1 目标
完全重写 PrismaticaUI 客户端的「账户中心」子界面,在保持现有功能(用户信息 / 余额 / 充值 / 账单流水 / 本机信息 / 反馈 / 重新激活 / 注销)的前提下,做到:

1. **视觉简洁克制** —— 白底 + 主色描边,主色 `#00b09c` 仅出现在数字与按钮上,与 qfluentwidgets 整体浅色基调一致。
2. **信息架构清晰** —— 仪表盘式:hero 卡 + 账单/设备双列 + 操作卡,一屏看清。
3. **服务层聚合** —— 新增 `AccountFacade`,把 `AuthService` / `BillingService` / `CloudCache` 的账户相关能力合并为统一信号源,UI 只订阅不拼装。

### 1.2 不做(YAGNI)
- 不新增会话详情(token 到期 / 活跃刷新状态)。
- 不新增使用统计(本月分析次数 / 折线图)。
- 不动 `PrismaticaAPI` 后端,不新增云端接口。
- 不重写 `BillTableWidget` 全屏账单明细。
- 不写单元测试(项目尚未配置 pytest)。

---

## 二、当前状态分析

### 2.1 现有文件
- [app/view/account_interface.py](../../app/view/account_interface.py) —— 账户中心主界面,358 行,内联 5 个子区块。
- [app/view/widgets/billing/balance_card.py](../../app/view/widgets/billing/balance_card.py) —— 余额卡(当前 hero 区域)。
- [app/view/widgets/billing/bill_table.py](../../app/view/widgets/billing/bill_table.py) —— 账单表。
- [app/view/widgets/billing/device_panel.py](../../app/view/widgets/billing/device_panel.py) —— 设备面板。
- [app/view/widgets/billing/recharge_dialog.py](../../app/view/widgets/billing/recharge_dialog.py) —— 充值弹窗。
- [app/view/widgets/billing/feedback_entry.py](../../app/view/widgets/billing/feedback_entry.py) —— 反馈弹窗。
- [app/view/widgets/auth/login_dialog.py](../../app/view/widgets/auth/login_dialog.py) —— 登录弹窗(用于重新激活)。

### 2.2 现有问题
1. **信息密度不均**:BalanceCard 占满宽度,BillTable + DevicePanel 比例失衡(已部分修复为 5:4,但仍显笨重)。
2. **标题层级不清**:`AccountInterface` 顶部仅一行 `StrongBodyLabel("账户中心")`,缺乏视觉锚点。
3. **反馈按钮孤立**:反馈按钮散落在底部,与"账户信息"语义割裂。
4. **服务层拼装散落**:UI 直接调 `auth.currentUserId()` / `billing.refreshUserFromCloud()` / `billTable.refresh()`,散落在多处,缺乏统一抽象。

### 2.3 既有约束(必须遵守)
- 命名规范:小驼峰变量/函数、大驼峰类、全大写下划线常量(见 `.trae/rules/命名规则.md`)。
- 分层规则:视图层只允许调用 `app.core.services`,禁止直接 import `app.core.api`(见 `.trae/rules/代码存放规则.md`)。
- 提交信息:中文,`[需求编号] 动作:标题`(本需求编号 `REQ-ACC-001`)。
- 资源引用:`:app/icons/xxx`、`:app/images/xxx`,通过 Qt 资源系统。

---

## 三、视觉与信息架构

### 3.1 整体结构
```
┌─ 顶部 hero 卡 (白底 + #00b09c 描边) ───────────────┐
│  [头像] 张三 [内测体验码]                ¥ 128.50    │
│         UID 19823 · 到期 2027-08-01      可用余额    │
│  ─────────────────────────────────────────────────── │
│       [重新激活]              [充值]                  │
└──────────────────────────────────────────────────────┘
┌─ 账单流水 ──────────┐  ┌─ 本机信息 ──────────────┐
│ 08-06  词频分析×3   │  │ 设备   DESKTOP-7K2      │
│ 08-05  AI 报告      │  │ 状态   [活跃]            │
│ 08-04  充值码       │  │ 登录   2026-08-06 09:12  │
│   查看全部 →         │  │ 最后   10 分钟前         │
└─────────────────────┘  └─────────────────────────┘
┌─ 操作 ────────────────────────────────────────────┐
│   [提交内测反馈]  [重新激活]  [注销本地凭证]       │
└───────────────────────────────────────────────────┘
```

### 3.2 视觉规范
- **背景**:沿用 qfluentwidgets 主窗口浅灰背景 `#f5f5f5`(由主题决定)。
- **卡片**:白底 + `1px solid rgba(0,0,0,0.08)` 边框,圆角 8px,内边距 14px。
- **主色使用边界**:`#00b09c` 仅出现在 hero 卡边框 + 金额数字 + 主要按钮 + 操作链接箭头。账单/设备的 tag 仍保留绿/橙/灰三色语义。
- **金额字号**:hero 卡金额 26px / 700 weight / `#00b09c`;账单行金额 12px。
- **头像**:首字符圆形渐变 `linear-gradient(135deg, #00b09c, #96c93d)`,48×48,白色字。本期不做头像上传。
- **按钮**:
  - 主操作(充值):`#00b09c` 实心,白字,圆角 4px,padding `7px 16px`。
  - 次操作(重新激活):白底 + 主色描边,主色字。
  - 危险操作(注销):白底 + `rgba(187,0,0,0.2)` 描边,`#b00` 字。

### 3.3 响应式约束
- 账单 / 设备双列:账单 `stretch=5`,设备 `stretch=4`(沿用现有比例)。
- 最小宽度:设备卡 280px(防止折叠时布局挤压)。
- 滚动容器:`ScrollArea` 包裹,跟随主窗口自适应。

---

## 四、文件与目录结构

```
app/
├── core/services/
│   └── account_facade.py           ★ 新增
└── view/widgets/account/           ★ 新建子包
    ├── __init__.py                 ★ 新增:导出 __all__
    ├── account_hero_card.py        ★ 新增
    ├── account_bills_card.py       ★ 新增
    ├── account_device_card.py      ★ 新增
    └── account_actions_card.py     ★ 新增
app/view/
└── account_interface.py            ✏️ 重写
```

**未改动文件**:
- `app/core/services/auth_service.py`(仅被 facade 调用)
- `app/core/services/billing_service.py`(仅被 facade 调用)
- `app/core/services/auth_gateway.py`、`billing_gateway.py`、`cloud_cache.py`
- `app/view/widgets/billing/*.py`(沿用)
- `app/view/widgets/auth/login_dialog.py`(沿用)

---

## 五、服务层 — AccountFacade

### 5.1 接口定义

```python
# app/core/services/account_facade.py
from typing import Optional, List, Dict, Any
from PySide6.QtCore import QObject, Signal


class AccountFacade(QObject):
    """账户中心数据聚合服务。

    把 AuthService / BillingService / CloudCache 中账户页相关的能力
    统一为 Qt 信号源,UI 组件只订阅不拼装。
    """

    userChanged = Signal(dict)        # {uid, displayName, plan, expireAt}
    balanceChanged = Signal(float)    # 可用余额(元)
    billsChanged = Signal(list)       # 最近 N 条账单
    deviceChanged = Signal(dict)      # {deviceName, status, loginAt, lastActive}
    authStatusChanged = Signal(bool)  # 是否已激活

    def initialize(self) -> None: ...
    def userSnapshot(self) -> Dict[str, Any]: ...
    def balanceSnapshot(self) -> float: ...
    def billsSnapshot(self) -> List[Dict[str, Any]]: ...
    def deviceSnapshot(self) -> Dict[str, Any]: ...
    def refreshFromCloud(self) -> None: ...


def getAccountFacade() -> AccountFacade: ...
```

### 5.2 内部依赖
- `getAuthService()` —— 用户 ID / 鉴权状态 / 显示名 / 计划类型 / 到期时间。
- `getBillingService()` —— 余额 / 账单。
- `CloudCache` —— 本地缓存(用户 / bills.json)。
- `signalBus.activationStatusChanged` —— 鉴权状态变化。
- `signalBus.licenseCorrupted` / `signalBus.sessionExpired` —— 错误横幅(沿用现有逻辑,facade 不重写)。

### 5.3 关键约束
1. **单例**:`getAccountFacade()` 全局唯一,与 `authService` / `billingService` 一致。
2. **不直接调云端**:facade 内部仍走 `BillingGateway` / `AuthGateway`,不引入 `CloudApi` 直调。
3. **失败吞咽**:云端刷新失败仅记日志,UI 拿本地缓存继续显示。
4. **生命周期**:`AccountInterface` 构造时调 `initialize()`,主程序退出前自动 GC(QObject 父对象由 facade 自身管理)。

---

## 六、UI 组件契约

### 6.1 AccountHeroCard
- **职责**:渲染用户头像 / 显示名 / UID / 计划标签 / 到期日 / 余额 / 动作条。
- **输入方法**:`setUser(dict)`、`setBalance(float)`、`setAuthEnabled(bool)`。
- **对外信号**:`reactivateRequested()`、`rechargeRequested()`。

### 6.2 AccountBillsCard
- **职责**:渲染最近 5 条账单流水 + 「查看全部」入口。
- **输入方法**:`setBills(list)`、`setUserId(str)`。
- **对外信号**:`viewAllRequested()`。

### 6.3 AccountDeviceCard
- **职责**:渲染设备名 / 状态徽章 / 登录时间 / 最后活跃时间。
- **输入方法**:`setDevice(dict)`、`setAuthEnabled(bool)`。
- **对外信号**:无(只读视图)。

### 6.4 AccountActionsCard
- **职责**:收纳反馈 / 重新激活 / 注销三个动作按钮。
- **输入方法**:`setAuthEnabled(bool)`。
- **对外信号**:`feedbackRequested()`、`reactivateRequested()`、`logoutRequested()`。

### 6.5 AccountInterface
**仅做编排**,不再内联具体 UI 元素:
```python
class AccountInterface(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        # 1) 构造 4 个组件,放入布局
        # 2) 调 facade.initialize(),订阅信号
        # 3) 把组件信号转发到 _onRecharge / _onFeedback / _onLogout / _onReactivate
        # 4) 初次 setUser / setBalance / setBills / setDevice(从 snapshot 取)
```

---

## 七、错误处理

| 场景 | 处理 |
|---|---|
| 凭证损坏(`licenseCorrupted`) | 沿用现有顶部红色横幅(在 `AccountInterface` 层处理,不进 facade) |
| 会话失效(`sessionExpired`) | 沿用现有顶部橙色横幅(同上) |
| 云端拉取失败 | facade 内部 swallow + log,UI 拿本地缓存继续显示 |
| 未激活状态 | hero 卡显示空态文案 + 「重新激活」按钮高亮,操作卡「充值/注销」禁用 |
| 账单为空 | 账单卡显示空态文案 + 「暂无账单」 |

---

## 八、实施步骤概要

1. **Step 1**:`AccountFacade` 实现 + 单测用例(若后续配 pytest 可补)。
2. **Step 2**:`view/widgets/account/` 子包骨架 + 4 个组件类(每个 ≤ 200 行)。
3. **Step 3**:`AccountInterface` 重写为编排器。
4. **Step 4**:`MainWindow` 中的 import 路径同步(若有微调)。
5. **Step 5**:`main.py` 启动验证 + 视觉对照截图。

---

## 九、验证步骤

1. **导入校验**:`python -c "from app.core.services.account_facade import getAccountFacade; getAccountFacade().initialize()"` 无报错。
2. **类型校验**:所有新增方法 / 信号命名符合 `.trae/rules/命名规则.md`。
3. **分层校验**:`view/widgets/account/*.py` 中不应 import `app.core.api.*`。
4. **启动验证**:`python main.py` 启动 → 账户页能渲染,信号槽连得通。
5. **交互验证**:模拟鉴权失败 → 注销 / 重新激活 流程不崩。
6. **视觉一致性**:与 `setting_interface.py` / `project_interface.py` 并排截图,字号 / 间距 / 主色使用位置一致。

---

## 十、需求编号

本设计对应需求编号 `REQ-ACC-001` —— 账户中心 UI + Service 重写。

后续提交信息示例:
```
[REQ-ACC-001] 完成:账户中心 UI + Service 重写
[REQ-ACC-001] 重构:新增 AccountFacade 聚合层
```

---

## 十一、附录

### 11.1 与现有模块的契约兼容
- `AccountInterface` 在 `MainWindow.__init__` 中通过 `self.accountInterface = AccountInterface(self)` 构造,本设计不改动该构造签名。
- `signalBus.activationStatusChanged` / `licenseCorrupted` / `sessionExpired` 沿用,`AccountInterface` 仍订阅这些信号。

### 11.2 不在范围内的潜在扩展(留待后续)
- 头像上传(需新增云端接口)
- 账单导出 CSV(沿用现有 `FreqAnalyzer` 导出即可)
- 设备管理(踢出其他设备,需新增云端接口)
- 订阅计划切换(需新增云端接口)

### 11.3 风险与回退
- **风险**:`AccountFacade` 引入新单例,若初始化顺序问题会导致订阅丢失 → 在 `initialize()` 内做幂等保护,二次调用不重复订阅。
- **回退**:git revert 即可,facade 与现有 `authService` / `billingService` 解耦,不会影响启动门。