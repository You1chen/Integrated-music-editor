"""Playlist panel — the play-queue drawer opened from the bottom bar.

A frameless right-side drawer overlaying the content area, sitting flush on
top of the footer bar.  Each queue entry shows its cover, title and artist,
plus an animated "now playing" indicator on the active row.

Panel layout (top → bottom):
  - Header: 「播放队列」 title · ⇅ edit/sort (edit mode) · 🗑 clear queue
  - Count row: ♪ 共N首歌曲 (green note)
  - Toolbar: 导入歌单 · ◎ 定位 · search box
  - Scrollable queue list (thin grey separators between rows)

Edit mode (⇅) reveals per-row actions: − remove / ＋ insert next / … info.
Covers are extracted on a background thread so a large queue opens snappy.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    QEasingCurve,
    QMutex,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QWaitCondition,
    pyqtSignal,
)
from PyQt6.QtGui import QImage, QPainter, QPixmap, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.audio_manager import extract_embedded_cover_image
from .content_stack import get_theme_colors

if TYPE_CHECKING:
    from .main_window import MainWindow


class _ElideLabel(QLabel):
    """Left-aligned, single-line label that elides on resize and is clickable."""

    clicked = pyqtSignal()

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._elide()

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height())

    def _elide(self) -> None:
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.TextElideMode.ElideRight, max(10, self.width())))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _PlayingIndicator(QWidget):
    """Small three-bar equalizer animation shown beside the active song title."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 14)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)

    def _tick(self) -> None:
        self._phase += 1
        self.update()

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _, _, theme, _ = get_theme_colors()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme))
        bar_w = 2.5
        gap = 2.0
        base_h = 3.0
        phase = self._phase * 0.85
        for i in range(3):
            h = base_h + abs(math.sin(phase + i * 1.9)) * (self.height() - base_h)
            x = i * (bar_w + gap)
            y = (self.height() - h) / 2.0
            p.drawRoundedRect(QRectF(x, y, bar_w, h), 1.25, 1.25)
        p.end()


class _QueueRow(QWidget):
    """One queue entry: cover · title(+playing indicator)/artist · duration.

    Clicking anywhere plays that song.  In edit mode three action buttons
    appear on the right: − remove / ＋ insert next / … song info.
    """

    COVER = 40

    def __init__(
        self, panel: "PlaylistPanel", index: int, song: dict, is_current: bool
    ) -> None:
        super().__init__()
        self._panel = panel
        self._index = index
        self._song = song

        self.setObjectName("queueRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("current", is_current)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 7, 8, 7)
        lay.setSpacing(10)

        # ── Cover ──
        self._cover = QLabel()
        self._cover.setObjectName("queueCover")
        self._cover.setFixedSize(self.COVER, self.COVER)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._cover)

        # ── Text column: title row + artist ──
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addStretch(1)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        self._title = _ElideLabel(song.get("title") or os.path.splitext(
            os.path.basename(song["path"])
        )[0])
        self._title.setToolTip(self._title._full)
        self._title.setStyleSheet("font-size: 13px; background: transparent;")
        self._title.clicked.connect(self._on_play)
        title_row.addWidget(self._title, stretch=1)

        self._indicator = _PlayingIndicator()
        self._indicator.setVisible(is_current)
        if is_current:
            self._indicator.start()
        title_row.addWidget(self._indicator)
        col.addLayout(title_row)

        artist = song.get("artist", "") or ""
        self._artist = _ElideLabel(artist)
        self._artist.setStyleSheet(
            "font-size: 11px; color: gray; background: transparent;"
        )
        self._artist.setCursor(Qt.CursorShape.ArrowCursor)
        col.addWidget(self._artist)
        col.addStretch(1)
        lay.addLayout(col, stretch=1)

        # ── Duration ──
        dur = song.get("duration", 0)
        mins = int(dur // 60) if dur and dur > 0 else 0
        secs = int(dur % 60) if dur and dur > 0 else 0
        self._dur_label = QLabel(f"{mins}:{secs:02d}" if dur and dur > 0 else "--:--")
        self._dur_label.setStyleSheet("font-size: 12px; color: gray; background: transparent;")
        self._dur_label.setFixedWidth(40)
        self._dur_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        lay.addWidget(self._dur_label)

        # ── Edit-mode actions (hidden until edit mode is on) ──
        self._edit_btns: list[QPushButton] = []
        for text, tip, handler in (
            ("−", "从播放列表移除", self._on_remove),
            ("＋", "添加到下一首", self._on_insert_next),
            ("…", "查看/编辑歌曲信息", self._on_info),
        ):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.setFixedSize(26, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton { border: none; padding: 0;"
                " font-size: 14px; background: transparent; color: gray; }"
                "QPushButton:hover { color: #f58ea8; }"
            )
            btn.clicked.connect(handler)
            btn.setVisible(panel.edit_mode)
            self._edit_btns.append(btn)
            lay.addWidget(btn)

        # Click anywhere on the row (except the buttons) plays the song.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    # ── Public helpers ────────────────────────────────────────

    def set_current(self, is_current: bool) -> None:
        """Update the now-playing highlight + indicator."""
        if self.property("current") == is_current:
            return
        self.setProperty("current", is_current)
        # Repolish so the QSS current/hover selector applies.
        self.style().unpolish(self)
        self.style().polish(self)
        _bg, _fg, theme, _dark = get_theme_colors()
        self._title.setStyleSheet(
            f"font-size: 13px; background: transparent;"
            f"{f'color: {theme}; font-weight: 600;' if is_current else ''}"
        )
        self._indicator.setVisible(is_current)
        if is_current:
            self._indicator.start()
        else:
            self._indicator.stop()

    def set_edit_mode(self, on: bool) -> None:
        for btn in self._edit_btns:
            btn.setVisible(on)

    def set_cover(self, img: QImage) -> None:
        """Show the embedded cover, square-cropped to the thumbnail box."""
        pix = QPixmap.fromImage(img).scaled(
            self.COVER, self.COVER,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (pix.width() - self.COVER) // 2
        y = (pix.height() - self.COVER) // 2
        pix = pix.copy(x, y, self.COVER, self.COVER)
        self._cover.setPixmap(pix)

    # ── Events ────────────────────────────────────────────────

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_play()
        super().mousePressEvent(event)

    # ── Actions ───────────────────────────────────────────────

    def _on_play(self) -> None:
        self._panel.play_index(self._index)

    def _on_remove(self) -> None:
        self._panel.remove_index(self._index)

    def _on_insert_next(self) -> None:
        self._panel.insert_next(self._index)

    def _on_info(self) -> None:
        self._panel.show_info(self._index)


class _CoverLoader(QThread):
    """Background cover extractor.

    Paths are fed in from the panel and processed one at a time; the result
    (a pure QImage — safe to pass across threads) is emitted on completion.
    """

    cover_loaded = pyqtSignal(str, QImage)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._queue: list[str] = []
        self._mutex = QMutex()
        self._wake = QWaitCondition()
        self._stopping = False

    def request(self, path: str) -> None:
        self._mutex.lock()
        if path not in self._queue:
            self._queue.append(path)
        self._mutex.unlock()
        self._wake.wakeOne()

    def stop_and_wait(self) -> None:
        self._mutex.lock()
        self._stopping = True
        self._mutex.unlock()
        self._wake.wakeAll()
        self.wait(2000)

    def run(self) -> None:
        while True:
            self._mutex.lock()
            if self._stopping:
                self._mutex.unlock()
                return
            if self._queue:
                path = self._queue.pop(0)
                self._mutex.unlock()
                img = extract_embedded_cover_image(path)
                if img is not None:
                    self.cover_loaded.emit(path, img)
            else:
                self._wake.wait(self._mutex, 200)
                self._mutex.unlock()


class PlaylistPanel(QWidget):
    """Frameless right-side drawer listing the play queue."""

    PANEL_W = 360

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window
        self.edit_mode = False
        self._rows: list[_QueueRow] = []
        self._cover_cache: dict[str, QImage] = {}
        self._cover_pending: set[str] = set()
        self._closing = False
        self._anim: QPropertyAnimation | None = None

        self.setObjectName("playlistPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._build_ui()

        # Background cover extraction (kept alive for the panel's lifetime).
        self._cover_loader = _CoverLoader(self)
        self._cover_loader.cover_loaded.connect(self._on_cover_loaded)
        self._cover_loader.start()

        # Live refresh from the queue.
        self._mw.playlist.queue_changed.connect(self._rebuild)
        self._mw.playlist.current_changed.connect(self._refresh_current)
        self._rebuild()

    # ── UI construction ───────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header: title + edit/sort + clear ──
        header = QWidget()
        header.setObjectName("queueHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 12, 12, 8)
        hl.setSpacing(6)

        title = QLabel("播放队列")
        title.setStyleSheet("font-size: 16px; font-weight: 700; background: transparent;")
        hl.addWidget(title)
        hl.addStretch()

        self._edit_btn = QPushButton("⇅")
        self._edit_btn.setObjectName("queueHeaderBtn")
        self._edit_btn.setToolTip("编辑模式：移除 / 添加到下一首 / 查看信息")
        self._edit_btn.setCheckable(True)
        self._edit_btn.setFixedSize(30, 30)
        self._edit_btn.clicked.connect(self._toggle_edit_mode)
        hl.addWidget(self._edit_btn)

        self._clear_btn = QPushButton("🗑")
        self._clear_btn.setObjectName("queueHeaderBtn")
        self._clear_btn.setToolTip("清空播放列表")
        self._clear_btn.setFixedSize(30, 30)
        self._clear_btn.clicked.connect(self._on_clear)
        hl.addWidget(self._clear_btn)

        root.addWidget(header)

        # ── Count row: ♪ 共N首歌曲 ──
        count_row = QWidget()
        cl = QHBoxLayout(count_row)
        cl.setContentsMargins(16, 8, 16, 2)
        self._count_label = QLabel()
        self._count_label.setStyleSheet("background: transparent;")
        cl.addWidget(self._count_label)
        cl.addStretch()
        root.addWidget(count_row)

        # ── Toolbar: import + locate + search ──
        toolbar = QWidget()
        tl = QHBoxLayout(toolbar)
        tl.setContentsMargins(16, 4, 16, 8)
        tl.setSpacing(6)

        self._import_btn = QPushButton("导入歌单")
        self._import_btn.setToolTip("导入整个歌单或某个文件夹")
        self._import_btn.clicked.connect(self._on_import_menu)
        tl.addWidget(self._import_btn)

        self._locate_btn = QPushButton("◎ 定位")
        self._locate_btn.setToolTip("定位当前播放歌曲")
        self._locate_btn.setFixedHeight(28)
        self._locate_btn.clicked.connect(self._on_locate)
        tl.addWidget(self._locate_btn)

        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search_changed)
        tl.addWidget(self._search, stretch=1)
        root.addWidget(toolbar)

        # ── Empty hint ──
        self._empty = QLabel("播放列表为空\n在「歌单」页扫描文件夹后，右键歌曲即可导入")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("font-size: 13px; color: gray; padding: 40px;")
        root.addWidget(self._empty)

        # ── Scrollable queue ──
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(6, 4, 6, 4)
        self._rows_layout.setSpacing(0)
        self._rows_layout.addStretch()
        self._scroll.setWidget(self._rows_container)
        root.addWidget(self._scroll, stretch=1)

    # ── Drawer open/close + positioning ───────────────────────

    def open_drawer(self) -> None:
        """Slide in from the right edge, flush above the footer bar."""
        self._closing = False
        target = self._mw._playlist_panel_target_geometry()
        start = QRect(self._mw.width(), target.y(), target.width(), target.height())
        self.setGeometry(start)
        self.show()
        self.raise_()
        self.setFocus()
        self._animate_to(start, target, 200, QEasingCurve.Type.OutCubic)

    def close_drawer(self) -> None:
        if self._closing or not self.isVisible():
            return
        self._closing = True
        target = self.geometry()
        end = QRect(self._mw.width(), target.y(), target.width(), target.height())
        self._animate_to(target, end, 160, QEasingCurve.Type.InCubic, on_finish=self._after_close)

    def _after_close(self) -> None:
        self.hide()
        self._closing = False
        # The ☰ button reflects the panel's visible state — re-sync it now
        # that the drawer is finally hidden.
        self._mw._sync_playlist_btn()

    def _stop_anim(self) -> None:
        """Cancel any slide animation (e.g. window resize)."""
        if self._anim is not None and self._anim.state() == QPropertyAnimation.State.Running:
            self._anim.stop()
        self._anim = None

    def _animate_to(self, start: QRect, end: QRect, ms: int, curve, on_finish=None) -> None:
        self._stop_anim()
        anim = QPropertyAnimation(self, b"geometry", self)
        anim.setDuration(ms)
        anim.setEasingCurve(curve)
        anim.setStartValue(start)
        anim.setEndValue(end)
        if on_finish is not None:
            anim.finished.connect(on_finish)
        self._anim = anim
        anim.start()

    def shutdown(self) -> None:
        """Stop the background cover thread (called on app close)."""
        self._stop_anim()
        self._cover_loader.stop_and_wait()

    # ── Rebuild / refresh ─────────────────────────────────────

    def _rebuild(self) -> None:
        for row in self._rows:
            self._rows_layout.removeWidget(row)
            row.deleteLater()
        self._rows.clear()

        queue = self._mw.playlist.queue
        current = self._mw.playlist.current_index
        text = self._search.text().strip().lower()
        self._empty.setVisible(not queue)
        has_queue = bool(queue)
        self._edit_btn.setEnabled(has_queue)
        self._clear_btn.setEnabled(has_queue)
        self._import_btn.setEnabled(True)
        self._locate_btn.setEnabled(has_queue)

        # Drop cached covers for paths no longer in the queue.
        paths = {s.get("path") for s in queue}
        for p in list(self._cover_cache):
            if p not in paths:
                del self._cover_cache[p]

        for i, song in enumerate(queue):
            if text and text not in self._haystack(song):
                continue
            row = _QueueRow(self, i, song, is_current=(i == current))
            self._rows.append(row)
            self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
            row.set_edit_mode(self.edit_mode)
            self._request_cover(song.get("path"), row)

        self._update_count()

    def _refresh_current(self, *_args) -> None:
        """Only re-tint rows when the playing index changes (cheaper than rebuild)."""
        current = self._mw.playlist.current_index
        for row in self._rows:
            row.set_current(row._index == current)

    def _update_count(self) -> None:
        n = len(self._mw.playlist.queue)
        _bg, fg, _theme, _dark = get_theme_colors()
        self._count_label.setText(
            f'<span style="color:#19cf86;font-size:15px;">♪</span>'
            f'<span style="color:{fg};font-size:13px;"> 共{n}首歌曲</span>'
        )

    @staticmethod
    def _haystack(song: dict) -> str:
        return " ".join(
            str(song.get(k, "")) for k in ("title", "artist", "path")
        ).lower()

    # ── Cover loading ─────────────────────────────────────────

    def _request_cover(self, path: str, row: _QueueRow) -> None:
        img = self._cover_cache.get(path)
        if img is not None:
            row.set_cover(img)
            return
        if path and path not in self._cover_pending:
            self._cover_pending.add(path)
            self._cover_loader.request(path)

    def _on_cover_loaded(self, path: str, img: QImage) -> None:
        self._cover_pending.discard(path)
        self._cover_cache[path] = img
        for row in self._rows:
            if row._song.get("path") == path:
                row.set_cover(img)

    # ── Header handlers ───────────────────────────────────────

    def _toggle_edit_mode(self) -> None:
        self.edit_mode = not self.edit_mode
        self._edit_btn.setChecked(self.edit_mode)
        for row in self._rows:
            row.set_edit_mode(self.edit_mode)

    def _on_clear(self) -> None:
        n = len(self._mw.playlist.queue)
        if n == 0:
            return
        ret = QMessageBox.question(
            self,
            "清空播放列表",
            f"确定要清空播放列表吗？（共 {n} 首）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ret == QMessageBox.StandardButton.Yes:
            self._mw.playlist.clear()

    # ── Search ────────────────────────────────────────────────

    def _on_search_changed(self, _text: str) -> None:
        self._rebuild()

    # ── Locate now-playing entry ──────────────────────────────

    def _locate_current(self, scroll: bool = True) -> bool:
        """Scroll to the now-playing queue entry.  Returns True when found.

        If the entry is hidden by the search box, the search is cleared
        first so the song is reachable again.
        """
        current = self._mw.playlist.current_index
        row = next((r for r in self._rows if r._index == current), None)
        if row is None and self._search.text().strip():
            # filtered out by search — clear it so the entry is reachable
            self._search.clear()
            QApplication.processEvents()  # let the rebuilt rows lay out
            row = next((r for r in self._rows if r._index == current), None)
        if row is not None and scroll:
            self._rows_container.adjustSize()
            sb = self._scroll.verticalScrollBar()
            sb.setValue(max(0, min(row.y() - 16, sb.maximum())))
        return row is not None

    def _on_locate(self) -> None:
        if not self._locate_current():
            print("播放列表中没有正在播放的歌曲")

    # ── Import menu ───────────────────────────────────────────

    def _on_import_menu(self) -> None:
        """Open a menu: import the whole library or any folder (any depth)."""
        menu = QMenu(self)

        act_all = menu.addAction("整个歌单")
        act_all.triggered.connect(self._mw.import_playlist_from_library)

        from ..core.constants import PageRoute
        library = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
        tree = library.get_folder_tree() if library is not None else None
        if tree is not None and tree.children:
            menu.addSeparator()
            for child in sorted(tree.children, key=lambda n: n.name.lower()):
                self._add_flat_folder(menu, child, 0)

        menu.exec(self._import_btn.mapToGlobal(self._import_btn.rect().bottomLeft()))

    def _add_flat_folder(self, menu: QMenu, node, depth: int) -> None:
        """Add every folder as a directly-clickable item (indented by depth)."""
        count = node.total_song_count()
        label = "    " * depth + f"📁 {node.name} ({count}首)"
        act = menu.addAction(label)
        act.triggered.connect(
            lambda checked=False, n=node: self._import_folder(n)
        )
        for child in sorted(node.children, key=lambda n: n.name.lower()):
            self._add_flat_folder(menu, child, depth + 1)

    def _import_folder(self, node) -> None:
        from ..core.constants import PageRoute
        library = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
        if library is None:
            return
        songs = library.songs_under(node.full_path)
        self._mw.import_to_playlist(songs)
        print(f"已导入 {len(songs)} 首到播放列表")

    # ── Queue actions ─────────────────────────────────────────

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

    # ── Keys ──────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._mw.close_playlist_panel()
            return
        super().keyPressEvent(event)
