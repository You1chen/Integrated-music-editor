"""Playlist / media library page — scan folders, tree-browse songs, load audio."""

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

from .content_stack import _CURRENT_THEME_COLOR, _CURRENT_DARK

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Background scanner QThread ────────────────────────────────


class _ScanWorker(QThread):
    """Scans directories recursively for .mp3 files in a background thread."""

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
                            from mutagen.id3 import ID3
                            if isinstance(tags, ID3):
                                t = tags.get("TIT2")
                                if t and t.text:
                                    title = str(t.text[0])
                                a = tags.get("TPE1")
                                if a and a.text:
                                    artist = str(a.text[0])
                            else:
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

                    stem = os.path.splitext(full_path)[0]
                    has_lrc = os.path.isfile(stem + ".lrc")

                    songs.append({
                        "path": full_path,
                        "title": title,
                        "artist": artist,
                        "duration": duration,
                        "has_lrc": has_lrc,
                        "liked": False,
                    })

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


def _migrate_root_dir(cache: dict) -> str:
    """Read root_dir from cache, falling back to old root_dirs format.

    v1 cache used ``root_dirs`` (list); v2 uses ``root_dir`` (str).
    """
    root_dir = cache.get("root_dir", "")
    if root_dir:
        return root_dir
    old_dirs: list[str] = cache.get("root_dirs", [])
    if old_dirs:
        return old_dirs[0]
    return ""


# ── Tree data node ────────────────────────────────────────────


class _TreeNode:
    """A node in the directory tree."""
    __slots__ = ("name", "full_path", "children", "songs")

    def __init__(self, name: str, full_path: str) -> None:
        self.name = name
        self.full_path = full_path
        self.children: list[_TreeNode] = []
        self.songs: list[dict] = []

    def get_child(self, name: str) -> "_TreeNode | None":
        for c in self.children:
            if c.name == name:
                return c
        return None

    def total_song_count(self) -> int:
        """Count all songs in this subtree."""
        n = len(self.songs)
        for c in self.children:
            n += c.total_song_count()
        return n


# ── Song row widget ───────────────────────────────────────────


class _SongRow(QWidget):
    """A single song row with hover highlight."""

    song_clicked = pyqtSignal(str)
    like_toggled = pyqtSignal(str, bool)

    def __init__(
        self,
        song: dict,
        indent: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._song = song
        self._liked = song.get("liked", False)
        self._hover = False

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24 + indent * 20, 5, 16, 5)
        layout.setSpacing(8)

        # ── Title — Artist ──
        self._title_label = QLabel()
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self._title_label)

        # ── Has LRC indicator ──
        self._lrc_label = QLabel()
        self._lrc_label.setToolTip("存在同名歌词文件")
        layout.addWidget(self._lrc_label)

        # ── Duration ──
        self._dur_label = QLabel()
        self._dur_label.setStyleSheet("font-size: 13px; color: gray;")
        self._dur_label.setFixedWidth(48)
        self._dur_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._dur_label)

        self._refresh_labels()

        # ── Like button ──
        self._like_btn = QPushButton()
        self._like_btn.setFixedSize(30, 30)
        self._like_btn.setFlat(True)
        self._like_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._like_btn.clicked.connect(self._on_like)
        self._refresh_like_btn()
        layout.addWidget(self._like_btn)

        self._apply_bg()

    def _on_like(self) -> None:
        self._liked = not self._liked
        self._refresh_like_btn()
        self.like_toggled.emit(self._song["path"], self._liked)

    def _refresh_like_btn(self) -> None:
        """Update like button appearance based on liked state."""
        if self._liked:
            self._like_btn.setText("❤")
            self._like_btn.setToolTip("取消喜欢")
            self._like_btn.setStyleSheet(
                "QPushButton { border: none; font-size: 17px; padding: 0;"
                " color: #e74c3c; }"
                "QPushButton:hover { font-size: 20px; }"
            )
        else:
            self._like_btn.setText("♡")
            self._like_btn.setToolTip("喜欢")
            self._like_btn.setStyleSheet(
                "QPushButton { border: none; font-size: 17px; padding: 0;"
                " color: #999999; }"
                "QPushButton:hover { font-size: 20px; color: #e74c3c; }"
            )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.song_clicked.emit(self._song["path"])
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self._apply_bg()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._apply_bg()
        super().leaveEvent(event)

    def _apply_bg(self) -> None:
        if self._hover:
            theme = _CURRENT_THEME_COLOR
            alpha = "33" if _CURRENT_DARK else "22"
            self.setStyleSheet(
                f"background-color: {theme}{alpha}; border-radius: 6px;"
            )
        else:
            self.setStyleSheet("background: transparent;")

    def _refresh_labels(self) -> None:
        """Update all labels from the current song data."""
        s = self._song
        artist_str = f" — {s['artist']}" if s["artist"] else ""
        self._title_label.setText(f"{s['title']}{artist_str}")

        self._lrc_label.setText("📝" if s.get("has_lrc") else "")
        self._lrc_label.setVisible(bool(s.get("has_lrc")))

        dur = s.get("duration", 0)
        if dur > 0:
            mins = int(dur // 60)
            secs = int(dur % 60)
            self._dur_label.setText(f"{mins}:{secs:02d}")
        else:
            self._dur_label.setText("--:--")

    def update_song(self, song: dict) -> None:
        """Replace backing data and refresh display in-place."""
        self._song = song
        self._liked = song.get("liked", False)
        self._refresh_labels()
        self._refresh_like_btn()

    def matches(self, text: str, liked_only: bool = False) -> bool:
        """Check if this song matches a search filter (case-insensitive)."""
        if liked_only and not self._liked:
            return False
        if not text:
            return True
        lower = text.lower()
        return (
            lower in self._song["path"].lower()
            or lower in self._song["title"].lower()
            or lower in self._song["artist"].lower()
        )


# ── Tree branch widget (collapsible directory node) ──────────


class _TreeBranch(QWidget):
    """A collapsible directory node — recursive: can contain
    child _TreeBranch widgets and _SongRow widgets.
    """

    def __init__(
        self,
        node: _TreeNode,
        depth: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._node = node
        self._depth = depth
        self._expanded = False  # start collapsed
        self._branches: list[_TreeBranch] = []
        self._song_rows: list[_SongRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header button ──
        total = node.total_song_count()
        indent_px = depth * 20
        arrow = "▶"
        self._header_btn = QPushButton(
            f"{arrow} 📁 {node.name}  ({total}首)"
        )
        self._header_btn.setObjectName("collapsibleHeader")
        self._header_btn.setStyleSheet(
            f"#collapsibleHeader {{ padding-left: {12 + indent_px}px; text-align: left; }}"
        )
        self._header_btn.clicked.connect(self._toggle)
        outer.addWidget(self._header_btn)

        # ── Content area ──
        self._content = QWidget()
        self._content.setVisible(False)  # start collapsed
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 4)
        self._content_layout.setSpacing(0)

        # Child branches (subdirectories)
        for child_node in sorted(node.children, key=lambda n: n.name.lower()):
            branch = _TreeBranch(child_node, depth + 1)
            self._branches.append(branch)
            self._content_layout.addWidget(branch)

        # Song rows at this level
        for song in node.songs:
            row = _SongRow(song, indent=depth + 1)
            self._song_rows.append(row)
            self._content_layout.addWidget(row)

        outer.addWidget(self._content)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        arrow = "▼" if self._expanded else "▶"
        total = self._node.total_song_count()
        indent_px = self._depth * 20
        self._header_btn.setText(
            f"{arrow} 📁 {self._node.name}  ({total}首)"
        )
        self._header_btn.setStyleSheet(
            f"#collapsibleHeader {{ padding-left: {12 + indent_px}px; text-align: left; }}"
        )

    def collect_rows(self) -> list[_SongRow]:
        """Recursively collect all song rows in this subtree."""
        rows = list(self._song_rows)
        for b in self._branches:
            rows.extend(b.collect_rows())
        return rows

    def apply_filter(self, text: str, liked_only: bool = False) -> bool:
        """Show/hide based on search and liked-only filter.
        Returns True if anything is visible."""
        any_visible = False
        for row in self._song_rows:
            v = row.matches(text, liked_only)
            row.setVisible(v)
            if v:
                any_visible = True
        for b in self._branches:
            if b.apply_filter(text, liked_only):
                any_visible = True
        self.setVisible(any_visible)
        return any_visible


# ── Main playlist page ────────────────────────────────────────


class PlaylistPage(QScrollArea):
    """Media library page — scan folders, tree-browse songs, click to load audio."""

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)
        self._mw = main_window

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # ── State ──
        self._all_songs: list[dict] = []
        self._branches: list[_TreeBranch] = []
        self._filter_text: str = ""
        self._show_liked_only: bool = False
        self._search_timer: QTimer | None = None
        self._root_dir: str = ""

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

        # ── Liked-only filter toggle ──
        self._btn_liked = QPushButton("♡ 喜欢")
        self._btn_liked.setCheckable(True)
        self._btn_liked.setToolTip("仅显示喜欢的歌曲")
        self._btn_liked.toggled.connect(self._on_liked_filter_toggled)
        toolbar.addWidget(self._btn_liked)

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
        self._content_layout.setSpacing(2)
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

    # ── Cache ────────────────────────────────────────────────

    def _load_cache(self) -> None:
        cache = self._mw.config.get_playlist_cache()
        songs = cache.get("songs", [])
        self._root_dir = _migrate_root_dir(cache)
        if songs and self._root_dir and os.path.isdir(self._root_dir):
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
        self._root_dir = folder
        self._start_scan([folder])

    def _on_rescan(self) -> None:
        if not self._root_dir:
            cache = self._mw.config.get_playlist_cache()
            self._root_dir = _migrate_root_dir(cache)
        if not self._root_dir:
            self._mw.toast_overlay.show_toast(
                "warning", "请先选择文件夹再进行扫描"
            )
            return
        self._start_scan([self._root_dir])

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
        old_liked: set[str] = set()
        for s in old_cache.get("songs", []):
            if s.get("liked"):
                old_liked.add(s["path"])

        for s in songs:
            if s["path"] in old_liked:
                s["liked"] = True

        # ── Save cache ──
        cache = {
            "root_dir": self._root_dir,
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

    # ── Tree builder ──────────────────────────────────────────

    def _build_tree(self) -> _TreeNode:
        """Build a directory tree from the flat song list."""
        root = _TreeNode(
            os.path.basename(self._root_dir) or self._root_dir,
            self._root_dir,
        )

        for song in self._all_songs:
            try:
                rel = os.path.relpath(song["path"], self._root_dir)
            except ValueError:
                # Different drive — put under root directly
                root.songs.append(song)
                continue
            parts = rel.replace("\\", "/").split("/")
            dir_parts = parts[:-1]  # directory components

            node = root
            for part in dir_parts:
                if part == ".":
                    continue
                child = node.get_child(part)
                if child is None:
                    child_path = os.path.join(node.full_path, part)
                    child = _TreeNode(part, child_path)
                    node.children.append(child)
                node = child
            node.songs.append(song)

        return root

    # ── UI rebuild ────────────────────────────────────────────

    def _rebuild_ui(self) -> None:
        self._empty_label.setVisible(False)
        for branch in self._branches:
            self._content_layout.removeWidget(branch)
            branch.deleteLater()
        self._branches.clear()

        if not self._all_songs:
            self._empty_label.setVisible(True)
            return

        tree = self._build_tree()

        # ── Create root branch ──
        root_branch = _TreeBranch(tree, depth=0)
        self._branches.append(root_branch)
        self._content_layout.addWidget(root_branch)

        # ── Connect signals (recursively) ──
        for row in root_branch.collect_rows():
            row.song_clicked.connect(self._on_song_clicked)
            row.like_toggled.connect(self._on_like_toggled)

        if self._filter_text or self._show_liked_only:
            self._do_apply_filter()

    # ── Song click → load audio + auto-play ──────────────────

    def _on_song_clicked(self, path: str) -> None:
        if not os.path.isfile(path):
            self._mw.toast_overlay.show_toast("warning", "文件不存在")
            return

        # ── Same song → restart from beginning ──
        current_path = self._mw.audio_manager.local_path
        if current_path and os.path.normpath(current_path) == os.path.normpath(path):
            self._mw.audio_manager.current_time = 0
            if self._mw.audio_manager.paused:
                self._mw.audio_manager.toggle()
            name = os.path.basename(path)
            self._mw.toast_overlay.show_toast("success", f"重新播放：{name}")
            return

        # ── Different song → load and auto-play ──
        url = QUrl.fromLocalFile(path).toString()
        self._mw.audio_manager.set_source(url)
        self._mw.config.remember_mp3_path(path)

        # Auto-play once the audio is loaded
        try:
            self._mw.audio_manager.duration_changed.disconnect(
                self._on_duration_loaded_for_autoplay
            )
        except TypeError:
            pass
        self._mw.audio_manager.duration_changed.connect(
            self._on_duration_loaded_for_autoplay
        )

        name = os.path.basename(path)
        self._mw.toast_overlay.show_toast("success", f"已加载：{name}")

    def _on_duration_loaded_for_autoplay(self, duration: float) -> None:
        """One-shot handler: auto-play after song is loaded."""
        try:
            self._mw.audio_manager.duration_changed.disconnect(
                self._on_duration_loaded_for_autoplay
            )
        except TypeError:
            pass
        if self._mw.audio_manager.paused:
            self._mw.audio_manager.toggle()

    # ── Like toggle ───────────────────────────────────────────

    def _on_like_toggled(self, path: str, liked: bool) -> None:
        for s in self._all_songs:
            if s["path"] == path:
                s["liked"] = liked
                break
        self._mw.config.toggle_playlist_like(path)
        # Refresh liked-only filter (hide song immediately if unliked)
        if self._show_liked_only:
            liked_count = sum(1 for s in self._all_songs if s.get("liked"))
            self._btn_liked.setText(f"❤ 喜欢 ({liked_count})")
            self._do_apply_filter()

    # ── Song refresh (called by MetaEditorPage after saving) ──

    def refresh_song(self, old_path: str, new_path: str = "") -> None:
        """Re-read a single file's metadata and update the playlist entry.

        Called after metadata edits or file rename.  If *new_path* is
        given (rename), the cache key is updated too.
        """
        actual = new_path or old_path
        if not os.path.isfile(actual):
            return

        # ── Re-read metadata from file ──
        try:
            import mutagen
            audio = mutagen.File(actual)
        except Exception:
            return

        title = ""
        artist = ""
        duration = 0.0

        tags = getattr(audio, "tags", None)
        if tags is not None:
            try:
                from mutagen.id3 import ID3
                if isinstance(tags, ID3):
                    t = tags.get("TIT2")
                    if t and t.text:
                        title = str(t.text[0])
                    a = tags.get("TPE1")
                    if a and a.text:
                        artist = str(a.text[0])
                else:
                    title = _first(tags.get("title"))
                    artist = _first(tags.get("artist"))
            except Exception:
                pass

        if not title:
            title = os.path.splitext(os.path.basename(actual))[0]

        try:
            info = getattr(audio, "info", None)
            if info is not None:
                duration = round(info.length, 1)
        except Exception:
            pass

        stem = os.path.splitext(actual)[0]
        has_lrc = os.path.isfile(stem + ".lrc")

        # ── Update in-memory list ──
        updated_song = None
        for song in self._all_songs:
            if os.path.normpath(song["path"]) == os.path.normpath(old_path):
                song["path"] = actual
                song["title"] = title
                song["artist"] = artist
                song["duration"] = duration
                song["has_lrc"] = has_lrc
                updated_song = song
                break

        # ── Persist to cache JSON ──
        cache = self._mw.config.get_playlist_cache()
        for s in cache.get("songs", []):
            if os.path.normpath(s["path"]) == os.path.normpath(old_path):
                s["path"] = actual
                s["title"] = title
                s["artist"] = artist
                s["duration"] = duration
                s["has_lrc"] = has_lrc
                break
        self._mw.config.set_playlist_cache(cache)

        # ── In-place update the widget (no full rebuild) ──
        if updated_song is not None:
            for branch in self._branches:
                for row in branch.collect_rows():
                    if os.path.normpath(row._song["path"]) == os.path.normpath(old_path):
                        row.update_song(updated_song)
                        return

    # ── Search ────────────────────────────────────────────────

    def _on_search_text_changed(self, text: str) -> None:
        if self._search_timer is not None:
            self._search_timer.stop()
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(lambda: self._apply_filter(text))
        self._search_timer.start(300)

    def _apply_filter(self, text: str) -> None:
        self._filter_text = text
        self._do_apply_filter()

    def _on_liked_filter_toggled(self, checked: bool) -> None:
        self._show_liked_only = checked
        liked_count = sum(1 for s in self._all_songs if s.get("liked"))
        if checked:
            self._btn_liked.setText(f"❤ 喜欢 ({liked_count})")
        else:
            self._btn_liked.setText("♡ 喜欢")
        self._do_apply_filter()

    def _do_apply_filter(self) -> None:
        """Apply both text search and liked-only filter to all branches."""
        for branch in self._branches:
            branch.apply_filter(self._filter_text, self._show_liked_only)

    # ── showEvent ─────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        cache = self._mw.config.get_playlist_cache()
        songs = cache.get("songs", [])
        if songs and not self._all_songs:
            self._all_songs = songs
            self._root_dir = _migrate_root_dir(cache)
            self._rebuild_ui()
