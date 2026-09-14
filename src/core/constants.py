"""Application-wide constants, enums, and storage keys."""

from enum import IntEnum, StrEnum


class LocalKey:
    """Keys for persistent storage."""
    TOKEN = "token"
    GIST_ID = "gistId"
    GIST_ETAG = "gistEtag"
    GIST_FILE = "gistFile"
    LYRIC = "lyric"
    PREFERENCES = "preferences"


class SessionKey:
    """Keys for session-scoped in-memory storage."""
    AUDIO_SRC = "audioSrc"
    EDITOR_DETAILS_OPEN = "editorDetailsOpen"
    SYNC_MODE = "syncMode"
    SELECT_INDEX = "selectIndex"
    RATELIMIT = "ratelimit"


class ActionType(IntEnum):
    """Action types for the LRC state reducer."""
    PARSE = 0
    REFRESH = 1
    NEXT = 2
    TIME = 3
    INFO = 4
    SELECT = 5
    DELETE_TIME = 6
    GET_STATE = 7


class InputAction(StrEnum):
    """All bindable keyboard actions."""
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

    SEEK_BACKWARD = "seekBackward"
    SEEK_FORWARD = "seekForward"
    RESET_RATE = "resetRate"
    INCREASE_RATE = "increaseRate"
    DECREASE_RATE = "decreaseRate"
    TOGGLE_PLAY = "togglePlay"
    PREV_SONG = "prevSong"
    NEXT_SONG = "nextSong"

    COPY_LINE = "copyLine"
    SPLIT_LYRIC = "splitLyric"
    DELETE_LINES = "deleteLines"
    MERGE_LINES = "mergeLines"
    SELECT_ALL = "selectAll"

    SAVE = "save"
    EXPORT = "export"
    TRANSLATE = "translate"

    SHOW_HELP = "showHelp"
    UNDO = "undo"
    REDO = "redo"


class PlayMode(IntEnum):
    """Playback modes for the play queue."""
    SINGLE = 0
    SEQUENTIAL = 1
    LOOP = 2
    SINGLE_LOOP = 3
    SHUFFLE = 4


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


class AudioStateType(IntEnum):
    """Types of audio state changes."""
    PAUSE = 0
    GET_DURATION = 1
    RATE_CHANGE = 2


class SyncMode(IntEnum):
    """Synchronizer display mode."""
    SELECT = 0
    HIGHLIGHT = 1


class ThemeMode(IntEnum):
    AUTO = 0
    LIGHT = 1
    DARK = 2


class PageRoute:
    """Page indices in the QStackedWidget."""
    HOME = 0
    EDITOR = 1
    SYNCHRONIZER = 2
    PREFERENCES = 3
    META_EDITOR = 4
    PLAYLIST = 5
