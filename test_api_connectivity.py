"""Standalone API connectivity test tool.

Simple PyQt6 window: enter API Key → click test → see result.
 - Uses the openai library (same as test.py) → DeepSeek OpenAI-compatible API.
 - Model defaults to deepseek-v4-flash.
 - API Key is encrypted via Windows DPAPI (same as main app) and stored
   temporarily for the test session only.
 - On close, the encrypted key file + any test output .txt are deleted.
"""

from __future__ import annotations

import base64
import ctypes
import os
import sys
import threading
from ctypes import wintypes

from openai import OpenAI
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

# ── Windows DPAPI (same crypto_utils.py logic, inlined) ───────────────

_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32


def _protect(plaintext: bytes) -> bytes:
    data_in = _DATA_BLOB()
    data_in.cbData = len(plaintext)
    data_in.pbData = ctypes.cast(
        ctypes.create_string_buffer(plaintext, len(plaintext)),
        ctypes.POINTER(ctypes.c_char),
    )
    data_out = _DATA_BLOB()
    ok = _crypt32.CryptProtectData(
        ctypes.byref(data_in),
        None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptProtectData failed")
    try:
        result = ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _kernel32.LocalFree(data_out.pbData)
    return result


def _unprotect(ciphertext: bytes) -> bytes:
    data_in = _DATA_BLOB()
    data_in.cbData = len(ciphertext)
    data_in.pbData = ctypes.cast(
        ctypes.create_string_buffer(ciphertext, len(ciphertext)),
        ctypes.POINTER(ctypes.c_char),
    )
    data_out = _DATA_BLOB()
    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(data_in),
        None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(data_out),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed")
    try:
        result = ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        _kernel32.LocalFree(data_out.pbData)
    return result


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return base64.b64encode(_protect(plaintext.encode("utf-8"))).decode("ascii")


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    return _unprotect(base64.b64decode(ciphertext.encode("ascii"))).decode("utf-8")


# ── File paths ────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "_test_api_key.enc")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "_test_api_output.txt")


def cleanup_files() -> None:
    """Delete the encrypted key file and test output, if they exist."""
    for p in (KEY_FILE, OUTPUT_FILE):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


# ── Constants ─────────────────────────────────────────────────────────

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"


# ── Main Window ───────────────────────────────────────────────────────

class TestWindow(QDialog):
    def __init__(self) -> None:
        super().__init__()
        self._test_done: list[bool] = [False]
        self._watchdog: QTimer | None = None

        self.setWindowTitle("API 连通性测试 — DeepSeek")
        self.setFixedSize(500, 260)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Title
        title = QLabel("DeepSeek API 连通性测试")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Info line
        info = QLabel(
            f"<span style='color:#888;'>URL:</span> {DEEPSEEK_BASE_URL}<br>"
            f"<span style='color:#888;'>Model:</span> {DEEPSEEK_MODEL}"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet("font-size: 12px; padding: 4px;")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(info)

        layout.addSpacing(4)

        # API Key input
        key_layout = QHBoxLayout()
        key_layout.setSpacing(8)
        key_label = QLabel("API Key:")
        key_label.setFixedWidth(65)
        key_label.setStyleSheet("font-size: 13px;")
        key_layout.addWidget(key_label)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("sk-…")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setFont(QFont("Consolas", 11))
        self.key_input.setFixedHeight(36)
        # Load previously saved key (if any)
        if os.path.exists(KEY_FILE):
            try:
                with open(KEY_FILE, "r", encoding="utf-8") as f:
                    saved = f.read().strip()
                if saved:
                    self.key_input.setText(decrypt(saved))
            except Exception:
                pass
        key_layout.addWidget(self.key_input, stretch=1)
        layout.addLayout(key_layout)

        layout.addSpacing(4)

        # Test button
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setFixedHeight(42)
        self.btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_test.setStyleSheet(
            "QPushButton {"
            "  font-size: 15px; font-weight: bold;"
            "  color: #58a6ff; border: 2px solid #58a6ff;"
            "  border-radius: 6px; padding: 8px;"
            "}"
            "QPushButton:hover { background-color: rgba(88,166,255,0.15); }"
            "QPushButton:disabled {"
            "  border-color: #555; color: #666;"
            "}"
        )
        self.btn_test.clicked.connect(self._on_test)
        layout.addWidget(self.btn_test)

        # Status label
        self.status = QLabel("")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-size: 13px; padding: 4px;")
        layout.addWidget(self.status)

        layout.addStretch()

    # ── Save key ──────────────────────────────────────────────────────

    def _save_key(self) -> None:
        key = self.key_input.text().strip()
        if key:
            try:
                with open(KEY_FILE, "w", encoding="utf-8") as f:
                    f.write(encrypt(key))
            except Exception:
                pass
        else:
            try:
                if os.path.exists(KEY_FILE):
                    os.remove(KEY_FILE)
            except OSError:
                pass

    # ── Test logic (uses openai library — same as test.py) ────────────

    def _on_test(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            self._set_status("请输入 API Key", error=True)
            return

        self._save_key()

        self.btn_test.setEnabled(False)
        self.btn_test.setText("测试中…")
        self.status.setText("")

        self._test_done[0] = False

        def _work() -> None:
            try:
                client = OpenAI(
                    api_key=key,
                    base_url=DEEPSEEK_BASE_URL,
                    timeout=15.0,
                )
                response = client.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": "Hi"}],
                    max_tokens=5,
                )
                if not self._test_done[0]:
                    self._test_done[0] = True
                    content = response.choices[0].message.content
                    QTimer.singleShot(
                        0,
                        lambda: self._on_result(
                            True, f"连接成功 ✓\n响应: {content}"
                        ),
                    )
            except Exception as e:
                if not self._test_done[0]:
                    self._test_done[0] = True
                    QTimer.singleShot(0, lambda: self._on_result(False, str(e)))

        threading.Thread(target=_work, daemon=True).start()

        # Watchdog: 20 second timeout
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(
            lambda: self._on_result(False, "连接超时（20 秒无响应）")
            if not self._test_done[0] else None
        )
        self._watchdog.start(20000)

    def _on_result(self, ok: bool, msg: str) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
        self.btn_test.setEnabled(True)
        self.btn_test.setText("测试连接")

        if ok:
            self._set_status(msg, error=False)
            try:
                with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                    f.write(msg)
            except Exception:
                pass
        else:
            detail = self._format_error(msg)
            self._set_status(detail, error=True)

    def _format_error(self, err_msg: str) -> str:
        """Produce a concise error explanation."""
        if "401" in err_msg or "Unauthorized" in err_msg or "Invalid" in err_msg:
            return (
                "❌ 401 认证失败 — API Key 无效\n"
                "请检查 Key 是否正确、未过期、未删除"
            )
        elif "403" in err_msg or "Forbidden" in err_msg:
            return (
                "❌ 403 禁止访问 — Key 无权限\n"
                f"请确认该 Key 是否有 {DEEPSEEK_MODEL} 的调用权限"
            )
        elif "404" in err_msg or "Not Found" in err_msg:
            return "❌ 404 — 端点或模型不存在"
        elif "429" in err_msg:
            return "❌ 429 — 请求过于频繁，请稍后再试"
        elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            return f"❌ 连接超时 — 网络不通或 API 无响应\n{err_msg}"
        else:
            return f"❌ 连接失败:\n{err_msg}"

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#f85149" if error else "#3fb950"
        self.status.setText(f"<span style='color:{color}'>{text}</span>")
        self.status.setTextFormat(Qt.TextFormat.RichText)

    # ── Cleanup on close ──────────────────────────────────────────────

    def closeEvent(self, event) -> None:
        cleanup_files()
        super().closeEvent(event)


# ── Entry point ────────────────────────────────────────────────────────

def main() -> None:
    app = QApplication(sys.argv)
    cleanup_files()
    window = TestWindow()
    window.show()
    app.exec()
    cleanup_files()


if __name__ == "__main__":
    main()
