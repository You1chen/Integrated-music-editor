"""Playlist / media library page — scan folders, browse songs, load audio."""

from __future__ import annotations

import os
from datetime import datetime
from typing import TYPE_CHECKING

from PyQt6.QtCore import (
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Background scanner QThread ────────────────────────────────


class _ScanWorker(QThread):
    """Scans directories recursively for .mp3 files in a background thread.

    Follows the ``_ApiWorker`` pattern from ``_ai_assist.py``:
    data in via constructor, results out via pyqtSignal.
    """

    scan_finished = pyqtSignal(list)  # list[dict] — song entries

    def __init__(
        self, root_dirs: list[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._root_dirs = root_dirs

    def run(self) -> None:
        import mutagen

        songs: list[dict] = []

        for root_dir in self._root_dirs:
            if not os.path.isdir(root_dir):
                continue
            for dirpath, _dirnames, filenames in os.walk(root_dir):
                for fname in filenames:
                    if not fname.lower().endswith(".mp3"):
                        continue
                    full_path = os.path.join(dirpath, fname)
                    try:
                        audio = mutagen.File(full_path)
                    except Exception:
                        continue

                    title = ""
                    artist = ""
                    duration = 0.0

                    try:
                        tags = getattr(audio, "tags", None)
                    except Exception:
                        tags = None

                    if tags is not None:
                        try:
                            # ID3 (MP3)
                            from mutagen.id3 import ID3
                            if isinstance(tags, ID3):
                                t = tags.get("TIT2")
                                if t and t.text:
                                    title = str(t.text[0])
                                a = tags.get("TPE1")
                                if a and a.text:
                                    artist = str(a.text[0])
                            else:
                                # VorbisComment (FLAC, Ogg, …)
                                title = _first(tags.get("title"))
                                artist = _first(tags.get("artist"))
                        except Exception:
                            pass

                    if not title:
                        title = os.path.splitext(fname)[0]

                    try:
                        info = getattr(audio, "info", None)
                        if info is not None:
                            duration = round(info.length, 1)
                    except Exception:
                        pass

                    # Check for same-name .lrc
                    stem = os.path.splitext(full_path)[0]
                    has_lrc = os.path.isfile(stem + ".lrc")

                    # Preserve existing liked state
                    liked = False  # will be merged after scan

                    songs.append({
                        "path": full_path,
                        "title": title,
                        "artist": artist,
                        "duration": duration,
                        "has_lrc": has_lrc,
                        "liked": liked,
                    })

        # Sort: by directory then by title
        songs.sort(key=lambda s: (
            os.path.dirname(s["path"]).lower(),
            s["title"].lower(),
        ))

        self.scan_finished.emit(songs)


def _first(lst) -> str:
    """Safely extract first element from a list, or return ''."""
    try:
        return str(lst[0]) if lst else ""
    except (IndexError, TypeError):
        return ""


# ── Song row widget ───────────────────────────────────────────


class _SongRow(QWidget):
    """A single song row: title — artist | duration | ♡ like button."""

    song_clicked = pyqtSignal(str)   # full path
    like_toggled = pyqtSignal(str, bool)  # path, new liked state

    def __init__(
        self,
        song: dict,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._song = song
        self._liked = song.get("liked", False)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 6, 16, 6)
        layout.setSpacing(8)

        # ── Title — Artist ──
        artist_str = f" — {song['artist']}" if song["artist"] else ""
        title_label = QLabel(f"{song['title']}{artist_str}")
        title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        title_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(title_label)

        # ── Has LRC indicator ──
        if song.get("has_lrc"):
            lrc_lbl = QLabel("📝")
            lrc_lbl.setToolTip("存在同名歌词文件")
            lrc_lbl.setStyleSheet("font-size: 12px;")
            layout.addWidget(lrc_lbl)

        # ── Duration ──
        dur = song.get("duration", 0)
        if dur > 0:
            mins = int(dur // 60)
            secs = int(dur % 60)
            dur_label = QLabel(f"{mins}:{secs:02d}")
        else:
            dur_label = QLabel("--:--")
        dur_label.setStyleSheet("font-size: 13px; color: gray;")
        dur_label.setFixedWidth(48)
        dur_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(dur_label)

        # ── Like button ──
        self._like_btn = QPushButton("❤" if self._liked else "♡")
        self._like_btn.setFixedSize(28, 28)
        self._like_btn.setFlat(True)
        self._like_btn.setStyleSheet(
            "QPushButton { border: none; font-size: 16px; padding: 0; }"
        )
        self._like_btn.clicked.connect(self._on_like)
        layout.addWidget(self._like_btn)

    def _on_like(self) -> None:
        self._liked = not self._liked
        self._like_btn.setText("❤" if self._liked else "♡")
        self.like_toggled.emit(self._song["path"], self._liked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.song_clicked.emit(self._song["path"])
        super().mousePressEvent(event)

    def matches(self, text: str) -> bool:
        """Check if this song matches a search filter (case-insensitive)."""
        if not text:
            return True
        lower = text.lower()
        return (
            lower in self._song["path"].lower()
            or lower in self._song["title"].lower()
            or lower in self._song["artist"].lower()
        )


# ── Folder group widget (collapsible) ─────────────────────────


class _FolderGroup(QWidget):
    """A collapsible folder group header + song rows.

    Follows the ``_CollapsibleGroup`` pattern from ``preferences_page.py``.
    """

    def __init__(
        self,
        folder_path: str,
        songs: list[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._folder_path = folder_path
        self._songs = songs
        self._expanded = True
        self._song_rows: list[_SongRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header button ──
        count = len(songs)
        self._header_btn = QPushButton(f"▼ 📁 {folder_path}  ({count}首)")
        self._header_btn.setObjectName("collapsibleHeader")
        self._header_btn.clicked.connect(self._toggle)
        outer.addWidget(self._header_btn)

        # ── Content area ──
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 8)
        self._content_layout.setSpacing(0)

        for song in songs:
            row = _SongRow(song)
            self._song_rows.append(row)
            self._content_layout.addWidget(row)

        outer.addWidget(self._content)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        count = len(self._songs)
        arrow = "▼" if self._expanded else "▶"
        self._header_btn.setText(f"{arrow} 📁 {self._folder_path}  ({count}首)")

    def apply_filter(self, text: str) -> bool:
        """Show/hide rows based on search text.

        Returns True if at least one row is visible.
        """
        any_visible = False
        for row in self._song_rows:
            visible = row.matches(text)
            row.setVisible(visible)
            if visible:
                any_visible = True
        self.setVisible(any_visible)
        return any_visible

    @property
    def song_rows(self) -> list[_SongRow]:
        return self._song_rows


# ── Main playlist page ────────────────────────────────────────


class PlaylistPage(QScrollArea):
    """Media library page — scan folders, browse songs, click to load audio.

    Follows the ``MetaEditorPage`` scrollable pattern:
    QScrollArea → container QWidget → single QVBoxLayout.
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── State ──
        self._all_songs: list[dict] = []
        self._groups: list[_FolderGroup] = []
        self._filter_text: str = ""
        self._search_timer: QTimer | None = None

        # ── Container ──
        container = QWidget()
        self.setWidget(container)
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(24, 16, 24, 16)
        self._layout.setSpacing(12)

        # ── Toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._btn_select = QPushButton("选择文件夹")
        self._btn_select.clicked.connect(self._on_select_folder)
        toolbar.addWidget(self._btn_select)

        self._btn_rescan = QPushButton("重新扫描")
        self._btn_rescan.clicked.connect(self._on_rescan)
        toolbar.addWidget(self._btn_rescan)

        toolbar.addStretch()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索歌曲...")
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setFixedWidth(220)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self._search_input)

        self._layout.addLayout(toolbar)

        # ── Content area (dynamic) ──
        self._content_layout = QVBoxLayout()
        self._content_layout.setSpacing(6)
        self._layout.addLayout(self._content_layout)

        # ── Empty state ──
        self._empty_label = QLabel("请选择一个音乐文件夹开始扫描")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet(
            "font-size: 16px; color: gray; padding: 60px 0;"
        )
        self._content_layout.addWidget(self._empty_label)

        self._layout.addStretch()

        # ── Load cache on startup ──
        self._load_cache()

    # ── Public: signal connection helpers ──

    def connect_signals(self, groups: list[_FolderGroup]) -> None:
        """Connect song_clicked → audio_manager.set_source and like_toggled → persist.

        Called from main.py after page registration.
        """
        for group in groups:
            for row in group.song_rows:
                row.song_clicked.connect(self._on_song_clicked)
                row.like_toggled.connect(self._on_like_toggled)

    # ── Cache ────────────────────────────────────────────────

    def _load_cache(self) -> None:
        cache = self._mw.config.get_playlist_cache()
        songs = cache.get("songs", [])
        if songs:
            self._all_songs = songs
            self._rebuild_ui()
        else:
            self._all_songs = []

    # ── Scanning ─────────────────────────────────────────────

    def _on_select_folder(self) -> None:
        default_dir = self._mw.config.get_default_browse_dir()
        folder = QFileDialog.getExistingDirectory(
            self, "选择音乐文件夹", default_dir
        )
        if not folder:
            return
        self._start_scan([folder])

    def _on_rescan(self) -> None:
        cache = self._mw.config.get_playlist_cache()
        root_dirs = cache.get("root_dirs", [])
        if not root_dirs:
            self._mw.toast_overlay.show_toast(
                "warning", "请先选择文件夹再进行扫描"
            )
            return
        self._start_scan(root_dirs)

    def _start_scan(self, root_dirs: list[str]) -> None:
        self._mw.toast_overlay.show_toast("info", "正在扫描音乐文件...")
        self._btn_select.setEnabled(False)
        self._btn_rescan.setEnabled(False)

        self._worker = _ScanWorker(root_dirs, parent=self)
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.start()

    def _on_scan_finished(self, songs: list[dict]) -> None:
        # ── Merge liked state from existing cache ──
        old_cache = self._mw.config.get_playlist_cache()
        old_songs: dict[str, bool] = {}
        for s in old_cache.get("songs", []):
            if s.get("liked"):
                old_songs[s["path"]] = True

        for s in songs:
            if s["path"] in old_songs:
                s["liked"] = True

        # ── Determine root dirs ──
        root_dirs = list({os.path.dirname(s["path"]) for s in songs})
        # Try to find actual root: common prefix of all paths
        if songs:
            common = os.path.commonpath([s["path"] for s in songs])
            # Walk up to an actual directory that was selected
            # Heuristic: use the common prefix as root
            root_dirs = [common]

        # ── Save cache ──
        cache = {
            "root_dirs": root_dirs,
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "songs": songs,
        }
        self._mw.config.set_playlist_cache(cache)

        self._all_songs = songs
        self._rebuild_ui()

        self._btn_select.setEnabled(True)
        self._btn_rescan.setEnabled(True)
        self._mw.toast_overlay.show_toast(
            "success", f"扫描完成，共 {len(songs)} 首"
        )

    # ── UI rebuild ────────────────────────────────────────────

    def _rebuild_ui(self) -> None:
        # ── Clear old content ──
        self._empty_label.setVisible(False)
        for group in self._groups:
            self._content_layout.removeWidget(group)
            group.deleteLater()
        self._groups.clear()

        if not self._all_songs:
            self._empty_label.setVisible(True)
            return

        # ── Group songs by parent directory ──
        groups: dict[str, list[dict]] = {}
        for song in self._all_songs:
            parent_dir = os.path.dirname(song["path"])
            groups.setdefault(parent_dir, []).append(song)

        # ── Create FolderGroup widgets (sorted by folder name) ──
        for folder in sorted(groups.keys(), key=str.lower):
            group_widget = _FolderGroup(folder, groups[folder])
            self._groups.append(group_widget)
            self._content_layout.addWidget(group_widget)

        # ── Connect signals ──
        for group in self._groups:
            for row in group.song_rows:
                row.song_clicked.connect(self._on_song_clicked)
                row.like_toggled.connect(self._on_like_toggled)

        # ── Apply current filter ──
        if self._filter_text:
            self._apply_filter(self._filter_text)

    # ── Song click → load audio ───────────────────────────────

    def _on_song_clicked(self, path: str) -> None:
        if not os.path.isfile(path):
            self._mw.toast_overlay.show_toast("warning", "文件不存在")
            return
        url = QUrl.fromLocalFile(path).toString()
        self._mw.audio_manager.set_source(url)
        self._mw.config.remember_mp3_path(path)
        name = os.path.basename(path)
        self._mw.toast_overlay.show_toast("success", f"已加载：{name}")

    # ── Like toggle ───────────────────────────────────────────

    def _on_like_toggled(self, path: str, liked: bool) -> None:
        # Update in-memory
        for s in self._all_songs:
            if s["path"] == path:
                s["liked"] = liked
                break
        # Persist
        self._mw.config.toggle_playlist_like(path)

    # ── Search ────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str) -> None:
        # Debounce 300ms
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._apply_filter(text))
        self._search_timer.start(300)

    def _apply_filter(self, text: str) -> None:
        self._filter_text = text
        for group in self._groups:
            group.apply_filter(text)

    # ── showEvent ─────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Refresh if cache may have changed externally (e.g. liked state)
        cache = self._mw.config.get_playlist_cache()
        songs = cache.get("songs", [])
        if songs and not self._all_songs:
            self._all_songs = songs
            self._rebuild_ui()
