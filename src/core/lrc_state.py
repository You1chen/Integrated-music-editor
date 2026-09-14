"""Central LRC state manager built on QObject and signals."""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from .constants import ActionType
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
        """Restore state from a snapshot."""
        self.info = snap["info"]
        self.lyric = snap["lyric"]
        self.current_time = snap["current_time"]
        self.current_index = snap["current_index"]
        self.next_time = snap["next_time"]
        self.next_index = snap["next_index"]
        self.select_index = snap["select_index"]
        self.state_changed.emit()

    def _push_undo(self) -> None:
        """Save current state to undo stack before a mutation."""
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
        self._push_undo()
        state = parse_lrc(text, options)
        self.info = state.info
        self.lyric = state.lyric
        self.current_time = float("inf")
        self.current_index = 0
        self.next_time = float("-inf")
        self.next_index = 0
        self.select_index = guard(select, 0, max(0, len(self.lyric) - 1))
        self.state_changed.emit()

    def parse(self, text: str, options: TrimOptions) -> None:
        """Re-parse text into state."""
        self._push_undo()
        state = parse_lrc(text, options)
        self.info = state.info
        self.lyric = state.lyric
        self.select_index = guard(self.select_index, 0, max(0, len(self.lyric) - 1))
        self.state_changed.emit()

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
        """Set the time on the current line, then move the selection to the next line."""
        self._push_undo()
        index = self.select_index

        if 0 <= index < len(self.lyric):
            if self.lyric[index].time != audio_time:
                self.lyric[index] = LyricLine(
                    time=audio_time,
                    text=self.lyric[index].text,
                    translation=self.lyric[index].translation,
                )
        self.current_time = audio_time
        self.next_time = float("-inf")

        self.select_index = guard(index + 1, 0, max(0, len(self.lyric) - 1))
        self.state_changed.emit()

    def set_time(self, time_val: float) -> None:
        """Set the timestamp on the currently selected line."""
        self._push_undo()
        index = self.select_index
        if 0 <= index < len(self.lyric):
            if self.lyric[index].time != time_val:
                self.lyric[index] = LyricLine(
                    time=time_val,
                    text=self.lyric[index].text,
                    translation=self.lyric[index].translation,
                )
        self.current_time = time_val
        self.next_time = float("-inf")
        self.state_changed.emit()

    def set_text(self, index: int, text: str) -> None:
        """Set the lyric text on a specific line."""
        self._push_undo()
        if 0 <= index < len(self.lyric):
            self.lyric[index] = LyricLine(
                time=self.lyric[index].time,
                text=text,
                translation=self.lyric[index].translation,
            )
        self.state_changed.emit()

    def split_line(self, index: int, positions: list[int]) -> None:
        """Split a lyric line at the given character positions."""
        self._push_undo()
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

        self.lyric[index : index + 1] = new_lines
        self.state_changed.emit()

    def append_line(self, after_index: int, time: Optional[float] = None) -> None:
        """Insert a new empty line after *after_index*."""
        self._push_undo()
        if 0 <= after_index < len(self.lyric):
            ref = self.lyric[after_index]
            new_time = ref.time if time is None else time
            new_line = LyricLine(time=new_time, text="", translation="")
            self.lyric.insert(after_index + 1, new_line)
            self.select_index = after_index + 1
            self.state_changed.emit()

    def copy_line(self, index: int) -> None:
        """Duplicate the line at *index*, inserting the copy right below it."""
        self._push_undo()
        if 0 <= index < len(self.lyric):
            ref = self.lyric[index]
            new_line = LyricLine(
                time=ref.time,
                text=ref.text,
                translation="",
            )
            self.lyric.insert(index + 1, new_line)
            self.select_index = index + 1
            self.state_changed.emit()

    def insert_lines(
        self, after_index: int, texts: list[str], time: float | None = None
    ) -> None:
        """Insert one or more lyric lines after *after_index*."""
        self._push_undo()
        if not texts:
            return

        new_lines = [
            LyricLine(time=time, text=t, translation="") for t in texts
        ]

        if after_index == -1:
            insert_at = 0
        else:
            insert_at = min(after_index + 1, len(self.lyric))

        self.lyric[insert_at:insert_at] = new_lines
        self.select_index = insert_at + len(new_lines) - 1
        self.state_changed.emit()

    def set_translation(self, index: int, text: str) -> None:
        """Set the translation text on a lyric line."""
        self._push_undo()
        if 0 <= index < len(self.lyric):
            self.lyric[index] = LyricLine(
                time=self.lyric[index].time,
                text=self.lyric[index].text,
                translation=text,
            )
        self.state_changed.emit()

    def set_translations_batch(self, translations: dict[int, str]) -> int:
        """Set translations for multiple lines in a single undo step."""
        if not translations:
            return 0
        self._push_undo()
        count = 0
        for index, text in translations.items():
            if 0 <= index < len(self.lyric) and text:
                self.lyric[index] = LyricLine(
                    time=self.lyric[index].time,
                    text=self.lyric[index].text,
                    translation=text,
                )
                count += 1
        if count > 0:
            self.state_changed.emit()
        return count

    def set_info(self, name: str, value: str) -> None:
        """Set a metadata info field."""
        self._push_undo()
        value = value.strip()
        if value == "":
            self.info.pop(name, None)
        else:
            self.info[name] = value
        self.state_changed.emit()

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
        self._push_undo()
        index = self.select_index
        if 0 <= index < len(self.lyric) and self.lyric[index].time is not None:
            self.lyric[index] = LyricLine(
                time=None,
                text=self.lyric[index].text,
                translation=self.lyric[index].translation,
            )

            if index == self.current_index:
                self.current_time = float("inf")
                self.next_time = float("-inf")

            self.state_changed.emit()

    def delete_lines(self, indices: set[int]) -> None:
        """Remove one or more lines entirely (text, timestamp, translation)."""
        if not indices:
            return
        self._push_undo()
        for i in sorted(indices, reverse=True):
            if 0 <= i < len(self.lyric):
                del self.lyric[i]
        if not self.lyric:
            self.select_index = -1
        else:
            self.select_index = guard(self.select_index, 0, len(self.lyric) - 1)
        self.state_changed.emit()

    def merge_lines(self, indices: set[int]) -> None:
        """Merge contiguous selected lines into one."""
        if len(indices) < 2:
            return
        sorted_idx = sorted(indices)
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
        self.state_changed.emit()

    def get_state(self, callback: Callable[["LrcStateManager"], None]) -> None:
        """Pass the current state to *callback*."""
        callback(self)
        self.state_changed.emit()

    def stringify(self, options: Optional[FormatOptions] = None) -> str:
        """Convert current state to LRC-formatted string."""
        fmt = options or self._format_options
        state = LrcState(info=dict(self.info), lyric=list(self.lyric))
        return stringify_lrc(state, fmt)

    def update_format_options(self, options: FormatOptions) -> None:
        """Update the formatting options used for stringify."""
        self._format_options = options
