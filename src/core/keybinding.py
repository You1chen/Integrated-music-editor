"""Keyboard binding system — replaces keybindings.ts + default-keybindings.ts.

Supports configurable key-to-action mappings with Qt.Key codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from .constants import InputAction


@dataclass
class KeyBinding:
    """A single keyboard shortcut definition.

    Uses either a physical key code (Qt.Key) or a character key (str).
    Matches the KeyBinding interface from the web app.
    """
    code: Optional[Qt.Key] = None
    key: Optional[str] = None
    ctrl_key: bool = False
    shift_key: bool = False
    alt_key: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        result: Dict[str, Any] = {"ctrl": self.ctrl_key, "shift": self.shift_key, "alt": self.alt_key}
        if self.code is not None:
            result["code"] = int(self.code)
        if self.key is not None:
            result["key"] = self.key
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KeyBinding":
        """Deserialize from a JSON-friendly dict."""
        return cls(
            code=Qt.Key(data["code"]) if "code" in data else None,
            key=data.get("key"),
            ctrl_key=data.get("ctrl", False),
            shift_key=data.get("shift", False),
            alt_key=data.get("alt", False),
        )


# ── Action Labels (Chinese) ──────────────────────────────────

ACTION_LABELS: Dict[InputAction, str] = {
    # Synchronizer actions
    InputAction.SYNC: "打时间戳",
    InputAction.DELETE_TIME: "删除时间戳",
    InputAction.RESET_OFFSET: "重置偏移",
    InputAction.DECREASE_OFFSET: "减少偏移",
    InputAction.INCREASE_OFFSET: "增加偏移",
    InputAction.PREV_LINE: "上一行",
    InputAction.NEXT_LINE: "下一行",
    InputAction.FIRST_LINE: "首行",
    InputAction.LAST_LINE: "末行",
    InputAction.PAGE_UP: "上翻页",
    InputAction.PAGE_DOWN: "下翻页",
    # Audio control actions
    InputAction.SEEK_BACKWARD: "后退 5 秒",
    InputAction.SEEK_FORWARD: "前进 5 秒",
    InputAction.RESET_RATE: "重置播放速率",
    InputAction.INCREASE_RATE: "加速",
    InputAction.DECREASE_RATE: "减速",
    InputAction.TOGGLE_PLAY: "切换播放/暂停",
    # Lyric editing actions
    InputAction.COPY_LINE: "复制歌词行",
    InputAction.SPLIT_LYRIC: "拆分歌词行",
    InputAction.DELETE_LINES: "删除选中行",
    InputAction.MERGE_LINES: "合并选中行",
    InputAction.SELECT_ALL: "全选",
    # Toolbar actions
    InputAction.SAVE: "保存",
    InputAction.EXPORT: "导出/另存",
    InputAction.TRANSLATE: "翻译模式",
    # Global actions
    InputAction.SHOW_HELP: "显示帮助",
    InputAction.UNDO: "撤销",
    InputAction.REDO: "重做",
}

# Ordered groups for display
ACTION_GROUPS: List[Tuple[str, List[InputAction]]] = [
    ("打轴", [
        InputAction.SYNC, InputAction.DELETE_TIME,
        InputAction.PREV_LINE, InputAction.NEXT_LINE,
        InputAction.FIRST_LINE, InputAction.LAST_LINE,
        InputAction.PAGE_UP, InputAction.PAGE_DOWN,
        InputAction.RESET_OFFSET, InputAction.DECREASE_OFFSET,
        InputAction.INCREASE_OFFSET,
    ]),
    ("歌词编辑", [
        InputAction.COPY_LINE, InputAction.SPLIT_LYRIC,
        InputAction.DELETE_LINES, InputAction.MERGE_LINES,
        InputAction.SELECT_ALL,
    ]),
    ("工具栏", [
        InputAction.SAVE, InputAction.EXPORT,
        InputAction.TRANSLATE,
    ]),
    ("音频控制", [
        InputAction.TOGGLE_PLAY, InputAction.SEEK_BACKWARD,
        InputAction.SEEK_FORWARD, InputAction.RESET_RATE,
        InputAction.INCREASE_RATE, InputAction.DECREASE_RATE,
    ]),
    ("通用", [
        InputAction.UNDO, InputAction.REDO,
        InputAction.SHOW_HELP,
    ]),
]


# ── Key name helpers ─────────────────────────────────────────

def _key_name(code: Qt.Key) -> str:
    """Convert a Qt.Key to a human-readable string."""
    # Build a reverse map lazily
    _MAP: Dict[int, str] = {}
    if not _MAP:
        for name in dir(Qt.Key):
            if name.startswith("Key_"):
                val = getattr(Qt.Key, name)
                if isinstance(val, Qt.Key):
                    _MAP[int(val)] = name[4:]  # strip "Key_"
    return _MAP.get(int(code), f"Key({int(code)})")


def keybinding_to_string(binding: KeyBinding) -> str:
    """Format a KeyBinding as a human-readable string like 'Ctrl+Shift+S'."""
    parts: List[str] = []
    if binding.ctrl_key:
        parts.append("Ctrl")
    if binding.shift_key:
        parts.append("Shift")
    if binding.alt_key:
        parts.append("Alt")
    if binding.code is not None:
        name = _key_name(binding.code)
        # Shorten common names
        shorten: Dict[str, str] = {
            "Space": "Space", "Backspace": "Backspace", "Delete": "Delete",
            "Return": "Enter", "Escape": "Esc", "PageUp": "PageUp",
            "PageDown": "PageDown", "Home": "Home", "End": "End",
            "Up": "↑", "Down": "↓", "Left": "←", "Right": "→",
            "Minus": "-", "Equal": "=",
        }
        parts.append(shorten.get(name, name))
    elif binding.key is not None:
        parts.append(binding.key)
    return "+".join(parts) if parts else "(未设置)"


def action_to_string(action: InputAction) -> str:
    """Format an action's bindings as a human-readable string."""
    bindings = DEFAULT_BINDINGS.get(action, [])
    if not bindings:
        return "(无)"
    return " / ".join(keybinding_to_string(b) for b in bindings)


# ── Default Key Bindings ────────────────────────────────────

DEFAULT_BINDINGS: Dict[InputAction, List[KeyBinding]] = {
    # Synchronizer actions
    InputAction.SYNC: [
        KeyBinding(code=Qt.Key.Key_Space),
    ],
    InputAction.DELETE_TIME: [
        KeyBinding(code=Qt.Key.Key_Backspace),
    ],
    InputAction.RESET_OFFSET: [
        KeyBinding(code=Qt.Key.Key_0),
    ],
    InputAction.DECREASE_OFFSET: [
        KeyBinding(code=Qt.Key.Key_Minus),
    ],
    InputAction.INCREASE_OFFSET: [
        KeyBinding(code=Qt.Key.Key_Equal),
    ],
    InputAction.PREV_LINE: [
        KeyBinding(code=Qt.Key.Key_Up),
        KeyBinding(code=Qt.Key.Key_W),
        KeyBinding(code=Qt.Key.Key_J),
    ],
    InputAction.NEXT_LINE: [
        KeyBinding(code=Qt.Key.Key_Down),
        KeyBinding(code=Qt.Key.Key_S),
        KeyBinding(code=Qt.Key.Key_K),
    ],
    InputAction.FIRST_LINE: [
        KeyBinding(code=Qt.Key.Key_Home),
    ],
    InputAction.LAST_LINE: [
        KeyBinding(code=Qt.Key.Key_End),
    ],
    InputAction.PAGE_UP: [
        KeyBinding(code=Qt.Key.Key_PageUp),
    ],
    InputAction.PAGE_DOWN: [
        KeyBinding(code=Qt.Key.Key_PageDown),
    ],

    # Audio control actions
    InputAction.SEEK_BACKWARD: [
        KeyBinding(code=Qt.Key.Key_Left),
        KeyBinding(code=Qt.Key.Key_A),
        KeyBinding(code=Qt.Key.Key_H),
    ],
    InputAction.SEEK_FORWARD: [
        KeyBinding(code=Qt.Key.Key_Right),
        KeyBinding(code=Qt.Key.Key_D),
        KeyBinding(code=Qt.Key.Key_L),
    ],
    InputAction.RESET_RATE: [
        KeyBinding(code=Qt.Key.Key_R),
    ],
    InputAction.INCREASE_RATE: [
        KeyBinding(code=Qt.Key.Key_Up, ctrl_key=True),
        KeyBinding(code=Qt.Key.Key_J, ctrl_key=True),
    ],
    InputAction.DECREASE_RATE: [
        KeyBinding(code=Qt.Key.Key_Down, ctrl_key=True),
        KeyBinding(code=Qt.Key.Key_K, ctrl_key=True),
    ],
    InputAction.TOGGLE_PLAY: [
        KeyBinding(code=Qt.Key.Key_Return, ctrl_key=True),
    ],

    # Lyric editing actions
    InputAction.COPY_LINE: [
        KeyBinding(code=Qt.Key.Key_C, ctrl_key=True),
    ],
    InputAction.SPLIT_LYRIC: [
        KeyBinding(code=Qt.Key.Key_D, ctrl_key=True),
    ],
    InputAction.DELETE_LINES: [
        KeyBinding(code=Qt.Key.Key_Delete),
    ],
    InputAction.MERGE_LINES: [
        KeyBinding(code=Qt.Key.Key_H, ctrl_key=True),
    ],
    InputAction.SELECT_ALL: [
        KeyBinding(code=Qt.Key.Key_A, ctrl_key=True),
    ],

    # Toolbar actions
    InputAction.SAVE: [
        KeyBinding(code=Qt.Key.Key_S, ctrl_key=True),
    ],
    InputAction.EXPORT: [
        KeyBinding(code=Qt.Key.Key_S, ctrl_key=True, shift_key=True),
    ],
    InputAction.TRANSLATE: [
        KeyBinding(code=Qt.Key.Key_T, ctrl_key=True),
    ],

    # Global actions
    InputAction.SHOW_HELP: [
        KeyBinding(key="?"),
    ],
    InputAction.UNDO: [
        KeyBinding(code=Qt.Key.Key_Z, ctrl_key=True),
    ],
    InputAction.REDO: [
        KeyBinding(code=Qt.Key.Key_Y, ctrl_key=True),
    ],
}


class KeyBindingManager:
    """Manages keyboard shortcut detection and matching.

    Supports user-customizable bindings on top of defaults.
    """

    def __init__(
        self,
        user_overrides: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> None:
        self._user_overrides: Dict[InputAction, List[KeyBinding]] = {}
        if user_overrides:
            for action_str, binding_dicts in user_overrides.items():
                try:
                    action = InputAction(action_str)
                    self._user_overrides[action] = [
                        KeyBinding.from_dict(d) for d in binding_dicts
                    ]
                except ValueError:
                    pass  # Unknown action, ignore

    @property
    def bindings(self) -> Dict[InputAction, List[KeyBinding]]:
        """Effective bindings: user overrides merged with defaults."""
        result = dict(DEFAULT_BINDINGS)
        result.update(self._user_overrides)
        return result

    @property
    def user_overrides(self) -> Dict[str, List[Dict[str, Any]]]:
        """Serializable user overrides for persistence."""
        return {
            action.value: [b.to_dict() for b in bindings]
            for action, bindings in self._user_overrides.items()
        }

    def get_matched_action(self, event: QKeyEvent) -> Optional[InputAction]:
        """Find the InputAction matching a QKeyEvent.

        Checks user overrides first, then defaults.
        """
        effective = self.bindings
        for action, bindings in effective.items():
            if self._match_key_binding(event, bindings):
                return action
        return None

    def set_user_binding(self, action: InputAction, bindings: List[KeyBinding]) -> None:
        """Set a custom binding for an action (overrides default)."""
        self._user_overrides[action] = bindings

    def reset_user_binding(self, action: InputAction) -> None:
        """Remove custom binding, reverting to default."""
        self._user_overrides.pop(action, None)

    def reset_all(self) -> None:
        """Clear all user overrides."""
        self._user_overrides.clear()

    def _match_key_binding(self, event: QKeyEvent, bindings: List[KeyBinding]) -> bool:
        """Check if a QKeyEvent matches any KeyBinding in a list.

        Ports matchKeyBinding from keybindings.ts:
        - Ctrl/Cmd: strict bidirectional check
        - Shift/Alt: only require if binding specifies, allow extras otherwise
        - Key match: checks Qt.Key code first, then character key string
        """
        for binding in bindings:
            # Ctrl modifier: strict bidirectional (prevents conflicts with browser
            # shortcuts, same rationale applies in desktop app)
            event_ctrl = (
                event.modifiers() & Qt.KeyboardModifier.ControlModifier
            ) == Qt.KeyboardModifier.ControlModifier
            event_meta = (
                event.modifiers() & Qt.KeyboardModifier.MetaModifier
            ) == Qt.KeyboardModifier.MetaModifier
            ctrl_or_meta = event_ctrl or event_meta

            if binding.ctrl_key and not ctrl_or_meta:
                continue
            if not binding.ctrl_key and ctrl_or_meta:
                continue

            # Shift: when binding uses Ctrl, check bidirectionally so that
            # Ctrl+Shift+X doesn't accidentally match a Ctrl+X binding.
            # When binding doesn't use Ctrl, keep permissive (allow extra Shift).
            event_shift = (
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            ) == Qt.KeyboardModifier.ShiftModifier
            if binding.ctrl_key:
                if binding.shift_key != event_shift:
                    continue
            elif binding.shift_key and not event_shift:
                continue

            # Alt: require exact match if binding specifies
            if binding.alt_key:
                event_alt = (
                    event.modifiers() & Qt.KeyboardModifier.AltModifier
                ) == Qt.KeyboardModifier.AltModifier
                if not event_alt:
                    continue

            # Check key: code (Qt.Key) first, then character key
            if binding.code is not None and event.key() == binding.code:
                return True
            if binding.key is not None and event.text() == binding.key:
                return True

        return False
