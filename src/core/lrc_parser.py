"""Pure Python port of @lrc-maker/lrc-parser.

Parses LRC (Lyrics) formatted text into structured data and vice versa.

Translation convention
---------------------
**Every** timestamped line (body / original-text line) ends with
exactly 4 spaces.  A line that does *not* end with 4 spaces and
shares its timestamp with the previous (marked) line is a translation.

Music players ignore trailing whitespace, so the marker is invisible
during playback — it exists purely as a semantic signal for this parser.

:func:`is_translation_marker` is the single function that encodes this
rule.  Every piece of code that needs to know whether a line is a body
or a translation should call this function rather than hard-coding a
space-count check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

Fixed = Literal[0, 1, 2, 3]

# Regex patterns — identical to the TypeScript version
_TIME_TAG_RE = re.compile(r"\[\s*(\d{1,3}):(\d{1,2}(?:[:.]\d{1,3})?)\s*]")
_INFO_TAG_RE = re.compile(r"\[\s*(\w{1,6})\s*:(.*?)]")

# ── Translation marker ──────────────────────────────────────

_TRANSLATION_MARKER = "    "  # exactly 4 spaces


def is_translation_marker(text: str) -> bool:
    """Return ``True`` when *text* ends with the translation marker.

    A lyric line whose text ends with 4 spaces owns the **next**
    timestamped line as its translation.  Call this function from
    anywhere that needs to make that determination — do not inline
    a ``.endswith("    ")`` check.
    """
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
    """Parse an LRC-formatted string into structured state.

    **Translation detection** (two-pass):

    1. Every line is parsed normally — timestamps, info tags, untimed text.
    2. A second pass classifies each timestamped line:
       - Has 4 trailing spaces → **body line** (strip marker).
         If the *next* line shares the same timestamp but has *no* marker,
         it is merged as this line's translation.
       - No trailing spaces → **translation** of the previous body line
         (if timestamps match and the previous line is marked).  Legacy
         files (no markers anywhere) are handled correctly: unmarked
         lines simply stay as independent body lines.

    Music players ignore trailing whitespace, so the markers are invisible
    during normal playback.
    """
    if options is None:
        options = TrimOptions()

    lines = re.split(r"\r\n|\n|\r", lrc_string)

    info: Dict[str, str] = {}
    lyric: List[LyricLine] = []

    # ── Pass 1: parse every line ────────────────────────────
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

            # Info tag (only at column 0)
            if indent == 0:
                match = _INFO_TAG_RE.match(line)
                if match is not None:
                    value = match.group(2).strip()
                    if value:
                        info[match.group(1)] = value
                    continue

            # "[" but not timestamp or info → untimed text
            lyric.append(LyricLine(text=line))
            continue

        # Untimed text
        lyric.append(LyricLine(text=line))

    # ── Pass 2: classify body vs translation via 4-space marker ──
    # Rule: every body line ends with 4 spaces.  A line without the
    # marker that shares a timestamp with the preceding body is a
    # translation.  Legacy files (no markers at all) fall through
    # harmlessly — unmarked lines simply stay as independent bodies.
    i = 0
    while i < len(lyric):
        line = lyric[i]
        if line.time is None:
            i += 1
            continue

        if is_translation_marker(line.text):
            # ── Body line ──
            line.text = line.text.rstrip()  # clean the marker
            # Is the next line a translation of this one?
            if (
                i + 1 < len(lyric)
                and lyric[i + 1].time is not None
                and lyric[i + 1].time == line.time
                and not is_translation_marker(lyric[i + 1].text)
            ):
                line.translation = lyric[i + 1].text
                del lyric[i + 1]
        else:
            # ── No marker: translation of the previous body? ──
            if (
                i > 0
                and lyric[i - 1].time is not None
                and line.time == lyric[i - 1].time
                and is_translation_marker(lyric[i - 1].text)
            ):
                lyric[i - 1].translation = line.text
                del lyric[i]
                i -= 1  # reprocess the (now-merged) body line
        i += 1

    # ── Apply trimming ──────────────────────────────────────
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

    Every timestamped (body) line is suffixed with 4 spaces so that
    :func:`parse` can distinguish body lines from translations:
    a line with the marker is a body; one without is a translation.
    """
    # Info tags
    infos = [f"[{name}: {value}]" for name, value in state.info.items()]

    # Lyric lines
    lines = []
    for line in state.lyric:
        if line.time is None:
            if line.text:
                lines.append(line.text)
        else:
            text = format_text(line.text, options.space_start, options.space_end)
            tag = convert_time_to_tag(line.time, options.fixed)

            # Every body line carries the 4-space marker
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
