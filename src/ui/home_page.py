"""Home page — help/intro with tips (replaces home.tsx)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PageRoute

if TYPE_CHECKING:
    from .main_window import MainWindow


class HomePage(QWidget):
    """Help/intro page shown on launch."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(16)

        # Title
        title = QLabel("集成歌曲编辑器")
        title.setObjectName("homeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 28px; font-weight: bold; margin-bottom: 16px;")
        layout.addWidget(title)

        # Tips section
        tips_label = QLabel("提示")
        tips_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(tips_label)

        # Step 1: Go to synchronizer (lyric maker)
        step1 = QLabel("1. 切换到歌词制作页面，导入或粘贴歌词文本。")
        step1.setWordWrap(True)
        step1.setStyleSheet("font-size: 15px;")
        layout.addWidget(step1)

        btn_sync = QPushButton("  → 歌词制作")
        btn_sync.setObjectName("homeSyncButton")
        btn_sync.clicked.connect(
            lambda: main_window.content_stack.set_page(PageRoute.SYNCHRONIZER)
        )
        layout.addWidget(btn_sync)

        # Step 2: Load audio
        step2 = QLabel("2. 点击左下方按钮，载入音频文件。")
        step2.setWordWrap(True)
        step2.setStyleSheet("font-size: 15px;")
        layout.addWidget(step2)

        # Step 3: Start making lyrics
        step3 = QLabel("3. 在歌词制作页面中，开始制作滚动歌词吧～")
        step3.setWordWrap(True)
        step3.setStyleSheet("font-size: 15px;")
        layout.addWidget(step3)

        layout.addStretch()

        # Bottom tips
        tips_text = """
点击这里可以回到这个帮助页面 | 点击这里切换页面
这里可以加载音频，控制播放 | 这里可以调节播放速度
        """
        tips = QLabel(tips_text.strip())
        tips.setWordWrap(True)
        tips.setStyleSheet("font-size: 13px; opacity: 0.7;")
        tips.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(tips)
