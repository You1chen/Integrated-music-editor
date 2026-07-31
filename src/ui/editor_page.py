"""Editor page — LRC text editor with metadata fields and toolbar (replaces editor.tsx)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import PageRoute
from ..core.lrc_parser import FormatOptions

if TYPE_CHECKING:
    from .main_window import MainWindow


class EditorPage(QWidget):
    """LRC text editor with metadata fields and toolbar."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # ── Meta Info Group ─────────────────────────────
        meta_group = QGroupBox("元信息")
        meta_group.setCheckable(True)
        meta_group.setChecked(True)
        meta_layout = QFormLayout(meta_group)
        meta_layout.setSpacing(4)

        self.ti_input = QLineEdit()
        self.ti_input.setPlaceholderText("歌曲名")
        self.ti_input.editingFinished.connect(
            lambda: self._on_info_changed("ti", self.ti_input.text())
        )

        self.ar_input = QLineEdit()
        self.ar_input.setPlaceholderText("艺人名")
        self.ar_input.editingFinished.connect(
            lambda: self._on_info_changed("ar", self.ar_input.text())
        )

        self.al_input = QLineEdit()
        self.al_input.setPlaceholderText("所属专辑")
        self.al_input.editingFinished.connect(
            lambda: self._on_info_changed("al", self.al_input.text())
        )

        meta_layout.addRow("[ti:", self.ti_input)
        meta_layout.addRow("[ar:", self.ar_input)
        meta_layout.addRow("[al:", self.al_input)

        layout.addWidget(meta_group)

        # ── Toolbar ────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        btn_upload = QPushButton("加载文本")
        btn_upload.clicked.connect(self._on_upload_text)
        toolbar.addWidget(btn_upload)

        btn_copy = QPushButton("全选复制")
        btn_copy.clicked.connect(self._on_copy)
        toolbar.addWidget(btn_copy)

        btn_download = QPushButton("下载")
        btn_download.clicked.connect(self._on_download)
        toolbar.addWidget(btn_download)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # ── Text Editor ────────────────────────────────
        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("editorArea")
        self.text_edit.setFont(QFont("Consolas", 13))
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Install event filter to detect focus loss and re-parse
        self.text_edit.installEventFilter(self)
        layout.addWidget(self.text_edit, stretch=1)

        # Connect state changes -> update text edit
        self._mw.lrc_state.state_changed.connect(self._update_from_state)
        self._suppress_update = False

        # Initial load
        self._update_from_state()

    def _update_from_state(self) -> None:
        """Update the text edit from current LRC state."""
        if self._suppress_update:
            return
        text = self._mw.lrc_state.stringify(self._mw.format_options)
        current = self.text_edit.toPlainText()
        if text != current:
            self.text_edit.setPlainText(text)

        # Update metadata fields
        self.ti_input.setText(self._mw.lrc_state.info.get("ti", ""))
        self.ar_input.setText(self._mw.lrc_state.info.get("ar", ""))
        self.al_input.setText(self._mw.lrc_state.info.get("al", ""))

    def _parse_current_text(self) -> None:
        """Parse the text edit content into the state manager."""
        text = self.text_edit.toPlainText()
        self._suppress_update = True
        self._mw.lrc_state.parse(text, self._mw.trim_options)
        self._suppress_update = False

    def _on_info_changed(self, name: str, value: str) -> None:
        self._mw.lrc_state.set_info(name, value)

    def _on_upload_text(self) -> None:
        # Determine initial directory: last path > default browse dir > home
        default_dir = self._mw.config.get_default_browse_dir()
        last_path = self._mw.config.get_last_lrc_path()
        if last_path and os.path.exists(os.path.dirname(last_path)):
            start_dir = os.path.dirname(last_path)
        elif default_dir and os.path.exists(default_dir):
            start_dir = default_dir
        else:
            start_dir = ""

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开歌词文件",
            start_dir,
            "歌词文件 (*.lrc *.txt);;所有文件 (*)",
        )
        if file_path:
            # Save last path if preference is enabled
            self._mw.config.remember_lrc_path(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self.text_edit.setPlainText(text)
                self._parse_current_text()
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载文件失败：{e}")

    def _on_copy(self) -> None:
        self.text_edit.selectAll()
        self.text_edit.copy()
        cursor = self.text_edit.textCursor()
        cursor.clearSelection()
        self.text_edit.setTextCursor(cursor)

    def _on_download(self) -> None:
        # Build filename from metadata
        info = self._mw.lrc_state.info
        parts = []
        for key in ("ti", "ar"):
            v = info.get(key)
            if v:
                parts.append(v)
        if not parts:
            parts.append(info.get("al", "lyrics"))
        filename = " - ".join(parts) + ".lrc"
        # Sanitize
        import re
        filename = re.sub(r'[<>:"/\\|?*]', "_", filename).strip()

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存歌词",
            filename,
            "LRC 文件 (*.lrc);;文本文件 (*.txt);;所有文件 (*)",
        )
        if file_path:
            text = self._mw.lrc_state.stringify(self._mw.format_options)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"保存文件失败：{e}")

    def showEvent(self, event) -> None:
        """Called when this page becomes visible."""
        super().showEvent(event)
        # Sync editor from current state first (state may have changed
        # while user was on other pages). Do NOT parse editor text into
        # state here — the editor may contain stale content, and parsing
        # it would overwrite valid state (e.g., timestamps added in the
        # synchronizer page). Editor→state sync only happens on explicit
        # user actions: FocusOut (eventFilter) or file load (_on_upload_text).
        self._update_from_state()

    def eventFilter(self, obj, event):
        """Parse text when text edit loses focus."""
        from PyQt6.QtCore import QEvent
        if obj is self.text_edit and event.type() == QEvent.Type.FocusOut:
            self._parse_current_text()
        return super().eventFilter(obj, event)
