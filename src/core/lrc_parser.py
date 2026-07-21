"""Pure Python port of @lrc-maker/lrc-parser.

Parses LRC (Lyrics) formatted text into structured data and vice versa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

Fixed = Literal[0, 1, 2, 3]

# Regex patterns — identical to the TypeScript version
_TIME_TAG_RE = re.compile(r"\[\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]")
_INFO_TAG_RE = re.compile(r"\[\s*(\w{1,6})\s*:(.*?)]")


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
    """Parse an LRC-formatted string into structured state.

    Args:
        lrc_string: Raw LRC text content.
        options: Trim options for whitespace handling.

    Returns:
        LrcState with parsed info metadata and lyric lines.
    """
    if options is None:
        options = TrimOptions()

    lines = re.split(r"\r\n|\n|\r", lrc_string)

    info: Dict[str, str] = {}
    lyric: List[LyricLine] = []

    for line in lines:
        # Skip genuinely empty lines — they carry no useful information
        # and would be emitted as blank lines by stringify().
        if not line:
            continue
        # Line does not start with "["
        if line[0] != "[":
            lyric.append(LyricLine(text=line))
            continue

        # Now, line starts with "[" — try time tag first
        match = _TIME_TAG_RE.search(line)
        if match is not None:
            mm = int(match.group(1))
            ss = float(match.group(2).replace(":", "."))
            text = line[match.end():]

            lyric.append(LyricLine(
                time=mm * 60 + ss,
                text=text,
            ))
            continue

        # Try info tag
        match = _INFO_TAG_RE.match(line)
        if match is not None:
            value = match.group(2).strip()
            if value == "":
                continue
            info[match.group(1)] = value
            continue

        # Starts with "[" but doesn't match time or info tag
        lyric.append(LyricLine(text=line))

    # Merge consecutive lines with the same timestamp: second line → translation
    merged: List[LyricLine] = []
    i = 0
    while i < len(lyric):
        line = lyric[i]
        # Peek at next line: if same timestamp (and not None) and current line has no translation
        if (
            line.time is not None
            and not line.translation
            and i + 1 < len(lyric)
            and lyric[i + 1].time == line.time
        ):
            line.translation = lyric[i + 1].text
            i += 2
        else:
            i += 1
        merged.append(line)

    # Apply trimming
    if options.trim_start and options.trim_end:
        for line in merged:
            line.text = line.text.strip()
    elif options.trim_start:
        for line in merged:
            line.text = line.text.lstrip()
    elif options.trim_end:
        for line in merged:
            line.text = line.text.rstrip()

    return LrcState(info=info, lyric=merged)


def convert_time_to_tag(time: Optional[float], fixed: Fixed, with_brackets: bool = True) -> str:
    """Convert a time in seconds to an LRC timestamp tag.

    Args:
        time: Time in seconds, or None for empty string.
        fixed: Number of decimal places (0-3).
        with_brackets: Whether to wrap with [].

    Returns:
        Formatted tag like "[01:23.456]" or "01:23.456".
    """
    if time is None:
        return ""

    mm = int(time // 60)
    ss = time % 60

    # Python formatting: pad mm to 2 digits, ss to 2+fixed digits with `fixed` decimal places
    format_str = f"{{:0{2 + fixed + (1 if fixed > 0 else 0)}.{fixed}f}}"
    ss_str = format_str.format(ss)

    result = f"{mm:02d}:{ss_str}"
    return f"[{result}]" if with_brackets else result


def format_text(text: str, space_start: int, space_end: int) -> str:
    """Format lyric text with leading/trailing spaces.

    Args:
        text: Raw text.
        space_start: Number of leading spaces (negative means no change).
        space_end: Number of trailing spaces (negative means no change).

    Returns:
        Formatted text.
    """
    new_text = text
    if space_start >= 0:
        new_text = " " * space_start + new_text.lstrip()
    if space_end >= 0:
        new_text = new_text.rstrip() + " " * space_end
    return new_text


def stringify(state: LrcState, options: FormatOptions) -> str:
    """Convert structured LRC state back to an LRC-formatted string.

    Args:
        state: The parsed LRC state.
        options: Format options for stringification.

    Returns:
        LRC-formatted string.
    """
    # Info tags
    infos = [f"[{name}: {value}]" for name, value in state.info.items()]

    # Lyric lines
    lines = []
    for line in state.lyric:
        if line.time is None:
            # Skip empty lines (defense-in-depth: parse() already filters
            # them, but state could be mutated by external code).
            if line.text:
                lines.append(line.text)
        else:
            text = format_text(line.text, options.space_start, options.space_end)
            tag = convert_time_to_tag(line.time, options.fixed)
            lines.append(f"{tag}{text}")
            # Output translation as a separate line with the same timestamp
            if line.translation:
                trans_text = format_text(line.translation, options.space_start, options.space_end)
                lines.append(f"{tag}{trans_text}")

    return options.end_of_line.join(infos + lines)


def guard(value: float, min_val: float, max_val: float) -> float:
    """Clamp a value between min and max."""
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value
