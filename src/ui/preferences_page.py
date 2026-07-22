"""Preferences page — all settings (replaces preferences.tsx).

Theme color, dark/light mode, language, LRC format, toggles.
All sections are collapsible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QKeyEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import os

from ..core.constants import ThemeMode
from ..core.keybinding import (
    ACTION_GROUPS,
    ACTION_LABELS,
    DEFAULT_BINDINGS,
    KeyBinding,
    keybinding_to_string,
)
from ..core.lrc_parser import Fixed, convert_time_to_tag, format_text
from .content_stack import (
    THEME_COLORS,
    DEFAULT_THEME_COLOR,
    apply_theme,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Collapsible Group ──────────────────────────────────────────


class _CollapsibleGroup(QWidget):
    """A group box whose content can be toggled by clicking the header.

    Uses the global QSS ``#collapsibleHeader`` for theming — no inline
    styles, so it updates automatically when the theme changes.
    """

    def __init__(self, title: str, expanded: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._expanded = expanded
        self._title = title

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Header button — styled via global QSS #collapsibleHeader
        self._header_btn = QPushButton(self._arrow() + " " + title)
        self._header_btn.setObjectName("collapsibleHeader")
        self._header_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header_btn.clicked.connect(self._toggle)
        self._layout.addWidget(self._header_btn)

        # Content area
        self._content = QWidget()
        self._content.setStyleSheet("QWidget { background: transparent; border: none; }")
        self._content.setVisible(expanded)
        self._layout.addWidget(self._content)

    def _arrow(self) -> str:
        return "▼" if self._expanded else "▶"

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._header_btn.setText(self._arrow() + " " + self._title)
        self._content.setVisible(self._expanded)

    def set_content_layout(self, child_layout: QHBoxLayout | QFormLayout | QVBoxLayout) -> None:
        """Set the layout for the collapsible content area."""
        self._content.setLayout(child_layout)


class PreferencesPage(QScrollArea):
    """Scrollable preferences/settings page."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__()
        self._mw = main_window

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        container = QWidget()
        self.setWidget(container)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        prefs = main_window.config.get_preferences()

        # ── About ────────────────────────────────────
        about = _CollapsibleGroup("关于")
        about_layout = QFormLayout()
        about_layout.addRow("版本：", QLabel("6.0.0 (Python)"))
        about_layout.addRow(
            "项目地址：",
            QLabel('<a href="https://github.com/magic-akari/lrc-maker">GitHub</a>'),
        )
        about.set_content_layout(about_layout)
        layout.addWidget(about)

        # ── Theme Mode ───────────────────────────────
        theme = _CollapsibleGroup("主题模式")
        theme_layout = QFormLayout()

        self._theme_combo = QComboBox()
        modes = [
            (ThemeMode.AUTO, "跟随系统"),
            (ThemeMode.LIGHT, "亮色模式"),
            (ThemeMode.DARK, "暗色模式"),
        ]
        current_mode = prefs.get("themeMode", ThemeMode.AUTO)
        for i, (mode, name) in enumerate(modes):
            self._theme_combo.addItem(name, mode)
            if mode == current_mode:
                self._theme_combo.setCurrentIndex(i)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_mode_changed)
        theme_layout.addRow("主题模式:", self._theme_combo)
        theme.set_content_layout(theme_layout)
        layout.addWidget(theme)

        # ── Theme Color ──────────────────────────────
        color_group = _CollapsibleGroup("主题颜色")
        color_layout = QHBoxLayout()

        self._color_buttons: dict[str, QPushButton] = {}
        current_color = prefs.get("themeColor", DEFAULT_THEME_COLOR)

        for name, color in THEME_COLORS.items():
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {color}; border-radius: 14px;"
                f"  border: {'3px solid white' if color == current_color else '1px solid #888'};"
                f"}}"
                f"QPushButton:hover {{"
                f"  border: 3px solid white;"
                f"}}"
            )
            btn.clicked.connect(lambda checked, c=color: self._on_color_pick(c))
            color_layout.addWidget(btn)
            self._color_buttons[name] = btn

        # Custom color button
        custom_btn = QPushButton("#")
        custom_btn.setFixedSize(28, 28)
        custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        custom_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #888; border-radius: 14px;"
            "  color: white; font-weight: bold;"
            "  border: 1px solid #888;"
            "}"
            "QPushButton:hover {"
            "  border: 3px solid white;"
            "}"
        )
        custom_btn.clicked.connect(self._on_custom_color)
        color_layout.addWidget(custom_btn)

        color_layout.addStretch()
        color_group.set_content_layout(color_layout)
        layout.addWidget(color_group)

        # ── Display Options ──────────────────────────
        display = _CollapsibleGroup("显示")
        display_layout = QFormLayout()

        self._show_waveform_cb = QCheckBox()
        self._show_waveform_cb.setChecked(prefs.get("showWaveform", True))
        self._show_waveform_cb.toggled.connect(self._on_toggle_changed)
        display_layout.addRow("显示音频波形:", self._show_waveform_cb)

        self._space_btn_cb = QCheckBox()
        self._space_btn_cb.setChecked(prefs.get("screenButton", False))
        self._space_btn_cb.toggled.connect(self._on_toggle_changed)
        display_layout.addRow("启用虚拟空格键:", self._space_btn_cb)

        self._builtin_audio_cb = QCheckBox()
        self._builtin_audio_cb.setChecked(prefs.get("builtInAudio", False))
        self._builtin_audio_cb.toggled.connect(self._on_toggle_changed)
        display_layout.addRow("使用内置音频播放器:", self._builtin_audio_cb)

        self._show_welcome_cb = QCheckBox()
        self._show_welcome_cb.setChecked(self._mw.config.get_show_welcome())
        self._show_welcome_cb.toggled.connect(self._on_toggle_changed)
        display_layout.addRow("启动时显示欢迎引导:", self._show_welcome_cb)

        display.set_content_layout(display_layout)
        layout.addWidget(display)

        # ── File Memory ─────────────────────────────
        file_memory = _CollapsibleGroup("文件与路径记忆", expanded=False)
        file_memory_layout = QFormLayout()

        self._remember_draft_cb = QCheckBox()
        self._remember_draft_cb.setChecked(self._mw.config.get_remember_draft())
        self._remember_draft_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "记住草稿（自动保存/加载打轴进度）:", self._remember_draft_cb
        )

        self._remember_lrc_cb = QCheckBox()
        self._remember_lrc_cb.setChecked(self._mw.config.get_remember_last_lrc())
        self._remember_lrc_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "记住上次打开的歌词文件:", self._remember_lrc_cb
        )

        self._remember_mp3_cb = QCheckBox()
        self._remember_mp3_cb.setChecked(self._mw.config.get_remember_last_mp3())
        self._remember_mp3_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "记住上次打开的音频文件:", self._remember_mp3_cb
        )

        # Default browse directory
        browse_layout = QHBoxLayout()
        self._browse_dir_input = QLineEdit()
        self._browse_dir_input.setText(self._mw.config.get_default_browse_dir())
        self._browse_dir_input.textChanged.connect(self._on_file_memory_changed)
        browse_layout.addWidget(self._browse_dir_input)

        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._on_browse_dir)
        browse_layout.addWidget(browse_btn)

        file_memory_layout.addRow("默认浏览目录:", browse_layout)

        # Default cover browse directory
        cover_browse_layout = QHBoxLayout()
        self._cover_browse_dir_input = QLineEdit()
        self._cover_browse_dir_input.setText(
            self._mw.config.get_default_cover_browse_dir()
        )
        self._cover_browse_dir_input.textChanged.connect(self._on_file_memory_changed)
        cover_browse_layout.addWidget(self._cover_browse_dir_input)

        cover_browse_btn = QPushButton("浏览...")
        cover_browse_btn.clicked.connect(self._on_cover_browse_dir)
        cover_browse_layout.addWidget(cover_browse_btn)

        file_memory_layout.addRow("默认封面浏览目录:", cover_browse_layout)

        self._remember_rate_cb = QCheckBox()
        self._remember_rate_cb.setChecked(self._mw.config.get_remember_playback_rate())
        self._remember_rate_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "记住上次播放倍速:", self._remember_rate_cb
        )

        self._show_save_warning_cb = QCheckBox()
        self._show_save_warning_cb.setChecked(self._mw.config.get_show_save_warning())
        self._show_save_warning_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "保存时显示覆盖警告:", self._show_save_warning_cb
        )

        self._show_draft_warning_cb = QCheckBox()
        self._show_draft_warning_cb.setChecked(self._mw.config.get_show_draft_warning())
        self._show_draft_warning_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "关闭/导入时显示覆写警告:", self._show_draft_warning_cb
        )

        self._enable_smart_import_cb = QCheckBox()
        self._enable_smart_import_cb.setChecked(self._mw.config.get_enable_smart_import())
        self._enable_smart_import_cb.toggled.connect(self._on_toggle_changed)
        file_memory_layout.addRow(
            "播放音频时智能查找歌词:", self._enable_smart_import_cb
        )

        file_memory.set_content_layout(file_memory_layout)
        layout.addWidget(file_memory)

        # ── Sync Assist ──────────────────────────────
        sync = _CollapsibleGroup("打轴辅助", expanded=False)
        sync_layout = QFormLayout()

        self._reaction_time = QSpinBox()
        self._reaction_time.setRange(0, 500)
        self._reaction_time.setSingleStep(10)
        self._reaction_time.setValue(prefs.get("reactionTimeMs", 100))
        self._reaction_time.setSuffix(" ms")
        self._reaction_time.setToolTip(
            "按下空格时，时间戳会向前偏移这个毫秒数，用于补偿听到下一句到按下按键之间的反应延迟"
        )
        self._reaction_time.valueChanged.connect(self._on_toggle_changed)
        sync_layout.addRow("反应时间:", self._reaction_time)

        self._auto_seek_cb = QCheckBox()
        self._auto_seek_cb.setChecked(prefs.get("autoSeekVerify", False))
        self._auto_seek_cb.toggled.connect(self._on_toggle_changed)
        sync_layout.addRow("打轴后自动跳转验证:", self._auto_seek_cb)

        self._auto_seek_delay = QDoubleSpinBox()
        self._auto_seek_delay.setRange(0.1, 5.0)
        self._auto_seek_delay.setSingleStep(0.1)
        self._auto_seek_delay.setDecimals(1)
        self._auto_seek_delay.setValue(prefs.get("autoSeekDelay", 1.0))
        self._auto_seek_delay.setSuffix(" 秒")
        self._auto_seek_delay.valueChanged.connect(self._on_toggle_changed)
        sync_layout.addRow("跳转延迟:", self._auto_seek_delay)

        sync.set_content_layout(sync_layout)
        layout.addWidget(sync)

        # ── LRC Format ───────────────────────────────
        format_group = _CollapsibleGroup("歌词输出格式控制", expanded=False)
        format_layout = QFormLayout()

        self._fixed_combo = QComboBox()
        for v in [0, 1, 2, 3]:
            self._fixed_combo.addItem(str(v), v)
        self._fixed_combo.setCurrentIndex(prefs.get("fixed", 3))
        self._fixed_combo.currentIndexChanged.connect(self._on_fixed_changed)
        format_layout.addRow("时间标签小数点:", self._fixed_combo)

        self._space_start = QSpinBox()
        self._space_start.setRange(-1, 10)
        self._space_start.setValue(prefs.get("spaceStart", 1))
        self._space_start.valueChanged.connect(self._on_format_changed)
        format_layout.addRow("左侧空格:", self._space_start)

        self._space_end = QSpinBox()
        self._space_end.setRange(-1, 10)
        self._space_end.setValue(prefs.get("spaceEnd", 0))
        self._space_end.valueChanged.connect(self._on_format_changed)
        format_layout.addRow("右侧空格:", self._space_end)

        # Format preview
        self._format_preview = QLabel()
        format_layout.addRow("预览：", self._format_preview)

        format_group.set_content_layout(format_layout)
        layout.addWidget(format_group)

        # ── Keyboard Shortcuts ──────────────────────────
        self._shortcut_labels: dict[str, QLabel] = {}
        shortcuts_group = self._create_shortcuts_section()
        layout.addWidget(shortcuts_group)

        layout.addStretch()

        # Update preview
        self._update_format_preview()

    # ── Keyboard Shortcuts ────────────────────────────────────

    def _create_shortcuts_section(self) -> _CollapsibleGroup:
        """Build the keyboard shortcuts settings group with search filter."""
        group = _CollapsibleGroup("快捷键", expanded=False)

        content_layout = QVBoxLayout()
        content_layout.setSpacing(6)

        # Top bar: search + reset-all
        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 4)
        top_layout.setSpacing(8)

        self._shortcut_search = QLineEdit()
        self._shortcut_search.setPlaceholderText("搜索快捷键…")
        self._shortcut_search.setClearButtonEnabled(True)
        self._shortcut_search.textChanged.connect(self._on_shortcut_search)
        top_layout.addWidget(self._shortcut_search, stretch=1)

        reset_all_btn = QPushButton("↺ 重置全部")
        reset_all_btn.setObjectName("collapsibleHeader")
        reset_all_btn.clicked.connect(self._on_reset_all_shortcuts)
        top_layout.addWidget(reset_all_btn)

        content_layout.addLayout(top_layout)

        # Each sub-group is a nested collapsible section
        self._shortcut_rows: dict[str, tuple[_CollapsibleGroup, QLayout]] = {}
        self._shortcut_subgroups: list[_CollapsibleGroup] = []
        user_bindings = self._mw.keybinding_manager.bindings

        btn_style = (
            "font-size: 12px; padding: 3px 8px;"
            "border: 1px solid #555; border-radius: 4px;"
            "background: transparent;"
        )

        for group_name, actions in ACTION_GROUPS:
            sub = _CollapsibleGroup(group_name, expanded=True)
            sub_layout = QVBoxLayout()
            sub_layout.setSpacing(1)

            for action in actions:
                bindings = user_bindings.get(action, [])
                label_text = ACTION_LABELS.get(action, action.value)
                keys_text = " / ".join(keybinding_to_string(b) for b in bindings) if bindings else "(未设置)"

                row = QHBoxLayout()
                row.setContentsMargins(4, 1, 4, 1)
                row.setSpacing(6)

                action_lbl = QLabel(label_text)
                action_lbl.setFixedWidth(80)
                action_lbl.setStyleSheet("font-size: 13px;")
                row.addWidget(action_lbl)

                keys_lbl = QLabel(keys_text)
                keys_lbl.setFixedWidth(140)
                keys_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                keys_lbl.setStyleSheet(
                    "font-family: 'Consolas', monospace; font-size: 12px;"
                    "color: #aaa; background: #333; border-radius: 3px;"
                    "padding: 2px 6px;"
                )
                row.addWidget(keys_lbl)
                self._shortcut_labels[action.value] = keys_lbl

                edit_btn = QPushButton("编辑")
                edit_btn.setFixedWidth(72)
                edit_btn.setStyleSheet(btn_style)
                edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                edit_btn.clicked.connect(
                    lambda checked, a=action: self._on_edit_shortcut(a)
                )
                row.addWidget(edit_btn)

                reset_btn = QPushButton("重置")
                reset_btn.setFixedWidth(72)
                reset_btn.setStyleSheet(btn_style)
                reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                reset_btn.clicked.connect(
                    lambda checked, a=action: self._on_reset_shortcut(a)
                )
                row.addWidget(reset_btn)

                sub_layout.addLayout(row)
                self._shortcut_rows[action.value] = (sub, row)

            sub.set_content_layout(sub_layout)
            self._shortcut_subgroups.append(sub)
            content_layout.addWidget(sub)

        content_layout.addStretch()
        group.set_content_layout(content_layout)
        return group

    def _on_shortcut_search(self, text: str) -> None:
        """Filter shortcut rows by search text."""
        query = text.strip().lower()

        # Build label lookup once
        from ..core.constants import InputAction
        _label_map: dict[str, str] = {}
        for act, label in ACTION_LABELS.items():
            _label_map[act.value] = label

        # Track visible children per sub-group
        group_visible: dict[int, bool] = {}

        for action_str, (sub, row) in self._shortcut_rows.items():
            label = _label_map.get(action_str, action_str)
            visible = not query or query in label.lower()

            for i in range(row.count()):
                w = row.itemAt(i).widget()
                if w:
                    w.setVisible(visible)

            gid = id(sub)
            group_visible[gid] = group_visible.get(gid, False) or visible

        # Show/hide sub-groups; auto-expand those with matches when searching
        for sub in self._shortcut_subgroups:
            if query:
                has_match = group_visible.get(id(sub), False)
                sub.setVisible(has_match)
                if has_match and not sub._expanded:
                    sub._toggle()  # expand to reveal matches
            else:
                sub.setVisible(True)

    def _refresh_shortcut_labels(self) -> None:
        """Update all shortcut label texts from current bindings."""
        user_bindings = self._mw.keybinding_manager.bindings
        for action_str, lbl in self._shortcut_labels.items():
            try:
                from ..core.constants import InputAction
                act = InputAction(action_str)
                bindings = user_bindings.get(act, [])
                keys_text = " / ".join(keybinding_to_string(b) for b in bindings) if bindings else "(未设置)"
                lbl.setText(keys_text)
            except ValueError:
                pass

    def _on_edit_shortcut(self, action) -> None:
        """Open the key-capture dialog for a specific action."""
        dialog = _KeyCaptureDialog(action, self._mw, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_shortcut_labels()

    def _on_reset_shortcut(self, action) -> None:
        """Reset a single action to its default bindings."""
        self._mw.keybinding_manager.reset_user_binding(action)
        self._mw.config.set_keybindings(
            self._mw.keybinding_manager.user_overrides
        )
        self._refresh_shortcut_labels()

    def _on_reset_all_shortcuts(self) -> None:
        """Reset all shortcuts to defaults."""
        self._mw.keybinding_manager.reset_all()
        self._mw.config.set_keybindings({})
        self._refresh_shortcut_labels()

    # ── Handlers ─────────────────────────────────────────

    def _on_theme_mode_changed(self, index: int) -> None:
        mode = self._theme_combo.itemData(index)
        if mode is not None:
            prefs = self._mw.config.get_preferences()
            prefs["themeMode"] = mode
            self._mw.update_preferences(prefs)
            self._mw.lrc_state.state_changed.emit()  # refresh inline row styles

    def _on_color_pick(self, color: str) -> None:
        prefs = self._mw.config.get_preferences()
        prefs["themeColor"] = color
        self._mw.update_preferences(prefs)
        self._update_color_buttons(color)
        self._mw.lrc_state.state_changed.emit()  # refresh inline row styles

    def _on_custom_color(self) -> None:
        color = QColorDialog.getColor()
        if color.isValid():
            hex_color = color.name()
            prefs = self._mw.config.get_preferences()
            prefs["themeColor"] = hex_color
            self._mw.update_preferences(prefs)
            self._mw.lrc_state.state_changed.emit()  # refresh inline row styles

    def _on_toggle_changed(self) -> None:
        self._save_prefs()

    def _on_fixed_changed(self, index: int) -> None:
        self._save_prefs()
        self._update_format_preview()

    def _on_format_changed(self) -> None:
        self._save_prefs()
        self._update_format_preview()

    def _save_prefs(self) -> None:
        prefs = self._mw.config.get_preferences()
        prefs["showWaveform"] = self._show_waveform_cb.isChecked()
        prefs["screenButton"] = self._space_btn_cb.isChecked()
        prefs["builtInAudio"] = self._builtin_audio_cb.isChecked()
        prefs["fixed"] = self._fixed_combo.currentData()
        prefs["spaceStart"] = self._space_start.value()
        prefs["spaceEnd"] = self._space_end.value()
        prefs["lang"] = "zh-CN"
        prefs["themeMode"] = self._theme_combo.currentData()
        prefs["rememberDraft"] = self._remember_draft_cb.isChecked()
        prefs["rememberLastLrc"] = self._remember_lrc_cb.isChecked()
        prefs["rememberLastMp3"] = self._remember_mp3_cb.isChecked()
        prefs["defaultBrowseDir"] = self._browse_dir_input.text()
        prefs["defaultCoverBrowseDir"] = self._cover_browse_dir_input.text()
        prefs["rememberPlaybackRate"] = self._remember_rate_cb.isChecked()
        prefs["showWelcome"] = self._show_welcome_cb.isChecked()
        prefs["showSaveWarning"] = self._show_save_warning_cb.isChecked()
        prefs["showDraftWarning"] = self._show_draft_warning_cb.isChecked()
        prefs["enableSmartImport"] = self._enable_smart_import_cb.isChecked()
        prefs["reactionTimeMs"] = self._reaction_time.value()
        prefs["autoSeekVerify"] = self._auto_seek_cb.isChecked()
        prefs["autoSeekDelay"] = self._auto_seek_delay.value()
        self._mw.update_preferences(prefs)

    def _on_file_memory_changed(self) -> None:
        self._save_prefs()

    def _on_browse_dir(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择默认浏览目录",
            self._browse_dir_input.text() or "D:/歌手",
        )
        if dir_path:
            self._browse_dir_input.setText(dir_path)
            self._save_prefs()

    def _on_cover_browse_dir(self) -> None:
        dir_path = QFileDialog.getExistingDirectory(
            self,
            "选择默认封面浏览目录",
            self._cover_browse_dir_input.text()
            or self._mw.config.get_default_browse_dir(),
        )
        if dir_path:
            self._cover_browse_dir_input.setText(dir_path)
            self._save_prefs()

    def _update_color_buttons(self, selected: str) -> None:
        for name, btn in self._color_buttons.items():
            color = THEME_COLORS.get(name, "")
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {color}; border-radius: 14px;"
                f"  border: {'3px solid white' if color == selected else '1px solid #888'};"
                f"}}"
                f"QPushButton:hover {{"
                f"  border: 3px solid white;"
                f"}}"
            )

    def _update_format_preview(self) -> None:
        fixed: Fixed = self._fixed_combo.currentData() or 3
        ss = self._space_start.value()
        se = self._space_end.value()

        time_str = convert_time_to_tag(83.456, fixed)
        text_str = format_text("   hello   world~   ", ss, se)
        self._format_preview.setText(f"{time_str}{text_str}")


# ── Key Capture Dialog ─────────────────────────────────────────


class _KeyCaptureDialog(QDialog):
    """Modal dialog that captures a single key press for shortcut rebinding."""

    def __init__(self, action, main_window: "MainWindow", parent=None) -> None:
        super().__init__(parent)
        self._action = action
        self._mw = main_window
        self._captured: KeyBinding | None = None

        action_label = ACTION_LABELS.get(action, action.value)
        self.setWindowTitle(f"设置快捷键 — {action_label}")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Instruction
        instr = QLabel("请按下新的快捷键组合…")
        instr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instr.setStyleSheet("font-size: 14px; color: #aaa;")
        layout.addWidget(instr)

        # Captured key display
        self._capture_label = QLabel("等待按键…")
        self._capture_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._capture_label.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 18px;"
            "color: #fff; background: #333; border-radius: 6px;"
            "padding: 16px; min-height: 48px;"
        )
        layout.addWidget(self._capture_label)

        # Current bindings
        user_bindings = main_window.keybinding_manager.bindings
        current = user_bindings.get(action, [])
        current_text = " / ".join(keybinding_to_string(b) for b in current) if current else "(未设置)"
        current_lbl = QLabel(f"当前: {current_text}")
        current_lbl.setStyleSheet("font-size: 12px; color: #888;")
        current_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(current_lbl)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self._on_clear)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        ok_btn = QPushButton("确认")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_confirm)
        btn_layout.addWidget(ok_btn)

        layout.addLayout(btn_layout)

        # Capture key events
        self.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        """Capture key press events."""
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            modifiers = event.modifiers()

            # Ignore standalone modifier keys
            if key in (
                Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
            ):
                return True

            ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
            alt = bool(modifiers & Qt.KeyboardModifier.AltModifier)

            # For character keys, prefer the text representation
            text = event.text()
            if text and len(text) == 1 and text.isprintable() and not ctrl and not alt:
                # Plain key with maybe shift → use character
                self._captured = KeyBinding(
                    key=text.lower() if not shift else text,
                    ctrl_key=ctrl,
                    shift_key=shift,
                    alt_key=alt,
                )
            else:
                self._captured = KeyBinding(
                    code=key,
                    ctrl_key=ctrl,
                    shift_key=shift,
                    alt_key=alt,
                )

            self._capture_label.setText(keybinding_to_string(self._captured))
            return True
        return super().eventFilter(obj, event)

    def _on_clear(self) -> None:
        """Clear the shortcut for this action."""
        self._mw.keybinding_manager.reset_user_binding(self._action)
        self._mw.config.set_keybindings(
            self._mw.keybinding_manager.user_overrides
        )
        self.accept()

    def _on_confirm(self) -> None:
        """Save the captured shortcut."""
        if self._captured is not None:
            self._mw.keybinding_manager.set_user_binding(
                self._action, [self._captured]
            )
            self._mw.config.set_keybindings(
                self._mw.keybinding_manager.user_overrides
            )
        self.accept()
