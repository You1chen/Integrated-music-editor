"""Play queue manager — owns the play list, current index, and playback mode.

Wraps the existing :class:`AudioManager` (the single QMediaPlayer) so the
whole app shares one player.  The manager advances tracks on natural
end-of-media according to the selected :class:`PlayMode`, and exposes
list mutations used by the playlist panel ("-" remove, "+" insert-next).

Design notes
------------
- A song loaded *outside* the queue (drag-and-drop, a direct 歌单 click)
  is never auto-advanced: ``_on_media_ended`` only reacts when the audio
  currently loaded matches the queue's current entry (path comparison).
- The queue stores lightweight entries (path/title/artist/duration), not
  the big song dicts owned by the playlist page.
"""

from __future__ import annotations

import os
import random
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QObject, QUrl, pyqtSignal

from .constants import PlayMode

if TYPE_CHECKING:
    from .audio_manager import AudioManager


class PlaylistManager(QObject):
    """Play queue + playback mode on top of a shared AudioManager."""

    queue_changed = pyqtSignal()
    current_changed = pyqtSignal(int)
    mode_changed = pyqtSignal(PlayMode)

    def __init__(
        self,
        audio_manager: "AudioManager",
        config=None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._am = audio_manager
        self._config = config
        self._queue: list[dict] = []
        self._current_index: int = -1
        self._mode: PlayMode = PlayMode.SINGLE
        self._autoplay_connected = False

        self._am.media_ended.connect(self._on_media_ended)
        self._load()

    # ── Read-only accessors ──────────────────────────────────

    @property
    def queue(self) -> list[dict]:
        return list(self._queue)

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def mode(self) -> PlayMode:
        return self._mode

    @property
    def current_song(self) -> Optional[dict]:
        if 0 <= self._current_index < len(self._queue):
            return self._queue[self._current_index]
        return None

    def current_path(self) -> str:
        song = self.current_song
        return song["path"] if song else ""

    # ── Mode ─────────────────────────────────────────────────

    def set_mode(self, mode: PlayMode) -> None:
        if mode != self._mode:
            self._mode = mode
            self.mode_changed.emit(mode)

    # ── Queue mutations ──────────────────────────────────────

    @staticmethod
    def _entry(song: dict) -> dict:
        """Reduce a song dict to the lightweight queue entry."""
        title = song.get("title") or os.path.splitext(
            os.path.basename(song["path"])
        )[0]
        return {
            "path": song["path"],
            "title": title,
            "artist": song.get("artist", ""),
            "duration": song.get("duration", 0),
        }

    def set_queue(self, songs: list[dict]) -> None:
        """Replace the whole queue (no auto-play)."""
        self._queue = [self._entry(s) for s in songs if s.get("path")]
        self._current_index = -1
        self._emit_all()

    def add_songs(self, songs: list[dict]) -> None:
        """Append songs to the end of the queue."""
        self._queue.extend(self._entry(s) for s in songs if s.get("path"))
        self._emit_all()

    def clear(self) -> None:
        self._queue = []
        self._current_index = -1
        self._emit_all()

    def remove_at(self, index: int) -> None:
        """Remove an entry; keep playback running if it was the current one."""
        n = len(self._queue)
        if not (0 <= index < n):
            return
        self._queue.pop(index)
        if index < self._current_index:
            self._current_index -= 1
        elif index == self._current_index:
            # Current removed — the already-loaded audio keeps playing, but
            # there is no longer a queue anchor to auto-advance from.
            self._current_index = -1
        self._emit_all()

    def insert_next(self, index: int) -> None:
        """Duplicate the entry at *index* right after the current song.

        This is "add as next", not "move": the source entry stays put, so
        the same song can be queued repeatedly.
        """
        n = len(self._queue)
        if not (0 <= index < n):
            return
        entry = dict(self._queue[index])
        if 0 <= self._current_index < len(self._queue):
            insert_at = self._current_index + 1
        else:
            insert_at = len(self._queue)
        self._queue.insert(insert_at, entry)
        self._emit_all()

    # ── Playback ─────────────────────────────────────────────

    def play_index(self, index: int) -> None:
        """Load and play the entry at *index*."""
        n = len(self._queue)
        if not (0 <= index < n):
            return
        path = self._queue[index]["path"]
        if not os.path.isfile(path):
            # Leave the current selection untouched — caller prints a message.
            return
        self._current_index = index
        self._am.set_source(QUrl.fromLocalFile(path).toString())
        # Keep "last played mp3" in sync so saving lyrics targets THIS song,
        # not whatever was loaded first (张冠李戴 guard relies on this).
        if self._config is not None and hasattr(self._config, "remember_mp3_path"):
            self._config.remember_mp3_path(path)
        self._connect_autoplay()
        # Only the playing pointer moved — the queue itself is unchanged.
        # Emitting queue_changed here would make the drawer rebuild every row
        # on every切歌 (a multi-second GUI freeze on large libraries). The
        # panel only needs current_changed to re-tint the now-playing row, and
        # the footer already refreshes song info / like state on it too.
        self.current_changed.emit(index)
        self._save()

    def next(self) -> None:
        """Play the next track in the queue (wraps around)."""
        n = len(self._queue)
        if n == 0 or self._current_index < 0:
            return
        self.play_index((self._current_index + 1) % n)

    def prev(self) -> None:
        """Play the previous track in the queue (wraps around)."""
        n = len(self._queue)
        if n == 0 or self._current_index < 0:
            return
        self.play_index((self._current_index - 1) % n)

    def _connect_autoplay(self) -> None:
        if self._autoplay_connected:
            return
        self._am.duration_changed.connect(self._on_autoplay)
        self._autoplay_connected = True

    def _on_autoplay(self, _duration: float) -> None:
        try:
            self._am.duration_changed.disconnect(self._on_autoplay)
        except TypeError:
            pass
        self._autoplay_connected = False
        self._am.play()

    # ── End-of-media advancement ─────────────────────────────

    def _on_media_ended(self) -> None:
        n = len(self._queue)
        if self._current_index < 0 or n == 0:
            return

        cur = self._queue[self._current_index]
        if _norm_path(self._am.local_path) != _norm_path(cur["path"]):
            # A song loaded outside the queue finished — do not advance.
            return

        mode = self._mode
        if mode == PlayMode.SINGLE:
            return
        if mode == PlayMode.SINGLE_LOOP:
            self._am.restart()
            return
        if mode == PlayMode.SEQUENTIAL:
            if self._current_index + 1 < n:
                self.play_index(self._current_index + 1)
            return
        if mode == PlayMode.LOOP:
            self.play_index((self._current_index + 1) % n)
            return
        if mode == PlayMode.SHUFFLE:
            if n <= 1:
                self._am.restart()
                return
            j = self._current_index
            while j == self._current_index:
                j = random.randrange(n)
            self.play_index(j)

    # ── Internal ─────────────────────────────────────────────

    def _emit_all(self) -> None:
        self.queue_changed.emit()
        self.current_changed.emit(self._current_index)
        self._save()

    def _load(self) -> None:
        """Restore the persisted queue + current index (no auto-play)."""
        if self._config is None or not hasattr(self._config, "get_play_queue"):
            return
        data = self._config.get_play_queue() or {}
        songs = [
            dict(e) for e in data.get("songs", [])
            if isinstance(e, dict) and e.get("path")
        ]
        idx = data.get("index", -1)
        self._queue = songs
        self._current_index = idx if 0 <= idx < len(songs) else -1
        # Restore the last-session playback mode (defaults to SINGLE when the
        # config predates mode persistence).
        if hasattr(self._config, "get_last_play_mode"):
            try:
                self._mode = PlayMode(self._config.get_last_play_mode())
            except (TypeError, ValueError):
                self._mode = PlayMode.SINGLE

    def _save(self) -> None:
        """Persist the queue + current index (best-effort, skipped without config)."""
        if self._config is None or not hasattr(self._config, "set_play_queue"):
            return
        self._config.set_play_queue(
            [dict(e) for e in self._queue], self._current_index
        )


def _norm_path(p: str) -> str:
    """Normalize a path for comparison (Windows case-insensitive)."""
    return os.path.normcase(os.path.normpath(p))
