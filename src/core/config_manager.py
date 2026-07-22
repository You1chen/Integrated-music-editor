"""Configuration manager — JSON-based persistence (replaces localStorage/sessionStorage)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from PyQt6.QtCore import QStandardPaths

from .crypto_utils import encrypt, decrypt


class ConfigManager:
    """Manages persistent and session-scoped application configuration.

    Persistent data is stored as JSON files in the app's data directory.
    Session data lives in memory only (like sessionStorage).
    """

    APP_NAME = "lrc-maker"

    def __init__(self) -> None:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._data_dir = os.path.join(base, self.APP_NAME)
        os.makedirs(self._data_dir, exist_ok=True)

        self._config_path = os.path.join(self._data_dir, "config.json")
        self._lyric_path = os.path.join(self._data_dir, "lyric.txt")
        self._session_registry_path = os.path.join(
            self._data_dir, "session_drafts.txt"
        )

        # Lazy-loaded persistent cache
        self._config: Optional[Dict[str, Any]] = None

        # Session storage (in-memory only)
        self._session: Dict[str, Any] = {}

    # ── Helpers ────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        if self._config is not None:
            return self._config
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._config = {}
        return self._config

    def _save_config(self) -> None:
        if self._config is not None:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)

    # ── Persistent: Preferences ────────────────────────────

    def get_preferences(self) -> Dict[str, Any]:
        return self._load_config().get("preferences", {})

    def set_preferences(self, data: Dict[str, Any]) -> None:
        cfg = self._load_config()
        cfg["preferences"] = data
        self._save_config()

    # ── Persistent: Lyrics ─────────────────────────────────

    def _get_draft_path(self) -> str:
        """Compute the draft file path based on current source file location.

        Priority: LRC directory > MP3 directory > app data fallback.
        The draft is named ``{source_stem}.lrc-maker-draft.txt``.
        """
        # Use LRC source directory if available
        lrc_path = self.get_last_lrc_path()
        if lrc_path:
            lrc_dir = os.path.dirname(lrc_path)
            if os.path.exists(lrc_dir):
                stem = os.path.splitext(os.path.basename(lrc_path))[0]
                return os.path.join(lrc_dir, f"{stem}.lrc-maker-draft.txt")

        # Fall back to MP3 source directory
        mp3_path = self.get_last_mp3_path()
        if mp3_path:
            mp3_dir = os.path.dirname(mp3_path)
            if os.path.exists(mp3_dir):
                stem = os.path.splitext(os.path.basename(mp3_path))[0]
                return os.path.join(mp3_dir, f"{stem}.lrc-maker-draft.txt")

        # Absolute fallback: app data directory
        return self._lyric_path

    def get_lyric(self) -> str:
        try:
            with open(self._get_draft_path(), "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def set_lyric(self, text: str) -> None:
        path = self._get_draft_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        self._register_draft(path)

    # ── Session Draft Registry ───────────────────────────────

    def _register_draft(self, path: str) -> None:
        """Append a draft path to the session cleanup registry.

        The registry survives crashes: if the app doesn't shut down
        cleanly, leftover entries are picked up and deleted on the
        next launch.
        """
        if not path:
            return
        try:
            with open(self._session_registry_path, "a", encoding="utf-8") as f:
                f.write(path + "\n")
        except OSError:
            pass

    def _all_draft_candidates(self) -> "set[str]":
        """Return every draft path that could exist for the current session."""
        candidates: set[str] = set()
        candidates.add(self._get_draft_path())
        candidates.add(self._lyric_path)

        lrc = self.get_last_lrc_path()
        if lrc:
            d = os.path.dirname(lrc)
            stem = os.path.splitext(os.path.basename(lrc))[0]
            candidates.add(os.path.join(d, f"{stem}.lrc-maker-draft.txt"))

        mp3 = self.get_last_mp3_path()
        if mp3:
            d = os.path.dirname(mp3)
            stem = os.path.splitext(os.path.basename(mp3))[0]
            candidates.add(os.path.join(d, f"{stem}.lrc-maker-draft.txt"))

        return candidates

    def cleanup_session_drafts(self) -> bool:
        """Delete every draft ever written this session (and any leftover
        from a previous crashed session), then remove the registry file.

        Returns True when every candidate was deleted (or already absent).
        """
        # 1.  Replay the registry — every draft path ever written
        try:
            with open(self._session_registry_path, "r", encoding="utf-8") as f:
                registered = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            registered = []

        # 2.  Belt-and-suspenders: also cover current LRC / MP3 locations
        all_paths = set(registered) | self._all_draft_candidates()

        # 3.  Delete one by one
        failed: list[str] = []
        for path in all_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass  # already clean
            except OSError:
                failed.append(path)

        # 4.  Remove the registry — everything we know about is gone
        try:
            os.remove(self._session_registry_path)
        except FileNotFoundError:
            pass

        return len(failed) == 0

    # ── Session ────────────────────────────────────────────

    def get_session(self, key: str, default: Any = None) -> Any:
        return self._session.get(key, default)

    def set_session(self, key: str, value: Any) -> None:
        self._session[key] = value

    # ── Convenience Methods ────────────────────────────────

    def get_audio_src(self) -> str:
        return self.get_session("audioSrc", "")

    def set_audio_src(self, src: str) -> None:
        self.set_session("audioSrc", src)

    def get_sync_mode(self) -> int:
        return self.get_session("syncMode", 0)

    def set_sync_mode(self, mode: int) -> None:
        self.set_session("syncMode", mode)

    def get_select_index(self) -> int:
        return self.get_session("selectIndex", 0)

    def set_select_index(self, index: int) -> None:
        self.set_session("selectIndex", index)

    # ── File Path Preferences ────────────────────────────

    def get_remember_last_lrc(self) -> bool:
        return self.get_preferences().get("rememberLastLrc", True)

    def get_remember_last_mp3(self) -> bool:
        return self.get_preferences().get("rememberLastMp3", True)

    def get_remember_playback_rate(self) -> bool:
        return self.get_preferences().get("rememberPlaybackRate", True)

    def get_show_save_warning(self) -> bool:
        return self.get_preferences().get("showSaveWarning", True)

    def get_show_draft_warning(self) -> bool:
        return self.get_preferences().get("showDraftWarning", True)

    def get_enable_smart_import(self) -> bool:
        return self.get_preferences().get("enableSmartImport", True)

    def get_reaction_time_ms(self) -> int:
        """Reaction time offset in milliseconds (default 100ms).

        When stamping a timestamp, the captured audio time is shifted
        backward by this amount to compensate for human reaction delay.
        """
        return self.get_preferences().get("reactionTimeMs", 100)

    def get_remember_draft(self) -> bool:
        return self.get_preferences().get("rememberDraft", True)

    def get_default_browse_dir(self) -> str:
        return self.get_preferences().get("defaultBrowseDir", "D:/歌手")

    def get_default_cover_browse_dir(self) -> str:
        """Default directory for browsing cover art images."""
        return self.get_preferences().get(
            "defaultCoverBrowseDir",
            self.get_default_browse_dir(),
        )

    def get_last_lrc_path(self) -> str:
        return self._load_config().get("lastLrcPath", "")

    def set_last_lrc_path(self, path: str) -> None:
        cfg = self._load_config()
        cfg["lastLrcPath"] = path
        self._save_config()

    def get_last_mp3_path(self) -> str:
        return self._load_config().get("lastMp3Path", "")

    def set_last_mp3_path(self, path: str) -> None:
        cfg = self._load_config()
        cfg["lastMp3Path"] = path
        self._save_config()

    def get_draft_path(self) -> str:
        """Public accessor for the current draft file path."""
        return self._get_draft_path()

    def delete_draft(self) -> None:
        """Delete all draft files for the current source files.

        Because ``_get_draft_path()`` may change when the user imports an LRC
        from a different directory (e.g. LRC in ``D:/lyrics/`` but MP3 in
        ``D:/music/``), we clean up drafts in ALL possible locations, not just
        the one currently returned by ``_get_draft_path()``.  Otherwise
        ``_do_smart_import()`` would find the orphaned draft next to the MP3
        and load stale content.
        """
        # Collect unique candidate paths
        candidates = set()

        # Current primary draft path
        candidates.add(self._get_draft_path())

        # Draft next to LRC file (may differ from primary if MP3 path takes priority)
        lrc_path = self.get_last_lrc_path()
        if lrc_path:
            lrc_dir = os.path.dirname(lrc_path)
            if os.path.exists(lrc_dir):
                stem = os.path.splitext(os.path.basename(lrc_path))[0]
                candidates.add(os.path.join(lrc_dir, f"{stem}.lrc-maker-draft.txt"))

        # Draft next to MP3 file
        mp3_path = self.get_last_mp3_path()
        if mp3_path:
            mp3_dir = os.path.dirname(mp3_path)
            if os.path.exists(mp3_dir):
                stem = os.path.splitext(os.path.basename(mp3_path))[0]
                candidates.add(os.path.join(mp3_dir, f"{stem}.lrc-maker-draft.txt"))

        # App-data fallback
        candidates.add(self._lyric_path)

        for path in candidates:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def get_last_playback_rate(self) -> float:
        return self._load_config().get("lastPlaybackRate", 1.0)

    def set_last_playback_rate(self, rate: float) -> None:
        cfg = self._load_config()
        cfg["lastPlaybackRate"] = rate
        self._save_config()

    # ── Persistent: Keybindings ──────────────────────────

    def get_keybindings(self) -> Dict[str, Any]:
        """Get user-defined keybinding overrides."""
        return self._load_config().get("keybindings", {})

    def set_keybindings(self, data: Dict[str, Any]) -> None:
        """Save user-defined keybinding overrides."""
        cfg = self._load_config()
        cfg["keybindings"] = data
        self._save_config()

    # ── Persistent: API Configs (encrypted, multiple named entries) ──

    def get_api_configs(self) -> "list[dict[str, str]]":
        """Get all decrypted API configurations.

        Returns a list of ``{"name": ..., "url": ..., "api_key": ..., "model": ...}``.
        Only entries with all three of url/api_key/model are included.
        """
        raw_cfg = self._load_config()

        # ── Migration: old single apiConfig → new apiConfigs list ──
        if "apiConfig" in raw_cfg and "apiConfigs" not in raw_cfg:
            old = raw_cfg.pop("apiConfig")
            migrated = {
                "name": encrypt("默认") if old.get("url") else "",
                "url": old.get("url", ""),
                "api_key": old.get("api_key", ""),
                "model": old.get("model", ""),
            }
            raw_cfg["apiConfigs"] = [migrated]
            self._save_config()

        raw_list: list[dict[str, str]] = raw_cfg.get("apiConfigs", [])
        result: list[dict[str, str]] = []
        for entry in raw_list:
            cfg: dict[str, str] = {}
            for field in ("name", "url", "api_key", "model"):
                ciphertext = entry.get(field, "")
                try:
                    cfg[field] = decrypt(ciphertext) if ciphertext else ""
                except Exception:
                    cfg[field] = ""
            if cfg.get("url") and cfg.get("api_key") and cfg.get("model"):
                result.append(cfg)
        return result

    def add_api_config(
        self, name: str, url: str, api_key: str, model: str
    ) -> None:
        """Encrypt and append a new API configuration."""
        cfg = self._load_config()
        # Migrate old format if present
        if "apiConfig" in cfg and "apiConfigs" not in cfg:
            self.get_api_configs()  # triggers migration
            cfg = self._load_config()
        configs: list[dict[str, str]] = cfg.get("apiConfigs", [])
        configs.append({
            "name": encrypt(name) if name else "",
            "url": encrypt(url) if url else "",
            "api_key": encrypt(api_key) if api_key else "",
            "model": encrypt(model) if model else "",
        })
        cfg["apiConfigs"] = configs
        self._save_config()

    def remove_api_config(self, index: int) -> None:
        """Remove an API configuration by its index in the list."""
        cfg = self._load_config()
        configs: list[dict[str, str]] = cfg.get("apiConfigs", [])
        if 0 <= index < len(configs):
            configs.pop(index)
            cfg["apiConfigs"] = configs
            self._save_config()

    def has_api_configs(self) -> bool:
        """Return True when at least one API config is saved."""
        return len(self.get_api_configs()) > 0
