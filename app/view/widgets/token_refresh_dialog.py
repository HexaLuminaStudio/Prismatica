# coding: utf-8
"""
Token刷新对话框
用户输入账号密码后刷新Token
支持自动填充已保存的账号密码
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit

from qfluentwidgets import (
    MessageBoxBase,
    SubtitleLabel,
    LineEdit,
    PasswordLineEdit,
    BodyLabel,
)


class TokenRefreshDialog(MessageBoxBase):
    """Token刷新对话框"""

    # 数据安全声明
    SECURITY_NOTICE = "密码只在本机用于请求官方 token，不上传、不保存到任何服务器，仅本地缓存以方便下次登录。"

    def __init__(self, title: str, username: str = "", password: str = "", parent=None):
        super().__init__(parent=parent)
        self.titleText = title

        # 标题
        self.titleLabel = SubtitleLabel(title, self)
        self.titleLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 安全声明标签
        self.securityLabel = BodyLabel(self)
        self.securityLabel.setText(self.SECURITY_NOTICE)
        self.securityLabel.setStyleSheet(
            """
            QLabel {
                color: #666666;
                font-size: 13px;
                padding: 8px 12px;
                background: #FFF9E6;
                border-radius: 4px;
                border: 1px solid #FFE58F;
            }
        """
        )
        self.securityLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.securityLabel.setWordWrap(True)

        # 用户名输入
        self.usernameEdit = LineEdit(self)
        self.usernameEdit.setPlaceholderText("请输入用户名/邮箱")
        if username:
            self.usernameEdit.setText(username)

        # 密码输入
        self.passwordEdit = PasswordLineEdit(self)
        self.passwordEdit.setPlaceholderText("请输入密码")
        if password:
            self.passwordEdit.setText(password)

        # 设置对话框属性
        self.widget.setFixedWidth(400)
        self.yesButton.setText("确认")
        self.cancelButton.setText("取消")

        # 布局
        formLayout = QFormLayout()
        formLayout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        formLayout.addRow("用户名：", self.usernameEdit)
        formLayout.addRow("密　码：", self.passwordEdit)

        self.viewLayout.addWidget(self.titleLabel, 0, Qt.AlignmentFlag.AlignCenter)
        self.viewLayout.addSpacing(8)
        self.viewLayout.addWidget(self.securityLabel)
        self.viewLayout.addSpacing(12)
        self.viewLayout.addLayout(formLayout)

    def getCredentials(self):
        """获取输入的凭证"""
        return {
            "username": self.usernameEdit.text().strip(),
            "password": self.passwordEdit.text().strip(),
        }

    def setPlaceholderText(self, username: str = "", password: str = ""):
        """设置占位符文本"""
        if username:
            self.usernameEdit.setPlaceholderText(username)
        if password:
            self.passwordEdit.setPlaceholderText(password)
