# 清洗规则预设目录

> ⚠️ 本目录中的 JSON 文件由插件官方维护，**插件运行时不会写入本目录**。
> 如需新增预设，请直接复制 `template.json` 后按字段说明修改文件名与内容。

## 当前预设

| 文件 | 用途 |
|------|------|
| `chinese_corpus.json` | 中文语料（移除英文/数字/标点/特殊符号，统一小写） |
| `english_corpus.json` | 英文语料（含缩写展开、移除数字/标点/特殊符号） |
| `web_text.json` | 网页文本（去 URL/HTML/邮箱/@提及） |
| `template.json` | **字段示例模板**（建议重命名为 `_template.json` 后排除，或直接删除） |

> ℹ️ 下拉框仅显示本目录中不以 `_` 开头的 JSON 文件。
> `template.json` 默认会被扫描到下拉框中，介意可改名为 `_template.json`。

## JSON 字段规范

```json
{
  "name": "显示名称（必填）",
  "description": "说明（可选，UI 不展示）",
  "rule": {
    "removeEnglish": false,
    "removeDigits": false,
    "removePunct": false,
    "removeWhitespace": true,
    "removeSpecialSymbols": false,
    "customRemoveList": [],
    "customRegexList": [],
    "replaceMap": {},
    "lowercase": false
  }
}
```

### 字段含义

| 字段 | 类型 | 默认 | 含义 |
|------|------|------|------|
| `removeEnglish` | bool | `false` | 移除所有英文字母（A-Z/a-z） |
| `removeDigits` | bool | `false` | 移除所有数字（0-9） |
| `removePunct` | bool | `false` | 移除所有 Unicode 标点（含中文标点） |
| `removeWhitespace` | bool | `true` | 合并连续空白为单个空格 |
| `removeSpecialSymbols` | bool | `false` | 移除 emoji/货币/数学符号等特殊字符 |
| `customRemoveList` | string[] | `[]` | 按字符串字面量移除（每行一项） |
| `customRegexList` | string[] | `[]` | 自定义正则表达式（编译失败会被忽略） |
| `replaceMap` | object | `{}` | 字符串替换字典：`{"原串": "新串"}` |
| `lowercase` | bool | `false` | 是否统一转为小写 |

## 如何新增预设

1. 复制 `template.json` 为新文件，例如 `my_corpus.json`
2. 修改 `name` 字段为你想要的显示名
3. 按需启用/修改 `rule` 中的字段
4. 重启插件或在 UI 中通过下拉框查看

下拉框会自动按文件名字典序扫描 `*.json` 文件，**以下划线 `_` 开头的文件**会被忽略。