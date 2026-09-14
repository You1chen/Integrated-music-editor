"""Synchronizer page — the core lyrics timing tool."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QFont, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from .content_stack import is_dark_theme, theme_events
from .synchronizer._ai_assist import (
    build_prompt_text,
    perform_pattern_matching,
    show_ai_assist_dialog,
)
from .synchronizer._expand_editor import ExpandEditorDialog
from .synchronizer._helpers import _contrast_for_theme, _rgba
from .synchronizer._lyric_input import _LyricInput
from .synchronizer._lyric_row import _LyricRow
from .synchronizer._translation_row import _TranslationRow

if TYPE_CHECKING:
    from .main_window import MainWindow


def _mp3_to_lrc_path(mp3_path: str) -> str:
    """Derive the matching .lrc path from an audio file path."""
    stem = os.path.splitext(os.path.basename(mp3_path))[0]
    return os.path.join(os.path.dirname(mp3_path), f"{stem}.lrc")


class SynchronizerPage(QWidget):
    """Core timing tool: shows lyric lines with timestamp buttons and handles keyboard input."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._toolbar = self._create_toolbar()
        layout.addLayout(self._toolbar)

        self._lyric_input = _LyricInput(self)
        self._lyric_input.submit_requested.connect(self._on_lyric_input_submit)
        self._lyric_input.hide()
        layout.addWidget(self._lyric_input)

        self._btn_expand = QPushButton("⤢")
        self._btn_expand.setParent(self._lyric_input)
        self._btn_expand.setToolTip("展开为大幅歌词编辑窗口")
        self._btn_expand.setFixedSize(26, 26)
        self._btn_expand.setFont(QFont("Segoe UI Symbol", 13))
        self._btn_expand.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_expand.clicked.connect(self._on_expand_input)
        QTimer.singleShot(0, self._reposition_expand_button)

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

        self._scroll.viewport().installEventFilter(self)

        self._lyric_input.installEventFilter(self)

        self._space_btn: QPushButton | None = None

        self._rows: list[_LyricRow] = []
        self._trans_rows: list[_TranslationRow] = []

        self._suppress_refresh = False
        self._translation_mode = False
        self._append_target_index: int | None = None
        self._multi_selected: set[int] = set()
        self._input_was_playing: bool | None = None
        self._trans_was_playing: bool | None = None

        self._mw.lrc_state.state_changed.connect(self._refresh_rows)

        theme_events.changed.connect(self._refresh_rows)

        self._rebuild_all()

    def _create_toolbar(self) -> QHBoxLayout:
        """Build the top toolbar with action buttons and mode toggle."""
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        toolbar.setSpacing(6)

        self._btn_translate = QPushButton("翻译")
        self._btn_translate.setToolTip("切换翻译编辑模式")
        self._btn_translate.setCheckable(True)
        self._btn_translate.setChecked(False)
        self._btn_translate.clicked.connect(self._on_translate_toggle)
        toolbar.addWidget(self._btn_translate)

        self._btn_pattern_match = QPushButton("模式匹配")
        self._btn_pattern_match.setToolTip("从粘贴的翻译文本中匹配翻译")
        self._btn_pattern_match.clicked.connect(self._on_pattern_match)
        self._btn_pattern_match.hide()
        toolbar.addWidget(self._btn_pattern_match)

        self._btn_new = QPushButton("新建")
        self._btn_new.setToolTip("创建与当前音频同名的空白歌词草稿")
        self._btn_new.clicked.connect(self._on_new_draft)
        toolbar.addWidget(self._btn_new)

        self._btn_import = QPushButton("导入")
        self._btn_import.setToolTip("导入 LRC 文件")
        self._btn_import.clicked.connect(self._on_import)
        toolbar.addWidget(self._btn_import)

        self._btn_export = QPushButton("导出")
        self._btn_export.setToolTip("导出 LRC 文件")
        self._btn_export.clicked.connect(self._on_export)
        toolbar.addWidget(self._btn_export)

        self._btn_edit = QPushButton("编辑")
        self._btn_edit.setToolTip("直接编辑歌词文本")
        self._btn_edit.clicked.connect(self._on_edit_text)
        toolbar.addWidget(self._btn_edit)

        self._btn_save = QPushButton("保存")
        self._btn_save.setToolTip("保存并覆写源 LRC 文件")
        self._btn_save.clicked.connect(self._on_save)
        toolbar.addWidget(self._btn_save)

        toolbar.addStretch()

        return toolbar

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

    def _on_translate_toggle(self) -> None:
        self._translation_mode = self._btn_translate.isChecked()
        self._btn_pattern_match.setVisible(self._translation_mode)
        self._rebuild_all()

    def _on_translation_changed(self, index: int, text: str) -> None:
        """Live update translation text on each keystroke."""
        if self._trans_was_playing is None:
            self._trans_was_playing = self._pause_for_edit()
        state = self._mw.lrc_state
        if 0 <= index < len(state.lyric):
            state.lyric[index].translation = text
        state.state_changed.emit()

    def _on_translation_finished(self, index: int) -> None:
        """User finished editing — push one undo snapshot and resume playback."""
        self._mw.lrc_state._push_undo()
        self._resume_after_edit(bool(self._trans_was_playing))
        self._trans_was_playing = None

    def _on_ai_assist(
        self, target_text_edit: QPlainTextEdit | None = None
    ) -> None:
        """Open the AI assist dialog."""
        was_playing = self._pause_for_edit()
        try:
            show_ai_assist_dialog(self, target_text_edit)
        finally:
            self._resume_after_edit(was_playing)

    def _build_prompt_text(self) -> tuple[str, int] | None:
        """Build the AI translation prompt."""
        return build_prompt_text(self)

    def _on_pattern_match(self, initial_text: str = "") -> None:
        """Open a dialog where user pastes LRC text containing translations."""
        was_playing = self._pause_for_edit()
        dialog = QDialog(self)
        dialog.setWindowTitle("模式匹配 - 匹配翻译")
        dialog.resize(700, 500)
        dialog.setMinimumSize(500, 350)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(12, 12, 12, 12)
        dlg_layout.setSpacing(8)

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

        cb_overwrite = QCheckBox("覆写已有翻译（默认跳过已翻译的行）")
        cb_overwrite.setStyleSheet("font-size: 12px; color: #aaa;")
        dlg_layout.addWidget(cb_overwrite)

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

        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                input_text = text_edit.toPlainText().strip()
                if not input_text:
                    self._mw.toast_overlay.show_toast("warning", "未输入任何文本")
                    return
                overwrite = cb_overwrite.isChecked()
                QTimer.singleShot(
                    0,
                    lambda: perform_pattern_matching(
                        self, input_text, overwrite=overwrite
                    ),
                )
        finally:
            self._resume_after_edit(was_playing)

    def _on_new_draft(self) -> None:
        """Create a blank draft named after the currently loaded audio file."""
        was_playing = self._pause_for_edit()
        try:
            mp3_path = self._mw.audio_manager.local_path
            if not mp3_path:
                QMessageBox.information(self, "提示", "请先加载音频文件")
                return

            lrc_path = _mp3_to_lrc_path(mp3_path)

            if os.path.exists(lrc_path):
                QMessageBox.warning(
                    self,
                    "同名文件已存在",
                    f"已存在同名文件：{os.path.basename(lrc_path)}\n"
                    "新建草稿保存时会覆盖它，请换一个文件名。",
                )
                return

            self._mw.lrc_state.init_from_text("", self._mw.trim_options)
            self._mw.config.set_last_lrc_path(lrc_path)
            self._mw.toast_overlay.show_toast("success", f"已创建新草稿：{os.path.basename(lrc_path)}")
        finally:
            self._resume_after_edit(was_playing)

    def _on_import(self) -> None:
        """Import LRC file: clear draft → smart import → file browser."""
        was_playing = self._pause_for_edit()
        state = self._mw.lrc_state

        timer_was_active = self._mw.audio_manager._timer.isActive()
        self._mw.audio_manager._timer.stop()

        try:
            if len(state.lyric) > 0:
                state.init_from_text("", self._mw.trim_options)

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

            self._file_browser_import()
        finally:
            if timer_was_active:
                self._mw.audio_manager._timer.start(
                    self._mw.audio_manager._TIMER_INTERVAL
                )
            self._resume_after_edit(was_playing)

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
            self._mw.config.remember_lrc_path(file_path)

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self._mw.lrc_state.init_from_text(text, self._mw.trim_options)
                self._mw.toast_overlay.show_toast("success", "歌词已导入")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"导入失败：{e}")

    def _do_smart_import(self) -> None:
        """Look for ``{audio_stem}.lrc`` next to the MP3 and load it."""
        mp3_path = self._mw.config.get_last_mp3_path()
        if not mp3_path:
            self._mw.toast_overlay.show_toast("warning", "未找到音频文件路径")
            self._file_browser_import()
            return

        lrc_path = _mp3_to_lrc_path(mp3_path)

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
                self._mw.config.remember_lrc_path(lrc_path)
                self._mw.toast_overlay.show_toast("success", "已加载同名歌词文件")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"加载歌词文件失败：{e}")
        else:
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
        was_playing = self._pause_for_edit()
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
                mp3_path = self._mw.config.get_last_mp3_path()
                if mp3_path:
                    parts.append(os.path.splitext(os.path.basename(mp3_path))[0])
                else:
                    parts.append("lyrics")
        filename = re.sub(r'[<>:"/\\|?*]', "_", " - ".join(parts)).strip() + ".lrc"

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
            self._mw.config.remember_lrc_path(file_path)
            self._mw.toast_overlay.show_toast("success", "歌词已导出")
        self._resume_after_edit(was_playing)

    def _on_edit_text(self) -> None:
        """Open a dialog to directly edit the LRC text."""
        was_playing = self._pause_for_edit()
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
            self._do_save()
        self._resume_after_edit(was_playing)

    def _on_preview(self) -> None:
        """Show a read-only preview of the LRC output."""
        was_playing = self._pause_for_edit()
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

        try:
            dialog.exec()
        finally:
            self._resume_after_edit(was_playing)

    def _on_save(self) -> None:
        """Save current state by overwriting the source LRC file."""
        if self._mw.config.get_show_save_warning():
            self._show_save_warning_dialog()
        else:
            self._do_save()

    def _do_save(self) -> None:
        """Overwrite the source LRC file."""
        text = self._mw.lrc_state.stringify(self._mw.format_options)
        lrc_path = self._mw.config.get_last_lrc_path()
        if not lrc_path:
            mp3_path = self._mw.audio_manager.local_path
            if mp3_path:
                lrc_path = _mp3_to_lrc_path(mp3_path)
                self._mw.config.set_last_lrc_path(lrc_path)
        ok, msg = self._mw.config.overwrite_lrc(text)
        if ok:
            self._mw.toast_overlay.show_toast("success", msg)
            self._mw.lrc_state.state_changed.emit()
            self._notify_playlist()
        else:
            QMessageBox.warning(self, "错误", msg)

    def _notify_playlist(self) -> None:
        """Refresh the playlist's 📝 indicator for this song."""
        try:
            from ..core.constants import PageRoute
            playlist_page = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
            if playlist_page is not None and hasattr(playlist_page, "refresh_song"):
                path = self._mw.audio_manager.local_path
                if path:
                    playlist_page.refresh_song(path)
        except Exception:
            pass

    def _show_save_warning_dialog(self) -> None:
        """Show the overwrite warning dialog with preview/cancel options."""
        was_playing = self._pause_for_edit()
        dialog = QDialog(self)
        dialog.setWindowTitle("保存确认")
        dialog.setMinimumWidth(420)

        dlg_layout = QVBoxLayout(dialog)
        dlg_layout.setContentsMargins(20, 20, 20, 20)
        dlg_layout.setSpacing(16)

        msg_label = QLabel(
            "\"保存\"会覆写你的源文件，\n此操作不可撤销，是否预览覆写效果？"
        )
        msg_label.setWordWrap(True)
        msg_label.setStyleSheet("font-size: 14px;")
        dlg_layout.addWidget(msg_label)

        self._save_warning_cb = QCheckBox("不再显示此警告")
        dlg_layout.addWidget(self._save_warning_cb)

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

        try:
            result = dialog.exec()

            if self._save_warning_cb.isChecked():
                prefs = self._mw.config.get_preferences()
                prefs["showSaveWarning"] = False
                self._mw.update_preferences(prefs)

            if result != QDialog.DialogCode.Accepted:
                return

            self._do_save()
        finally:
            self._resume_after_edit(was_playing)

    def _on_save_preview(self, warning_dialog: QDialog) -> None:
        """Preview then confirm save flow from the warning dialog."""
        self._on_preview()

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
            warning_dialog.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Handle keyboard shortcuts on this page."""
        action = self._mw.keybinding_manager.get_matched_action(event)

        state = self._mw.lrc_state
        audio = self._mw.audio_manager

        if action == InputAction.DELETE_TIME:
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
                was_playing = self._pause_for_edit()
                state.copy_line(state.select_index)
                self._append_target_index = state.select_index
                target = state.select_index
                QTimer.singleShot(0, lambda: self._scroll_to_row(target))
                self._mw.toast_overlay.show_toast(
                    "success", f"已复制第 {target} 行歌词"
                )
                self._resume_after_edit(was_playing)
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

        elif action == InputAction.DELETE_LINES:
            event.accept()
            self._on_delete_selected()
            return

        elif action == InputAction.MERGE_LINES:
            event.accept()
            self._on_merge_selected()
            return

        elif action == InputAction.SELECT_ALL:
            event.accept()
            n = len(state.lyric)
            if n > 0:
                self._multi_selected = set(range(n))
                state.select(lambda _: 0)
            return

        if event.key() == Qt.Key.Key_Escape and not event.modifiers():
            self._multi_selected.clear()
            state.deselect()
            self._append_target_index = None
            event.accept()
            return

        if state.select_index == -1:
            if self._mw.handle_global_key(event):
                return

        super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        """Handle scroll-area background clicks and lyric-input focus/resize events."""
        if obj == self._lyric_input:
            if event.type() == QEvent.Type.FocusIn:
                if self._input_was_playing is None:
                    self._input_was_playing = self._pause_for_edit()
            elif event.type() == QEvent.Type.Resize:
                self._reposition_expand_button()
            return super().eventFilter(obj, event)
        if obj == self._scroll.viewport() and event.type() == QEvent.Type.MouseButtonPress:
            pos = event.position().toPoint()
            child = self._scroll.viewport().childAt(pos)
            while child is not None:
                if isinstance(child, (_LyricRow, _TranslationRow)):
                    return False
                child = child.parentWidget()
            self._multi_selected.clear()
            self._mw.lrc_state.deselect()
            self._append_target_index = None
            return False
        return super().eventFilter(obj, event)

    def _rebuild_all(self) -> None:
        """Full rebuild: clear and recreate all rows."""
        self._suppress_refresh = True

        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        for row in self._trans_rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._trans_rows.clear()

        state = self._mw.lrc_state
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()

        if self._rows_layout.count() > 0:
            self._rows_layout.takeAt(self._rows_layout.count() - 1)

        for i, line in enumerate(state.lyric):
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
            row.multi_select_toggled.connect(self._on_multi_select_toggled)
            row.delete_requested.connect(self._on_delete_selected)
            row.merge_requested.connect(self._on_merge_selected)
            self._rows_layout.addWidget(row)
            self._rows.append(row)

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

        self._rows_layout.addStretch()

        self._suppress_refresh = False
        self._refresh_rows()

        self._update_input_visibility()
        self._restyle_input()

    def _refresh_rows(self) -> None:
        """Update all rows from current state (no rebuild unless count changed)."""
        if self._suppress_refresh:
            return

        state = self._mw.lrc_state

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

        self._update_input_visibility()
        self._restyle_input()
        self._restyle_space_button()

        if len(self._rows) != len(state.lyric):
            self._multi_selected.clear()
            self._rebuild_all()
            return

        n = len(state.lyric)
        self._multi_selected = {i for i in self._multi_selected if 0 <= i < n}

        sync_mode = self._mw.config.get_sync_mode()

        for i, row in enumerate(self._rows):
            line = state.lyric[i]
            selected = (i == state.select_index)
            at_current = (
                sync_mode == SyncMode.HIGHLIGHT and i == state.current_index
            )
            multi_sel = i in self._multi_selected
            row.update_state(
                line=line,
                selected=selected,
                at_current=at_current,
                fixed=fixed,
                space_start=space_start,
                space_end=space_end,
                theme_color=theme_color,
                is_dark=is_dark,
                multi_selected=multi_sel,
            )

        if self._translation_mode:
            for i, trans_row in enumerate(self._trans_rows):
                if i < len(state.lyric):
                    trans_row.update_state(
                        line=state.lyric[i],
                        theme_color=theme_color,
                        is_dark=is_dark,
                        multi_selected=(i in self._multi_selected),
                    )

    def _get_sync_time(self) -> float:
        """Return current audio time minus the reaction-time offset."""
        reaction_ms = self._mw.config.get_reaction_time_ms()
        return max(0.0, self._mw.audio_manager.current_time - reaction_ms / 1000.0)

    def _pause_for_edit(self) -> bool:
        """Pause playback for an editing session; True when it was playing."""
        audio = self._mw.audio_manager
        was_playing = not audio.paused
        if was_playing:
            audio.toggle()
        return was_playing

    def _resume_after_edit(self, was_playing: bool) -> None:
        """Resume playback only if the editing session had paused it."""
        if was_playing and self._mw.audio_manager.paused:
            self._mw.audio_manager.toggle()

    def _on_sync(self) -> None:
        """Called by on-screen space button."""
        audio = self._mw.audio_manager
        if audio.duration:
            seek_time = self._get_sync_time()
            self._mw.lrc_state.next_(seek_time)
            self._append_target_index = self._mw.lrc_state.select_index
            self._scroll_to_row(self._mw.lrc_state.select_index)
            prefs = self._mw.config.get_preferences()
            if prefs.get("autoSeekVerify", False):
                delay_ms = int(prefs.get("autoSeekDelay", 1.0) * 1000)

                def _seek_back() -> None:
                    was_paused = audio.paused
                    audio.current_time = seek_time
                    if not was_paused and audio.paused:
                        audio.toggle()

                QTimer.singleShot(delay_ms, _seek_back)

    def _on_jump_prev_timestamp(self) -> None:
        """Seek to the previous line's timestamp without changing selection."""
        state = self._mw.lrc_state
        idx = state.select_index
        if idx < 0:
            return
        for i in range(idx - 1, -1, -1):
            t = state.lyric[i].time
            if t is not None and t > 0:
                self._mw.audio_manager.current_time = t
                return

    def _on_jump_next_timestamp(self) -> None:
        """Seek to the next line's timestamp without changing selection."""
        state = self._mw.lrc_state
        idx = state.select_index
        if idx < 0:
            return
        n = len(state.lyric)
        for i in range(idx + 1, n):
            t = state.lyric[i].time
            if t is not None and t > 0:
                self._mw.audio_manager.current_time = t
                return

    def _on_seek(self, time: float) -> None:
        """Seek audio to a specific time (timestamp button clicked)."""
        audio = self._mw.audio_manager
        if audio.duration > 0:
            audio.current_time = time

    def _on_edit_timestamp(self, index: int) -> None:
        """Open a dialog to manually edit a timestamp."""
        was_playing = self._pause_for_edit()
        line = self._mw.lrc_state.lyric[index]
        prefs = self._mw.config.get_preferences()
        current_tag = convert_time_to_tag(line.time, prefs.get("fixed", 3)) if line.time is not None else ""

        dialog = QInputDialog(self)
        dialog.setWindowTitle("编辑时间戳")
        dialog.setLabelText("输入时间戳 (mm:ss.xxx)：")
        dialog.setTextValue(current_tag)

        line_edit = dialog.findChild(QLineEdit)
        if line_edit and "." in current_tag:
            dot = current_tag.rfind(".")
            start = dot + 1
            length = len(current_tag) - dot - 2
            if length > 0:
                QTimer.singleShot(
                    0,
                    lambda le=line_edit, s=start, l=length: le.setSelection(s, l),
                )

        ok = dialog.exec() == QDialog.DialogCode.Accepted
        new_tag = dialog.textValue()
        if ok and new_tag.strip():
            self._parse_and_set_time(index, new_tag.strip())
        self._resume_after_edit(was_playing)

    def _parse_and_set_time(self, index: int, tag: str) -> None:
        """Parse a user-entered timestamp string and set it on the line."""
        match = re.match(r"^\[?\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]?$", tag)
        if not match:
            return
        mm = int(match.group(1))
        ss = float(match.group(2).replace(":", "."))
        time_val = mm * 60 + ss

        self._mw.lrc_state.select(lambda _: index)
        self._mw.lrc_state.set_time(time_val)

        audio = self._mw.audio_manager
        if audio.duration > 0:
            audio.current_time = time_val
            if audio.paused:
                audio.toggle()

    def _on_row_clicked(self, index: int) -> None:
        """User clicked the text area of a row → single-select, clear multi-select."""
        self._multi_selected.clear()
        self._mw.lrc_state.select(lambda _: index)
        self._append_target_index = index
        self.setFocus()

    def _on_multi_select_toggled(self, index: int) -> None:
        """Toggle a row in or out of the multi-selection (Ctrl+click)."""
        if index in self._multi_selected:
            self._multi_selected.discard(index)
        else:
            if not self._multi_selected:
                cur = self._mw.lrc_state.select_index
                n = len(self._mw.lrc_state.lyric)
                if 0 <= cur < n and cur != index:
                    self._multi_selected.add(cur)
            self._multi_selected.add(index)
        self._mw.lrc_state.select(lambda _: index)
        self._refresh_rows()

    def _get_effective_selection(self) -> set[int]:
        """Return the indices selected for batch operations."""
        if self._multi_selected:
            return set(self._multi_selected)
        idx = self._mw.lrc_state.select_index
        if 0 <= idx < len(self._mw.lrc_state.lyric):
            return {idx}
        return set()

    def _on_delete_selected(self) -> None:
        """Delete all selected lines (from context menu or Delete key)."""
        selected = self._get_effective_selection()
        if not selected:
            return
        was_playing = self._pause_for_edit()
        count = len(selected)
        self._mw.lrc_state.delete_lines(selected)
        self._multi_selected.clear()
        self._append_target_index = None
        if count == 1:
            self._mw.toast_overlay.show_toast("success", "已删除 1 行")
        else:
            self._mw.toast_overlay.show_toast("success", f"已删除 {count} 行")
        self._resume_after_edit(was_playing)

    def _on_merge_selected(self) -> None:
        """Merge all selected lines (from context menu or Ctrl+H)."""
        selected = self._get_effective_selection()
        if len(selected) < 2:
            self._mw.toast_overlay.show_toast(
                "warning", "至少需要选中两行才能合并"
            )
            return
        sorted_idx = sorted(selected)
        is_adjacent = all(
            sorted_idx[i] == sorted_idx[i - 1] + 1
            for i in range(1, len(sorted_idx))
        )
        if not is_adjacent:
            self._mw.toast_overlay.show_toast(
                "warning", "选中的行不相邻，无法合并"
            )
            return
        count = len(selected)
        was_playing = self._pause_for_edit()
        self._mw.lrc_state.merge_lines(selected)
        self._multi_selected.clear()
        self._append_target_index = None
        self._mw.toast_overlay.show_toast(
            "success", f"已将 {count} 行合并为 1 行"
        )
        self._resume_after_edit(was_playing)

    def _on_edit_lyric(self, index: int) -> None:
        """Enter inline edit mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_edit_mode()

    def _on_split_lyric(self, index: int) -> None:
        """Enter inline split mode on the specified row."""
        if 0 <= index < len(self._rows):
            self._rows[index].enter_split_mode()

    def _on_lyric_text_changed(self, index: int, new_text: str) -> None:
        """Inline edit confirmed — update state, or drop the line when it was cleared."""
        state = self._mw.lrc_state
        if not (0 <= index < len(state.lyric)):
            return
        old_text = state.lyric[index].text
        if new_text == old_text:
            return
        was_playing = self._pause_for_edit()
        if not new_text.strip() and old_text.strip():
            state.delete_lines({index})
            self._mw.toast_overlay.show_toast(
                "success", f"第 {index + 1} 行歌词已删除"
            )
        else:
            state.set_text(index, new_text)
            self._mw.toast_overlay.show_toast(
                "success", f"第 {index + 1} 行歌词已更新"
            )
        self._resume_after_edit(was_playing)

    def _on_lyric_split_done(self, index: int, cleaned_text: str, positions: list) -> None:
        """Inline split confirmed — update state with the split."""
        state = self._mw.lrc_state
        if not (0 <= index < len(state.lyric)):
            return

        was_playing = self._pause_for_edit()
        state.set_text(index, cleaned_text)
        state.split_line(index, positions)

        target = state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target))

        count = len(positions) + 1
        if count == 2:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已一分为二")
        else:
            self._mw.toast_overlay.show_toast("success", f"第 {index + 1} 行已分裂为 {count} 行")
        self._resume_after_edit(was_playing)

    def _on_append_lyric(self, index: int) -> None:
        """Append an empty line after the selected row at the current audio position."""
        was_playing = self._pause_for_edit()
        self._mw.lrc_state.append_line(index, time=self._get_sync_time())
        target = self._mw.lrc_state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target))
        self._mw.toast_overlay.show_toast("success", f"已在第 {index + 1} 行后追加新行")
        self._resume_after_edit(was_playing)

    def _on_lyric_input_submit(self) -> None:
        """Handle Enter in the lyric input box."""
        self._insert_lyric_lines(self._lyric_input.toPlainText())
        self._lyric_input.clear()
        self._resume_after_edit(bool(self._input_was_playing))
        self._input_was_playing = None

    def _ensure_draft_for_song(self) -> None:
        """Point the draft at the loaded song's same-name .lrc when none is set."""
        mp3_path = self._mw.audio_manager.local_path
        if not mp3_path:
            return
        lrc_path = _mp3_to_lrc_path(mp3_path)
        if os.path.exists(lrc_path) or self._mw.config.get_last_lrc_path():
            return
        self._mw.config.set_last_lrc_path(lrc_path)
        self._mw.toast_overlay.show_toast("success", f"已自动新建草稿：{os.path.basename(lrc_path)}")

    def _insert_lyric_lines(self, raw: str) -> int:
        """Insert lyric text into the state, one line per lyric."""
        lines = [ln.strip() for ln in raw.split("\n")]
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()

        if not lines:
            return 0

        self._ensure_draft_for_song()

        state = self._mw.lrc_state

        if self._append_target_index is not None and 0 <= self._append_target_index < len(state.lyric):
            after_index = self._append_target_index
            ref_time = state.lyric[self._append_target_index].time
        else:
            after_index = -1
            ref_time = 0.0

        state.insert_lines(after_index, lines, ref_time)
        self._append_target_index = None

        target_idx = state.select_index
        QTimer.singleShot(0, lambda: self._scroll_to_row(target_idx))

        count = len(lines)
        if count == 1:
            self._mw.toast_overlay.show_toast("success", "已添加歌词")
        else:
            self._mw.toast_overlay.show_toast("success", f"已添加 {count} 行歌词")
        return count

    def _on_expand_input(self) -> None:
        """Open the large lyric-entry window (expanded input box)."""
        dialog = ExpandEditorDialog(
            self._mw,
            initial_text=self._lyric_input.toPlainText(),
            parent=self,
        )
        dialog.lyrics_submitted.connect(self._on_expand_submit)
        dialog.exec()

    def _on_expand_submit(self, text: str) -> None:
        """Commit text from the expanded editor as lyric lines."""
        if self._insert_lyric_lines(text) > 0:
            self._lyric_input.clear()

    def _update_input_visibility(self) -> None:
        """Show or hide the lyric input box."""
        self._lyric_input.setVisible(not self._translation_mode)

    def _restyle_input(self) -> None:
        """Apply theme styling to the lyric input box + its expand icon."""
        prefs = self._mw.config.get_preferences()
        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()
        fg = "#eeeeee" if is_dark else "#111111"

        self._lyric_input.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  color: {fg};"
            f"  font-size: 14px;"
            f"  background-color: {_rgba(theme_color, 0.07)};"
            f"  border: 1px solid {theme_color};"
            f"  border-radius: 4px;"
            f"  padding: 4px 8px;"
            f"  margin: 2px 4px;"
            f"}}"
            f"QPlainTextEdit:focus {{"
            f"  border-color: {theme_color};"
            f"  background-color: {_rgba(theme_color, 0.13)};"
            f"}}"
        )

        if hasattr(self, "_btn_expand"):
            self._btn_expand.setStyleSheet(
                f"QPushButton {{"
                f"  border: none; background: transparent;"
                f"  color: {_rgba(fg, 0.5)}; border-radius: 6px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  background-color: {_rgba(theme_color, 0.15)};"
                f"  color: {theme_color};"
                f"}}"
                f"QPushButton:pressed {{"
                f"  background-color: {_rgba(theme_color, 0.25)};"
                f"  color: {theme_color};"
                f"}}"
            )
        self._reposition_expand_button()

    def _reposition_expand_button(self) -> None:
        """Keep the expand icon pinned to the input box's bottom-right corner."""
        btn = getattr(self, "_btn_expand", None)
        if btn is None:
            return
        w = self._lyric_input.width()
        h = self._lyric_input.height()
        btn.move(max(0, w - btn.width() - 6), max(0, h - btn.height() - 4))

    def _scroll_to_row(self, index: int) -> None:
        """Scroll so the given row is visible."""
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self._scroll.ensureWidgetVisible(row, 0, 40)

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
        super().showEvent(event)
        self._refresh_rows()
        self.setFocus()
        prefs = self._mw.config.get_preferences()
        self.set_space_button_visible(prefs.get("screenButton", False))
