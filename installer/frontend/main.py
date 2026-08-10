# coding: utf-8
"""Prismatica Fluent 安装器入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from installer.frontend.runtime_bootstrap import installOptionalDependencyStubs

installOptionalDependencyStubs()

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from installer.frontend.core import bundledPath
from installer.frontend.window import InstallerWindow


def parseArguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prismatica Fluent 安装器")
    parser.add_argument("--backend", type=Path, help="开发模式下指定 Inno 安装核心")
    parser.add_argument("--logo", type=Path, help="开发模式下指定安装器 Logo")
    parser.add_argument("--license", type=Path, help="开发模式下指定许可协议")
    parser.add_argument("--screenshot", type=Path, help="渲染首屏并保存截图后退出")
    return parser.parse_args()


def main() -> int:
    arguments = parseArguments()
    backendPath = arguments.backend or bundledPath("backend/PrismaticaCoreSetup.exe")
    logoPath = arguments.logo or bundledPath("assets/installer_logo.png")
    licensePath = arguments.license or bundledPath("assets/LICENSE.txt")

    app = QApplication(sys.argv)
    app.setApplicationName("Prismatica Installer")
    app.setApplicationDisplayName("安装 Prismatica")
    app.setAttribute(Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings)
    appFont = QFont("Microsoft YaHei UI", 9)
    appFont.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
    app.setFont(appFont)
    setTheme(Theme.AUTO)
    setThemeColor("#00B09C")

    window = InstallerWindow(backendPath, logoPath, licensePath)
    window.show()
    window.raise_()
    window.activateWindow()

    if arguments.screenshot:
        arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)

        def saveScreenshot() -> None:
            window.grab().save(str(arguments.screenshot))
            app.quit()

        from PySide6.QtCore import QTimer

        QTimer.singleShot(1500, saveScreenshot)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
