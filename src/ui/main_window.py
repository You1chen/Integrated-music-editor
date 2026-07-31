"""Main application window — QMainWindow with header + content stack + footer."""

from __future__ import annotations

import math
import os

from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
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
        # Guard against _save_state during initial draft/file restoration
        self._restoring_draft = False
        # Track which audio last triggered _try_load_matching_lrc
        # (used to detect audio switches and clear stale lyrics)
        self._last_audio_for_lrc: str = ""

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

        # ── Load saved draft (consume it — read then delete) ──
        self._restoring_draft = True
        if self.config.get_remember_draft():
            saved_lyric = self.config.get_lyric()
            if saved_lyric:
                self.lrc_state.init_from_text(
                    text=saved_lyric,
                    options=self._trim_options,
                    select=self.config.get_select_index(),
                )
            self.config.delete_draft()  # consumed — won't exist again until exit

        # Restore audio source: last path takes priority (if remember enabled)
        if self.config.get_remember_last_mp3():
            last_mp3 = self.config.get_last_mp3_path()
            if last_mp3:
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

        self._restoring_draft = False

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
        app_instance = QApplication.instance()
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
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        # ── Don't steal keys from text-input widgets ──
        focus_widget = QApplication.focusWidget()
        if focus_widget is not None and isinstance(
            focus_widget, (QLineEdit, QPlainTextEdit, QTextEdit)
        ):
            return super().eventFilter(obj, event)

        # ── Intercept when a lyric is selected on the sync page ──
        sync_page = self.content_stack._pages.get(PageRoute.SYNCHRONIZER)
        has_selection = (
            self.lrc_state.select_index != -1
            or bool(
                sync_page is not None
                and getattr(sync_page, "_multi_selected", set())
            )
        )
        if (
            sync_page is not None
            and self.content_stack.currentIndex() == PageRoute.SYNCHRONIZER
            and has_selection
        ):
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            action = self.keybinding_manager.get_matched_action(event)

            # ── Shift held → playback mode (even when lyrics selected) ──
            if shift:
                if action == InputAction.SYNC:
                    self.audio_manager.toggle()
                    return True
                # Other audio shortcuts (seek, rate, …) fall
                # through to handle_global_key below
            else:
                # ── No Shift → synchronizer mode ──────────────
                # Left / Right arrows → jump to prev / next timestamp
                if action == InputAction.SEEK_BACKWARD and event.key() == Qt.Key.Key_Left:
                    sync_page._on_jump_prev_timestamp()
                    return True
                if action == InputAction.SEEK_FORWARD and event.key() == Qt.Key.Key_Right:
                    sync_page._on_jump_next_timestamp()
                    return True

                # Block audio shortcuts
                if action in (
                    InputAction.SEEK_BACKWARD,
                    InputAction.SEEK_FORWARD,
                    InputAction.RESET_RATE,
                    InputAction.TOGGLE_PLAY,
                    InputAction.INCREASE_RATE,
                    InputAction.DECREASE_RATE,
                ):
                    return True

                # Space → timestamp
                if action == InputAction.SYNC:
                    sync_page._on_sync()
                    return True

        # Fall through to normal global-key handling
        if self.handle_global_key(event):
            return True
        return super().eventFilter(obj, event)

    # ── Close Event (draft handling) ────────────────────────

    def closeEvent(self, event) -> None:
        """Handle draft lifecycle on window close.

        - If ``overwriteSourceOnExit``: overwrite the source LRC file.
        - If ``rememberDraft``: write one draft to AppData/draft.lrc.
        The draft is read back on next launch and immediately deleted.
        """
        if len(self.lrc_state.lyric) > 0:
            self.audio_manager._timer.stop()
            self._closing = True

            if self.config.get_overwrite_source_on_exit():
                text = self.lrc_state.stringify(self._format_options)
                self.config.overwrite_lrc(text)

            if self.config.get_remember_draft():
                text = self.lrc_state.stringify(self._format_options)
                self.config.set_lyric(text)

            self.lrc_state.init_from_text("", self._trim_options)

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
            # Snapshot timestamps so we can detect a sync / set_time undo
            pre_count = len(self.lrc_state.lyric)
            pre_times = [line.time for line in self.lrc_state.lyric]

            self.lrc_state.undo()

            # When the line count is unchanged and a timestamp was
            # removed or changed, seek the audio back so the user can
            # re-listen and re-stamp straight away.
            if len(self.lrc_state.lyric) == pre_count:
                seek_secs = float(
                    self.config.get_preferences().get("undoSeekBackSeconds", 3.0)
                )
                if seek_secs > 0:
                    for i in range(pre_count):
                        old_t = pre_times[i]
                        new_t = self.lrc_state.lyric[i].time
                        if old_t is not None and old_t != new_t:
                            seek_target = max(0.0, old_t - seek_secs)
                            if self.audio_manager.duration > 0:
                                self.audio_manager.current_time = seek_target
                            break

            return True
        elif action == InputAction.REDO:
            self.lrc_state.redo()
            return True

        return False

    # ── Signal Wiring ───────────────────────────────────────

    def _connect_signals(self) -> None:
        # Header navigation
        self.header_bar.page_requested.connect(self.content_stack.set_page)
        self.header_bar.help_requested.connect(self._show_help_dialog)

        # Audio manager -> footer + lrc_state + toast
        self.audio_manager.state_changed.connect(self._on_audio_state_changed)
        self.audio_manager.error_occurred.connect(self._on_audio_error)
        self.audio_manager.duration_changed.connect(self._on_duration_loaded)

        # LRC state changes -> select-index persistence only
        self.lrc_state.state_changed.connect(self._save_select_index)

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
        src = self.audio_manager.src
        if not src:
            return
        path = QUrl(src).toLocalFile()
        if not path or not os.path.isfile(path):
            return

        previous_audio = self._last_audio_for_lrc
        self._last_audio_for_lrc = path

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
                self.config.remember_lrc_path(lrc_path)
                self.toast_overlay.show_toast(
                    "success",
                    f"已自动加载同名歌词：{os.path.basename(lrc_path)}",
                )
            except Exception:
                pass
            return  # Only load the first match (.lrc preferred over .txt)

        # No matching LRC found for this audio.
        # If the audio has changed since the last load, clear the previous
        # song's lyrics so they don't linger.  We track the audio path
        # directly rather than relying on lastLrcPath (which may not be
        # set when rememberLastLrc is off, or when lyrics were manually
        # entered without a file).
        if previous_audio and previous_audio != path:
            self.lrc_state.init_from_text("", self._trim_options)
            self.config.set_last_lrc_path("")  # clear stale path too

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

    def _save_select_index(self) -> None:
        """Persist the current select_index (lightweight, no draft)."""
        if self._closing or self._restoring_draft:
            return
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
        dlg.setMinimumSize(460, 420)

        lay = QVBL(dlg)
        lay.setContentsMargins(28, 24, 28, 16)
        lay.setSpacing(12)

        # ── Title ──
        title = QLabel("集成歌曲编辑器")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        # ── Subtitle ──
        sub = QLabel("LRC 歌词制作 · 打轴 · 翻译 · 元数据编辑")
        sub.setStyleSheet("font-size: 13px; color: gray;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)

        lay.addSpacing(4)

        # ── Steps ──
        for text in [
            "1. 点击左下角加载音频，或将文件拖入窗口",
            "2. 在「歌词制作」页面导入歌词文本",
            "3. 播放音频，按空格键逐行打时间戳 🎵",
            "4. 用编辑功能精细调整：拆分、合并、删除、翻译",
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
        """Show the help dialog with feature overview, workflow, and shortcuts."""
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
        dialog.resize(720, 560)
        dialog.setMinimumSize(600, 420)

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

        def _section(lay: QVBL, title: str, desc: str) -> None:
            """Add a titled section to a layout."""
            tl = QLabel(title)
            tl.setStyleSheet("font-size: 15px; font-weight: bold;")
            lay.addWidget(tl)
            dl = QLabel(desc)
            dl.setStyleSheet("font-size: 13px;")
            dl.setWordWrap(True)
            lay.addWidget(dl)

        # ══════════════════════════════════════════════════════════
        # Tab 1: 关于
        # ══════════════════════════════════════════════════════════
        _, lay_about = _make_tab("关于")
        for text, style in [
            ("集成歌曲编辑器", "font-size: 22px; font-weight: bold;"),
            (
                "一款为歌曲制作滚动歌词（LRC 文件）的桌面工具。\n"
                "边听歌边按空格键，逐行打上时间戳，轻松完成打轴。\n\n"
                "支持多选编辑、批量删除、相邻行合并、AI 辅助翻译、\n"
                "主题自定义、快捷键全自定义等功能。",
                "font-size: 14px; line-height: 1.6;",
            ),
            (
                "✨ 你可以用它来：\n"
                "• 给喜欢的歌曲制作精准的滚动歌词\n"
                "• 为外语歌曲添加中文翻译\n"
                "• 修正网上下载的歌词时间不准的问题\n"
                "• 用 AI 帮忙翻译歌词（支持 OpenAI 兼容接口）\n"
                "• 批量删除、合并歌词行，高效整理歌词\n"
                "• 编辑音频元数据（ID3 / VorbisComment）和封面图\n"
                "• 根据个人喜好调整主题颜色、亮暗模式和显示风格",
                "font-size: 13px; line-height: 1.8;",
            ),
            (
                "💡 提示：按 ? 键或点击顶栏的 ? 按钮可随时打开此帮助",
                "font-size: 12px; color: gray;",
            ),
        ]:
            lbl = QLabel(text)
            lbl.setStyleSheet(style)
            lbl.setWordWrap(True)
            lay_about.addWidget(lbl)
        lay_about.addStretch()

        # ══════════════════════════════════════════════════════════
        # Tab 2: 使用流程
        # ══════════════════════════════════════════════════════════
        _, lay_flow = _make_tab("使用流程")
        steps = [
            ("① 载入歌曲",
             "点击左下角的加载按钮（或按 Ctrl+R），选择歌曲文件。\n"
             "支持 MP3、FLAC、WAV 等常见格式，也可以直接把文件拖到窗口底部。\n"
             "载入后主页会显示封面和滚动歌词轴，波形图会自动生成。"),
            ("② 导入歌词",
             "点击「歌词制作」页面的「导入」按钮，选择歌词文件。\n"
             "如果歌曲旁边有同名 .lrc/.txt 文件，软件会自动加载。\n"
             "也可以直接在歌词输入框中粘贴歌词，按 Enter 提交。"),
            ("③ 开始打轴",
             "这是最核心的步骤：\n"
             "• 播放音频，听到某句歌词开始时按 空格键 → 自动记录当前时间\n"
             "• 光标自动跳到下一行，继续听、继续按空格\n"
             "• 波形图可以帮你预判歌词出现的位置\n"
             "• 可以调慢播放速度（右下角滑块），慢速精准打轴"),
            ("④ 精细编辑",
             "打轴完成后进入编辑阶段：\n"
             "• 点击时间戳按钮 → 跳转到该位置复听\n"
             "• Backspace → 删除当前行的时间戳（重新打）\n"
             "• Delete → 彻底删除选中的歌词行\n"
             "• 双击歌词文字 → 内联编辑文本\n"
             "• 右键菜单 → 编辑 / 拆分 / 追加新行\n"
             "• Ctrl+H → 合并相邻选中行为一行"),
            ("⑤ 保存导出",
             "• Ctrl+S 保存 → 直接覆盖源歌词文件\n"
             "• Ctrl+Shift+S 导出 → 另存为新文件\n"
             "• Ctrl+L 预览 → 查看最终 LRC 输出效果\n"
             "• 退出时可选自动覆写源文件（在设置中配置）"),
        ]
        for title, desc in steps:
            _section(lay_flow, title, desc)
        lay_flow.addStretch()

        # ══════════════════════════════════════════════════════════
        # Tab 3: 歌词编辑
        # ══════════════════════════════════════════════════════════
        _, lay_edit = _make_tab("歌词编辑")
        edit_sections = [
            ("🖱️ 多选操作",
             "• Ctrl+左键 点击歌词行 → 加入或移出多选（虚线边框标记）\n"
             "• Ctrl+A → 全选所有歌词行\n"
             "• 单击任意行 → 取消多选，单选该行\n"
             "• Esc → 取消所有选择\n"
             "• 多选后可以批量删除或合并"),
            ("🗑️ 删除歌词行",
             "• Delete 键 → 删除所有选中的行（完全删除，含时间和翻译）\n"
             "• Backspace 键 → 仅清除时间戳，保留歌词文本\n"
             "• 支持单选和多选批量删除\n"
             "• 所有删除操作均可 Ctrl+Z 撤销"),
            ("🔗 合并歌词行",
             "• Ctrl+H → 将选中的相邻行合并为一行\n"
             "• 合并取最早的时间戳，拼接所有文本内容\n"
             "• 仅相邻行可合并（不相邻会提示错误）\n"
             "• 示例：选中第 1,2,3 行按 Ctrl+H → 合并为一行"),
            ("✂️ 拆分歌词行",
             "• Ctrl+D 或右键菜单「拆分」→ 进入拆分模式\n"
             "• 在文本中插入 // 标记拆分位置，按 Enter 确认\n"
             "• 或在光标位置按 Ctrl+Enter 直接拆分\n"
             "• // 标记会被完全移除，不会出现在最终歌词中"),
            ("📝 追加和复制",
             "• Ctrl+右键 点击歌词行 → 在该行下方追加空行（继承时间戳）\n"
             "• Ctrl+C → 复制当前行到下方（同样文本和时间戳）\n"
             "• 歌词输入框支持多行粘贴，Enter 提交"),
            ("🌐 翻译模式",
             "• Ctrl+T → 切换翻译编辑模式\n"
             "• 每行下方出现翻译输入框，可直接编辑\n"
             "• 「模式匹配」→ 粘贴带翻译的 LRC，自动按时间戳匹配\n"
             "• 「AI 辅助翻译」→ 调用 AI API 批量翻译（需配置模型）"),
        ]
        for title, desc in edit_sections:
            _section(lay_edit, title, desc)
        lay_edit.addStretch()

        # ══════════════════════════════════════════════════════════
        # Tab 4: 快捷键参考
        # ══════════════════════════════════════════════════════════
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
