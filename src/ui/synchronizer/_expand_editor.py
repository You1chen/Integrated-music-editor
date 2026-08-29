"""Expanded lyric editor — a large window for comfortable lyric entry.

Opened from the synchronizer page's input box ("展开" button).  It hosts:

- A large plain-text editor that fills the window.
- The same ``AudioControls`` play bar as the main window (a second,
  independently-wired instance sharing the app's audio/playlist state).
- A Ctrl+F regex find/replace bar.

The edited text is committed back through ``lyrics_submitted(str)`` —
the synchronizer page reuses its normal input-box insert logic.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QKeyEvent, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.audio_manager import AudioState, AudioStateData
from ..audio_controls import AudioControls

if TYPE_CHECKING:
    from ..main_window import MainWindow


class _ExpandTextEdit(QPlainTextEdit):
    """Text editor that emits ``find_requested`` on Ctrl+F."""

    find_requested = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() == Qt.Key.Key_F
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.find_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _FindReplaceBar(QWidget):
    """Inline regex find/replace bar for a ``QPlainTextEdit``.

    Find uses ``QPlainTextEdit.find(QRegularExpression)`` so cursor positions
    stay in document coordinates; replacement expands ``\\1``-style
    backreferences best-effort via Python ``re`` when regex mode is on.
    """

    def __init__(self, editor: QPlainTextEdit, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = editor

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("查找…")
        self._find_edit.setClearButtonEnabled(True)
        self._find_edit.returnPressed.connect(self.find_next)
        self._find_edit.installEventFilter(self)
        layout.addWidget(self._find_edit, stretch=2)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("替换为…")
        self._replace_edit.setClearButtonEnabled(True)
        self._replace_edit.returnPressed.connect(self.replace_one)
        self._replace_edit.installEventFilter(self)
        layout.addWidget(self._replace_edit, stretch=2)

        self._regex_check = QCheckBox("正则")
        self._regex_check.setToolTip("按正则表达式查找")
        layout.addWidget(self._regex_check)

        self._case_check = QCheckBox("区分大小写")
        layout.addWidget(self._case_check)

        btn_find = QPushButton("查找")
        btn_find.clicked.connect(self.find_next)
        layout.addWidget(btn_find)

        btn_replace = QPushButton("替换")
        btn_replace.clicked.connect(self.replace_one)
        layout.addWidget(btn_replace)

        btn_replace_all = QPushButton("全部替换")
        btn_replace_all.clicked.connect(self.replace_all)
        layout.addWidget(btn_replace_all)

        btn_close = QPushButton("✕")
        btn_close.setFixedWidth(30)
        btn_close.setToolTip("关闭查找")
        btn_close.clicked.connect(self.close_bar)
        layout.addWidget(btn_close)

    # ── Regex helpers ────────────────────────────────────────

    def _make_re(self) -> QRegularExpression | None:
        text = self._find_edit.text()
        if not text:
            return None
        # Case sensitivity is controlled via the FindCaseSensitively flag
        # passed to find() — the flag overrides the regex's own option.
        pattern = text if self._regex_check.isChecked() else QRegularExpression.escape(text)
        re_obj = QRegularExpression(pattern)
        return re_obj if re_obj.isValid() else None

    def _find_flags(self) -> QTextDocument.FindFlag:
        if self._case_check.isChecked():
            return QTextDocument.FindFlag.FindCaseSensitively
        return QTextDocument.FindFlag(0)

    def _expand_backref(self, matched_text: str, replacement: str) -> str:
        """Expand ``\\1`` backreferences in *replacement* for regex mode."""
        if not self._regex_check.isChecked():
            return replacement
        try:
            flags = 0 if self._case_check.isChecked() else re.IGNORECASE
            pyre = re.compile(self._find_edit.text(), flags)
            m = pyre.search(matched_text)
            if m is not None:
                return m.expand(replacement)
        except re.error:
            pass
        return replacement

    # ── Actions ──────────────────────────────────────────────

    def find_next(self, checked: bool = False) -> bool:
        re_obj = self._make_re()
        if re_obj is None:
            return False
        flags = self._find_flags()
        found = self._editor.find(re_obj, flags)
        if not found:
            cursor = self._editor.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self._editor.setTextCursor(cursor)
            found = self._editor.find(re_obj, flags)
        return found

    def replace_one(self, checked: bool = False) -> None:
        re_obj = self._make_re()
        if re_obj is None:
            return
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            replacement = self._expand_backref(
                cursor.selectedText(), self._replace_edit.text()
            )
            cursor.insertText(replacement)
        self.find_next()

    def replace_all(self, checked: bool = False) -> int:
        re_obj = self._make_re()
        if re_obj is None:
            return 0
        editor = self._editor
        cursor = editor.textCursor()
        flags = self._find_flags()
        cursor.beginEditBlock()
        try:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            editor.setTextCursor(cursor)
            count = 0
            guard = 0
            while guard < 100000:
                guard += 1
                if not editor.find(re_obj, flags):
                    break
                c = editor.textCursor()
                if c.selectionStart() == c.selectionEnd():
                    # Zero-length match — advance one char to avoid looping.
                    if not c.movePosition(QTextCursor.MoveOperation.NextCharacter):
                        break
                    editor.setTextCursor(c)
                    continue
                replacement = self._expand_backref(
                    c.selectedText(), self._replace_edit.text()
                )
                c.insertText(replacement)
                count += 1
        finally:
            cursor.endEditBlock()
        return count

    def open_bar(self, focus_find: bool = True) -> None:
        self.show()
        cursor = self._editor.textCursor()
        if cursor.hasSelection() and cursor.selectedText():
            self._find_edit.setText(cursor.selectedText())
        if focus_find:
            self._find_edit.setFocus()
            self._find_edit.selectAll()

    def close_bar(self, checked: bool = False) -> None:
        self.hide()
        cursor = self._editor.textCursor()
        cursor.clearSelection()
        self._editor.setTextCursor(cursor)
        self._editor.setFocus()

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return True
        return super().eventFilter(obj, event)


class ExpandEditorDialog(QDialog):
    """Large lyric-entry window: text editor + play controls + Ctrl+F find/replace."""

    lyrics_submitted = pyqtSignal(str)

    def __init__(
        self,
        main_window: "MainWindow",
        initial_text: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent if parent is not None else main_window)
        self._mw = main_window

        self.setWindowTitle("歌词编辑 - 展开")
        self.resize(920, 720)
        self.setMinimumSize(640, 500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Large text editor ──
        self._editor = _ExpandTextEdit()
        self._editor.setPlainText(initial_text)
        self._editor.setPlaceholderText("在此输入歌词，每行一句…")
        self._editor.setFont(QFont("Consolas", 13))
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        # ── Find/replace bar (hidden until Ctrl+F) ──
        self._find_bar = _FindReplaceBar(self._editor, self)
        self._find_bar.hide()
        self._editor.find_requested.connect(self._find_bar.open_bar)

        layout.addWidget(self._find_bar)
        layout.addWidget(self._editor, stretch=1)

        # ── Play controls (identical to the main window's) ──
        self._audio_controls = self._build_audio_controls()
        layout.addWidget(self._audio_controls)
        # This second AudioControls owns its own WaveformWidget, which starts
        # a decoder QThread at construction.  The main window shuts down only
        # the *footer* bar's waveform on close, so the dialog must stop its
        # own on the way out — otherwise Qt warns "QThread: Destroyed while
        # thread '' is still running" and the thread outlives the dialog.
        self.finished.connect(self._on_finished)

        # ── Bottom action row ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        hint = QLabel("Ctrl+F 查找替换")
        hint.setStyleSheet("font-size: 12px; color: gray;")
        btn_row.addWidget(hint)
        btn_row.addStretch()
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_submit = QPushButton("提交歌词")
        btn_submit.clicked.connect(self._on_submit)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_submit)
        layout.addLayout(btn_row)

        self._editor.setFocus()

    def _build_audio_controls(self) -> AudioControls:
        """Create a second AudioControls wired to the shared audio state."""
        mw = self._mw
        ac = AudioControls(mw)

        mw.audio_manager.state_changed.connect(ac.update_state)
        mw.audio_manager.current_time_changed.connect(ac.on_current_time_changed)
        mw.playlist.mode_changed.connect(ac.update_mode_label)
        # Bound method (not a lambda) so the connection is auto-broken when
        # the dialog — and this second AudioControls — is destroyed.
        mw.lrc_state.state_changed.connect(ac.refresh_fixed)

        prefs = mw.config.get_preferences()
        ac.set_waveform_visible(prefs.get("showWaveform", True))
        ac.set_fixed(prefs.get("fixed", 3))
        ac.update_mode_label(mw.playlist.mode)
        ac.set_mode_lock(bool(getattr(mw, "_sync_active", False)))

        # Reflect the player's current state immediately (the dialog may
        # open while audio is already loaded / playing / at a custom rate).
        ac.update_state(AudioStateData(AudioState.PAUSE_CHANGED, mw.audio_manager.paused))
        ac.update_state(AudioStateData(AudioState.DURATION_LOADED, mw.audio_manager.duration))
        ac.update_state(AudioStateData(AudioState.RATE_CHANGED, mw.audio_manager.playback_rate))
        return ac

    def _on_finished(self, *_args) -> None:
        """Stop the dialog's own waveform decoder when it closes."""
        waveform = getattr(self._audio_controls, "_waveform", None)
        if waveform is not None:
            waveform.shutdown()

    def _on_submit(self) -> None:
        text = self._editor.toPlainText()
        if not text.strip():
            return
        self.lyrics_submitted.emit(text)
        self.accept()

    def text(self) -> str:
        return self._editor.toPlainText()
