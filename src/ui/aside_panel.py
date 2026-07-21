"""Aside panel — sync mode toggle and download button (replaces asidepanel.tsx)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import SyncMode

if TYPE_CHECKING:
    from .main_window import MainWindow
    from .synchronizer_page import SynchronizerPage


class AsidePanel(QWidget):
    """Floating panel on the synchronizer page: sync mode toggle + download."""

    def __init__(
        self,
        main_window: "MainWindow",
        sync_page: "SynchronizerPage",
    ) -> None:
        super().__init__(sync_page)
        self._mw = main_window
        self._sync_page = sync_page

        self.setFixedWidth(48)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(8)

        # Sync mode toggle button (lock icon concept)
        self._mode_btn = QPushButton("🔒")
        self._mode_btn.setToolTip("切换打轴模式（选择 / 高亮）")
        self._mode_btn.setFixedSize(36, 36)
        self._mode_btn.clicked.connect(self._toggle_mode)
        layout.addWidget(self._mode_btn)

        # Download button
        self._download_btn = QPushButton("⬇")
        self._download_btn.setToolTip("下载 LRC 文件")
        self._download_btn.setFixedSize(36, 36)
        self._download_btn.clicked.connect(self._on_download)
        layout.addWidget(self._download_btn)

        layout.addStretch()

        self._update_mode_button()

    def _toggle_mode(self) -> None:
        current = self._mw.config.get_sync_mode()
        new_mode = (
            SyncMode.HIGHLIGHT if current == SyncMode.SELECT else SyncMode.SELECT
        )
        self._mw.config.set_sync_mode(new_mode)
        self._update_mode_button()

    def _update_mode_button(self) -> None:
        mode = self._mw.config.get_sync_mode()
        if mode == SyncMode.SELECT:
            self._mode_btn.setText("🔒")
            self._mode_btn.setToolTip("选择模式（点击切换到高亮模式）")
        else:
            self._mode_btn.setText("🔓")
            self._mode_btn.setToolTip("高亮模式（点击切换到选择模式）")

    def _on_download(self) -> None:
        info = self._mw.lrc_state.info
        parts = []
        for key in ("ti", "ar"):
            v = info.get(key)
            if v:
                parts.append(v)
        if not parts:
            parts.append(info.get("al", "lyrics"))
        import re
        filename = re.sub(r'[<>:"/\\|?*]', "_", " - ".join(parts)).strip() + ".lrc"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存 LRC", filename,
            "LRC 文件 (*.lrc);;所有文件 (*)",
        )
        if file_path:
            text = self._mw.lrc_state.stringify(self._mw.format_options)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
