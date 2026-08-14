"""Playlist panel — the play queue UI opened from the bottom bar.

Modeless dialog listing the play queue.  Each entry offers:
  - click the title → play that song
  - "-" remove from the queue
  - "+" insert a copy as the next song (source stays put)
  - "…" view / edit that song's metadata and literal lyrics

A search box filters entries; "导入歌单" imports the whole media library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .content_stack import get_theme_colors

if TYPE_CHECKING:
    from .main_window import MainWindow


class _QueueRow(QWidget):
    """One queue entry with play / remove / insert-next / info actions."""

    def __init__(self, panel: "PlaylistPanel", index: int, song: dict, is_current: bool) -> None:
        super().__init__()
        self._panel = panel
        self._index = index
        self._song = song

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)

        prefix = "▶ " if is_current else ""
        title = f"{prefix}{song['title']}"
        if song.get("artist"):
            title += f" — {song['artist']}"

        self._title_btn = QPushButton(title)
        self._title_btn.setObjectName("queueTitle")
        self._title_btn.setFlat(True)
        self._title_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_btn.clicked.connect(self._on_play)
        layout.addWidget(self._title_btn, stretch=1)

        dur = song.get("duration", 0)
        if dur and dur > 0:
            mins = int(dur // 60)
            secs = int(dur % 60)
            self._dur_label = QLabel(f"{mins}:{secs:02d}")
        else:
            self._dur_label = QLabel("--:--")
        self._dur_label.setStyleSheet("font-size: 12px; color: gray;")
        layout.addWidget(self._dur_label)

        for text, tip, handler in (
            ("−", "从播放列表移除", self._on_remove),
            ("＋", "添加到下一首", self._on_insert_next),
            ("…", "查看/编辑歌曲信息", self._on_info),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFixedSize(30, 28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(handler)
            layout.addWidget(btn)

        if is_current:
            _bg, _fg, theme, _dark = get_theme_colors()
            self.setStyleSheet(
                f"_QueueRow {{ background: {theme}22; border-radius: 6px; }}"
            )

    def _on_play(self) -> None:
        self._panel.play_index(self._index)

    def _on_remove(self) -> None:
        self._panel.remove_index(self._index)

    def _on_insert_next(self) -> None:
        self._panel.insert_next(self._index)

    def _on_info(self) -> None:
        self._panel.show_info(self._index)


class PlaylistPanel(QDialog):
    """Non-modal queue panel."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window

        self.setWindowTitle("播放列表")
        self.resize(400, 520)
        self.setMinimumSize(340, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Toolbar: search + import ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        self._import_btn = QPushButton("导入歌单")
        self._import_btn.setToolTip("把整个歌单按自然顺序导入播放列表")
        self._import_btn.clicked.connect(self._on_import_all)
        toolbar.addWidget(self._import_btn)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search, stretch=1)
        layout.addLayout(toolbar)

        # ── Empty hint ──
        self._empty = QLabel("播放列表为空\n点击「导入歌单」开始")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("font-size: 13px; color: gray; padding: 40px;")
        layout.addWidget(self._empty)

        # ── Scrollable queue ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_container)
        layout.addWidget(self._scroll, stretch=1)

        self._rows: list[_QueueRow] = []

        # ── Live refresh from the queue ──
        self._mw.playlist.queue_changed.connect(self._rebuild)
        self._mw.playlist.current_changed.connect(self._rebuild)
        self._rebuild()

    # ── Rebuild ──────────────────────────────────────────────

    def _rebuild(self) -> None:
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        queue = self._mw.playlist.queue
        current = self._mw.playlist.current_index
        text = self._search.text().strip().lower()

        self._empty.setVisible(not queue)

        for i, song in enumerate(queue):
            if text and text not in self._haystack(song):
                continue
            row = _QueueRow(self, i, song, is_current=(i == current))
            self._rows.append(row)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)

    @staticmethod
    def _haystack(song: dict) -> str:
        return " ".join(
            str(song.get(k, "")) for k in ("title", "artist", "path")
        ).lower()

    # ── Search ───────────────────────────────────────────────

    def _on_search_changed(self, _text: str) -> None:
        self._rebuild()

    # ── Actions ──────────────────────────────────────────────

    def _on_import_all(self) -> None:
        self._mw.import_playlist_from_library()

    def play_index(self, index: int) -> None:
        self._mw.playlist.play_index(index)

    def remove_index(self, index: int) -> None:
        self._mw.playlist.remove_at(index)

    def insert_next(self, index: int) -> None:
        self._mw.playlist.insert_next(index)

    def show_info(self, index: int) -> None:
        queue = self._mw.playlist.queue
        if 0 <= index < len(queue):
            from .song_info_dialog import SongInfoDialog
            SongInfoDialog(self._mw, queue[index], self).exec()
