# coding: utf-8
"""Prismatica Fluent 安装器回归测试。"""

from pathlib import Path

from installer.frontend.core import InstallOptions, buildInstallerArguments, parseProgressState
from installer.frontend.window import InstallerWindow


def test_build_installer_arguments_preserves_user_choices(tmp_path: Path) -> None:
    options = InstallOptions(
        installDir=Path(r"C:\Program Files\Prismatica"),
        createDesktopIcon=True,
        associateProjectFiles=False,
    )

    arguments = buildInstallerArguments(
        options,
        tmp_path / "install.progress",
        tmp_path / "install.log",
    )

    assert "/VERYSILENT" in arguments
    assert "/DIR=C:\\Program Files\\Prismatica" in arguments
    assert "/MERGETASKS=desktopicon,!fileassoc" in arguments


def test_parse_progress_state_clamps_and_recovers() -> None:
    assert parseProgressState("42|正在安装 Prismatica") == (42, "正在安装 Prismatica")
    assert parseProgressState("135|") == (100, "正在写入程序文件")
    assert parseProgressState("invalid") == (0, "正在写入程序文件")


def test_installer_window_uses_real_option_state(qtbot, tmp_path: Path) -> None:
    backendPath = tmp_path / "PrismaticaCoreSetup.exe"
    backendPath.write_bytes(b"placeholder")
    logoPath = Path(r"D:\Desktop\Logo.png")
    licensePath = Path(__file__).parents[1] / "LICENSE.txt"
    window = InstallerWindow(backendPath, logoPath, licensePath)
    qtbot.addWidget(window)

    assert window._stack.count() == 5
    assert not window._licenseCheck.isChecked()
    window._showPage(window.LICENSE_PAGE)
    assert not window._nextButton.isEnabled()
    window._licenseCheck.setChecked(True)
    assert window._nextButton.isEnabled()
    window._showPage(window.OPTIONS_PAGE)
    assert window._fileAssocCheck.isChecked()
