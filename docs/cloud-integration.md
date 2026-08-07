# Prismatica 云端接入(2026-08-07 P0-A)

## 概述

P0-A 把 Prismatica 从「单机软件」升级为「**多端账号 + 订阅 + 积分**」统一服务。
桌面端(PySide6)现在通过云端 API 走完整的鉴权 / 订阅 / 扣费闭环。

## 架构

```
+--------------------+         HTTP / JSON          +----------------------+
|  PrismaticaUI      |  <-------------------------->   |  PrismaticaAPI       |
|  (PySide6 桌面)    |                                |  (Flask + MySQL 8)   |
|                    |  /v1/auth/*                   |                      |
|  - cloud_auth      |  /v1/account/*                |  - 鉴权 (M2-M4)      |
|  - cloud_account   |  /v1/billing/*                |  - 账号 (M5)         |
|  - cloud_billing   |  /v1/auth/redeem              |  - 订阅 (M6)         |
|  - feature_gate    |                                |  - 计费 (M7)         |
|  - cloud_insight   |                                |  - cron  (M8)        |
|                    |                                |  - 错误码+限速+审计(M9) |
+--------------------+                                +----------------------+
```

## 桌面端模块

| 模块                       | 路径                                          | 职责 |
|----------------------------|-----------------------------------------------|------|
| `CloudApi`                 | `app/core/services/cloud_api.py`               | HTTP 客户端,401 自动 refresh,本地会话状态 |
| `CloudAuth`                | `app/core/services/cloud_auth.py`              | 注册/登录/退出/找回密码/修改密码,本地加密会话文件 |
| `CloudAccount`             | `app/core/services/cloud_account.py`           | `/me` / `devices` / 订阅 / 账单 / 注销 |
| `CloudBilling`             | `app/core/services/cloud_billing.py`           | estimate / preauth / settle / refund |
| `FeatureGate`              | `app/core/services/feature_gate.py`            | 高级功能扣费闭环入口 |
| `CloudInsightService`      | `app/core/services/cloud_insight_service.py`   | AI 洞察接入扣费闭环(M12) |
| `cloud_user`               | `app/core/services/cloud_user.py`              | 多设备上限校验 |

## 桌面端 UI

| 组件                          | 路径                                                  | 功能 |
|-------------------------------|-------------------------------------------------------|------|
| `LoginDialog`                 | `app/view/widgets/account/login_dialog.py`            | 邮箱+密码+注册+忘记密码 |
| `AccountPanel`                | `app/view/widgets/account/account_panel.py`           | 抽屉式「我的账户」:概览/订阅/设备/兑换/修改密码/注销 |
| `AccountNavWidget`            | `app/view/widgets/account/account_nav.py`              | 主窗口 BOTTOM 区的账户入口,根据登录状态切换 |
| `RedeemDialog`                | `app/view/widgets/account/redeem_dialog.py`           | 兑换码对话框 |
| `SubscriptionCard`            | `app/view/widgets/account/subscription_card.py`       | 单条订阅展示 |

## 信号总线(扩展)

`app/core/utils/signal_bus.py` 新增 5 个信号:

| 信号名                  | 载荷                | 触发方                  | 订阅方                |
|------------------------|--------------------|-----------------------|----------------------|
| `sessionChanged`       | `bool`             | CloudAuth.login/logout | 头像 / 抽屉 / 登录窗 |
| `balanceChanged`       | `int`              | CloudApi 余额变化      | 抽屉 / 头像红点      |
| `devicesChanged`       | 无参               | CloudAccount.revoke    | 抽屉「设备」子页     |
| `maxDevicesReached`    | `int`              | CloudApi MAX_DEVICES   | InfoBar / 抽屉       |
| `featureBlocked`       | `(reason, message)`| FeatureGate           | UI 弹通用对话框     |

## 计费闭环流程(M12)

```python
# 1. AI 洞察走 CloudInsightService.runWithBilling
insight = getCloudInsightService()
result = insight.runWithBilling("freq", {"rows": "..."}, corpusMeta=None)
#    ↓ 内部步骤:
#    a. gate.requireFeature("ai_insight", resourceUsed=...)
#       - 未登录 → reason='login_required'
#       - 余额不足 → reason='insufficient_balance'
#       - 通过 → preauth(走 /v1/billing/preauth,自动带 Idempotency-Key)
#    b. 调 LLM
#    c. LLM 完成 → settle(realCost)
#       LLM 失败 → refund(自动)
```

## 配置

`app/core/utils/config.py` 的 `cfg.cloudBaseUrl` 字段(默认 `http://103.236.55.211:8000`):
- dev: 改 `.env` 或运行时 UI
- prod: 通过部署脚本注入

## 错误码参考

详见 `PrismaticaAPI/app/errors.py`,常用:
- `INVALID_CREDENTIALS` / `ACCOUNT_LOCKED` / `EMAIL_ALREADY_USED`
- `MAX_DEVICES_REACHED` (3 台上限)
- `INSUFFICIENT_BALANCE` / `BILL_NOT_FOUND` / `BILL_ALREADY_SETTLED`
- `RATE_LIMITED` (限速)

## 本地会话文件

`cloud_session.enc` 存于 `<DATA_FOLDER>/cloud_session.enc`,由设备特征派生 AES-GCM 密钥加密。
**重装系统 / 更换设备** 会导致无法解密,届时需重新登录。

## 测试覆盖

- `tests/test_cloud_clients.py` — 13 用例(login/register/refresh/logout/change_password/me/devices/revoke/delete/billing/estimate/preauth)
- `tests/test_cloud_insight_service.py` — 2 用例(资源估算)
- `tests/test_signal_bus.py` — 7 用例(信号触发 / MAX_DEVICES_REACHED)
- `tests/test_feature_gate.py` — 3 用例(login_required / insufficient_balance / handleBlockReason)
- `tests/test_account_widgets_smoke.py` — 7 用例(widget 构造)
- **合计 32 用例** 全过。
