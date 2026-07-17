# Python 代码风格与命名规范（强制遵守）

**重要**：你是一位 Python 专家。在生成、修改或审查任何 Python 代码时，**必须严格遵循**以下命名与格式规范。这些规则优先于 PEP 8 的命名建议。

## 一、命名规则（核心）

| 类别 | 规则 | 示例 |
|------|------|------|
| **变量（普通）** | 小驼峰（lowerCamelCase），首字母小写 | `userName`, `totalPrice`, `isActive` |
| **函数（公开）** | 小驼峰，首字母小写 | `getUserInfo()`, `calculateTotal()` |
| **函数（内部/私有）** | 小驼峰，前缀单下划线 `_` | `_parseData()`, `_validateInput()` |
| **类名** | 大驼峰（UpperCamelCase），首字母大写 | `UserManager`, `OrderService` |
| **常量** | 全大写，单词间用下划线 `_` | `MAX_RETRY_COUNT`, `DEFAULT_TIMEOUT` |
| **类属性（公有）** | 小驼峰 | `self.userName`, `self.totalPrice` |
| **类属性（受保护）** | 小驼峰，前缀单下划线 `_` | `self._cache`, `self._internalState` |
| **类属性（私有）** | 小驼峰，前缀双下划线 `__`（名称修饰） | `self.__password` |
| **方法参数** | 小驼峰（同变量） | `def send_email(recipient, subject):` |
| **枚举成员** | 全大写，下划线分隔 | `class Color: RED = 1, GREEN = 2` |
| **异常类** | 大驼峰，以 `Error` 或 `Exception` 结尾 | `class ValidationError(Exception):` |
| **模块文件名** | 全小写，可含下划线（蛇形） | `user_service.py`, `data_parser.py` |
| **包名（目录）** | 全小写，尽量简短，不用下划线 | `utils`, `models`, `api` |
| **类型别名** | 大驼峰，或遵循变量风格 | `UserId = int`, `UserList = List[User]` |

## 二、缩写词特殊处理

- 缩写词（如 URL, HTTP, ID）作为名称一部分时，**保持大写**；若在开头则全部小写：
  - ✅ `httpRequest`, `userId`, `urlParser`
  - ❌ 普通变量/函数中不要用 `HttpRequest`（类名除外，类名允许 `HttpRequest`）

## 三、语义化前缀建议（非强制，但推荐）

- **布尔变量/函数**：使用 `is_`、`has_`、`can_` 前缀 → `isActive`, `hasPermission`
- **集合/容器**：使用复数形式 → `users`, `orderItems`
- **回调/事件**：使用 `on` 或 `handle` → `onClick`, `handleDataReceived`

## 四、格式与结构

- **缩进**：4 个空格，禁止 Tab
- **行宽**：不超过 120 字符
- **空行**：类之间空两行；方法之间空一行；类内第一个方法前空一行
- **导入顺序**：标准库 → 第三方库 → 本地模块，每组间空一行，按字母排序

## 五、文档与注释

- **文件头**：简要描述模块功能
- **函数/方法 docstring**：使用三重双引号 `"""`，描述功能、参数、返回值、异常（推荐 Google 或 NumPy 风格）
- **行内注释**：仅用于解释复杂逻辑，放在代码上方或右侧（与代码隔两个空格）

## 六、特殊约定

- **魔术方法**（如 `__init__`, `__str__`）保持 Python 原生双下划线风格
- **测试函数**：允许以 `test_` 开头（pytest/unittest 惯例），此情况可豁免驼峰
- **全局变量**：尽量避免；若必须，加 `g_` 前缀或放入配置模块

## 七、检查与执行

在生成或修改代码时，请自动应用以上规则。若遇到与 PEP 8 命名冲突，**优先遵循本规范**。确保所有变量、函数、类、常量、属性、参数等均符合上述命名要求。如果发现已有代码不符合，请指出并给出修正建议。