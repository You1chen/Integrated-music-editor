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
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .content_stack import _CURRENT_THEME_COLOR, _CURRENT_DARK, _rgba

if TYPE_CHECKING:
    from .main_window import MainWindow


# ── Background scanner QThread ────────────────────────────────


class _ScanWorker(QThread):
    """Scans directories recursively for .mp3 files in a background thread.

    Supports two modes:
      - full scan (existing_songs is None): mutagen-parses every file.
      - incremental scan (existing_songs given): reuses cached entries whose
        mtime/size fingerprint is unchanged, only re-parsing files that were
        added or modified on disk. Deleted files are dropped from the result.
    """

    scan_finished = pyqtSignal(list, int, int, int)  # (songs, added, updated, removed)

    def __init__(
        self,
        root_dirs: list[str],
        existing_songs: list[dict] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root_dirs = root_dirs
        self._existing_songs = existing_songs

    def run(self) -> None:
        import mutagen

        # Index existing songs by normalized path for incremental matching.
        existing: dict[str, dict] = {}
        if self._existing_songs:
            existing = {
                _norm_path(s["path"]): s
                for s in self._existing_songs
                if s.get("path")
            }
        seen: set[str] = set()
        added = 0
        updated = 0

        songs: list[dict] = []

        for root_dir in self._root_dirs:
            if not os.path.isdir(root_dir):
                continue
            for dirpath, _dirnames, filenames in os.walk(root_dir):
                for fname in filenames:
                    if not fname.lower().endswith(".mp3"):
                        continue
                    full_path = os.path.join(dirpath, fname)
                    key = _norm_path(full_path)

                    try:
                        st = os.stat(full_path)
                    except OSError:
                        continue

                    cached = existing.get(key)

                    # ── Fast path: fingerprint unchanged → reuse entry ──
                    if cached is not None and (
                        cached.get("mtime_ns") == st.st_mtime_ns
                        and cached.get("size") == st.st_size
                    ):
                        song = dict(cached)  # keep stored "path" string stable
                        stem = os.path.splitext(full_path)[0]
                        song["has_lrc"] = os.path.isfile(stem + ".lrc")
                        songs.append(song)
                        seen.add(key)
                        continue

                    # ── Slow path: new or modified file → full parse ──
                    try:
                        audio = mutagen.File(full_path)
                    except Exception:
                        continue

                    # ── Extract tags ──
                    title = ""
                    artist = ""
                    album = ""
                    albumartist = ""
                    lyricist = ""
                    composer = ""
                    year = ""
                    genre = ""
                    comment = ""
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
                                album = _id3_tag_text(tags, "TALB")
                                albumartist = _id3_tag_text(tags, "TPE2")
                                lyricist = _id3_tag_text(tags, "TEXT")
                                composer = _id3_tag_text(tags, "TCOM")
                                year = _id3_tag_text(tags, "TDRC") or _id3_tag_text(tags, "TYER")
                                genre = _id3_tag_text(tags, "TCON")
                                comment = _id3_comment_text(tags)
                            else:
                                title = _first(tags.get("title"))
                                artist = _first(tags.get("artist"))
                                album = _first(tags.get("album"))
                                albumartist = _first(tags.get("albumartist"))
                                lyricist = _first(tags.get("lyricist"))
                                composer = _first(tags.get("composer"))
                                year = _first(tags.get("date")) or _first(tags.get("year"))
                                genre = _first(tags.get("genre"))
                                comment = _first(tags.get("comment")) or _first(tags.get("description"))
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
                        "album": album,
                        "albumartist": albumartist,
                        "lyricist": lyricist,
                        "composer": composer,
                        "year": year,
                        "genre": genre,
                        "comment": comment,
                        "duration": duration,
                        "has_lrc": has_lrc,
                        "liked": False,
                        "mtime_ns": st.st_mtime_ns,
                        "size": st.st_size,
                    })
                    seen.add(key)
                    if cached is not None:
                        updated += 1
                    else:
                        added += 1

        # Songs that existed before but are no longer on disk
        removed = sum(1 for k in existing if k not in seen)

        songs.sort(key=lambda s: (
            os.path.dirname(s["path"]).lower(),
            s["title"].lower(),
        ))

        self.scan_finished.emit(songs, added, updated, removed)


def _norm_path(p: str) -> str:
    """Normalize a path for use as a dict key (Windows case-insensitive)."""
    return os.path.normcase(os.path.normpath(p))


def _first(lst) -> str:
    """Safely extract first element from a list, or return ''."""
    try:
        return str(lst[0]) if lst else ""
    except (IndexError, TypeError):
        return ""


def _id3_tag_text(tags, frame_name: str) -> str:
    """Get the first text value from an ID3 frame by name (e.g. 'TALB')."""
    try:
        frame = tags.get(frame_name)
        if frame is not None and frame.text:
            return str(frame.text[0])
    except Exception:
        pass
    return ""


def _id3_comment_text(tags) -> str:
    """Get the first COMM frame's text, or ''."""
    try:
        for key in tags:
            if key.startswith("COMM"):
                frame = tags[key]
                if frame.text:
                    return str(frame.text[0])
    except Exception:
        pass
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
    import_requested = pyqtSignal(str)

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
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

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

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction("导入到播放列表")
        act.triggered.connect(
            lambda: self.import_requested.emit(self._song["path"])
        )
        menu.exec(self.mapToGlobal(pos))

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
            alpha = 0.20 if _CURRENT_DARK else 0.13
            self.setStyleSheet(
                f"background-color: {_rgba(theme, alpha)}; border-radius: 6px;"
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
        """Check if this song matches a search filter (case-insensitive).

        Searches all metadata fields: path, title, artist, album,
        albumartist, lyricist, composer, year, genre, comment.
        """
        if liked_only and not self._liked:
            return False
        if not text:
            return True
        lower = text.lower()
        haystack = [
            self._song.get("path", ""),
            self._song.get("title", ""),
            self._song.get("artist", ""),
            self._song.get("album", ""),
            self._song.get("albumartist", ""),
            self._song.get("lyricist", ""),
            self._song.get("composer", ""),
            self._song.get("year", ""),
            self._song.get("genre", ""),
            self._song.get("comment", ""),
        ]
        return any(lower in h.lower() for h in haystack if h)


# ── Tree branch widget (collapsible directory node) ──────────


class _TreeBranch(QWidget):
    """A collapsible directory node — recursive: can contain
    child _TreeBranch widgets and _SongRow widgets.
    """

    import_requested = pyqtSignal(str)

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
        self._header_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._header_btn.customContextMenuRequested.connect(self._on_context_menu)
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
        self._set_expanded(not self._expanded)

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act = menu.addAction("导入到播放列表")
        act.triggered.connect(
            lambda: self.import_requested.emit(self._node.full_path)
        )
        menu.exec(self._header_btn.mapToGlobal(pos))

    def _set_expanded(self, expanded: bool) -> None:
        """Set expansion state programmatically (no toggle side-effects)."""
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self._content.setVisible(expanded)
        arrow = "▼" if expanded else "▶"
        total = self._node.total_song_count()
        indent_px = self._depth * 20
        self._header_btn.setText(
            f"{arrow} 📁 {self._node.name}  ({total}首)"
        )
        self._header_btn.setStyleSheet(
            f"#collapsibleHeader {{ padding-left: {12 + indent_px}px; text-align: left; }}"
        )

    def snapshot_expanded(self) -> dict[str, bool]:
        """Capture the current expansion state of this subtree."""
        snap = {self._node.full_path: self._expanded}
        for b in self._branches:
            snap.update(b.snapshot_expanded())
        return snap

    def restore_expanded(self, snap: dict[str, bool]) -> None:
        """Restore expansion state from a previous snapshot."""
        if self._node.full_path in snap:
            self._set_expanded(snap[self._node.full_path])
        for b in self._branches:
            b.restore_expanded(snap)

    def collect_rows(self) -> list[_SongRow]:
        """Recursively collect all song rows in this subtree."""
        rows = list(self._song_rows)
        for b in self._branches:
            rows.extend(b.collect_rows())
        return rows

    def collect_branches(self) -> list["_TreeBranch"]:
        """Recursively collect this branch and all descendants."""
        result = [self]
        for b in self._branches:
            result.extend(b.collect_branches())
        return result

    def apply_filter(self, text: str, liked_only: bool = False,
                      expand_matches: bool = False) -> bool:
        """Show/hide based on search and liked-only filter.
        When *expand_matches* is True, auto-expand branches that contain
        visible rows so the user can see matching songs immediately.
        Returns True if anything is visible."""
        any_visible = False
        for row in self._song_rows:
            v = row.matches(text, liked_only)
            row.setVisible(v)
            if v:
                any_visible = True
        for b in self._branches:
            if b.apply_filter(text, liked_only, expand_matches):
                any_visible = True
        if expand_matches and any_visible:
            self._set_expanded(True)
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
        self._saved_expanded: dict[str, bool] | None = None
        self._search_timer: QTimer | None = None
        self._root_dir: str = ""
        self._scan_incremental: bool = False

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
        self._start_scan([self._root_dir], incremental=True)

    def _start_scan(
        self, root_dirs: list[str], incremental: bool = False
    ) -> None:
        self._mw.toast_overlay.show_toast("info", "正在扫描音乐文件...")
        self._btn_select.setEnabled(False)
        self._btn_rescan.setEnabled(False)

        self._scan_incremental = incremental
        # Shallow-copy snapshot so a like toggle / refresh_song on the main
        # thread mid-scan can't race with the worker reading the same dicts.
        existing = [dict(s) for s in self._all_songs] if incremental else []
        self._worker = _ScanWorker(
            root_dirs, existing_songs=existing, parent=self
        )
        self._worker.scan_finished.connect(self._on_scan_finished)
        self._worker.start()

    def _on_scan_finished(
        self, songs: list[dict], added: int, updated: int, removed: int
    ) -> None:
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
        if self._scan_incremental:
            self._mw.toast_overlay.show_toast(
                "success",
                f"扫描完成，共 {len(songs)} 首"
                f"（新增 {added}，更新 {updated}，删除 {removed}）",
            )
        else:
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

        # Invalidate saved expansion snapshot (tree was rebuilt)
        self._saved_expanded = None

        tree = self._build_tree()

        # ── Create root branch ──
        root_branch = _TreeBranch(tree, depth=0)
        self._branches.append(root_branch)
        self._content_layout.addWidget(root_branch)

        # ── Connect signals (recursively) ──
        for row in root_branch.collect_rows():
            row.song_clicked.connect(self._on_song_clicked)
            row.like_toggled.connect(self._on_like_toggled)
            row.import_requested.connect(self._on_import_requested)
        for branch in root_branch.collect_branches():
            branch.import_requested.connect(self._on_import_requested)

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

    # ── Import into the play queue ─────────────────────────────

    def get_all_songs(self) -> list[dict]:
        """Return all scanned songs (shallow copy) in natural order."""
        return [dict(s) for s in self._all_songs]

    def get_folder_tree(self):
        """Return the directory tree root (``_TreeNode``) or None if empty."""
        if not self._all_songs:
            return None
        return self._build_tree()

    def songs_under(self, folder_path: str) -> list[dict]:
        """Return songs whose directory is *folder_path* or below it."""
        folder = os.path.normcase(os.path.normpath(folder_path))
        result = []
        for s in self._all_songs:
            d = os.path.normcase(os.path.normpath(os.path.dirname(s["path"])))
            if d == folder or d.startswith(folder + os.sep):
                result.append(dict(s))
        return result

    def _on_import_requested(self, path: str) -> None:
        """Context menu: import a folder subtree or a single song."""
        if os.path.isdir(path):
            songs = self.songs_under(path)
        else:
            songs = [dict(s) for s in self._all_songs
                     if os.path.normpath(s["path"]) == os.path.normpath(path)]
        if not songs:
            self._mw.toast_overlay.show_toast("warning", "该目录下没有歌曲")
            return
        self._mw.import_to_playlist(songs)
        self._mw.toast_overlay.show_toast(
            "success", f"已导入 {len(songs)} 首到播放列表"
        )

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

        # ── Extract all tags (mirrors _ScanWorker) ──
        title = ""
        artist = ""
        album = ""
        albumartist = ""
        lyricist = ""
        composer = ""
        year = ""
        genre = ""
        comment = ""
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
                    album = _id3_tag_text(tags, "TALB")
                    albumartist = _id3_tag_text(tags, "TPE2")
                    lyricist = _id3_tag_text(tags, "TEXT")
                    composer = _id3_tag_text(tags, "TCOM")
                    year = _id3_tag_text(tags, "TDRC") or _id3_tag_text(tags, "TYER")
                    genre = _id3_tag_text(tags, "TCON")
                    comment = _id3_comment_text(tags)
                else:
                    title = _first(tags.get("title"))
                    artist = _first(tags.get("artist"))
                    album = _first(tags.get("album"))
                    albumartist = _first(tags.get("albumartist"))
                    lyricist = _first(tags.get("lyricist"))
                    composer = _first(tags.get("composer"))
                    year = _first(tags.get("date")) or _first(tags.get("year"))
                    genre = _first(tags.get("genre"))
                    comment = _first(tags.get("comment")) or _first(tags.get("description"))
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

        # Fresh fingerprint so the next incremental rescan reuses this entry
        try:
            st = os.stat(actual)
            mtime_ns = st.st_mtime_ns
            size = st.st_size
        except OSError:
            mtime_ns = 0
            size = 0

        # ── Update in-memory list ──
        updated_song = None
        for song in self._all_songs:
            if os.path.normpath(song["path"]) == os.path.normpath(old_path):
                song["path"] = actual
                song["title"] = title
                song["artist"] = artist
                song["album"] = album
                song["albumartist"] = albumartist
                song["lyricist"] = lyricist
                song["composer"] = composer
                song["year"] = year
                song["genre"] = genre
                song["comment"] = comment
                song["duration"] = duration
                song["has_lrc"] = has_lrc
                song["mtime_ns"] = mtime_ns
                song["size"] = size
                updated_song = song
                break

        # ── Persist to cache JSON ──
        cache = self._mw.config.get_playlist_cache()
        for s in cache.get("songs", []):
            if os.path.normpath(s["path"]) == os.path.normpath(old_path):
                s["path"] = actual
                s["title"] = title
                s["artist"] = artist
                s["album"] = album
                s["albumartist"] = albumartist
                s["lyricist"] = lyricist
                s["composer"] = composer
                s["year"] = year
                s["genre"] = genre
                s["comment"] = comment
                s["duration"] = duration
                s["has_lrc"] = has_lrc
                s["mtime_ns"] = mtime_ns
                s["size"] = size
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
        """Apply both text search and liked-only filter to all branches.
        Snapshot expansion state before the first filter, restore when
        the filter is cleared, and auto-expand matching paths in between."""
        active = bool(self._filter_text) or self._show_liked_only

        if active and self._saved_expanded is None:
            # Snapshot user's manual expansion state before filtering
            self._saved_expanded = {}
            for branch in self._branches:
                self._saved_expanded.update(branch.snapshot_expanded())

        for branch in self._branches:
            branch.apply_filter(self._filter_text, self._show_liked_only,
                                expand_matches=active)

        if not active and self._saved_expanded is not None:
            # Filter cleared — restore original expansion state
            for branch in self._branches:
                branch.restore_expanded(self._saved_expanded)
            self._saved_expanded = None

    # ── showEvent ─────────────────────────────────────────────

    def showEvent(self, event) -> None:
        super().showEvent(event)
        cache = self._mw.config.get_playlist_cache()
        songs = cache.get("songs", [])
        if songs and not self._all_songs:
            self._all_songs = songs
            self._root_dir = _migrate_root_dir(cache)
            self._rebuild_ui()
