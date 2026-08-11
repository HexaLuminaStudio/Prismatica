# coding: utf-8
"""Prismatica 桌面端应用外壳视觉令牌。"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget
from qfluentwidgets import isDarkTheme


ACCENT = QColor("#00B09C")
LIGHT_PAGE_BACKGROUND = QColor("#F6F8FA")
DARK_PAGE_BACKGROUND = QColor("#202428")


@dataclass(frozen=True)
class ShellPalette:
    window: QColor
    titleBar: QColor
    navigation: QColor
    content: QColor
    surface: QColor
    surfaceAlt: QColor
    border: QColor
    text: QColor
    mutedText: QColor
    accentText: QColor
    accentSurface: QColor
    successText: QColor
    successSurface: QColor
    warningText: QColor
    warningSurface: QColor
    dangerText: QColor
    dangerSurface: QColor


def shellPalette(dark: bool | None = None) -> ShellPalette:
    dark = isDarkTheme() if dark is None else bool(dark)
    if dark:
        return ShellPalette(
            window=QColor("#181B1E"),
            titleBar=QColor("#1B1E21"),
            navigation=QColor(27, 30, 33, 238),
            content=pageBackgroundColor(True),
            surface=QColor("#292E32"),
            surfaceAlt=QColor("#32383D"),
            border=QColor("#434B51"),
            text=QColor("#F3F6F7"),
            mutedText=QColor("#AEB9BF"),
            accentText=QColor("#59E0D1"),
            accentSurface=QColor("#173D3A"),
            successText=QColor("#85D98A"),
            successSurface=QColor("#223A28"),
            warningText=QColor("#F2C96D"),
            warningSurface=QColor("#473A1D"),
            dangerText=QColor("#FF9B96"),
            dangerSurface=QColor("#4A2727"),
        )

    return ShellPalette(
        window=QColor("#EEF3F6"),
        titleBar=QColor("#F6F8FA"),
        navigation=QColor(247, 249, 250, 238),
        content=pageBackgroundColor(False),
        surface=QColor("#FFFFFF"),
        surfaceAlt=QColor("#F0F3F5"),
        border=QColor("#D6DEE3"),
        text=QColor("#20262C"),
        mutedText=QColor("#596873"),
        accentText=QColor("#007C70"),
        accentSurface=QColor("#E3F6F3"),
        successText=QColor("#107C10"),
        successSurface=QColor("#E7F4E7"),
        warningText=QColor("#725A00"),
        warningSurface=QColor("#FFF7D6"),
        dangerText=QColor("#A4262C"),
        dangerSurface=QColor("#FDE7E9"),
    )


def pageBackgroundColor(dark: bool | None = None) -> QColor:
    """返回所有业务页面共用的主题背景色。"""
    dark = isDarkTheme() if dark is None else bool(dark)
    return QColor(DARK_PAGE_BACKGROUND if dark else LIGHT_PAGE_BACKGROUND)


def applyMatplotlibTheme(root=None, dark: bool | None = None) -> None:
    """统一 Matplotlib 画布与已创建图表的明暗外观。"""
    palette = shellPalette(dark)
    try:
        import matplotlib
        from matplotlib import colors as matplotlibColors
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
    except ImportError:
        return

    matplotlib.rcParams.update(
        {
            "figure.facecolor": palette.surface.name(),
            "savefig.facecolor": palette.surface.name(),
            "axes.facecolor": palette.surface.name(),
            "axes.edgecolor": palette.border.name(),
            "axes.labelcolor": palette.text.name(),
            "text.color": palette.text.name(),
            "xtick.color": palette.mutedText.name(),
            "ytick.color": palette.mutedText.name(),
            "grid.color": palette.border.name(),
        }
    )
    if root is None:
        return

    def isNeutralColor(value) -> bool:
        try:
            red, green, blue = matplotlibColors.to_rgb(value)
        except (TypeError, ValueError):
            return False
        return max(red, green, blue) - min(red, green, blue) < 0.12

    for canvas in root.findChildren(FigureCanvasQTAgg):
        figure = canvas.figure
        figure.set_facecolor(palette.surface.name())
        figure.set_edgecolor(palette.border.name())
        for text in figure.texts:
            if isNeutralColor(text.get_color()):
                text.set_color(palette.text.name())
        for axes in figure.axes:
            axes.set_facecolor(palette.surface.name())
            axes.title.set_color(palette.text.name())
            axes.xaxis.label.set_color(palette.text.name())
            axes.yaxis.label.set_color(palette.text.name())
            axes.tick_params(colors=palette.mutedText.name())
            for spine in axes.spines.values():
                spine.set_color(palette.border.name())
            for text in axes.texts:
                if isNeutralColor(text.get_color()):
                    text.set_color(palette.text.name())
            legend = axes.get_legend()
            if legend is not None:
                legend.get_frame().set_facecolor(palette.surfaceAlt.name())
                legend.get_frame().set_edgecolor(palette.border.name())
                for text in legend.get_texts():
                    text.set_color(palette.text.name())
        canvas.draw_idle()


def setThemeRole(widget: QWidget, role: str, extraStyle: str = "") -> None:
    """为普通 Qt 文本控件声明语义颜色，并立即应用当前主题。"""
    widget.setProperty("prismaticaThemeRole", role)
    widget.setProperty("prismaticaThemeExtraStyle", extraStyle.strip())
    _applyThemeRole(widget)


def applyThemeRoles(root: QWidget) -> None:
    """刷新根控件下所有已声明的语义文字颜色。"""
    candidates = [root, *root.findChildren(QWidget)]
    for widget in candidates:
        if widget.property("prismaticaThemeRole"):
            _applyThemeRole(widget)


def _applyThemeRole(widget: QWidget) -> None:
    palette = shellPalette()
    colors = {
        "text": palette.text,
        "muted": palette.mutedText,
        "accent": palette.accentText,
        "success": palette.successText,
        "warning": palette.warningText,
        "danger": palette.dangerText,
    }
    role = str(widget.property("prismaticaThemeRole") or "text")
    color = colors.get(role, palette.text)
    extraStyle = str(widget.property("prismaticaThemeExtraStyle") or "")
    separator = " " if extraStyle else ""
    widget.setStyleSheet(f"color: {color.name()};{separator}{extraStyle}")


__all__ = [
    "ACCENT",
    "DARK_PAGE_BACKGROUND",
    "LIGHT_PAGE_BACKGROUND",
    "ShellPalette",
    "applyMatplotlibTheme",
    "applyThemeRoles",
    "pageBackgroundColor",
    "setThemeRole",
    "shellPalette",
]
