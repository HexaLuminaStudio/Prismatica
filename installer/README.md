# Prismatica Fluent 安装器

最终安装包由两层组成：

- `frontend/`：PySide6 + QFluentWidgets 单窗口安装体验。
- `Prismatica.iss`：负责提权、文件复制、升级、注册表、快捷方式和卸载的 Inno 核心。

## 构建

在 PowerShell 中运行：

```powershell
cd E:\Prismatica\PrismaticaUI
.\installer\build_installer.ps1
```

默认输入：

- Logo：`D:\Desktop\Logo.png`
- 程序发行目录：`D:\Desktop\main.dist`
- Inno Setup：`D:\Inno Setup 7\ISCC.exe`

默认输出：`D:\Desktop\PrismaticaSetup.exe`

也可以覆盖参数：

```powershell
.\installer\build_installer.ps1 `
    -SourceLogo "D:\Assets\Logo.png" `
    -AppDistDir "E:\Prismatica\build\main.dist" `
    -OutputDir "D:\Release"
```

安装器外层保持普通用户权限，点击“开始安装”后才由 Inno 核心触发 UAC。前端通过临时进度文件读取真实安装百分比；安装日志写入 `%TEMP%\PrismaticaInstaller`。

