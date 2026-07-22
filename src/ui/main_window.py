"""Main application window — QMainWindow with header + content stack + footer."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.audio_manager import AudioManager, AudioState, AudioStateData
from ..core.config_manager import ConfigManager
from ..core.constants import InputAction, PageRoute, SyncMode
from ..core.keybinding import KeyBindingManager
from ..core.lrc_parser import FormatOptions, Fixed, TrimOptions
from ..core.lrc_state import LrcStateManager
from .content_stack import ContentStack
from .footer_bar import FooterBar
from .header_bar import HeaderBar
from .toast_overlay import ToastOverlay


class MainWindow(QMainWindow):
    """Top-level application window.

    Orchestrates all shared state and connects signals between components.
    """

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("集成歌曲编辑器")
        self.setMinimumSize(800, 500)

        # ── Shared State Objects ────────────────────────────
        self.config = ConfigManager()
        self.lrc_state = LrcStateManager(self)
        self.audio_manager = AudioManager(self)
        self.keybinding_manager = KeyBindingManager(
            user_overrides=self.config.get_keybindings()
        )

        # Guard against _save_state re-creating draft during close
        self._closing = False

        # Load saved preferences
        prefs = self.config.get_preferences()

        # Trim options for parsing
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        self._trim_options = TrimOptions(
            trim_start=space_start >= 0,
            trim_end=space_end >= 0,
        )

        # Format options for stringify
        self._format_options = FormatOptions(
            space_start=space_start,
            space_end=space_end,
            fixed=prefs.get("fixed", 3),
            end_of_line="\r\n",
        )
        self.lrc_state.update_format_options(self._format_options)

        # ── Build UI ────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        self.header_bar = HeaderBar()
        layout.addWidget(self.header_bar)

        # Content stack (pages)
        self.content_stack = ContentStack(self)
        layout.addWidget(self.content_stack, stretch=1)

        # Footer (audio controls)
        self.footer_bar = FooterBar(self)
        layout.addWidget(self.footer_bar)

        # Toast overlay (positioned absolutely at top-right)
        self.toast_overlay = ToastOverlay(self)

        # ── Conect Signals ──────────────────────────────────
        self._connect_signals()

        # ── Load saved draft ────────────────────────────────
        if self.config.get_remember_draft():
            saved_lyric = self.config.get_lyric()
            if saved_lyric:
                self.lrc_state.init_from_text(
                    text=saved_lyric,
                    options=self._trim_options,
                    select=self.config.get_select_index(),
                )

        # Restore audio source: last path takes priority (if remember enabled)
        if self.config.get_remember_last_mp3():
            last_mp3 = self.config.get_last_mp3_path()
            if last_mp3:
                import os
                if os.path.exists(last_mp3):
                    url = QUrl.fromLocalFile(last_mp3).toString()
                    self.config.set_audio_src(url)
                    self.audio_manager.set_source(url)
        if not self.audio_manager.src:
            saved_src = self.config.get_audio_src()
            if saved_src:
                self.audio_manager.set_source(saved_src)

        # Restore last LRC file
        if self.config.get_remember_last_lrc():
            last_lrc = self.config.get_last_lrc_path()
            if last_lrc:
                import os
                if os.path.exists(last_lrc):
                    try:
                        with open(last_lrc, "r", encoding="utf-8") as f:
                            text = f.read()
                        self.lrc_state.init_from_text(
                            text=text,
                            options=self._trim_options,
                            select=self.config.get_select_index(),
                        )
                    except Exception:
                        pass  # Silently fail if file can't be loaded

        # Show home page by default
        self.content_stack.set_page(PageRoute.HOME)

        # Welcome dialog on startup (skipped when disabled in preferences)
        if self.config.get_show_welcome():
            QTimer.singleShot(400, self._show_welcome_dialog)

        # ── Install app-wide event filter for keyboard shortcuts ──
        # Must be on QApplication (not self) so that key events are
        # intercepted BEFORE they reach the focused child widget.
        # This way Space → timestamp always works when a lyric is
        # selected, even if focus is on the play button or elsewhere.
        from PyQt6.QtWidgets import QApplication as QA
        app_instance = QA.instance()
        if app_instance:
            app_instance.installEventFilter(self)

    # ── Event Filter (global keyboard) ──────────────────────

    def eventFilter(self, obj, event):
        """App-wide event filter: routes keyboard events.

        When a lyric line is selected on the synchronizer page:
        - Space (SYNC) always timestamps — never toggles play/pause
        - Audio shortcuts (seek, rate, toggle) are blocked entirely
        Other key events fall through to handle_global_key.

        Keyboard shortcuts are suppressed when a text-input widget has
        focus so that dialogs (like pattern-match / AI assist) receive
        normal text input.
        """
        from PyQt6.QtCore import QEvent
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        # ── Don't steal keys from text-input widgets ──
        from PyQt6.QtWidgets import QApplication
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and isinstance(
            focus_widget, (QLineEdit, QPlainTextEdit, QTextEdit)
        ):
            return super().eventFilter(obj, event)

        # ── Intercept when a lyric is selected on the sync page ──
        sync_page = self.content_stack._pages.get(PageRoute.SYNCHRONIZER)
        if (
            sync_page is not None
            and self.content_stack.currentIndex() == PageRoute.SYNCHRONIZER
            and self.lrc_state.select_index != -1
        ):
            action = self.keybinding_manager.get_matched_action(event)

            # Audio shortcuts must NOT fire when a lyric is selected
            if action in (
                InputAction.SEEK_BACKWARD,
                InputAction.SEEK_FORWARD,
                InputAction.RESET_RATE,
                InputAction.TOGGLE_PLAY,
                InputAction.INCREASE_RATE,
                InputAction.DECREASE_RATE,
            ):
                return True  # Swallow the event

            # Space → always timestamp, regardless of which widget has focus
            if action == InputAction.SYNC:
                sync_page._on_sync()
                return True

        # Fall through to normal global-key handling
        if self.handle_global_key(event):
            return True
        return super().eventFilter(obj, event)

    # ── Close Event (draft warning) ─────────────────────────

    def closeEvent(self, event) -> None:
        """Intercept window close to clean up all session drafts."""
        if len(self.lrc_state.lyric) > 0:
            # Stop audio timer to prevent it from re-creating drafts
            self.audio_manager._timer.stop()
            # Block _save_state, then clear UI
            self._closing = True
            self.lrc_state.init_from_text("", self._trim_options)

        # Delete every draft this session ever touched
        self.config.cleanup_session_drafts()
        self._closing = True
        super().closeEvent(event)

    # ── App-level keyboard handler ──────────────────────────

    def handle_global_key(self, event: QKeyEvent) -> bool:
        """Handle keyboard events globally.

        Returns True if the event was handled.
        """
        action = self.keybinding_manager.get_matched_action(event)

        if action is None:
            return False

        # Audio source must be set for audio actions
        if action in (
            InputAction.SEEK_BACKWARD,
            InputAction.SEEK_FORWARD,
            InputAction.RESET_RATE,
            InputAction.INCREASE_RATE,
            InputAction.DECREASE_RATE,
            InputAction.TOGGLE_PLAY,
        ):
            if not self.audio_manager.src:
                return False

        # Rate uses log scale from web app: playbackRate ∈ [1/e, e]
        # rate_slider_value = ln(playbackRate)
        # playbackRate = exp(rate_slider_value)
        import math

        rate = self.audio_manager.playback_rate

        if action == InputAction.SEEK_BACKWARD:
            self.audio_manager.step(event.modifiers(), -5)
            return True
        elif action == InputAction.SEEK_FORWARD:
            self.audio_manager.step(event.modifiers(), 5)
            return True
        elif action == InputAction.RESET_RATE:
            self.audio_manager.playback_rate = 1.0
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(1.0)
            return True
        elif action == InputAction.INCREASE_RATE:
            log_rate = math.log(rate)
            new_rate = math.exp(min(log_rate + 0.2, 1.0))
            self.audio_manager.playback_rate = new_rate
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(new_rate)
            return True
        elif action == InputAction.DECREASE_RATE:
            log_rate = math.log(rate) if rate > 0 else 0
            new_rate = math.exp(max(log_rate - 0.2, -1.0))
            self.audio_manager.playback_rate = new_rate
            if self.config.get_remember_playback_rate():
                self.config.set_last_playback_rate(new_rate)
            return True
        elif action == InputAction.TOGGLE_PLAY:
            self.audio_manager.toggle()
            return True
        elif action == InputAction.SHOW_HELP:
            self._show_help_dialog()
            return True
        elif action == InputAction.UNDO:
            self.lrc_state.undo()
            return True
        elif action == InputAction.REDO:
            self.lrc_state.redo()
            return True

        return False

    # ── Signal Wiring ───────────────────────────────────────

    def _connect_signals(self) -> None:
        # Header navigation
        self.header_bar.page_requested.connect(self.content_stack.set_page)

        # Audio manager -> footer + lrc_state + toast
        self.audio_manager.state_changed.connect(self._on_audio_state_changed)
        self.audio_manager.error_occurred.connect(self._on_audio_error)
        self.audio_manager.duration_changed.connect(self._on_duration_loaded)

        # LRC state changes -> save
        self.lrc_state.state_changed.connect(self._save_state)

        # Content stack notifies when synchronizer page is shown/hidden
        self.content_stack.sync_page_active_changed.connect(self._on_sync_page_changed)

    def _on_audio_state_changed(self, data: AudioStateData) -> None:
        self.footer_bar.update_audio_state(data)

    def _on_audio_error(self, message: str) -> None:
        self.toast_overlay.show_toast("warning", message)

    def _on_duration_loaded(self, duration: float) -> None:
        try:
            from ..core.lrc_parser import convert_time_to_tag
            self.lrc_state.set_info(
                "length",
                convert_time_to_tag(duration, self._format_options.fixed, False),
            )
            self.toast_overlay.show_toast("success", "音频已载入")
        except Exception:
            pass  # Prevent crash during audio metadata update

        # ── Auto-load same-name LRC ───────────────────────────
        self._try_load_matching_lrc()

    def _try_load_matching_lrc(self) -> None:
        """If a .lrc or .txt file with the same stem as the audio exists
        in the same directory, load it automatically.
        """
        import os
        src = self.audio_manager.src
        if not src:
            return
        path = QUrl(src).toLocalFile()
        if not path or not os.path.isfile(path):
            return

        base, _ = os.path.splitext(path)
        for ext in (".lrc", ".txt"):
            lrc_path = base + ext
            if not os.path.exists(lrc_path):
                continue
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    text = f.read()
                self.lrc_state.init_from_text(
                    text=text,
                    options=self._trim_options,
                    select=0,
                )
                if self.config.get_remember_last_lrc():
                    self.config.set_last_lrc_path(lrc_path)
                self.toast_overlay.show_toast(
                    "success",
                    f"已自动加载同名歌词：{os.path.basename(lrc_path)}",
                )
            except Exception:
                pass
            return  # Only load the first match (.lrc preferred over .txt)

    def _on_sync_page_changed(self, active: bool) -> None:
        if active:
            # Connect audio time -> lrc refresh
            self.audio_manager.current_time_changed.connect(
                self.lrc_state.refresh
            )
        else:
            # Disconnect when leaving sync page
            try:
                self.audio_manager.current_time_changed.disconnect(
                    self.lrc_state.refresh
                )
            except TypeError:
                pass  # Not connected

    def _save_state(self) -> None:
        """Persist state to config when it changes."""
        if self._closing:
            return
        if self.config.get_remember_draft():
            text = self.lrc_state.stringify(self._format_options)
            self.config.set_lyric(text)
            self.config.set_select_index(self.lrc_state.select_index)

    # ── Public Helpers ──────────────────────────────────────

    def _show_welcome_dialog(self) -> None:
        """Show the welcome guide on startup (one-shot, can be disabled).

        Contains the old HomePage content: a 3-step getting-started guide
        with a "don't show again" checkbox that persists to preferences.
        """
        from PyQt6.QtWidgets import (
            QCheckBox,
            QDialog,
            QDialogButtonBox,
            QLabel,
            QPushButton,
            QVBoxLayout as QVBL,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("欢迎使用集成歌曲编辑器")
        dlg.setMinimumSize(440, 380)

        lay = QVBL(dlg)
        lay.setContentsMargins(28, 24, 28, 16)
        lay.setSpacing(14)

        # ── Title ──
        title = QLabel("集成歌曲编辑器")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        # ── Steps ──
        for text in [
            "1. 切换到「歌词制作」页面，导入或粘贴歌词文本。",
            "2. 点击左下方按钮载入音频文件，或直接拖入。",
            "3. 播放音频、按空格键，就能逐行打时间轴啦～",
        ]:
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 15px;")
            lay.addWidget(lbl)

        lay.addSpacing(8)

        # ── Quick-jump button ──
        btn_sync = QPushButton("→ 前往歌词制作")
        btn_sync.setStyleSheet(
            "QPushButton { font-size: 15px; padding: 8px 16px; }"
        )
        btn_sync.clicked.connect(lambda: (
            self.content_stack.set_page(PageRoute.SYNCHRONIZER),
            dlg.accept(),
        ))
        lay.addWidget(btn_sync, alignment=Qt.AlignmentFlag.AlignCenter)

        lay.addStretch()

        # ── "Don't show again" ──
        cb = QCheckBox("启动时不再显示此引导")
        cb.setStyleSheet("font-size: 13px; color: #888888;")
        lay.addWidget(cb)

        # ── Close button ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)

        dlg.exec()

        # Persist preference
        if cb.isChecked():
            prefs = self.config.get_preferences()
            prefs["showWelcome"] = False
            self.config.set_preferences(prefs)

    def _show_help_dialog(self) -> None:
        """Show the help / about dialog with feature overview and tips."""
        from PyQt6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QLabel,
            QTabWidget,
            QScrollArea,
            QWidget,
            QVBoxLayout as QVBL,
            QHBoxLayout as QHBL,
        )
        from ..core.keybinding import (
            ACTION_GROUPS,
            ACTION_LABELS,
            action_to_string,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle("帮助")
        dialog.resize(680, 520)
        dialog.setMinimumSize(560, 400)

        layout = QVBL(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs = QTabWidget()
        layout.addWidget(tabs, stretch=1)

        # ── Helper: make a scrollable tab ──
        def _make_tab(title: str) -> tuple[QScrollArea, QVBL]:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            container = QWidget()
            lay = QVBL(container)
            lay.setContentsMargins(24, 20, 24, 20)
            lay.setSpacing(14)
            scroll.setWidget(container)
            tabs.addTab(scroll, title)
            return scroll, lay

        # ── Tab 1: 关于 ──
        _, lay_about = _make_tab("关于")
        for text, style in [
            ("集成歌曲编辑器", "font-size: 22px; font-weight: bold;"),
            (
                "这是一款帮你为歌曲制作滚动歌词（LRC 歌词文件）的小工具。\n"
                "边听歌边按空格键，就能轻松给每行歌词打上时间戳。",
                "font-size: 14px; line-height: 1.6;",
            ),
            (
                "✨ 你可以用它来：\n"
                "• 给喜欢的歌曲制作精准的滚动歌词\n"
                "• 为外语歌曲添加中文翻译\n"
                "• 修正网上下载的歌词时间不准的问题\n"
                "• 用 AI 帮忙翻译歌词\n"
                "• 根据个人喜好调整主题颜色和显示风格",
                "font-size: 13px; line-height: 1.8;",
            ),
            (
                "💡 提示：按 ? 键可以随时打开这个帮助窗口",
                "font-size: 12px; color: gray;",
            ),
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet(style)
            lbl.setWordWrap(True)
            lay_about.addWidget(lbl)
        lay_about.addStretch()

        # ── Tab 2: 使用流程 ──
        _, lay_flow = _make_tab("使用流程")
        steps = [
            ("① 载入歌曲",
             "点击左下角的加载按钮（或按 Ctrl+R），选择你的歌曲文件。\n"
             "支持 MP3、FLAC、WAV 等常见格式。也可以直接把歌曲文件拖到窗口底部。"),
            ("② 导入歌词",
             "点击工具栏的「导入」按钮，选择歌词文件。\n"
             "如果歌曲旁边有同名的歌词文件，软件会自动帮你找到它。\n"
             "歌词文件可以是 .lrc 或 .txt 格式，每行一句歌词即可。"),
            ("③ 开始打轴",
             "这是最关键的一步！在「歌词制作」页面：\n"
             "• 点击播放按钮开始听歌\n"
             "• 听到某句歌词开始唱的时候，按空格键——这行歌词就自动记录下当前时间\n"
             "• 光标会自动跳到下一行，继续听、继续按空格\n"
             "• 下方的波形图可以帮助你判断歌词出现的位置"),
            ("④ 检查和微调",
             "• 点击某行的时间数字可以跳到那个位置重新听\n"
             "• 如果时间不太对，选中那一行按 Backspace 删掉时间，重新打一次\n"
             "• 双击歌词文字可以修改歌词内容\n"
             "• 右键点击歌词行还有更多操作（拆分长句、插入空行等）"),
            ("⑤ 保存导出",
             "• 点击「保存」直接覆盖原来的歌词文件\n"
             "• 点击「导出」另存为一个新文件\n"
             "• 点击「预览」看看最终歌词文件长什么样"),
        ]
        for title, desc in steps:
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet("font-size: 15px; font-weight: bold;")
            lay_flow.addWidget(title_lbl)
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("font-size: 13px;")
            desc_lbl.setWordWrap(True)
            lay_flow.addWidget(desc_lbl)
        lay_flow.addStretch()

        # ── Tab 3: 实用技巧 ──
        _, lay_tips = _make_tab("实用技巧")
        tips = [
            ("🎯 怎么打得准？",
             "• 先粗打一遍，边听边按空格，不用纠结毫秒级精度\n"
             "• 打完后再从头听一遍，发现不对的就选中按 Backspace 重打\n"
             "• 善用波形图——歌词通常在有波峰的地方开始\n"
             "• 可以调慢播放速度（右下角的滑块），在难打的部分放慢来听"),
            ("⌨️ 键盘操作更高效",
             "• 全程用键盘就能完成打轴，不需要频繁切换鼠标\n"
             "• 空格打轴、↑↓ 选行、Backspace 删时间——这三个最常用\n"
             "• 快捷键可以在「设置」页面里自定义成你习惯的按键"),
            ("🌐 翻译歌词",
             "• 点击「翻译模式」按钮，每行歌词下方会出现翻译输入框\n"
             "• 如果你已有带翻译的歌词文件，用「模式匹配」可以自动匹配进去\n"
             "• 「AI 辅助翻译」可以调用 AI 帮你批量翻译（需要配置 API Key）"),
            ("🎨 个性化设置",
             "• 在「设置」页面可以切换亮色/暗色主题\n"
             "• 可以自定义主题色，选你喜欢的颜色\n"
             "• 波形图、虚拟空格键等辅助功能都可以按需开关\n"
             "• 歌词的时间精度（秒后几位）可以在设置中调整"),
            ("💾 数据安全",
             "• 你的歌词会随时自动保存为草稿，不小心关了软件也不会丢\n"
             "• 每一步操作都可以撤销（Ctrl+Z）和重做（Ctrl+Y）"),
        ]
        for title, desc in tips:
            title_lbl = QLabel(title)
            title_lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
            lay_tips.addWidget(title_lbl)
            desc_lbl = QLabel(desc)
            desc_lbl.setStyleSheet("font-size: 13px;")
            desc_lbl.setWordWrap(True)
            lay_tips.addWidget(desc_lbl)
        lay_tips.addStretch()

        # ── Tab 4: 快捷键参考 ──
        _, lay_keys = _make_tab("快捷键参考")
        note = QLabel("下面列出了所有默认快捷键，你可以在「设置」页面修改它们。")
        note.setStyleSheet("font-size: 12px; color: gray;")
        note.setWordWrap(True)
        lay_keys.addWidget(note)
        for group_name, actions in ACTION_GROUPS:
            group_lbl = QLabel(group_name)
            group_lbl.setStyleSheet(
                "font-size: 14px; font-weight: bold; margin-top: 4px;"
            )
            lay_keys.addWidget(group_lbl)
            for act in actions:
                label = ACTION_LABELS.get(act, act.value)
                binding_str = action_to_string(act)
                row = QHBL()
                key_lbl = QLabel(binding_str)
                key_lbl.setStyleSheet(
                    "font-size: 12px; font-family: Consolas, monospace; "
                    "background: palette(midlight); padding: 2px 8px; "
                    "border-radius: 3px;"
                )
                key_lbl.setFixedWidth(200)
                row.addWidget(key_lbl)
                desc_lbl = QLabel(label)
                desc_lbl.setStyleSheet("font-size: 13px;")
                row.addWidget(desc_lbl, stretch=1)
                lay_keys.addLayout(row)
        lay_keys.addStretch()

        # ── Close button ──
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        dialog.exec()

    def update_preferences(self, prefs: dict) -> None:
        """Apply preference changes to all components."""
        space_start = prefs.get("spaceStart", 1)
        space_end = prefs.get("spaceEnd", 0)
        self._trim_options = TrimOptions(
            trim_start=space_start >= 0,
            trim_end=space_end >= 0,
        )
        self._format_options = FormatOptions(
            space_start=space_start,
            space_end=space_end,
            fixed=prefs.get("fixed", 3),
            end_of_line="\r\n",
        )
        self.lrc_state.update_format_options(self._format_options)
        self.config.set_preferences(prefs)

        # Re-apply theme
        from .content_stack import apply_theme
        apply_theme(prefs)

    @property
    def format_options(self) -> FormatOptions:
        return self._format_options

    @property
    def trim_options(self) -> TrimOptions:
        return self._trim_options
