"""AI-assisted translation: dialog, prompt generation, API calls, pattern matching.

All functions in this module accept a ``sync_page`` parameter (the
``SynchronizerPage`` instance) to access shared state without tight
coupling to the page class itself.
"""

from __future__ import annotations

import re
import threading
from collections import defaultdict

from openai import OpenAI
from typing import TYPE_CHECKING


# ── Helpers ────────────────────────────────────────────────────────

def _extract_base_url(api_url: str) -> str:
    """Convert a full chat-completions URL to the base URL the openai
    library expects (strip trailing /v1/chat/completions or /chat/completions)."""
    return re.sub(r"/(v1/)?chat/completions/?$", "", api_url)


from PyQt6.QtCore import QThread as _QThread, pyqtSignal as _pyqtSignal

class _ApiWorker(_QThread):
    """QThread-based API worker — avoids Windows threading issues with httpx."""
    result_ready = _pyqtSignal(bool, str)  # ok, message/error

    def __init__(self, api_key: str, api_url: str, model: str,
                 messages: list[dict], timeout: float = 15.0,
                 temperature: float | None = None,
                 max_tokens: int | None = 5,
                 parent=None):
        super().__init__(parent)
        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._messages = messages
        self._timeout = timeout
        self._temperature = temperature
        self._max_tokens = max_tokens

    def run(self) -> None:
        try:
            client = OpenAI(
                api_key=self._api_key,
                base_url=_extract_base_url(self._api_url),
                timeout=self._timeout,
            )
            kwargs: dict = {
                "model": self._model,
                "messages": self._messages,
            }
            if self._temperature is not None:
                kwargs["temperature"] = self._temperature
            if self._max_tokens is not None:
                kwargs["max_tokens"] = self._max_tokens
            response = client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""
            self.result_ready.emit(True, content)
        except Exception as e:
            self.result_ready.emit(False, str(e))

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.lrc_parser import Fixed, LyricLine, convert_time_to_tag
from ..content_stack import is_dark_theme

if TYPE_CHECKING:
    from ..synchronizer_page import SynchronizerPage


# ── Prompt Generation ──────────────────────────────────────────────

def build_prompt_text(sync_page: "SynchronizerPage") -> tuple[str, int] | None:
    """Build the AI translation prompt from current LRC lyrics.

    Returns ``(prompt_text, line_count)`` or ``None`` if no usable lyrics.
    """
    state = sync_page._mw.lrc_state
    prefs = sync_page._mw.config.get_preferences()
    fixed: Fixed = prefs.get("fixed", 3)

    lines: list[str] = []
    for ln in state.lyric:
        if ln.time is None:
            continue
        if not ln.text.strip():
            continue
        tag = convert_time_to_tag(ln.time, fixed)
        lines.append(f"{tag}{ln.text}")

    if not lines:
        return None

    lyrics_text = "\n".join(lines)
    prompt = (
        f"{lyrics_text}\n\n"
        "请将以上歌词翻译成中文。严格按照以下格式输出，每行一条，不要输出JSON：\n"
        "[时间戳]翻译内容\n"
        "例如：\n"
        "[00:12.34]翻译后的歌词\n"
        "注意：只输出翻译，不要附带原文；作者歌手这些也要翻译，无时间戳的行不翻译；确保翻译符合上下文逻辑。"
    )
    return prompt, len(lines)


# ── AI Assist Dialog ───────────────────────────────────────────────

def show_ai_assist_dialog(
    sync_page: "SynchronizerPage",
    target_text_edit: QPlainTextEdit | None = None,
) -> None:
    """Open the AI assist dialog with two options for translation help.

    When *target_text_edit* is provided, API auto results fill that
    widget directly instead of opening a new pattern-match dialog.
    """
    mw = sync_page._mw

    dialog = QDialog(sync_page)
    dialog.setWindowTitle("AI 辅助翻译")
    dialog.resize(500, 400)
    dialog.setMinimumSize(420, 320)

    dlg_layout = QVBoxLayout(dialog)
    dlg_layout.setContentsMargins(16, 16, 16, 16)
    dlg_layout.setSpacing(12)

    # Title
    title = QLabel("AI 辅助翻译")
    title.setStyleSheet("font-size: 18px; font-weight: bold;")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dlg_layout.addWidget(title)

    dlg_layout.addSpacing(4)

    # Stacked widget for pages
    stack = QStackedWidget()
    dlg_layout.addWidget(stack, stretch=1)

    # ── Page 0: Two option buttons ──
    options_page = QWidget()
    options_layout = QVBoxLayout(options_page)
    options_layout.setContentsMargins(0, 0, 0, 0)
    options_layout.setSpacing(12)
    options_layout.addStretch()

    btn_chat = QPushButton("模型聊天网站")
    btn_chat.setStyleSheet(
        "QPushButton {"
        "  font-size: 15px; padding: 16px; border: 2px solid #aaa;"
        "  border-radius: 8px; text-align: left;"
        "}"
        "QPushButton:hover {"
        "  border-color: #58a6ff; background-color: rgba(88,166,255,0.1);"
        "}"
    )
    btn_chat.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_chat.clicked.connect(lambda: stack.setCurrentIndex(1))
    options_layout.addWidget(btn_chat)

    btn_api = QPushButton("API 自动")
    btn_api.setStyleSheet(
        "QPushButton {"
        "  font-size: 15px; padding: 16px; border: 2px solid #aaa;"
        "  border-radius: 8px; text-align: left;"
        "}"
        "QPushButton:hover {"
        "  border-color: #58a6ff; background-color: rgba(88,166,255,0.1);"
        "}"
    )
    btn_api.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_api.clicked.connect(lambda: stack.setCurrentIndex(2))
    options_layout.addWidget(btn_api)

    options_layout.addStretch()
    stack.addWidget(options_page)  # index 0

    # ── Page 1: Model chat website ──
    chat_page = QWidget()
    chat_layout = QVBoxLayout(chat_page)
    chat_layout.setContentsMargins(0, 0, 0, 0)
    chat_layout.setSpacing(12)

    btn_back = QPushButton("← 返回")
    btn_back.setFlat(True)
    btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_back.clicked.connect(lambda: stack.setCurrentIndex(0))
    chat_layout.addWidget(btn_back)

    chat_layout.addSpacing(4)

    btn_copy_prompt = QPushButton("📋  生成并复制提示词")
    btn_copy_prompt.setStyleSheet(
        "QPushButton {"
        "  font-size: 14px; padding: 12px; border: 2px solid #58a6ff;"
        "  border-radius: 8px; color: #58a6ff; font-weight: bold;"
        "}"
        "QPushButton:hover { background-color: rgba(88,166,255,0.15); }"
    )
    btn_copy_prompt.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_copy_prompt.clicked.connect(lambda: _generate_and_copy_prompt(sync_page, dialog))
    chat_layout.addWidget(btn_copy_prompt)

    # Separator
    sep = QLabel("— AI 聊天网站 —")
    sep.setAlignment(Qt.AlignmentFlag.AlignCenter)
    sep.setStyleSheet("color: #888; font-size: 12px; margin-top: 8px;")
    chat_layout.addWidget(sep)

    # Links section
    links_widget = QWidget()
    links_layout = QVBoxLayout(links_widget)
    links_layout.setContentsMargins(8, 0, 8, 0)
    links_layout.setSpacing(8)

    deepseek_link = QPushButton("🔗  DeepSeek Chat → chat.deepseek.com")
    deepseek_link.setFlat(True)
    deepseek_link.setCursor(Qt.CursorShape.PointingHandCursor)
    deepseek_link.setStyleSheet(
        "QPushButton {"
        "  font-size: 13px; padding: 8px; color: #58a6ff; text-align: left;"
        "}"
        "QPushButton:hover { text-decoration: underline; color: #79c0ff; }"
    )
    deepseek_link.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl("https://chat.deepseek.com/"))
    )
    links_layout.addWidget(deepseek_link)

    kimi_link = QPushButton("🔗  Kimi Chat → kimi.com")
    kimi_link.setFlat(True)
    kimi_link.setCursor(Qt.CursorShape.PointingHandCursor)
    kimi_link.setStyleSheet(
        "QPushButton {"
        "  font-size: 13px; padding: 8px; color: #58a6ff; text-align: left;"
        "}"
        "QPushButton:hover { text-decoration: underline; color: #79c0ff; }"
    )
    kimi_link.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl("https://www.kimi.com/"))
    )
    links_layout.addWidget(kimi_link)

    chat_layout.addWidget(links_widget)
    chat_layout.addStretch()
    stack.addWidget(chat_page)  # index 1

    # ── Page 2: API auto ──
    api_page = QWidget()
    api_layout = QVBoxLayout(api_page)
    api_layout.setContentsMargins(0, 0, 0, 0)
    api_layout.setSpacing(12)

    btn_back2 = QPushButton("← 返回")
    btn_back2.setFlat(True)
    btn_back2.setCursor(Qt.CursorShape.PointingHandCursor)
    btn_back2.clicked.connect(lambda: stack.setCurrentIndex(0))
    api_layout.addWidget(btn_back2)

    # Sub-stack: model list (0) vs config form (1)
    api_substack = QStackedWidget()
    api_layout.addWidget(api_substack, stretch=1)
    api_layout.addStretch()

    # ── Helper: do the actual translation ──
    def _do_translate(cfg: dict) -> None:
        """Build prompt, call AI API in background, show result in a popup."""
        result = build_prompt_text(sync_page)
        if result is None:
            QMessageBox.warning(
                dialog, "无法翻译",
                "没有可用的歌词正文（需要带时间戳的歌词行）。"
            )
            return

        prompt, line_count = result
        api_url = cfg["url"]
        api_key = cfg["api_key"]
        model = cfg["model"]

        # ── Progress dialog ──
        progress = QDialog(dialog)
        progress.setWindowTitle("API 自动翻译")
        progress.setFixedSize(380, 110)
        progress.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
        )
        p_layout = QVBoxLayout(progress)
        p_layout.setContentsMargins(20, 14, 20, 14)
        p_layout.setSpacing(10)

        p_label = QLabel(f"正在调用 AI 翻译（{line_count} 行歌词）…")
        p_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p_label.setStyleSheet("font-size: 13px;")
        p_layout.addWidget(p_label)

        dots_label = QLabel()
        dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dots_label.setTextFormat(Qt.TextFormat.RichText)
        p_layout.addWidget(dots_label)

        _dot_frame2 = [0]

        def _anim_dots2() -> None:
            parts: list[str] = []
            for j in range(3):
                if j == _dot_frame2[0]:
                    parts.append("<span style='font-size:150%;color:#ddd'>●</span>")
                else:
                    parts.append("<span style='font-size:100%;color:#666'>●</span>")
            dots_label.setText(" ".join(parts))
            _dot_frame2[0] = (_dot_frame2[0] + 1) % 3

        dots_timer2 = QTimer(progress)
        dots_timer2.timeout.connect(_anim_dots2)
        dots_timer2.start(280)
        _anim_dots2()
        progress.show()

        def _on_translate_done(ok: bool, content: str) -> None:
            dots_timer2.stop()
            progress.accept()
            progress.deleteLater()

            if not ok:
                err_msg = content
                masked_key = api_key[:8] + "****" + api_key[-4:] if len(api_key) > 12 else "****"
                detail = (
                    f"错误：{err_msg}\n\n"
                    f"当前配置：\n"
                    f"  名称：{cfg.get('name', '?')}\n"
                    f"  URL ：{api_url}\n"
                    f"  Key ：{masked_key}\n"
                    f"  Model：{model}\n\n"
                )
                if "401" in err_msg or "Unauthorized" in err_msg:
                    detail += (
                        "401 表示 API Key 无效或未授权。请检查：\n"
                        "  ● Key 是否已过期或被删除\n"
                        "  ● Key 是否有该模型的调用权限\n"
                        "  ● URL 是否与 Key 所属平台一致"
                    )
                elif "404" in err_msg or "Not Found" in err_msg:
                    detail += (
                        "404 表示端点或模型不存在。请检查：\n"
                        "  ● API URL 是否正确\n"
                        "  ● Model 名称是否拼写正确"
                    )
                else:
                    detail += "请检查网络连接、URL 和 Key 是否正确。"
                QMessageBox.critical(dialog, "API 调用失败", detail)
                return

            if not content:
                QMessageBox.warning(
                    dialog, "AI 返回空内容",
                    "API 调用成功但未返回任何翻译文本。"
                )
                return

            # ── "翻译成功" confirm dialog ──
            _show_done(content)

        def _show_done(response_text: str) -> None:
            done = QDialog(dialog)
            done.setWindowTitle("翻译成功")
            done.setFixedSize(360, 100)
            done.setWindowFlags(
                Qt.WindowType.Dialog
                | Qt.WindowType.CustomizeWindowHint
                | Qt.WindowType.WindowTitleHint
            )
            d_layout = QVBoxLayout(done)
            d_layout.setContentsMargins(20, 16, 20, 16)
            d_layout.setSpacing(12)

            msg = QLabel("翻译完成。")
            msg.setWordWrap(True)
            msg.setStyleSheet("font-size: 13px;")
            d_layout.addWidget(msg)

            d_btns = QHBoxLayout()
            d_btns.setSpacing(8)
            d_btns.addStretch()

            btn_view = QPushButton("查看")
            btn_view.setStyleSheet(
                "QPushButton {"
                "  font-weight: bold; color: #58a6ff;"
                "  border: 2px solid #58a6ff;"
                "  padding: 6px 20px; border-radius: 4px;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(88,166,255,0.15);"
                "}"
            )
            btn_view.clicked.connect(
                lambda: (
                    done.accept(),
                    QTimer.singleShot(
                        50,
                        lambda: _show_result(response_text or ""),
                    ),
                )
            )
            d_btns.addWidget(btn_view)

            btn_cancel = QPushButton("取消")
            btn_cancel.clicked.connect(done.reject)
            d_btns.addWidget(btn_cancel)

            d_layout.addLayout(d_btns)
            done.exec()

        def _show_result(text: str) -> None:
            rd = QDialog()
            rd.setWindowTitle("翻译结果 — " + cfg.get("name", "API"))
            rd.resize(700, 500)
            rd.setMinimumSize(500, 350)

            rd_layout = QVBoxLayout(rd)
            rd_layout.setContentsMargins(12, 12, 12, 12)
            rd_layout.setSpacing(8)

            rd_edit = QPlainTextEdit()
            rd_edit.setPlainText(text)
            rd_edit.setFont(QFont("Consolas", 13))
            rd_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            rd_layout.addWidget(rd_edit, stretch=1)

            rd_btns = QHBoxLayout()
            rd_btns.setSpacing(8)

            btn_copy = QPushButton("复制")
            btn_copy.clicked.connect(
                lambda: (
                    QApplication.clipboard().setText(rd_edit.toPlainText()),
                    QMessageBox.information(
                        rd, "已复制", "翻译结果已复制到剪贴板。"
                    ),
                )
            )
            rd_btns.addWidget(btn_copy)

            btn_fill = QPushButton("填入模式匹配")
            btn_fill.setStyleSheet(
                "QPushButton {"
                "  font-weight: bold; color: #58a6ff;"
                "  border: 2px solid #58a6ff;"
                "  padding: 6px 16px; border-radius: 4px;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(88,166,255,0.15);"
                "}"
            )
            btn_fill.clicked.connect(
                lambda: _fill_pattern_match(rd, rd_edit.toPlainText())
            )
            rd_btns.addWidget(btn_fill)

            rd_btns.addStretch()
            btn_close = QPushButton("关闭")
            btn_close.clicked.connect(rd.accept)
            rd_btns.addWidget(btn_close)

            rd_layout.addLayout(rd_btns)
            rd.exec()

        def _fill_pattern_match(
            result_dialog: QDialog, text: str,
        ) -> None:
            result_dialog.accept()  # close result dialog
            dialog.accept()         # close AI assist dialog

            if target_text_edit is not None:
                target_text_edit.setPlainText(text)
                QMessageBox.information(
                    None, "已填入",
                    "翻译结果已填入模式匹配输入框，请检查后点击「匹配」。"
                )
            else:
                QTimer.singleShot(
                    100,
                    lambda: sync_page._on_pattern_match(initial_text=text),
                )

        _translate_worker = _ApiWorker(
            api_key=api_key,
            api_url=api_url,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=180.0,
            temperature=0.3,
            max_tokens=None,  # no limit for translation
            parent=progress,
        )
        _translate_worker.result_ready.connect(_on_translate_done)
        _translate_worker.start()

    # ── Build the model-list page ──
    def _build_model_list() -> None:
        """Rebuild the model-list page from saved configs."""
        old = api_substack.widget(0)
        if old is not None:
            api_substack.removeWidget(old)
            old.deleteLater()

        configs = mw.config.get_api_configs()

        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(4, 8, 4, 0)
        list_layout.setSpacing(8)

        btn_add = QPushButton("加入新模型")
        btn_add.setStyleSheet(
            "QPushButton {"
            "  font-size: 14px; padding: 10px; border: 2px dashed #aaa;"
            "  border-radius: 6px; color: #aaa;"
            "}"
            "QPushButton:hover {"
            "  border-color: #58a6ff; color: #58a6ff;"
            "}"
        )
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(lambda: api_substack.setCurrentIndex(1))
        list_layout.addWidget(btn_add)

        if configs:
            list_layout.addSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(6)

        for i, cfg in enumerate(configs):
            row = QFrame()
            row.setStyleSheet(
                "QFrame {"
                "  border: 1px solid #444; border-radius: 6px;"
                "  padding: 8px; background: rgba(128,128,128,0.05);"
                "}"
                "QFrame:hover { border-color: #666; }"
            )
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 8, 12, 8)
            row_layout.setSpacing(10)

            info_label = QLabel(f"{cfg['name']}\n"
                                f"<span style='font-size:11px;color:#888;'>"
                                f"{cfg['model']}</span>")
            info_label.setTextFormat(Qt.TextFormat.RichText)
            info_label.setStyleSheet("font-size: 14px; border: none;")
            row_layout.addWidget(info_label, stretch=1)

            btn_translate = QPushButton("翻译")
            btn_translate.setFixedSize(70, 36)
            btn_translate.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_translate.setStyleSheet(
                "QPushButton {"
                "  font-size: 13px; border: 2px solid #58a6ff;"
                "  border-radius: 4px; color: #58a6ff; font-weight: bold;"
                "}"
                "QPushButton:hover {"
                "  background-color: rgba(88,166,255,0.15);"
                "}"
            )
            btn_translate.clicked.connect(
                lambda checked, c=cfg: _do_translate(c)
            )
            row_layout.addWidget(btn_translate)

            _small_btn_style = (
                "QPushButton {"
                "  font-size: 12px; border: 1px solid #666;"
                "  border-radius: 4px; color: #aaa;"
                "}"
                "QPushButton:hover {"
                "  border-color: #58a6ff; color: #58a6ff;"
                "}"
            )

            btn_test = QPushButton("测")
            btn_test.setFixedSize(28, 28)
            btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_test.setToolTip("测试连接")
            btn_test.setStyleSheet(_small_btn_style)

            def _make_test_handler(btn: QPushButton, c: dict):
                def _handler() -> None:
                    btn.setEnabled(False)
                    btn.setText("…")
                    def _done(ok: bool, msg: str) -> None:
                        btn.setEnabled(True)
                        if ok:
                            btn.setText("✓")
                            btn.setStyleSheet(
                                _small_btn_style.replace(
                                    "color: #aaa;", "color: #3fb950;"
                                ).replace(
                                    "border-color: #58a6ff;",
                                    "border-color: #3fb950;",
                                )
                            )
                            btn.setToolTip("连接成功")
                        else:
                            btn.setText("✗")
                            btn.setStyleSheet(
                                _small_btn_style.replace(
                                    "color: #aaa;", "color: #f85149;"
                                ).replace(
                                    "border-color: #58a6ff;",
                                    "border-color: #f85149;",
                                )
                            )
                            btn.setToolTip(f"连接失败：{msg}")
                    worker = _ApiWorker(
                        api_key=c["api_key"],
                        api_url=c["url"],
                        model=c["model"],
                        messages=[{"role": "user", "content": "Hi"}],
                        timeout=10.0,
                        parent=btn,
                    )
                    worker.result_ready.connect(_done)
                    worker.start()
                return _handler

            btn_test.clicked.connect(_make_test_handler(btn_test, cfg))
            row_layout.addWidget(btn_test)

            btn_del = QPushButton("✕")
            btn_del.setFixedSize(28, 28)
            btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_del.setStyleSheet(_small_btn_style)
            btn_del.setToolTip("删除此配置")
            btn_del.clicked.connect(
                lambda checked, idx=i: (
                    mw.config.remove_api_config(idx),
                    _build_model_list(),
                )
            )
            row_layout.addWidget(btn_del)

            scroll_layout.addWidget(row)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)

        if configs:
            list_layout.addWidget(scroll, stretch=1)
        else:
            empty_hint = QLabel("暂无已保存的模型，请点击上方按钮添加")
            empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_hint.setStyleSheet("font-size: 13px; color: #888;")
            list_layout.addWidget(empty_hint, stretch=1)

        api_substack.insertWidget(0, list_page)
        api_substack.setCurrentIndex(0)

    # ── Build the config-form page ──
    def _build_config_form(edit_index: int | None = None) -> None:
        old = api_substack.widget(1)
        if old is not None:
            api_substack.removeWidget(old)
            old.deleteLater()

        form_page = QWidget()
        outer = QVBoxLayout(form_page)
        outer.setContentsMargins(4, 8, 4, 0)
        outer.setSpacing(10)

        title_text = "编辑模型配置" if edit_index is not None else "配置新的 API 模型"
        form_title = QLabel(title_text)
        form_title.setStyleSheet("font-size: 15px; font-weight: bold;")
        outer.addWidget(form_title)

        INPUT_H = 40
        LABEL_W = 80

        def _row(label_text: str, input_widget: QLineEdit) -> QHBoxLayout:
            lbl = QLabel(label_text)
            lbl.setFixedSize(LABEL_W, INPUT_H)
            lbl.setStyleSheet("font-size: 13px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(lbl)
            row.addWidget(input_widget, stretch=1)
            return row

        name_input = QLineEdit()
        name_input.setPlaceholderText("例如：我的 DeepSeek")
        name_input.setFont(QFont("Microsoft YaHei", 11))
        name_input.setFixedHeight(INPUT_H)
        outer.addLayout(_row("自定义名称", name_input))

        url_input = QLineEdit()
        url_input.setPlaceholderText("https://api.deepseek.com/v1/chat/completions")
        url_input.setFont(QFont("Consolas", 11))
        url_input.setFixedHeight(INPUT_H)
        outer.addLayout(_row("API URL", url_input))

        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.setPlaceholderText("sk-…")
        key_input.setFont(QFont("Consolas", 11))
        key_input.setFixedHeight(INPUT_H)
        outer.addLayout(_row("API Key", key_input))

        model_input = QLineEdit()
        model_input.setPlaceholderText("deepseek-chat")
        model_input.setFont(QFont("Consolas", 11))
        model_input.setFixedHeight(INPUT_H)
        outer.addLayout(_row("Model", model_input))

        if edit_index is not None:
            configs = mw.config.get_api_configs()
            if 0 <= edit_index < len(configs):
                cfg = configs[edit_index]
                name_input.setText(cfg.get("name", ""))
                url_input.setText(cfg.get("url", ""))
                key_input.setText(cfg.get("api_key", ""))
                model_input.setText(cfg.get("model", ""))

        outer.addStretch()

        feedback = QLabel()
        feedback.setStyleSheet("font-size: 12px; padding: 4px;")
        feedback.setWordWrap(True)
        feedback.hide()
        outer.addWidget(feedback)

        def _show_feedback(text: str, is_error: bool = False) -> None:
            color = "#f85149" if is_error else "#3fb950"
            feedback.setText(f"<span style='color:{color}'>{text}</span>")
            feedback.show()

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()

        btn_cancel = QPushButton("取消")
        btn_cancel.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 8px 16px; border: 1px solid #aaa;"
            "  border-radius: 4px;"
            "}"
            "QPushButton:hover { border-color: #f85149; color: #f85149; }"
        )
        btn_cancel.clicked.connect(lambda: api_substack.setCurrentIndex(0))
        btn_row.addWidget(btn_cancel)

        btn_test_save = QPushButton("测试并保存")
        btn_test_save.setStyleSheet(
            "QPushButton {"
            "  font-size: 13px; padding: 8px 16px; border: 2px solid #58a6ff;"
            "  border-radius: 4px; color: #58a6ff; font-weight: bold;"
            "}"
            "QPushButton:hover { background-color: rgba(88,166,255,0.15); }"
            "QPushButton:disabled {"
            "  border-color: #555; color: #666;"
            "}"
        )
        btn_row.addWidget(btn_test_save)

        outer.addLayout(btn_row)

        def _test_and_save() -> None:
            name = name_input.text().strip()
            u = url_input.text().strip()
            k = key_input.text().strip()
            m = model_input.text().strip()
            if not name or not u or not k or not m:
                _show_feedback("请填写完整的名称、URL、Key 和 Model", True)
                return

            btn_test_save.setEnabled(False)
            btn_test_save.setText("测试中…")
            feedback.hide()

            def _on_test_result(ok: bool, msg: str) -> None:
                _watchdog.stop()
                btn_test_save.setEnabled(True)
                btn_test_save.setText("测试并保存")
                if ok:
                    if edit_index is not None:
                        configs = mw.config.get_api_configs()
                        if 0 <= edit_index < len(configs):
                            configs[edit_index] = {
                                "name": name, "url": u,
                                "api_key": k, "model": m,
                            }
                            raw_cfg = mw.config._load_config()
                            raw_cfg["apiConfigs"] = []
                            mw.config._save_config()
                            for c in configs:
                                mw.config.add_api_config(
                                    c["name"], c["url"],
                                    c["api_key"], c["model"],
                                )
                    else:
                        mw.config.add_api_config(name, u, k, m)
                    _show_feedback("连接成功，配置已加密保存 ✓")
                    QTimer.singleShot(800, _build_model_list)
                else:
                    _show_feedback(f"连接失败，未保存：{msg}", True)

            worker = _ApiWorker(
                api_key=k,
                api_url=u,
                model=m or "default",
                messages=[{"role": "user", "content": "Hi"}],
                timeout=10.0,
                parent=form_page,
            )
            worker.result_ready.connect(_on_test_result)
            worker.start()

            _watchdog = QTimer(form_page)
            _watchdog.setSingleShot(True)
            _watchdog.timeout.connect(
                lambda: _on_test_result(False, "连接超时（15 秒无响应）")
            )
            _watchdog.start(15000)

        btn_test_save.clicked.connect(_test_and_save)

        api_substack.insertWidget(1, form_page)

    # ── Initial state ──
    if mw.config.has_api_configs():
        _build_model_list()
        _build_config_form()  # prepare the form for later use
    else:
        _build_model_list()  # empty list with "add" button
        _build_config_form()
        api_substack.setCurrentIndex(1)  # auto-enter config form

    stack.addWidget(api_page)  # index 2

    dialog.exec()


def _generate_and_copy_prompt(
    sync_page: "SynchronizerPage", parent_dialog: QDialog
) -> None:
    """Generate the AI translation prompt and copy to clipboard."""
    result = build_prompt_text(sync_page)
    if result is None:
        print("没有可用的歌词正文（需要带时间戳的歌词行）")
        return

    prompt, line_count = result
    QApplication.clipboard().setText(prompt)
    print(f"提示词已复制到剪贴板（共 {line_count} 行歌词）")


# ── Pattern Matching ───────────────────────────────────────────────

def perform_pattern_matching(sync_page: "SynchronizerPage", input_text: str,
                             overwrite: bool = False) -> None:
    """Match translations in a background thread, then fill them one by one.

    - Background thread: pure regex matching (never touches Qt / UI)
    - Main thread QTimer poll: check if matching is done
    - Main thread QTimer fill: apply one translation at a time,
      updating the visible _TranslationRow directly → line-by-line effect

    When *overwrite* is True, existing translations are also replaced.
    """
    state = sync_page._mw.lrc_state
    prefs = sync_page._mw.config.get_preferences()
    fixed: Fixed = prefs.get("fixed", 3)

    # Snapshot lyric data for the worker thread (plain Python, no Qt)
    lyric_snapshot: list[tuple[float | None, str, str]] = [
        (ln.time, ln.text, ln.translation) for ln in state.lyric
    ]

    # ── Progress dialog with animated dots ────────────────
    progress = QDialog(sync_page)
    progress.setWindowTitle("模式匹配")
    progress.setFixedSize(300, 110)
    progress.setWindowFlags(
        Qt.WindowType.Dialog
        | Qt.WindowType.CustomizeWindowHint
        | Qt.WindowType.WindowTitleHint
    )
    p_layout = QVBoxLayout(progress)
    p_layout.setContentsMargins(20, 14, 20, 14)
    p_layout.setSpacing(6)

    p_label = QLabel("正在匹配翻译…")
    p_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    p_label.setStyleSheet("font-size: 13px;")
    p_layout.addWidget(p_label)

    dots_label = QLabel()
    dots_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    dots_label.setTextFormat(Qt.TextFormat.RichText)
    p_layout.addWidget(dots_label)

    _dot_frame = [0]

    def _anim_dots() -> None:
        parts: list[str] = []
        for j in range(3):
            if j == _dot_frame[0]:
                parts.append("<span style='font-size:150%;color:#ddd'>●</span>")
            else:
                parts.append("<span style='font-size:100%;color:#666'>●</span>")
        dots_label.setText(" ".join(parts))
        _dot_frame[0] = (_dot_frame[0] + 1) % 3

    dots_timer = QTimer(progress)
    dots_timer.timeout.connect(_anim_dots)
    dots_timer.start(280)
    _anim_dots()

    progress.show()

    # ── Background matching (plain Python thread, no Qt) ──
    _matches: list[tuple[int, str]] = []
    _done = [False]

    def _match_work() -> None:
        input_lines = [
            ln for ln in re.split(r"\r\n|\n|\r", input_text) if ln
        ]
        ts_groups: dict[str, list[str]] = defaultdict(list)
        for line in input_lines:
            m = re.match(
                r"^(\[\s*\d{1,3}:\d{1,2}(?:[:.]\d{1,3})?\s*])(.*)", line
            )
            if m:
                ts_groups[m.group(1)].append(m.group(2))

        for i, (time_val, lyric_text, translation) in enumerate(lyric_snapshot):
            if translation and not overwrite:
                continue
            if time_val is None:
                continue
            our_tag = convert_time_to_tag(time_val, fixed)
            texts = ts_groups.get(our_tag)
            if not texts:
                continue
            result: str | None = None
            if len(texts) == 2:
                our = lyric_text.strip()
                t0 = texts[0].strip()
                t1 = texts[1].strip()
                if t0 == our and t1 != our:
                    result = t1
                elif t1 == our and t0 != our:
                    result = t0
            elif len(texts) == 1:
                t = texts[0].strip()
                if t and t != lyric_text.strip():
                    result = t
            if result:
                _matches.append((i, result))
        _done[0] = True

    threading.Thread(target=_match_work, daemon=True).start()

    # ── Poll timer: wait for matching, then fill one by one ─
    def _poll() -> None:
        if not _done[0]:
            return
        _poll_timer.stop()

        if not _matches:
            dots_timer.stop()
            progress.accept()
            progress.deleteLater()
            print("未找到匹配的翻译文本")
            return

        theme_color = prefs.get("themeColor", "#f58ea8")
        is_dark = is_dark_theme()
        state._push_undo()
        _queue = list(_matches)
        _count = [0]

        def _fill_one() -> None:
            if not _queue:
                _fill_timer.stop()
                dots_timer.stop()
                progress.accept()
                progress.deleteLater()
                state.state_changed.emit()
                print(f"成功匹配 {_count[0]} 条翻译")
                return

            idx, text = _queue.pop(0)
            if 0 <= idx < len(state.lyric):
                state.lyric[idx] = LyricLine(
                    time=state.lyric[idx].time,
                    text=state.lyric[idx].text,
                    translation=text,
                )
                _count[0] += 1
                if idx < len(sync_page._trans_rows):
                    sync_page._trans_rows[idx].update_state(
                        line=state.lyric[idx],
                        theme_color=theme_color,
                        is_dark=is_dark,
                    )
                # Scroll to keep the filled row visible
                sync_page._scroll_to_row(idx)

        _fill_timer = QTimer(sync_page)
        _fill_timer.timeout.connect(_fill_one)
        _fill_timer.start(0)

    _poll_timer = QTimer(sync_page)
    _poll_timer.timeout.connect(_poll)
    _poll_timer.start(30)
