"""Central LRC state manager built on QObject and signals."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .lrc_parser import (
    FormatOptions,
    Fixed,
    LyricLine,
    LrcState,
    TrimOptions,
    guard,
    parse as parse_lrc,
    stringify as stringify_lrc,
)

_MAX_UNDO = 100


class LrcStateManager(QObject):
    """Central state manager for LRC lyrics data."""

    state_changed = pyqtSignal()
    document_changed = pyqtSignal()
    format_changed = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.info: Dict[str, str] = {}
        self.lyric: List[LyricLine] = []
        self.current_time: float = float("inf")
        self.current_index: int = 0
        self.next_time: float = float("-inf")
        self.next_index: int = 0
        self.select_index: int = 0

        self._format_options = FormatOptions()

        self._undo_stack: List[Dict[str, Any]] = []
        self._redo_stack: List[Dict[str, Any]] = []

    def _notify_document_changed(self) -> None:
        """Invalidate the playback interval and notify document/state consumers."""
        self.current_time = float("inf")
        self.next_time = float("-inf")
        self.document_changed.emit()
        self.state_changed.emit()

    def _snapshot(self) -> Dict[str, Any]:
        """Capture current state for undo/redo."""
        return {
            "info": dict(self.info),
            "lyric": [LyricLine(time=ln.time, text=ln.text, translation=ln.translation) for ln in self.lyric],
            "current_time": self.current_time,
            "current_index": self.current_index,
            "next_time": self.next_time,
            "next_index": self.next_index,
            "select_index": self.select_index,
        }

    def _restore(self, snap: Dict[str, Any]) -> None:
        """Restore a document snapshot; its playback interval is no longer valid."""
        self.info = snap["info"]
        self.lyric = snap["lyric"]
        self.current_index = snap["current_index"]
        self.next_index = snap["next_index"]
        self.select_index = snap["select_index"]
        self._notify_document_changed()

    def _push_undo(self) -> None:
        """Save current state to undo stack before a validated mutation."""
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > _MAX_UNDO:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self) -> None:
        """Undo the last change."""
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        self._restore(self._undo_stack.pop())

    def redo(self) -> None:
        """Redo the last undone change."""
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        self._restore(self._redo_stack.pop())

    def init_from_text(self, text: str, options: TrimOptions, select: int = 0) -> None:
        """Initialize state by parsing LRC text."""
        state = parse_lrc(text, options)
        new_select = guard(select, 0, max(0, len(state.lyric) - 1))
        if self.info == state.info and self.lyric == state.lyric:
            if self.select_index != new_select:
                self.select_index = new_select
                self.state_changed.emit()
            return
        self._push_undo()
        self.info = state.info
        self.lyric = state.lyric
        self.current_index = 0
        self.next_index = 0
        self.select_index = new_select
        self._notify_document_changed()

    def parse(self, text: str, options: TrimOptions) -> None:
        """Re-parse text into state."""
        state = parse_lrc(text, options)
        if self.info == state.info and self.lyric == state.lyric:
            return
        self._push_undo()
        self.info = state.info
        self.lyric = state.lyric
        self.select_index = guard(self.select_index, 0, max(0, len(self.lyric) - 1))
        self._notify_document_changed()

    def refresh(self, audio_time: float) -> None:
        """Update the current/next index from the audio position."""
        if self.current_time <= audio_time < self.next_time:
            return

        record = {
            "currentTime": float("-inf"),
            "currentIndex": 0,
            "nextTime": float("inf"),
            "nextIndex": 0,
        }

        for i, line in enumerate(self.lyric):
            t = line.time
            if t is not None:
                if t < record["nextTime"] and t > audio_time:
                    record["nextTime"] = t
                    record["nextIndex"] = i
                if t > record["currentTime"] and t <= audio_time:
                    record["currentTime"] = t
                    record["currentIndex"] = i

        self.current_time = record["currentTime"]
        self.current_index = record["currentIndex"]
        self.next_time = record["nextTime"]
        self.next_index = record["nextIndex"]
        self.state_changed.emit()

    def next_(self, audio_time: float) -> None:
        """Set the current line's time and advance selection, even if time is unchanged."""
        index = self.select_index
        new_select = guard(index + 1, 0, max(0, len(self.lyric) - 1))
        changed = 0 <= index < len(self.lyric) and self.lyric[index].time != audio_time
        if changed:
            self._push_undo()
            line = self.lyric[index]
            self.lyric[index] = LyricLine(
                time=audio_time, text=line.text, translation=line.translation,
            )
        selection_changed = self.select_index != new_select
        self.select_index = new_select
        if changed:
            self._notify_document_changed()
        elif selection_changed:
            self.state_changed.emit()

    def set_time(self, time_val: float) -> None:
        """Set the timestamp on the currently selected line."""
        index = self.select_index
        if not (0 <= index < len(self.lyric)) or self.lyric[index].time == time_val:
            return
        self._push_undo()
        line = self.lyric[index]
        self.lyric[index] = LyricLine(
            time=time_val, text=line.text, translation=line.translation,
        )
        self._notify_document_changed()

    def set_text(self, index: int, text: str) -> None:
        """Set the lyric text on a specific line."""
        if not (0 <= index < len(self.lyric)) or self.lyric[index].text == text:
            return
        self._push_undo()
        self.lyric[index] = LyricLine(
            time=self.lyric[index].time,
            text=text,
            translation=self.lyric[index].translation,
        )
        self._notify_document_changed()

    def split_line(self, index: int, positions: list[int]) -> None:
        """Split a lyric line at the given character positions."""
        if not (0 <= index < len(self.lyric)):
            return

        line = self.lyric[index]
        text = line.text

        valid = sorted(set(p for p in positions if 0 < p < len(text)))
        if not valid:
            return

        segments: list[str] = []
        prev = 0
        for pos in valid:
            segments.append(text[prev:pos])
            prev = pos
        segments.append(text[prev:])

        segments = [s for s in segments if s]
        if len(segments) <= 1:
            return

        new_lines = [
            LyricLine(
                time=line.time,
                text=seg,
                translation=line.translation if i == 0 else "",
            )
            for i, seg in enumerate(segments)
        ]

        self._push_undo()
        self.lyric[index : index + 1] = new_lines
        self._notify_document_changed()

    def append_line(self, after_index: int, time: Optional[float] = None) -> None:
        """Insert a new empty line after *after_index*."""
        if not (0 <= after_index < len(self.lyric)):
            return
        self._push_undo()
        ref = self.lyric[after_index]
        new_time = ref.time if time is None else time
        new_line = LyricLine(time=new_time, text="", translation="")
        self.lyric.insert(after_index + 1, new_line)
        self.select_index = after_index + 1
        self._notify_document_changed()

    def copy_line(self, index: int) -> None:
        """Duplicate the line at *index*, inserting the copy right below it."""
        if not (0 <= index < len(self.lyric)):
            return
        self._push_undo()
        ref = self.lyric[index]
        new_line = LyricLine(
            time=ref.time,
            text=ref.text,
            translation="",
        )
        self.lyric.insert(index + 1, new_line)
        self.select_index = index + 1
        self._notify_document_changed()

    def insert_lines(
        self, after_index: int, texts: list[str], time: float | None = None
    ) -> None:
        """Insert lines after a valid index, or at the start with index -1."""
        if not texts or not (-1 <= after_index < len(self.lyric)):
            return

        new_lines = [
            LyricLine(time=time, text=t, translation="") for t in texts
        ]
        insert_at = after_index + 1
        self._push_undo()
        self.lyric[insert_at:insert_at] = new_lines
        self.select_index = insert_at + len(new_lines) - 1
        self._notify_document_changed()

    def set_translation(self, index: int, text: str) -> None:
        """Set the translation text on a lyric line."""
        if not (0 <= index < len(self.lyric)) or self.lyric[index].translation == text:
            return
        self._push_undo()
        self.lyric[index] = LyricLine(
            time=self.lyric[index].time,
            text=self.lyric[index].text,
            translation=text,
        )
        self._notify_document_changed()

    def set_translations_batch(self, translations: dict[int, str]) -> int:
        """Set changed translations (including empty text) in a single undo step."""
        changes = {
            index: text for index, text in translations.items()
            if 0 <= index < len(self.lyric) and self.lyric[index].translation != text
        }
        if not changes:
            return 0
        self._push_undo()
        for index, text in changes.items():
            self.lyric[index] = LyricLine(
                time=self.lyric[index].time,
                text=self.lyric[index].text,
                translation=text,
            )
        self._notify_document_changed()
        return len(changes)

    def set_info(self, name: str, value: str) -> None:
        """Set a metadata info field."""
        value = value.strip()
        if (not value and name not in self.info) or (value and self.info.get(name) == value):
            return
        self._push_undo()
        if value == "":
            self.info.pop(name, None)
        else:
            self.info[name] = value
        self._notify_document_changed()

    def select(self, selector_fn: Callable[[int], int]) -> None:
        """Change the selected line index via *selector_fn*."""
        new_index = guard(
            selector_fn(self.select_index),
            0,
            max(0, len(self.lyric) - 1),
        )
        if self.select_index != new_index:
            self.select_index = new_index
            self.state_changed.emit()

    def deselect(self) -> None:
        """Clear row selection (select_index = -1)."""
        if self.select_index != -1:
            self.select_index = -1
            self.state_changed.emit()

    def delete_time(self) -> None:
        """Remove the timestamp from the selected line."""
        index = self.select_index
        if not (0 <= index < len(self.lyric)) or self.lyric[index].time is None:
            return
        self._push_undo()
        self.lyric[index] = LyricLine(
            time=None,
            text=self.lyric[index].text,
            translation=self.lyric[index].translation,
        )
        self._notify_document_changed()

    def delete_lines(self, indices: set[int]) -> None:
        """Remove one or more lines entirely (text, timestamp, translation)."""
        valid = sorted((i for i in indices if 0 <= i < len(self.lyric)), reverse=True)
        if not valid:
            return
        self._push_undo()
        for i in valid:
            del self.lyric[i]
        if not self.lyric:
            self.select_index = -1
        else:
            self.select_index = guard(self.select_index, 0, len(self.lyric) - 1)
        self._notify_document_changed()

    def merge_lines(self, indices: set[int]) -> None:
        """Merge contiguous selected lines into one."""
        if len(indices) < 2:
            return
        sorted_idx = sorted(indices)
        if sorted_idx[0] < 0 or sorted_idx[-1] >= len(self.lyric):
            return
        for i in range(1, len(sorted_idx)):
            if sorted_idx[i] != sorted_idx[i - 1] + 1:
                return
        first_idx = sorted_idx[0]
        last_idx = sorted_idx[-1]

        self._push_undo()

        earliest = min(
            (self.lyric[i].time
             for i in sorted_idx if self.lyric[i].time is not None),
            default=None,
        )
        merged_text = " ".join(
            self.lyric[i].text.strip()
            for i in sorted_idx
            if self.lyric[i].text.strip()
        )
        first_translation = self.lyric[first_idx].translation

        merged = LyricLine(time=earliest, text=merged_text,
                           translation=first_translation)
        self.lyric[first_idx:last_idx + 1] = [merged]

        self.select_index = first_idx
        self._notify_document_changed()

    def get_state(self, callback: Callable[["LrcStateManager"], None]) -> None:
        """Pass the current state to *callback* without broadcasting a change."""
        callback(self)

    def stringify(self, options: Optional[FormatOptions] = None) -> str:
        """Convert current state to LRC-formatted string."""
        fmt = options or self._format_options
        state = LrcState(info=dict(self.info), lyric=list(self.lyric))
        return stringify_lrc(state, fmt)

    def update_format_options(self, options: FormatOptions) -> None:
        """Notify format consumers only when serialization options change."""
        if self._format_options == options:
            return
        self._format_options = copy.copy(options)
        self.format_changed.emit()
