"""Parse LRC formatted text into structured state and stringify it back."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

Fixed = Literal[0, 1, 2, 3]

_TIME_TAG_RE = re.compile(r"\[\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]")
_INFO_TAG_RE = re.compile(r"\[\s*(\w{1,6})\s*:(.*?)]")

_TRANSLATION_MARKER = "    "


def is_translation_marker(text: str) -> bool:
    """Return ``True`` when *text* ends with the 4-space translation marker."""
    return text.endswith(_TRANSLATION_MARKER)


@dataclass
class LyricLine:
    """A single line in the lyrics, optionally with a timestamp and translated text."""
    time: Optional[float] = None
    text: str = ""
    translation: str = ""


@dataclass
class LrcState:
    """Parsed LRC state: metadata info map + list of lyric lines."""
    info: Dict[str, str] = field(default_factory=dict)
    lyric: List[LyricLine] = field(default_factory=list)


@dataclass
class TrimOptions:
    """Options for trimming whitespace from lyric text."""
    trim_start: bool = False
    trim_end: bool = False


@dataclass
class FormatOptions:
    """Options for stringifying LRC state back to text."""
    space_start: int = 1
    space_end: int = 0
    fixed: Fixed = 3
    end_of_line: str = "\r\n"


def parse(lrc_string: str, options: Optional[TrimOptions] = None) -> LrcState:
    """Parse an LRC-formatted string into structured state."""
    if options is None:
        options = TrimOptions()

    lines = re.split(r"\r\n|\n|\r", lrc_string)

    info: Dict[str, str] = {}
    lyric: List[LyricLine] = []

    for line in lines:
        if not line:
            continue

        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)

        if stripped and stripped[0] == "[":
            match = _TIME_TAG_RE.search(stripped)
            if match is not None:
                mm = int(match.group(1))
                ss = float(match.group(2).replace(":", "."))
                text = stripped[match.end():]

                lyric.append(LyricLine(
                    time=mm * 60 + ss,
                    text=text,
                ))
                continue

            if indent == 0:
                match = _INFO_TAG_RE.match(line)
                if match is not None:
                    value = match.group(2).strip()
                    if value:
                        info[match.group(1)] = value
                    continue

            lyric.append(LyricLine(text=line))
            continue

        lyric.append(LyricLine(text=line))

    i = 0
    while i < len(lyric):
        line = lyric[i]
        if line.time is None:
            i += 1
            continue

        if is_translation_marker(line.text):
            line.text = line.text.rstrip()
            if (
                i + 1 < len(lyric)
                and lyric[i + 1].time is not None
                and lyric[i + 1].time == line.time
                and not is_translation_marker(lyric[i + 1].text)
            ):
                line.translation = lyric[i + 1].text
                del lyric[i + 1]
        else:
            if (
                i > 0
                and lyric[i - 1].time is not None
                and line.time == lyric[i - 1].time
                and is_translation_marker(lyric[i - 1].text)
            ):
                lyric[i - 1].translation = line.text
                del lyric[i]
                i -= 1
        i += 1

    if options.trim_start and options.trim_end:
        for line in lyric:
            line.text = line.text.strip()
    elif options.trim_start:
        for line in lyric:
            line.text = line.text.lstrip()
    elif options.trim_end:
        for line in lyric:
            line.text = line.text.rstrip()

    return LrcState(info=info, lyric=lyric)


def convert_time_to_tag(time: Optional[float], fixed: Fixed, with_brackets: bool = True) -> str:
    """Convert a time in seconds to an LRC timestamp tag."""
    if time is None:
        return ""

    mm = int(time // 60)
    ss = time % 60

    format_str = f"{{:0{2 + fixed + (1 if fixed > 0 else 0)}.{fixed}f}}"
    ss_str = format_str.format(ss)

    result = f"{mm:02d}:{ss_str}"
    return f"[{result}]" if with_brackets else result


def format_text(text: str, space_start: int, space_end: int) -> str:
    """Format lyric text with leading and trailing spaces."""
    new_text = text
    if space_start >= 0:
        new_text = " " * space_start + new_text.lstrip()
    if space_end >= 0:
        new_text = new_text.rstrip() + " " * space_end
    return new_text


def stringify(state: LrcState, options: FormatOptions) -> str:
    """Convert structured LRC state back to an LRC-formatted string."""
    infos = [f"[{name}: {value}]" for name, value in state.info.items()]

    lines = []
    for line in state.lyric:
        if line.time is None:
            if line.text:
                lines.append(line.text)
        else:
            text = format_text(line.text, options.space_start, options.space_end)
            tag = convert_time_to_tag(line.time, options.fixed)

            lines.append(f"{tag}{text}{_TRANSLATION_MARKER}")
            if line.translation:
                trans_text = format_text(
                    line.translation, options.space_start, options.space_end,
                )
                lines.append(f"{tag}{trans_text}")

    return options.end_of_line.join(infos + lines)


def guard(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value
