# Prismatica 内测账号邮件模板

## 交付文件

- `prismatica-beta-account.html`：正式发送模板，采用邮件客户端兼容的表格布局与内联样式。
- `email-preview.html`：填入虚构示例数据的本地预览文件，仅用于检查效果，请勿直接发送。
- `assets/prismatica-logo.png`：项目 Logo，发送时作为内联图片附加。
- `Prismatica-EULA.txt`：正式软件许可协议，建议作为普通附件随邮件发送。

## 模板变量

发送前逐一替换以下变量，并对变量内容进行 HTML 转义：

| 变量 | 内容 |
| --- | --- |
| `{{recipient_name}}` | 收件人称呼，例如“王老师” |
| `{{recipient_email}}` | 收件人邮箱 |
| `{{beta_account}}` | 为该收件人发放的内测账号 |
| `{{beta_password}}` | 对应的初始密码 |
| `{{download_url}}` | 内测安装包下载地址 |
| `{{guide_url}}` | 使用说明文件的下载地址 |
| `{{qq_group_url}}` | QQ 群邀请链接；邮件中明确标注为自愿加入 |
| `{{send_date}}` | 发送日期，例如“2026 年 8 月 12 日” |

推荐邮件主题：

```text
{{recipient_name}}，您的 Prismatica 内测账号已开通
```

## 图片发送方式

HTML 中项目 Logo 的地址为：

```html
cid:prismatica-logo
```

发送邮件时，将 `assets/prismatica-logo.png` 作为内联附件附加，并设置：

```text
Content-ID: <prismatica-logo>
```

仓库中目前没有独立的 Hexalumina Studio Logo 文件，因此模板使用文字字标，并在 HTML 中保留了 `cid:hexalumina-logo` 的替换示例。获得正式工作室 Logo 后，应替换文字字标，不要继续发送占位素材。

## 安全要求

- 每位内测人员必须单独生成和发送一封邮件，不要把完整名单、全部账号或全部密码写进同一个 HTML 文件。
- 不要通过抄送或密送把不同收件人的凭据放在同一封邮件中。
- 发送日志中不要记录明文密码；如需记录发送状态，仅保存收件人标识、模板版本和发送结果。
- 发送前检查账号、密码与收件人邮箱的对应关系，并先向自己的测试邮箱发送一封预览邮件。
- 建议在 QQ 邮箱、Outlook 和手机邮箱中各检查一次实际效果。
