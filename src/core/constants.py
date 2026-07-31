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
    PREVIEW = "preview"
    LOAD_AUDIO = "loadAudio"

    # Global
    SHOW_HELP = "showHelp"
    UNDO = "undo"
    REDO = "redo"


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
