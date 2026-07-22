"""Synchronizer page — the core lyrics timing tool (replaces synchronizer.tsx).

Displays lyrics lines with clickable timestamp buttons,
lets the user insert/remove timestamps while audio plays, using keyboard shortcuts.

Sub-widgets and large dialog flows live in the ``.synchronizer`` sub-package:
- ``._lyric_input``  : ``_LyricInput``
- ``._lyric_row``    : ``_LyricRow``
- ``._translation_row`` : ``_TranslationRow``
- ``._ai_assist``    : AI translate dialog, prompt generation, pattern matching
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import InputAction, SyncMode
from ..core.lrc_parser import (
    Fixed,
    convert_time_to_tag,
)
from .content_stack import is_dark_theme
from .synchronizer._ai_assist import (
    build_prompt_text,
    perform_pattern_matching,
    show_ai_assist_dialog,
)
from .synchronizer._helpers import _contrast_for_theme
from .synchronizer._lyric_input import _LyricInput
from .synchronizer._lyric_row import _LyricRow
from .synchronizer._translation_row import _TranslationRow

if TYPE_CHECKING:
    from .main_window import MainWindow


class SynchronizerPage(QWidget):
    """Core timing tool: shows lyric lines with timestamp buttons,
    handles keyboard input for timestamps.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Toolbar ───────────────────────────────────
        self._toolbar = self._create_toolbar()
        layout.addLayout(self._toolbar)

        # ── Lyric input box (top of lyrics list) ──────
        self._lyric_input = _LyricInput(self)
        self._lyric_input.submit_requested.connect(self._on_lyric_input_submit)
        self._lyric_input.hide()
        layout.addWidget(self._lyric_input)

        # ── Scroll area for lyric rows ────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._rows_container = QWidget()
        self._rows_container.setObjectName("lyricList")
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(4, 4, 4, 4)
        self._rows_layout.setSpacing(1)
        self._rows_layout.addStretch()

        self._scroll.setWidget(self._rows_container)
        layout.addWidget(self._scroll, stretch=1)

        # ── Detect background clicks for deselection ──
        self._scroll.viewport().installEventFilter(self)

        # ── Space Button (optional, absolute-positioned) ─
        self._space_btn: QPushButton | None = None

        # ── Row widgets cache ─────────────────────────
        self._rows: list[_LyricRow] = []
        self._trans_rows: list[_TranslationRow] = []

        # ── State ─────────────────────────────────────
        self._suppress_refresh = False
        self._translation_mode = False
        self._append_target_index: int | None = None  # row index for append-after mode

        # Connect state changes
        self._mw.lrc_state.state_changed.connect(self._refresh_rows)

        # Initial render
        self._rebuild_all()

    # ── Toolbar ─────────────────────────────────────────────

    def _create_toolbar(self) -> QHBoxLayout:
        """Build the top toolbar with action buttons and mode toggle."""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(6)

        # Translate toggle button
        self._btn_translate = QPushButton("翻译")
        self._btn_translate.setToolTip("切换翻译编辑模式")
        self._btn_translate.setCheckable(True)
        self._btn_translate.setChecked(False)
        self._btn_translate.clicked.connect(self._on_translate_toggle)
        toolbar.addWidget(self._btn_translate)

        # Pattern match button (only visible in translation mode)
        self._btn_pattern_match = QPushButton("模式匹配")
        self._btn_pattern_match.setToolTip("从粘贴的翻译文本中匹配翻译")
        self._btn_pattern_match.clicked.connect(self._on_pattern_match)
        self._btn_pattern_match.hide()
        toolbar.addWidget(self._btn_pattern_match)

        # Import button
        self._btn_import = QPushButton("导入")
        self._btn_import.setToolTip("导入 LRC 文件")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        # Export button
        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("导出 LRC 文件")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        # Edit text button
        self._btn_edit = QPushButton("编辑")
        self._btn_edit.setToolTip("直接编辑歌词文本")
        self._btn_edit.clicked.connect(self._on_edit_text)
        toolbar.addWidget(self._btn_edit)

        # Save button — overwrite source file
        self._btn_save = QPushButton("保存")
        self._btn_save.setToolTip("保存并覆写源 LRC 文件")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        # Preview button — read-only output preview
        self._btn_preview = QPushButton("预览")
        self._btn_preview.setToolTip("预览 LRC 输出效果")
        self._btn_preview.clicked.connect(self._on_preview)
        toolbar.addWidget(self._btn_preview)

        toolbar.addStretch()

        return toolbar

    # ── Public API ──────────────────────────────────────────

    def set_space_button_visible(self, visible: bool) -> None:
        """Show/hide the on-screen space button (from preferences)."""
        if visible:
            if self._space_btn is None:
                self._space_btn = QPushButton("空格", self)
                self._space_btn.setFixedSize(100, 100)
                self._space_btn.clicked.connect(self._on_sync)
                self._reposition_space_button()
            self._restyle_space_button()
            self._space_btn.show()
        else:
            if self._space_btn:
                self._space_btn.hide()

    def _restyle_space_button(self) -> None:
        """Apply theme-aware styling to the on-screen space button."""
        if self._space_btn is None:
            return
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        contrast = _contrast_for_theme(theme_color)
        self._space_btn.setStyleSheet(
            f"QPushButton {{"
            f"  color: {contrast}; background-color: {theme_color};"
            f"  border: none; border-radius: 50px;"
            f"  font-size: 14px; font-weight: bold;"
            f"}}"
            f"QPushButton:hover {{"
            f"  border: 2px solid {contrast};"
            f"}}"
        )

    # ── Toolbar Handlers ────────────────────────────────────

    def _on_translate_toggle(self) -> None:
        """Toggle translation editing mode on/off."""
        self._translation_mode = self._btn_translate.isChecked()
        self._btn_pattern_match.setVisible(self._translation_mode)
        self._rebuild_all()

    def _on_translation_changed(self, index: int, text: str) -> None:
        """Live update translation text (no undo push — per-keystroke)."""
        state = self._mw.lrc_state
        if 0 <= index < len(state.lyric):
            state.lyric[index].translation = text
        # Emit state_changed so draft is auto-saved and UI stays fresh
        state.state_changed.emit()

    def _on_translation_finished(self, index: int) -> None:
        """User finished editing (Enter / focus loss) — push one undo snapshot."""
        self._mw.lrc_state._push_undo()

    def _on_ai_assist(
        self, target_text_edit: QPlainTextEdit | None = None
    ) -> None:
        """Open the AI assist dialog — delegates to ``_ai_assist`` module."""
        show_ai_assist_dialog(self, target_text_edit)

    def _build_prompt_text(self) -> tuple[str, int] | None:
        """Build the AI translation prompt — delegates to ``_ai_assist`` module."""
        return build_prompt_text(self)

    def _on_pattern_match(self, initial_text: str = "") -> None:
        """Open a dialog where user pastes LRC text containing translations.

        When *initial_text* is provided, the text area is pre-filled with it
        (used by AI auto-translate to feed the API response into matching).
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("模式匹配 - 匹配翻译")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        # Instructions
        instr_label = QLabel(
            "粘贴包含翻译的 LRC 文本，支持两种格式：\n"
            "  ●  [时间戳]歌词 + [相同时间戳]翻译（成对识别）\n"
            "  ●  [时间戳]翻译文本（直接作为翻译）\n"
        )
        instr_label.setWordWrap(True)
        instr_label.setStyleSheet("font-size: 12px; color: #888; padding-bottom: 4px;")
        dlg_layout.addWidget(instr_label)

        text_edit = QPlainTextEdit()
        text_edit.setPlaceholderText("在此粘贴 LRC 文本…")
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        if initial_text:
            text_edit.setPlainText(initial_text)
        dlg_layout.addWidget(text_edit, stretch=1)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_ai_assist = QPushButton("AI辅助")
        btn_ai_assist.setToolTip("通过 AI 聊天网站或 API 自动生成翻译")
        btn_ai_assist.clicked.connect(lambda: self._on_ai_assist(target_text_edit=text_edit))
        btn_ai_assist.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 6px 14px; border: 1px solid #aaa;"
            "  border-radius: 4px;"
            "}"
            "QPushButton:hover { border-color: #58a6ff; color: #58a6ff; }"
        )
        btn_layout.addWidget(btn_ai_assist)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_match = QPushButton("匹配")
        btn_match.clicked.connect(dialog.accept)
        btn_match.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_match)
        dlg_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            input_text = text_edit.toPlainText().strip()
            if not input_text:
                self._mw.toast_overlay.show_toast("warning", "未输入任何文本")
                return
            # Defer so the modal dialog fully unwinds before we start
            QTimer.singleShot(0, lambda: perform_pattern_matching(self, input_text))

    # ── Import / Export ─────────────────────────────────────

    def _on_import(self) -> None:
        """Import LRC file: clear draft → smart import → file browser."""
        state = self._mw.lrc_state

        # Stop audio timer during the entire import flow.  Otherwise
        # refresh() → state_changed → _save_state() would re-create the
        # draft file between delete_draft() and the user picking a new
        # LRC (the smart-import and file-browser dialogs are modal).
        timer_was_active = self._mw.audio_manager._timer.isActive()
        self._mw.audio_manager._timer.stop()

        try:
            if len(state.lyric) > 0:
                # Clear UI first, then delete draft file
                state.init_from_text("", self._mw.trim_options)
                self._mw.config.delete_draft()

            # Smart import (when audio is loaded)
            audio = self._mw.audio_manager
            if audio.src and audio.duration > 0 and self._mw.config.get_enable_smart_import():
                reply = QMessageBox.question(
                    self,
                    "智能查找",
                    "你是否要寻找当前歌曲的歌词",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self._do_smart_import()
                    return

            # Fallback to file browser
            self._file_browser_import()
        finally:
            if timer_was_active:
                self._mw.audio_manager._timer.start(
                    self._mw.audio_manager._TIMER_INTERVAL
                )

    def _file_browser_import(self) -> None:
        """Open a file dialog for the user to pick an LRC file manually."""
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
            "导入歌词",
            start_dir,
            "歌词文件 (*.lrc *.txt);;所有文件 (*)",
        )
        if file_path:
            if self._mw.config.get_remember_last_lrc():
                self._mw.config.set_last_lrc_path(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                self._mw.toast_overlay.show_toast("success", "歌词已导入")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"导入失败：{e}")

    def _do_smart_import(self) -> None:
        """Look for ``{audio_stem}.lrc`` next to the MP3 and load it.

        Since drafts are now always deleted before import, we only need to
        check for the matching LRC file.  Falls back to asking the user
        whether to create a new empty draft.
        """
        mp3_path = self._mw.config.get_last_mp3_path()
        if not mp3_path:
            self._mw.toast_overlay.show_toast("warning", "未找到音频文件路径")
            self._file_browser_import()
            return

        audio_dir = os.path.dirname(mp3_path)
        stem = os.path.splitext(os.path.basename(mp3_path))[0]
        lrc_path = os.path.join(audio_dir, f"{stem}.lrc")

        # Same-name LRC next to MP3
        if os.path.exists(lrc_path):
            if lrc_path == self._mw.config.get_last_lrc_path():
                with open(lrc_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                self._mw.toast_overlay.show_toast("info", "已是当前歌词文件")
                return
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                if self._mw.config.get_remember_last_lrc():
                    self._mw.config.set_last_lrc_path(lrc_path)
                self._mw.toast_overlay.show_toast("success", "已加载同名歌词文件")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载歌词文件失败：{e}")
        else:
            # Ask to create new draft
            reply = QMessageBox.question(
                self,
                "新建草稿",
                "未找到歌词文件，是否新建草稿？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._mw.lrc_state.init_from_text("", self._mw.trim_options)
                self._mw.toast_overlay.show_toast("success", "已创建新草稿")
            else:
                self._file_browser_import()

    def _on_export(self) -> None:
        """Export current LRC state to a file chosen by user."""
        info = self._mw.lrc_state.info
        parts = []
        for key in ("ti", "ar"):
            v = info.get(key)
            if v:
                parts.append(v)
        if not parts:
            al = info.get("al", "")
            if al:
                parts.append(al)
            else:
                # Fall back to current audio filename
                mp3_path = self._mw.config.get_last_mp3_path()
                if mp3_path:
                    parts.append(os.path.splitext(os.path.basename(mp3_path))[0])
                else:
                    parts.append("lyrics")
        filename = re.sub(r'[<>:"/\\|?*]', "_", " - ".join(parts)).strip() + ".lrc"

        # Determine initial directory
        default_dir = self._mw.config.get_default_browse_dir()
        last_path = self._mw.config.get_last_lrc_path()
        if last_path and os.path.exists(os.path.dirname(last_path)):
            start_dir = os.path.join(os.path.dirname(last_path), filename)
        elif default_dir and os.path.exists(default_dir):
            start_dir = os.path.join(default_dir, filename)
        else:
            start_dir = filename

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出歌词", start_dir,
            "LRC 文件 (*.lrc);;所有文件 (*)",
        )
        if file_path:
            text = self._mw.lrc_state.stringify(self._mw.format_options)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(text)
            self._mw.toast_overlay.show_toast("success", "歌词已导出")

    def _on_edit_text(self) -> None:
        """Open a dialog to directly edit the LRC text."""
        current_text = self._mw.lrc_state.stringify(self._mw.format_options)

        dialog = QDialog(self)
        dialog.setWindowTitle("编辑歌词文本")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        text_edit = QPlainTextEdit()
        text_edit.setPlainText(current_text)
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dlg_layout.addWidget(text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)
        btn_save = QPushButton("保存")
        btn_save.clicked.connect(dialog.accept)
        btn_save.setDefault(True)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        dlg_layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_text = text_edit.toPlainText()
            self._mw.lrc_state.init_from_text(new_text, self._mw.trim_options)
            self._mw.toast_overlay.show_toast("success", "歌词已更新")

    def _on_preview(self) -> None:
        """Show a read-only preview of the LRC output."""
        text = self._mw.lrc_state.stringify(self._mw.format_options)

        dialog = QDialog(self)
        dialog.setWindowTitle("预览 LRC")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

        text_edit = QPlainTextEdit()
        text_edit.setPlainText(text)
        text_edit.setReadOnly(True)
        text_edit.setFont(QFont("Consolas", 13))
        text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        dlg_layout.addWidget(text_edit, stretch=1)

        btn_layout = QHBoxLayout()
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        dlg_layout.addLayout(btn_layout)

        dialog.exec()

    # ── Save ─────────────────────────────────────────────────

    def _on_save(self) -> None:
        """Save current state by overwriting the source LRC file."""
        last_path = self._mw.config.get_last_lrc_path()
        if not last_path or not os.path.exists(last_path):
            self._mw.toast_overlay.show_toast(
                "warning", "未找到源文件，请先导入歌词文件"
            )
            return

        if self._mw.config.get_show_save_warning():
            self._show_save_warning_dialog(last_path)
        else:
            self._do_save(last_path)

    def _do_save(self, path: str) -> None:
        """Actually write the LRC string to the given path."""
        text = self._mw.lrc_state.stringify(self._mw.format_options)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._mw.config.delete_draft()
            self._mw.toast_overlay.show_toast("success", "歌词已保存")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存失败：{e}")

    def _show_save_warning_dialog(self, path: str) -> None:
        """Show the overwrite warning dialog with preview/cancel options."""
        dialog = QDialog(self)
        dialog.setWindowTitle("保存确认")
        dialog.setMinimumWidth(420)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(20, 20, 20, 20)
        dlg_layout.setSpacing(16)

        # Warning icon + message
        msg_label = QLabel(
            "\"保存\"会覆写你的源文件，\n此操作不可撤销，是否预览覆写效果？"
        )
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 14px;")
        dlg_layout.addWidget(msg_label)

        # "Never show again" checkbox
        self._save_warning_cb = QCheckBox("不再显示此警告")
        dlg_layout.addWidget(self._save_warning_cb)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(dialog.reject)

        btn_preview = QPushButton("预览")
        btn_preview.clicked.connect(lambda: self._on_save_preview(dialog))

        btn_layout.addStretch()
        btn_layout.addWidget(btn_preview)
        btn_layout.addWidget(btn_cancel)
        dlg_layout.addLayout(btn_layout)

        result = dialog.exec()

        # Persist "never show" preference
        if self._save_warning_cb.isChecked():
            prefs = self._mw.config.get_preferences()
            prefs["showSaveWarning"] = False
            self._mw.update_preferences(prefs)

        # If user closed via X or cancel, do nothing
        if result != QDialog.DialogCode.Accepted:
            return

        # User confirmed save (after preview)
        self._do_save(path)

    def _on_save_preview(self, warning_dialog: QDialog) -> None:
        """Preview then confirm save flow from the warning dialog."""
        # Show preview first
        self._on_preview()

        # After preview closes, ask for final confirmation
        confirm = QDialog(warning_dialog)
        confirm.setWindowTitle("确认覆写")
        confirm.setMinimumWidth(360)

        cnf_layout = QVBoxLayout(confirm)
        cnf_layout.setContentsMargins(20, 20, 20, 20)
        cnf_layout.setSpacing(14)

        cnf_label = QLabel("确认覆写源文件？")
        cnf_label.setStyleSheet("font-size: 14px;")
        cnf_layout.addWidget(cnf_label)

        cnf_btn_layout = QHBoxLayout()
        cnf_btn_layout.setSpacing(8)
        cnf_btn_cancel = QPushButton("取消")
        cnf_btn_cancel.clicked.connect(confirm.reject)
        cnf_btn_confirm = QPushButton("确认")
        cnf_btn_confirm.clicked.connect(confirm.accept)
        cnf_btn_confirm.setDefault(True)
        cnf_btn_layout.addStretch()
        cnf_btn_layout.addWidget(cnf_btn_cancel)
        cnf_btn_layout.addWidget(cnf_btn_confirm)
        cnf_layout.addLayout(cnf_btn_layout)

        if confirm.exec() == QDialog.DialogCode.Accepted:
            # Close the warning dialog with Accepted to trigger save
            warning_dialog.accept()

    # ── Keyboard Handler ────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts on this page.

        Ports the onKeydown handler from synchronizer.tsx.

        Note: when a lyric line is selected, the app-wide event filter
        in MainWindow already intercepts Space (→ timestamp) and blocks
        audio shortcuts — so those cases never reach this method.
        """
        action = self._mw.keybinding_manager.get_matched_action(event)

        state = self._mw.lrc_state
        audio = self._mw.audio_manager

        if action == InputAction.SYNC:
            # Space: toggle play/pause only when no line is selected
            if state.select_index == -1:
                event.accept()
                audio.toggle()
                return
            # Line is selected → timestamp it
            if audio.duration:
                event.accept()
                seek_time = self._get_sync_time()
                state.next_(seek_time)
                self._append_target_index = state.select_index
                self._scroll_to_row(state.select_index)
                # Auto-seek verify: jump back to the new timestamp after a delay
                prefs = self._mw.config.get_preferences()
                if prefs.get("autoSeekVerify", False):
                    delay_ms = int(prefs.get("autoSeekDelay", 1.0) * 1000)

                    def _seek_back() -> None:
                        was_paused = audio.paused
                        audio.current_time = seek_time
                        # QMediaPlayer.setPosition may pause on some backends
                        if not was_paused and audio.paused:
                            audio.toggle()

                    QTimer.singleShot(delay_ms, _seek_back)
                return

        elif action == InputAction.DELETE_TIME:
            event.accept()
            state.delete_time()
            return

        elif action == InputAction.RESET_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), 0, line.time)
            return

        elif action == InputAction.DECREASE_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), -0.5, line.time)
            return

        elif action == InputAction.INCREASE_OFFSET:
            if audio.duration:
                line = state.lyric[state.select_index] if 0 <= state.select_index < len(state.lyric) else None
                if line and line.time is not None:
                    event.accept()
                    audio.step(event.modifiers(), 0.5, line.time)
            return

        elif action == InputAction.PREV_LINE:
            event.accept()
            state.select(lambda i: i - 1)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.NEXT_LINE:
            event.accept()
            state.select(lambda i: i + 1)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.FIRST_LINE:
            event.accept()
            state.select(lambda _: 0)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.LAST_LINE:
            event.accept()
            state.select(lambda _: float("inf"))
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.PAGE_UP:
            event.accept()
            state.select(lambda i: i - 10)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.PAGE_DOWN:
            event.accept()
            state.select(lambda i: i + 10)
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.UNDO:
            event.accept()
            state.undo()
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.REDO:
            event.accept()
            state.redo()
            self._append_target_index = state.select_index
            self._scroll_to_row(state.select_index)
            return

        elif action == InputAction.COPY_LINE:
            if 0 <= state.select_index < len(state.lyric):
                event.accept()
                state.copy_line(state.select_index)
                self._append_target_index = state.select_index
                target = state.select_index
                QTimer.singleShot(0, lambda: self._scroll_to_row(target))
                self._mw.toast_overlay.show_toast(
                    "success", f"已复制第 {target} 行歌词"
                )
            return

        elif action == InputAction.SPLIT_LYRIC:
            if 0 <= state.select_index < len(state.lyric):
                event.accept()
                self._on_split_lyric(state.select_index)
            return

        elif action == InputAction.SAVE:
            event.accept()
            self._on_save()
            return

        elif action == InputAction.EXPORT:
            event.accept()
            self._on_export()
            return

        elif action == InputAction.TRANSLATE:
            event.accept()
            self._btn_translate.setChecked(not self._btn_translate.isChecked())
            self._on_translate_toggle()
            return

        elif action == InputAction.PREVIEW:
            event.accept()
            self._on_preview()
            return

        elif action == InputAction.LOAD_AUDIO:
            event.accept()
            self._mw.footer_bar.audio_controls._on_load_audio()
            return

        # Esc → deselect current row
        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            state.deselect()
            self._append_target_index = None
            event.accept()
            return

        # Forward audio shortcuts only when no lyric line is selected
        if state.select_index == -1:
            if self._mw.handle_global_key(event):
                return

        super().keyPressEvent(event)

    # ── Event Filter (viewport background clicks) ────────────

    def eventFilter(self, obj, event):
        """Detect clicks on empty space of the scroll area → deselect row."""
        from PyQt6.QtCore import QEvent
        if obj == self._scroll.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            pos = event.position().toPoint()
            child = self._scroll.viewport().childAt(pos)
            # Walk up parent chain — if click is inside a row, let it handle it
            while child is not None:
                if isinstance(child, (_LyricRow, _TranslationRow)):
                    return False
                child = child.parentWidget()
            # Click on empty space → deselect
            self._mw.lrc_state.deselect()
            self._append_target_index = None
            return False
        return super().eventFilter(obj, event)

    # ── Internal: Row Management ────────────────────────────

    def _rebuild_all(self) -> None:
        """Full rebuild: clear and recreate all rows."""
        self._suppress_refresh = True

        # Remove existing lyric rows
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        # Remove existing translation rows
        for row in self._trans_rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._trans_rows.clear()

        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()

        # Remove stretch (last item)
        if self._rows_layout.count() > 0:
            self._rows_layout.takeAt(self._rows_layout.count() - 1)

        for i, line in enumerate(state.lyric):
            # Create lyric row (always present)
            row = _LyricRow(
                index=i,
                line=line,
                fixed=prefs.get("fixed", 3),
                space_start=prefs.get("spaceStart", 1),
                space_end=prefs.get("spaceEnd", 0),
                theme_color=theme_color,
                is_dark=is_dark,
                parent=self._rows_container,
            )
            row.seek_requested.connect(self._on_seek)
            row.edit_requested.connect(self._on_edit_timestamp)
            row.edit_lyric_requested.connect(self._on_edit_lyric)
            row.split_lyric_requested.connect(self._on_split_lyric)
            row.lyric_text_changed.connect(self._on_lyric_text_changed)
            row.lyric_split_done.connect(self._on_lyric_split_done)
            row.append_requested.connect(self._on_append_lyric)
            row.row_clicked.connect(self._on_row_clicked)
            self._rows_layout.addWidget(row)
            self._rows.append(row)

            # If translation mode is active, insert a translation row below
            if self._translation_mode:
                trans_row = _TranslationRow(
                    index=i,
                    line=line,
                    theme_color=theme_color,
                    is_dark=is_dark,
                    parent=self._rows_container,
                )
                trans_row.translation_changed.connect(self._on_translation_changed)
                trans_row.translation_finished.connect(self._on_translation_finished)
                trans_row.row_clicked.connect(self._on_row_clicked)
                self._rows_layout.addWidget(trans_row)
                self._trans_rows.append(trans_row)

        # Re-add stretch
        self._rows_layout.addStretch()

        self._suppress_refresh = False
        self._refresh_rows()

        # Also update input box visibility & styling on full rebuild
        self._update_input_visibility()
        self._restyle_input()

    def _refresh_rows(self) -> None:
        """Update all rows from current state (no rebuild unless count changed)."""
        if self._suppress_refresh:
            return

        state = self._mw.lrc_state

        # Exit edit/split mode on the previously selected row when selection moves
        cur = state.select_index
        prev = getattr(self, "_prev_select_idx", -1)
        if prev != cur and 0 <= prev < len(self._rows):
            self._rows[prev].exit_edit_mode()
        self._prev_select_idx = cur
        prefs = self._mw.config.get_preferences()
        fixed: Fixed = prefs.get("fixed", 3)
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()

        # Update input box visibility
        self._update_input_visibility()
        self._restyle_input()
        self._restyle_space_button()

        # Rebuild if count changed
        if len(self._rows) != len(state.lyric):
            self._rebuild_all()
            return

        sync_mode = self._mw.config.get_sync_mode()

        for i, row in enumerate(self._rows):
            line = state.lyric[i]
            selected = (i == state.select_index)
            at_current = (
                sync_mode == SyncMode.HIGHLIGHT and i == state.current_index
            )
            row.update_state(
                line=line,
                selected=selected,
                at_current=at_current,
                fixed=fixed,
                space_start=space_start,
                space_end=space_end,
                theme_color=theme_color,
                is_dark=is_dark,
            )

        # Update translation rows if active
        if self._translation_mode:
            for i, trans_row in enumerate(self._trans_rows):
                if i < len(state.lyric):
                    trans_row.update_state(
                        line=state.lyric[i],
                        theme_color=theme_color,
                        is_dark=is_dark,
                    )

    # ── Internal: Signal Handlers ──────────────────────────

    def _get_sync_time(self) -> float:
        """Get current audio time minus reaction time offset.

        Returns a time shifted backward by ``reactionTimeMs`` milliseconds
        (clamped to >= 0) so the timestamp lands closer to when the lyric
        actually started rather than when the user reacted.
        """
        reaction_ms = self._mw.config.get_reaction_time_ms()
        return max(0.0, self._mw.audio_manager.current_time - reaction_ms / 1000.0)

    def _on_sync(self) -> None:
        """Called by on-screen space button."""
        audio = self._mw.audio_manager
        if audio.duration:
            seek_time = self._get_sync_time()
            self._mw.lrc_state.next_(seek_time)
            self._append_target_index = self._mw.lrc_state.select_index
            self._scroll_to_row(self._mw.lrc_state.select_index)
            # Auto-seek verify
            prefs = self._mw.config.get_preferences()
            if prefs.get("autoSeekVerify", False):
                delay_ms = int(prefs.get("autoSeekDelay", 1.0) * 1000)

                def _seek_back() -> None:
                    was_paused = audio.paused
                    audio.current_time = seek_time
                    if not was_paused and audio.paused:
                        audio.toggle()

                QTimer.singleShot(delay_ms, _seek_back)

    def _on_seek(self, time: float) -> None:
        """Seek audio to a specific time (timestamp button clicked)."""
        audio = self._mw.audio_manager
        if audio.duration > 0:
            audio.current_time = time

    def _on_edit_timestamp(self, index: int) -> None:
        """Open a dialog to manually edit a timestamp (Ctrl+click)."""
        line = self._mw.lrc_state.lyric[index]
        prefs = self._mw.config.get_preferences()
        current_tag = convert_time_to_tag(line.time, prefs.get("fixed", 3)) if line.time is not None else ""
        new_tag, ok = QInputDialog.getText(
            self,
            "编辑时间戳",
            f"输入时间戳 (mm:ss.xxx)：",
            text=current_tag,
        )
        if ok and new_tag.strip():
            self._parse_and_set_time(index, new_tag.strip())

    def _parse_and_set_time(self, index: int, tag: str) -> None:
        """Parse a user-entered timestamp string and set it on the line."""
        match = re.match(r"^\[?\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]?$", tag)
        if not match:
            return
        mm = int(match.group(1))
        ss = float(match.group(2).replace(":", "."))
        time_val = mm * 60 + ss

        # Select the line first, then set time
        self._mw.lrc_state.select(lambda _: index)
        self._mw.lrc_state.set_time(time_val)

    def _on_row_clicked(self, index: int) -> None:
        """User clicked the text area of a row → select it and set append target."""
        self._mw.lrc_state.select(lambda _: index)
        self._append_target_index = index
        self.setFocus()

    def _on_edit_lyric(self, index: int) -> None:
        """Enter inline edit mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_edit_mode()

    def _on_split_lyric(self, index: int) -> None:
        """Enter inline split mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_split_mode()

    def _on_lyric_text_changed(self, index: int, new_text: str) -> None:
        """Inline edit confirmed — update state."""
        state = self._mw.lrc_state
        if 0 <= index < len(state.lyric):
            if new_text != state.lyric[index].text:
                state.set_text(index, new_text)
                self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行歌词已更新")

    def _on_lyric_split_done(self, index: int, cleaned_text: str, positions: list) -> None:
        """Inline split confirmed — update state with split."""
        state = self._mw.lrc_state
        if not (0 <= index < len(state.lyric)):
            return

        state.set_text(index, cleaned_text)
        state.split_line(index, positions)

        # Defer scroll so the layout processes new rows first
        target = state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target))

        count = len(positions) + 1
        if count == 2:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已一分为二")
        else:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已分裂为 {count} 行")

    def _on_append_lyric(self, index: int) -> None:
        """Append a new empty line after the selected row (same timestamp)."""
        self._mw.lrc_state.append_line(index)
        target = self._mw.lrc_state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target))
        self._mw.toast_overlay.show_toast("success", f"已在第 {index + 1} 行后追加新行")

    # ── Lyric Input Box ────────────────────────────────────

    def _on_lyric_input_submit(self) -> None:
        """Handle Enter in the lyric input box.

        Splits input by newlines, filters empty lines, then either:
        - Inserts at top (timestamp 0.0) when no row has been clicked
        - Appends after the clicked row (same timestamp)
        """
        raw = self._lyric_input.toPlainText()
        # Split by newlines, strip each line, filter empty
        lines = [ln.strip() for ln in raw.split("\n")]
        lines = [ln for ln in lines if ln]

        if not lines:
            return

        state = self._mw.lrc_state

        # Determine insert position and timestamp
        if self._append_target_index is not None and 0 <= self._append_target_index < len(state.lyric):
            # Append after the selected row, with that row's timestamp
            after_index = self._append_target_index
            ref_time = state.lyric[self._append_target_index].time
        else:
            # Insert at top, timestamp 0
            after_index = -1
            ref_time = 0.0

        state.insert_lines(after_index, lines, ref_time)

        # Clear input and reset append target
        self._lyric_input.clear()
        self._append_target_index = None

        # Defer scroll so the layout processes the new rows first —
        # otherwise ensureWidgetVisible uses stale positions and the
        # scrollbar snaps to the top instead of the inserted lines.
        target_idx = state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target_idx))

        count = len(lines)
        if count == 1:
            self._mw.toast_overlay.show_toast("success", "已添加歌词")
        else:
            self._mw.toast_overlay.show_toast("success", f"已添加 {count} 行歌词")

    def _update_input_visibility(self) -> None:
        """Show or hide the lyric input box.

        Visible only when a song is active AND translation mode is off.
        """
        state = self._mw.lrc_state
        audio = self._mw.audio_manager
        has_song = len(state.lyric) > 0 or bool(audio.src)
        self._lyric_input.setVisible(has_song and not self._translation_mode)

    def _restyle_input(self) -> None:
        """Apply theme styling to the lyric input box."""
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()
        fg = "#eeeeee" if is_dark else "#111111"

        self._lyric_input.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  color: {fg};"
            f"  font-size: 14px;"
            f"  background-color: {theme_color}11;"
            f"  border: 1px solid {theme_color};"
            f"  border-radius: 4px;"
            f"  padding: 4px 8px;"
            f"  margin: 2px 4px;"
            f"}}"
            f"QPlainTextEdit:focus {{"
            f"  border-color: {theme_color};"
            f"  background-color: {theme_color}22;"
            f"}}"
        )

    # ── Internal: Scrolling ─────────────────────────────────

    def _scroll_to_row(self, index: int) -> None:
        """Scroll so the given row is visible."""
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self._scroll.ensureWidgetVisible(row, 0, 40)

    # ── Space Button ────────────────────────────────────────

    def _reposition_space_button(self) -> None:
        """Reposition the space button to bottom-right of this widget."""
        if self._space_btn:
            w = self.width()
            h = self.height()
            self._space_btn.move(w - 130, h - 130)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reposition_space_button()

    def showEvent(self, event) -> None:
        """Called when this page becomes visible."""
        super().showEvent(event)
        self._refresh_rows()
        self.setFocus()
        # Apply space button preference
        prefs = self._mw.config.get_preferences()
        self.set_space_button_visible(prefs.get("screenButton", False))
