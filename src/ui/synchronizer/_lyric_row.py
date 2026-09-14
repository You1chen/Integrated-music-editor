"""One row in the synchronizer: timestamp button + text display."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QWidget,
)

from ...core.lrc_parser import Fixed, LyricLine, convert_time_to_tag, format_text
from ._helpers import _contrast_for_theme, _rgba


class _TimeButton(QPushButton):
    """Timestamp button that reports double-clicks for inline editing."""

    double_clicked = pyqtSignal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        event.accept()


class _LyricRow(QFrame):
    """One row in the synchronizer: timestamp button + text display."""

    seek_requested = pyqtSignal(float)
    edit_requested = pyqtSignal(int)
    row_clicked = pyqtSignal(int)
    multi_select_toggled = pyqtSignal(int)

    edit_lyric_requested = pyqtSignal(int)
    split_lyric_requested = pyqtSignal(int)
    append_requested = pyqtSignal(int)

    delete_requested = pyqtSignal()
    merge_requested = pyqtSignal()

    lyric_text_changed = pyqtSignal(int, str)
    lyric_split_done = pyqtSignal(int, str, list)

    def __init__(
        self,
        index: int,
        line: LyricLine,
        fixed: Fixed,
        space_start: int,
        space_end: int,
        theme_color: str,
        is_dark: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._index = index
        self._line = line
        self._fixed = fixed
        self._space_start = space_start
        self._space_end = space_end
        self._theme_color = theme_color
        self._is_dark = is_dark
        self._selected = False
        self._multi_selected = False
        self._at_current = False

        self.setFixedHeight(36)
        self.setFrameShape(QFrame.Shape.NoFrame)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 8, 2)
        layout.setSpacing(6)

        self._time_btn = _TimeButton()
        self._time_btn.setFixedWidth(105)
        self._time_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._time_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._time_btn.setToolTip("单击跳转 · 双击或 Ctrl+单击编辑时间戳")
        self._time_btn.clicked.connect(self._on_time_clicked)
        self._time_btn.double_clicked.connect(self._on_time_double_clicked)
        layout.addWidget(self._time_btn)

        self._display_stack = QStackedWidget()

        text = format_text(line.text, space_start, space_end)
        self._text_label = QLabel(text)
        self._text_label.setTextFormat(Qt.TextFormat.PlainText)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._display_stack.addWidget(self._text_label)

        self._edit_input = QLineEdit()
        self._edit_input.returnPressed.connect(self._on_edit_confirm)
        self._edit_input.installEventFilter(self)
        self._display_stack.addWidget(self._edit_input)

        self._split_input = QTextEdit()
        self._split_input.setFixedHeight(44)
        self._split_input.setTabChangesFocus(True)
        self._split_input.textChanged.connect(self._on_split_text_changed)
        self._split_input.installEventFilter(self)
        self._display_stack.addWidget(self._split_input)

        layout.addWidget(self._display_stack, stretch=1)

        from PyQt6.QtCore import QTimer
        self._split_blink_timer = QTimer(self)
        self._split_blink_timer.timeout.connect(self._toggle_split_blink)
        self._split_blink_on = True

        self._restyle()

    @property
    def lyric_index(self) -> int:
        return self._index

    @property
    def is_editing(self) -> bool:
        """True when the row is in edit or split mode."""
        return self._display_stack.currentIndex() != 0

    def update_state(
        self,
        line: LyricLine,
        selected: bool,
        at_current: bool,
        fixed: Fixed,
        space_start: int,
        space_end: int,
        theme_color: str,
        is_dark: bool,
        multi_selected: bool = False,
    ) -> None:
        """Update all display state at once (skips text if user is editing)."""
        self._line = line
        self._selected = selected
        self._multi_selected = multi_selected
        self._at_current = at_current
        self._fixed = fixed
        self._space_start = space_start
        self._space_end = space_end
        self._theme_color = theme_color
        self._is_dark = is_dark

        if line.time is not None:
            tag = convert_time_to_tag(line.time, fixed)
            self._time_btn.setText(tag)
        else:
            self._time_btn.setText("[--:--.---]")

        if self._display_stack.currentIndex() == 0:
            text = format_text(line.text, space_start, space_end)
            self._text_label.setText(text)

        self._restyle()

    def enter_edit_mode(self) -> None:
        self._edit_input.setText(self._line.text)
        self._display_stack.setCurrentIndex(1)
        self._edit_input.setFocus()
        self._edit_input.selectAll()

    def exit_edit_mode(self) -> None:
        """Exit both edit and split modes."""
        self._exit_edit_mode(save=True)
        self._exit_split_mode()

    def _exit_edit_mode(self, save: bool = True) -> None:
        """Leave edit mode, optionally saving changes."""
        in_edit = self._display_stack.currentIndex() == 1
        if save and in_edit:
            new_text = self._edit_input.text()
            if new_text != self._line.text:
                result = self._marker_split_result(new_text)
                if result is not None:
                    cleaned, positions = result
                    self.lyric_split_done.emit(self._index, cleaned, positions)
                else:
                    self.lyric_text_changed.emit(self._index, new_text)
        self._display_stack.setCurrentIndex(0)

    def enter_split_mode(self) -> None:
        """Switch to inline split mode (expands row for QTextEdit)."""
        self.setFixedHeight(58)
        self._split_input.setPlainText(self._line.text)
        self._display_stack.setCurrentIndex(2)
        self._split_input.setFocus()
        cursor = self._split_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        self._split_input.setTextCursor(cursor)
        self._split_blink_on = True
        self._split_blink_timer.start(500)
        self._apply_split_highlights()

    def _exit_split_mode(self) -> None:
        """Leave split mode and restore compact height."""
        self._split_blink_timer.stop()
        self._split_input.setExtraSelections([])
        self.setFixedHeight(36)
        self._display_stack.setCurrentIndex(0)

    def _on_edit_confirm(self) -> None:
        """Enter pressed in edit input."""
        self._exit_edit_mode(save=True)

    def _on_split_text_changed(self) -> None:
        self._apply_split_highlights()

    def _toggle_split_blink(self) -> None:
        self._split_blink_on = not self._split_blink_on
        self._apply_split_highlights()

    @staticmethod
    def _find_markers(text: str) -> list[int]:
        """Return start positions of all '//' in *text*."""
        positions: list[int] = []
        idx = 0
        while True:
            idx = text.find("//", idx)
            if idx == -1:
                break
            positions.append(idx)
            idx += 2
        return positions

    @staticmethod
    def _marker_split_result(text: str) -> "tuple[str, list[int]] | None":
        """Return ``(cleaned_text, split_positions)`` for interior '//' markers, else ``None``."""
        markers = _LyricRow._find_markers(text)
        if not any(0 < p < len(text) - 1 for p in markers):
            return None

        cleaned = text
        for pos in reversed(markers):
            cleaned = cleaned[:pos] + cleaned[pos + 2:]

        split_positions = [
            pos - 2 * i
            for i, pos in enumerate(markers)
            if 0 < pos - 2 * i < len(cleaned)
        ]
        if not split_positions:
            return None
        return cleaned, split_positions

    def _apply_split_highlights(self) -> None:
        """Highlight '//' markers with blinking ExtraSelections."""
        if not self._split_blink_on:
            self._split_input.setExtraSelections([])
            return

        text = self._split_input.toPlainText()
        markers = self._find_markers(text)
        if not markers:
            self._split_input.setExtraSelections([])
            return

        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffeb3b"))
        fmt.setForeground(QColor("#f44336"))
        fmt.setFontWeight(QFont.Weight.Bold)

        extra_selections: list[QTextEdit.ExtraSelection] = []
        for pos in markers:
            sel = QTextEdit.ExtraSelection()
            sel.format = fmt
            sel.cursor = QTextCursor(self._split_input.document())
            sel.cursor.setPosition(pos)
            sel.cursor.setPosition(pos + 2, QTextCursor.MoveMode.KeepAnchor)
            extra_selections.append(sel)

        self._split_input.setExtraSelections(extra_selections)

    def _do_cursor_split(self) -> None:
        """Ctrl+Enter: split at the current cursor position."""
        cursor = self._split_input.textCursor()
        pos = cursor.position()
        text = self._split_input.toPlainText()
        if 0 < pos < len(text):
            self.lyric_split_done.emit(self._index, text, [pos])
        self._exit_split_mode()

    def _do_marker_split(self) -> None:
        """Enter: split at all '//' marker positions."""
        text = self._split_input.toPlainText()
        result = self._marker_split_result(text)
        if result is None:
            self._exit_split_mode()
            return
        cleaned, split_positions = result
        self.lyric_split_done.emit(self._index, cleaned, split_positions)
        self._exit_split_mode()

    def eventFilter(self, obj, event):
        """Intercept Escape / Enter in edit and split inputs."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            if obj == self._edit_input:
                if event.key() == Qt.Key.Key_Escape:
                    self._exit_edit_mode(save=False)
                    return True
            elif obj == self._split_input:
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    self._exit_split_mode()
                    return True
                elif key == Qt.Key.Key_Return or key == Qt.Key.Key_Enter:
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        self._do_cursor_split()
                    else:
                        self._do_marker_split()
                    return True
        return super().eventFilter(obj, event)

    def _on_time_clicked(self) -> None:
        from PyQt6.QtWidgets import QApplication

        modifiers = QApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            self.edit_requested.emit(self._index)
        elif self._line.time is not None:
            self.seek_requested.emit(self._line.time)

    def _on_time_double_clicked(self) -> None:
        """Double-click timestamp → edit its value directly."""
        self.edit_requested.emit(self._index)

    def _restyle(self) -> None:
        """Apply QSS styling based on current state."""
        theme = self._theme_color
        bg = "transparent"
        border = "none"
        fg = "#eeeeee" if self._is_dark else "#111111"

        if self._selected and self._display_stack.currentIndex() == 0:
            bg = f"{theme}"
            border = f"1px solid {theme}"
        elif self._multi_selected and self._display_stack.currentIndex() == 0:
            bg = f"{theme}"
            border = f"1px solid {theme}"
        elif self._at_current and self._display_stack.currentIndex() == 0:
            bg = f"{theme}"

        self.setStyleSheet(
            f"_LyricRow {{ background-color: {bg}; border: {border}; border-radius: 4px; }}"
        )

        contrast = _contrast_for_theme(theme)
        if self._line.time is not None:
            btn_bg = f"{theme}" if not self._selected else "transparent"
            btn_text = contrast
            btn_style = (
                f"QPushButton {{"
                f"  background-color: {btn_bg};"
                f"  color: {btn_text};"
                f"  border: 1px solid {theme};"
                f"  border-radius: 4px;"
                f"  font-family: monospace;"
                f"  font-size: 12px;"
                f"  padding: 2px 4px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  background-color: {theme};"
                f"  color: {contrast};"
                f"  border-color: {theme};"
                f"}}"
            )
        else:
            btn_style = (
                f"QPushButton {{"
                f"  background-color: transparent;"
                f"  color: {_rgba(fg, 0.27)};"
                f"  border: 1px dashed {_rgba(fg, 0.13)};"
                f"  border-radius: 4px;"
                f"  font-family: monospace;"
                f"  font-size: 12px;"
                f"  padding: 2px 4px;"
                f"}}"
                f"QPushButton:hover {{"
                f"  border-color: {theme};"
                f"  color: {theme};"
                f"}}"
            )
        self._time_btn.setStyleSheet(btn_style)

        self._text_label.setStyleSheet(
            f"QLabel {{ color: {fg}; font-size: 14px; background: transparent; border: none; }}"
        )

        self._edit_input.setStyleSheet(
            f"QLineEdit {{"
            f"  color: {fg}; font-size: 14px; background: transparent;"
            f"  border: 1px solid {theme}; border-radius: 3px; padding: 2px 6px;"
            f"}}"
        )

        self._split_input.setStyleSheet(
            f"QTextEdit {{"
            f"  color: {fg}; font-size: 13px; background: transparent;"
            f"  border: 1px solid {theme}; border-radius: 3px; padding: 2px 6px;"
            f"}}"
        )

    def mousePressEvent(self, event) -> None:
        """Handle mouse clicks on the row's text area."""
        if self._time_btn.geometry().contains(event.pos()):
            super().mousePressEvent(event)
            return

        from PyQt6.QtWidgets import QApplication
        modifiers = QApplication.keyboardModifiers()
        ctrl_held = bool(modifiers & Qt.KeyboardModifier.ControlModifier)

        if event.button() == Qt.MouseButton.LeftButton:
            if ctrl_held:
                self.multi_select_toggled.emit(self._index)
            else:
                self.row_clicked.emit(self._index)
        elif event.button() == Qt.MouseButton.RightButton:
            if ctrl_held:
                self.append_requested.emit(self._index)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click on the text area enters edit mode."""
        if not self._time_btn.geometry().contains(event.pos()):
            self.edit_lyric_requested.emit(self._index)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        """Right-click context menu: edit / split / append / delete / merge."""
        from PyQt6.QtWidgets import QMenu

        menu = QMenu(self)

        edit_action = menu.addAction("✏️ 编辑")
        split_action = menu.addAction("✂️ 拆分")
        append_action = menu.addAction("📝 追加 (Ctrl+右键)")
        menu.addSeparator()
        delete_action = menu.addAction("🗑️ 删除 (Delete)")
        merge_action = menu.addAction("🔗 合并 (Ctrl+H)")

        edit_action.triggered.connect(
            lambda: self.edit_lyric_requested.emit(self._index)
        )
        split_action.triggered.connect(
            lambda: self.split_lyric_requested.emit(self._index)
        )
        append_action.triggered.connect(
            lambda: self.append_requested.emit(self._index)
        )
        delete_action.triggered.connect(
            lambda: self.delete_requested.emit()
        )
        merge_action.triggered.connect(
            lambda: self.merge_requested.emit()
        )

        menu.exec(event.globalPos())
