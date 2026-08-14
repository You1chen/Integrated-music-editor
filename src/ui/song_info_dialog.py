"""Song info dialog — view / edit a single song's metadata and literal lyrics.

Opened from the playlist panel's "…" button.  It shows the song at that
queue position (NOT the currently-playing one) and never touches playback:

  - 元信息 tab: embedded cover (read-only) + editable text tags, saved
    straight into the audio file with mutagen.
  - 歌词 tab: the no-timestamp literal lyrics.  Translations are shown on
    their own line prefixed with "↳ ".  On save, a similarity diff
    (difflib.SequenceMatcher) maps the edited plain text back onto the
    timestamped lines, so lightly-edited lines keep their timestamps and
    their translation pairing.
"""

from __future__ import annotations

import difflib
import os
from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import mutagen
from mutagen.id3 import ID3

from ..core.lrc_parser import (
    LyricLine,
    LrcState,
    TrimOptions,
    parse as parse_lrc,
    stringify as stringify_lrc,
)

if TYPE_CHECKING:
    from .main_window import MainWindow

#: Prefix marking a translation line in the plain (no-timestamp) editor.
_TRAN_MARKER = "↳ "

#: (field_key, label) for the metadata form.
_TEXT_FIELDS: list[tuple[str, str]] = [
    ("title", "歌名"),
    ("artist", "歌手"),
    ("album", "专辑"),
    ("albumartist", "专辑歌手"),
    ("lyricist", "词作者"),
    ("composer", "作曲者"),
    ("year", "年份"),
    ("genre", "流派"),
    ("comment", "备注"),
]


def _lrc_path(audio_path: str) -> str:
    stem = os.path.splitext(audio_path)[0]
    return stem + ".lrc"


# ── Metadata tag helpers (ID3 + VorbisComment) ──────────────────


def _first(lst) -> str:
    try:
        return str(lst[0]) if lst else ""
    except (IndexError, TypeError):
        return ""


def _read_tags(path: str) -> dict[str, str]:
    """Read the 9 editable text fields from an audio file, or empties."""
    result = {key: "" for key, _ in _TEXT_FIELDS}
    try:
        audio = mutagen.File(path)
    except Exception:
        return result
    if audio is None:
        return result
    tags = getattr(audio, "tags", None)
    if tags is None:
        return result

    try:
        if isinstance(tags, ID3):
            result["title"] = _id3_text(tags, "TIT2")
            result["artist"] = _id3_text(tags, "TPE1")
            result["album"] = _id3_text(tags, "TALB")
            result["albumartist"] = _id3_text(tags, "TPE2")
            result["lyricist"] = _id3_text(tags, "TEXT")
            result["composer"] = _id3_text(tags, "TCOM")
            result["year"] = _id3_text(tags, "TDRC") or _id3_text(tags, "TYER")
            result["genre"] = _id3_text(tags, "TCON")
            result["comment"] = _id3_comment(tags)
        else:
            result["title"] = _first(tags.get("title"))
            result["artist"] = _first(tags.get("artist"))
            result["album"] = _first(tags.get("album"))
            result["albumartist"] = _first(tags.get("albumartist"))
            result["lyricist"] = _first(tags.get("lyricist"))
            result["composer"] = _first(tags.get("composer"))
            result["year"] = _first(tags.get("date")) or _first(tags.get("year"))
            result["genre"] = _first(tags.get("genre"))
            result["comment"] = _first(tags.get("comment")) or _first(tags.get("description"))
    except Exception:
        pass
    return result


def _id3_text(tags: ID3, frame_name: str) -> str:
    try:
        frame = tags.get(frame_name)
        if frame is not None and frame.text:
            return str(frame.text[0])
    except Exception:
        pass
    return ""


def _id3_comment(tags: ID3) -> str:
    try:
        for key in tags:
            if key.startswith("COMM"):
                frame = tags[key]
                if frame.text:
                    return str(frame.text[0])
    except Exception:
        pass
    return ""


def _write_tags(path: str, values: dict[str, str]) -> bool:
    """Write the 9 text fields back into the audio file.  Returns success."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return False
    if audio is None:
        return False

    tags = getattr(audio, "tags", None)
    if tags is None:
        try:
            audio.add_tags()
            tags = audio.tags
        except Exception:
            return False

    try:
        if isinstance(tags, ID3):
            from mutagen.id3 import (
                COMM, TALB, TCOM, TCON, TDRC, TEXT, TIT2, TPE1, TPE2, TYER,
            )
            _set_id3(tags, TIT2, values["title"])
            _set_id3(tags, TPE1, values["artist"])
            _set_id3(tags, TALB, values["album"])
            _set_id3(tags, TPE2, values["albumartist"])
            _set_id3(tags, TEXT, values["lyricist"])
            _set_id3(tags, TCOM, values["composer"])
            tags.delall("TDRC")
            tags.delall("TYER")
            year = values["year"].strip()
            if year:
                try:
                    tags.add(TDRC(encoding=3, text=year))
                except Exception:
                    tags.add(TYER(encoding=3, text=year))
            _set_id3(tags, TCON, values["genre"])
            tags.delall("COMM")
            comment = values["comment"].strip()
            if comment:
                tags.add(COMM(encoding=3, lang="zho", desc="", text=comment))
        else:
            _set_vc(tags, "title", values["title"])
            _set_vc(tags, "artist", values["artist"])
            _set_vc(tags, "album", values["album"])
            _set_vc(tags, "albumartist", values["albumartist"])
            _set_vc(tags, "lyricist", values["lyricist"])
            _set_vc(tags, "composer", values["composer"])
            _set_vc(tags, "date", values["year"])
            _set_vc(tags, "genre", values["genre"])
            _set_vc(tags, "comment", values["comment"])
        audio.save()
        return True
    except Exception:
        return False


def _set_id3(tags: ID3, frame_cls, value: str) -> None:
    value = value.strip()
    tags.delall(frame_cls.__name__)
    if value:
        tags.add(frame_cls(encoding=3, text=value))


def _set_vc(tags, key: str, value: str) -> None:
    value = value.strip()
    if key in tags:
        del tags[key]
    if value:
        tags[key] = value


def _read_cover(path: str) -> Optional[QPixmap]:
    """Extract the embedded cover as a QPixmap, or None."""
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None:
        return None
    tags = getattr(audio, "tags", None)
    data = None
    if isinstance(tags, ID3):
        apic = tags.getall("APIC")
        if apic:
            data = apic[0].data
    else:
        pics = getattr(audio, "pictures", None)
        if pics:
            data = pics[0].data
    if not data:
        return None
    pix = QPixmap()
    if pix.loadFromData(data):
        return pix
    return None


# ── Literal lyrics ↔ timestamped lines ──────────────────────────


def _is_trans_line(text: str) -> bool:
    return text.startswith(_TRAN_MARKER)


def _strip_trans_line(text: str) -> str:
    return text[len(_TRAN_MARKER):] if _is_trans_line(text) else text


def _build_plain(state: LrcState) -> tuple[list[str], list[tuple[Optional[float], bool]]]:
    """Flatten lyric lines into plain text + per-line (time, is_translation)."""
    lines: list[str] = []
    meta: list[tuple[Optional[float], bool]] = []
    for ln in state.lyric:
        lines.append(ln.text)
        meta.append((ln.time, False))
        if ln.translation:
            lines.append(_TRAN_MARKER + ln.translation)
            meta.append((ln.time, True))
    return lines, meta


def _apply_lyric_edit(
    old_lines: list[str],
    old_meta: list[tuple[Optional[float], bool]],
    new_text: str,
) -> list[LyricLine]:
    """Map edited plain text back onto timestamped lyrics via a diff."""
    new_lines = [ln.rstrip("\r") for ln in new_text.split("\n")]
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)

    # (time, text, is_translation)
    flat: list[tuple[Optional[float], str, bool]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i in range(i1, i2):
                time, _ = old_meta[i]
                text = new_lines[j1 + (i - i1)]
                flat.append((time, text, _is_trans_line(text)))
        elif tag == "replace":
            k = min(i2 - i1, j2 - j1)
            for t in range(k):
                time, _ = old_meta[i1 + t]
                text = new_lines[j1 + t]
                flat.append((time, text, _is_trans_line(text)))
            for t in range(k, j2 - j1):
                text = new_lines[j1 + t]
                flat.append((None, text, _is_trans_line(text)))
        elif tag == "insert":
            for t in range(j1, j2):
                text = new_lines[t]
                flat.append((None, text, _is_trans_line(text)))
        # "delete": dropped

    # Re-pair translations onto the preceding original line.
    lyric: list[LyricLine] = []
    for time, text, is_trans in flat:
        if is_trans:
            if lyric:
                lyric[-1].translation = _strip_trans_line(text)
            else:
                lyric.append(LyricLine(time=None, text="", translation=_strip_trans_line(text)))
        else:
            lyric.append(LyricLine(time=time, text=text))
    return lyric


class SongInfoDialog(QDialog):
    """View / edit one song's metadata and literal lyrics."""

    def __init__(self, main_window: "MainWindow", song: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mw = main_window
        self._path = song.get("path", "")

        title = song.get("title") or os.path.splitext(os.path.basename(self._path))[0]
        self.setWindowTitle(title)
        self.resize(520, 560)
        self.setMinimumSize(440, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, stretch=1)

        # ── Tab 1: metadata ──
        meta_tab = QWidget()
        meta_layout = QVBoxLayout(meta_tab)
        meta_layout.setContentsMargins(12, 12, 12, 12)
        meta_layout.setSpacing(10)

        cover_row = QHBoxLayout()
        self._cover_label = QLabel("无封面")
        self._cover_label.setFixedSize(120, 120)
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover_label.setStyleSheet(
            "border: 1px solid gray; border-radius: 6px; font-size: 12px; color: gray;"
        )
        cover_row.addWidget(self._cover_label)
        cover_row.addStretch()
        meta_layout.addLayout(cover_row)

        form = QFormLayout()
        form.setSpacing(6)
        self._inputs: dict[str, QLineEdit] = {}
        for key, label in _TEXT_FIELDS:
            inp = QLineEdit()
            form.addRow(f"{label}:", inp)
            self._inputs[key] = inp
        meta_layout.addLayout(form)

        meta_save = QPushButton("保存元信息")
        meta_save.clicked.connect(self._on_save_meta)
        meta_layout.addWidget(meta_save)
        meta_layout.addStretch()
        self._tabs.addTab(meta_tab, "元信息")

        # ── Tab 2: literal lyrics ──
        lyric_tab = QWidget()
        lyric_layout = QVBoxLayout(lyric_tab)
        lyric_layout.setContentsMargins(12, 12, 12, 12)
        lyric_layout.setSpacing(8)

        hint = QLabel("无时间戳歌词 · 翻译行以「↳ 」开头")
        hint.setStyleSheet("font-size: 12px; color: gray;")
        lyric_layout.addWidget(hint)

        self._lyric_edit = QPlainTextEdit()
        self._lyric_edit.setPlaceholderText("暂无歌词")
        lyric_layout.addWidget(self._lyric_edit, stretch=1)

        lyric_save = QPushButton("保存歌词")
        lyric_save.clicked.connect(self._on_save_lyric)
        lyric_layout.addWidget(lyric_save)
        self._tabs.addTab(lyric_tab, "歌词")

        # ── Load ──
        self._load()

    # ── Load ────────────────────────────────────────────────

    def _load(self) -> None:
        # Cover
        pix = _read_cover(self._path)
        if pix is not None:
            self._cover_label.setPixmap(
                pix.scaled(
                    118, 118,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self._cover_label.setText("")

        # Metadata
        for key, value in _read_tags(self._path).items():
            self._inputs[key].setText(value)

        # Lyrics
        lrc_path = _lrc_path(self._path)
        if os.path.isfile(lrc_path):
            try:
                with open(lrc_path, "r", encoding="utf-8") as f:
                    text = f.read()
                state = parse_lrc(text, TrimOptions())
                self._info = dict(state.info)
                self._old_lines, self._old_meta = _build_plain(state)
            except Exception:
                self._info, self._old_lines, self._old_meta = {}, [], []
        else:
            self._info, self._old_lines, self._old_meta = {}, [], []
        self._lyric_edit.setPlainText("\n".join(self._old_lines))

    # ── Save ────────────────────────────────────────────────

    def _on_save_meta(self) -> None:
        values = {key: inp.text() for key, inp in self._inputs.items()}
        if not os.path.isfile(self._path):
            print("音频文件不存在")
            return
        if _write_tags(self._path, values):
            print("元信息已保存")
            self._refresh_playlist()
            self._invalidate_meta_page()
        else:
            print("元信息保存失败")

    def _on_save_lyric(self) -> None:
        if not self._path:
            return
        lyric = _apply_lyric_edit(
            self._old_lines, self._old_meta, self._lyric_edit.toPlainText()
        )
        state = LrcState(info=dict(self._info), lyric=lyric)
        text = stringify_lrc(state, self._mw.format_options)
        lrc_path = _lrc_path(self._path)
        try:
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError as e:
            print(f"歌词保存失败：{e}")
            return
        print("歌词已保存")
        self._refresh_playlist()

        # If this song is the one currently loaded, refresh the in-memory
        # lyrics so the home axis stays in sync (no playback disruption).
        if _norm(self._mw.audio_manager.local_path) == _norm(self._path):
            self._mw.lrc_state.init_from_text(text, self._mw.trim_options)

    # ── Best-effort refresh ─────────────────────────────────

    def _refresh_playlist(self) -> None:
        try:
            from ..core.constants import PageRoute
            page = self._mw.content_stack._pages.get(PageRoute.PLAYLIST)
            if page is not None and hasattr(page, "refresh_song"):
                page.refresh_song(self._path)
        except Exception:
            pass

    def _invalidate_meta_page(self) -> None:
        try:
            from ..core.constants import PageRoute
            page = self._mw.content_stack._pages.get(PageRoute.META_EDITOR)
            if page is not None and hasattr(page, "_last_audio_path"):
                page._last_audio_path = ""
        except Exception:
            pass


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))
