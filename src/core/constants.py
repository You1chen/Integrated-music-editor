"""Application-wide constants, enums, and storage keys."""

from enum import IntEnum, StrEnum


# ── Storage Keys ──────────────────────────────────────────────

class LocalKey:
    """Keys for persistent storage (localStorage equivalent)."""
    TOKEN = "token"
    GIST_ID = "gistId"
    GIST_ETAG = "gistEtag"
    GIST_FILE = "gistFile"
    LYRIC = "lyric"
    PREFERENCES = "preferences"


class SessionKey:
    """Keys for session-scoped storage (sessionStorage equivalent)."""
    AUDIO_SRC = "audioSrc"
    EDITOR_DETAILS_OPEN = "editorDetailsOpen"
    SYNC_MODE = "syncMode"
    SELECT_INDEX = "selectIndex"
    RATELIMIT = "ratelimit"


# ── LRC Action Types ──────────────────────────────────────────

class ActionType(IntEnum):
    """Action types for LRC state reducer (mirrors useLrc.ts ActionType)."""
    PARSE = 0
    REFRESH = 1
    NEXT = 2
    TIME = 3
    INFO = 4
    SELECT = 5
    DELETE_TIME = 6
    GET_STATE = 7


# ── Input Actions (Keyboard Bindings) ─────────────────────────

class InputAction(StrEnum):
    """All bindable actions, identical to web app's InputAction enum."""
    # Synchronizer actions
    SYNC = "sync"
    DELETE_TIME = "deleteTime"
    RESET_OFFSET = "resetOffset"
    DECREASE_OFFSET = "decreaseOffset"
    INCREASE_OFFSET = "increaseOffset"
    PREV_LINE = "prevLine"
    NEXT_LINE = "nextLine"
    FIRST_LINE = "firstLine"
    LAST_LINE = "lastLine"
    PAGE_UP = "pageUp"
    PAGE_DOWN = "pageDown"

    # Audio control actions
    SEEK_BACKWARD = "seekBackward"
    SEEK_FORWARD = "seekForward"
    RESET_RATE = "resetRate"
    INCREASE_RATE = "increaseRate"
    DECREASE_RATE = "decreaseRate"
    TOGGLE_PLAY = "togglePlay"
    PREV_SONG = "prevSong"
    NEXT_SONG = "nextSong"

    # Lyric editing actions
    COPY_LINE = "copyLine"
    SPLIT_LYRIC = "splitLyric"
    DELETE_LINES = "deleteLines"
    MERGE_LINES = "mergeLines"
    SELECT_ALL = "selectAll"

    # Toolbar actions
    SAVE = "save"
    EXPORT = "export"
    TRANSLATE = "translate"

    # Global
    SHOW_HELP = "showHelp"
    UNDO = "undo"
    REDO = "redo"


# ── Playback Mode ────────────────────────────────────────────

class PlayMode(IntEnum):
    """Playback modes for the play queue."""
    SINGLE = 0        # 单次播放 — 播完当前一首即停
    SEQUENTIAL = 1    # 顺序播放 — 从列表头到尾播完即停
    LOOP = 2          # 循环播放 — 列表播完从头再来
    SINGLE_LOOP = 3   # 单曲循环 — 单曲反复播放直到用户暂停
    SHUFFLE = 4       # 随机播放 — 每首播完随机选下一首


PLAY_MODE_LABELS = {
    PlayMode.SINGLE: "单次播放",
    PlayMode.SEQUENTIAL: "顺序播放",
    PlayMode.LOOP: "循环播放",
    PlayMode.SINGLE_LOOP: "单曲循环",
    PlayMode.SHUFFLE: "随机播放",
}

PLAY_MODE_ORDER = [
    PlayMode.SINGLE,
    PlayMode.SEQUENTIAL,
    PlayMode.LOOP,
    PlayMode.SINGLE_LOOP,
    PlayMode.SHUFFLE,
]


# ── Audio State ───────────────────────────────────────────────

class AudioStateType(IntEnum):
    """Types of audio state changes (mirrors AudioActionType)."""
    PAUSE = 0
    GET_DURATION = 1
    RATE_CHANGE = 2


# ── Sync Mode ─────────────────────────────────────────────────

class SyncMode(IntEnum):
    """Synchronizer display mode."""
    SELECT = 0
    HIGHLIGHT = 1


# ── Theme Mode ────────────────────────────────────────────────

class ThemeMode(IntEnum):
    """Color theme mode."""
    AUTO = 0
    LIGHT = 1
    DARK = 2


# ── Page Routes ───────────────────────────────────────────────

class PageRoute:
    """Page indices in the QStackedWidget."""
    HOME = 0
    EDITOR = 1
    SYNCHRONIZER = 2
    PREFERENCES = 3
    META_EDITOR = 4
    PLAYLIST = 5
