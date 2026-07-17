# 清洗规则预设目录

> ⚠️ 本目录(`app/view/widgets/freq_analyzer/presets/`)是**内置预设**目录,由项目代码随包发布,**程序不会写入本目录**。
> 如需新增预设,请使用 UI 中的「**导入预设**」按钮,或直接复制本目录中的 JSON 到 `config/clean_presets/` 后修改。

## 双目录策略

Prismatica 同时维护两个预设目录,UI 下拉框会合并显示:

| 目录 | 路径 | 性质 | 标识前缀 |
|------|------|------|----------|
| **内置预设** | `app/view/widgets/freq_analyzer/presets/` | 只读,随项目分发 | `(内置)` |
| **用户预设** | `<install>/config/clean_presets/` | 可写,用户私有数据 | `(自定义)` |

> **`<install>`** = 项目根目录(开发模式)或 exe 所在目录(打包后)。

## 当前内置预设

| 文件 | 用途 |
|------|------|
| `hsk.json` | HSK 语料专用预设 |

> ℹ️ 下拉框仅显示 JSON 文件,**以下划线 `_` 开头的文件**会被忽略(可作为隐藏模板)。

## JSON 字段规范

```json
{
  "name": "显示名称(必填)",
  "description": "说明(可选,UI 不展示)",
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
| `removeEnglish` | bool | `false` | 移除所有英文字母(A-Z/a-z) |
| `removeDigits` | bool | `false` | 移除所有数字(0-9) |
| `removePunct` | bool | `false` | 移除所有 Unicode 标点(含中文标点) |
| `removeWhitespace` | bool | `true` | 合并连续空白为单个空格 |
| `removeSpecialSymbols` | bool | `false` | 移除 emoji/货币/数学符号等特殊字符 |
| `customRemoveList` | string[] | `[]` | 按字符串字面量移除(每行一项) |
| `customRegexList` | string[] | `[]` | 自定义正则表达式(编译失败会被忽略) |
| `replaceMap` | object | `{}` | 字符串替换字典:`{"原串": "新串"}` |
| `lowercase` | bool | `false` | 是否统一转为小写 |

## 如何新增用户预设

### 方法 A:通过 UI(推荐)

1. 打开「语料分析」→「语料导入与清洗」
2. 点击「**导入预设**」按钮
3. 选择一个 JSON 文件(可批量多选)
4. 预设会自动复制到 `config/clean_presets/`,并出现在下拉框中
5. 点击「**应用预设**」立即使用
6. 需要修改可直接点击「**打开目录**」在文件管理器中编辑
7. 不需要的预设可点击「**删除**」(仅对用户预设有效)

### 方法 B:手动操作

1. 复制本目录中的任意 JSON 到 `config/clean_presets/`
2. 修改 `name` 字段为你想要的显示名
3. 按需启用/修改 `rule` 中的字段
4. 重启 Prismatica 或在 UI 下拉框刷新

## 用户预设目录的好处

- ✅ 重新部署软件不会丢失用户的自定义预设
- ✅ 项目源码目录保持只读,避免误删
- ✅ 多个语料库可共享同一套预设
- ✅ 预设可与团队成员通过 Git/网盘分发(`config/` 目录通常不提交到仓库)