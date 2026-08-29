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

    Draft lifecycle (simplified — 2026-07-22):
    - Startup: read ``draft.lrc`` → parse into memory → delete the file
    - Runtime: everything in memory, zero disk writes
    - Exit:    ask user → if yes, write ``draft.lrc`` to AppData
    """

    APP_NAME = "lrc-maker"

    def __init__(self) -> None:
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        self._data_dir = os.path.join(base, self.APP_NAME)
        os.makedirs(self._data_dir, exist_ok=True)

        self._config_path = os.path.join(self._data_dir, "config.json")
        self._draft_path = os.path.join(self._data_dir, "draft.lrc")
        self._playlist_cache_path = os.path.join(
            self._data_dir, "playlist_cache.json"
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

    def get_lyric(self) -> str:
        """Read the single draft file from AppData (if it exists)."""
        try:
            with open(self._draft_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    def set_lyric(self, text: str) -> None:
        """Write the draft to AppData (only called on exit)."""
        with open(self._draft_path, "w", encoding="utf-8") as f:
            f.write(text)

    def delete_draft(self) -> None:
        """Consume the draft — delete it (called at startup after reading)."""
        try:
            os.remove(self._draft_path)
        except FileNotFoundError:
            pass

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

    def remember_lrc_path(self, path: str) -> None:
        """Set *lastLrcPath* if the remember-Last-LRC preference is on."""
        if self.get_remember_last_lrc():
            self.set_last_lrc_path(path)

    def remember_mp3_path(self, path: str) -> None:
        """Set *lastMp3Path* if the remember-last-MP3 preference is on."""
        if self.get_remember_last_mp3():
            self.set_last_mp3_path(path)

    def get_remember_playback_rate(self) -> bool:
        return self.get_preferences().get("rememberPlaybackRate", True)

    def get_show_save_warning(self) -> bool:
        return self.get_preferences().get("showSaveWarning", True)

    def get_show_welcome(self) -> bool:
        """Whether to show the welcome/guide dialog on startup."""
        return self.get_preferences().get("showWelcome", True)

    def get_enable_smart_import(self) -> bool:
        return self.get_preferences().get("enableSmartImport", True)

    def get_reaction_time_ms(self) -> int:
        """Reaction time offset in milliseconds (default 100ms).

        When stamping a timestamp, the captured audio time is shifted
        backward by this amount to compensate for human reaction delay.
        """
        return self.get_preferences().get("reactionTimeMs", 100)

    def get_undo_seek_back_seconds(self) -> float:
        """Seconds to seek back after undoing a sync/timestamp (default 3.0).

        When the user presses Ctrl+Z to undo a timestamp operation, the
        audio playhead jumps back by this many seconds so they can
        re-listen and re-stamp without manually seeking.  Capped at 10 s.
        """
        return float(self.get_preferences().get("undoSeekBackSeconds", 3.0))

    def get_remember_draft(self) -> bool:
        return self.get_preferences().get("rememberDraft", True)

    def get_overwrite_source_on_exit(self) -> bool:
        """Whether to overwrite the source LRC file with draft content on exit.

        When True: before deleting the draft on close, the source LRC file
        is overwritten with the current draft content.
        When False: the draft file is kept as-is for the next session.
        """
        return self.get_preferences().get("overwriteSourceOnExit", False)

    # ── Overwrite (the single source of truth) ──────────────

    def overwrite_lrc(self, text: str) -> tuple[bool, str]:
        """Overwrite the source LRC file with *text*.

        Returns ``(success, message)``.

        Safety checks:
        1. A source LRC path must be recorded.
        2. When audio is loaded the MP3 stem must match the LRC stem —
           otherwise a stale ``lastLrcPath`` from a previous session
           would silently overwrite a different song's LRC file
           ("张冠李戴").
        """
        lrc_path = self.get_last_lrc_path()
        if not lrc_path:
            return False, "未找到源歌词文件 — 请先导入歌词文件或新建草稿"

        lrc_stem = os.path.splitext(os.path.basename(lrc_path))[0]

        # ── Cross-song contamination guard ──────────────────
        mp3_path = self.get_last_mp3_path()
        if mp3_path and os.path.isfile(mp3_path):
            mp3_stem = os.path.splitext(os.path.basename(mp3_path))[0]
            if mp3_stem != lrc_stem:
                return False, (
                    f"音频「{mp3_stem}」与歌词文件「{lrc_stem}」不匹配，"
                    f"拒绝覆写以防止张冠李戴"
                )
            # ── Directory guard: only ever write to the audio's
            # own folder.  A stale ``lastLrcPath`` pointing at the
            # same stem in a different folder must not be touched.
            lrc_dir = os.path.normcase(os.path.normpath(os.path.dirname(lrc_path)))
            mp3_dir = os.path.normcase(os.path.normpath(os.path.dirname(mp3_path)))
            if lrc_dir != mp3_dir:
                return False, (
                    f"歌词文件目录与音频不一致"
                    f"（音频: {mp3_path}），拒绝跨目录覆写"
                )

        # ── Write ───────────────────────────────────────────
        try:
            os.makedirs(os.path.dirname(lrc_path), exist_ok=True)
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(text)
            return True, "歌词已保存"
        except OSError as e:
            return False, f"写入失败：{e}"

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
        """Public accessor for the draft file path (AppData/draft.lrc)."""
        return self._draft_path

    def get_last_playback_rate(self) -> float:
        return self._load_config().get("lastPlaybackRate", 1.0)

    def set_last_playback_rate(self, rate: float) -> None:
        cfg = self._load_config()
        cfg["lastPlaybackRate"] = rate
        self._save_config()

    def get_last_volume(self) -> Dict[str, Any]:
        """Restore the last-session output volume + mute state.

        Defaults to the QAudioOutput defaults (100% / unmuted) so the first
        run is unchanged from before persistence existed.
        """
        return {
            "volume": float(self._load_config().get("lastVolume", 1.0)),
            "muted": bool(self._load_config().get("lastMuted", False)),
        }

    def set_last_volume(self, volume: float, muted: bool) -> None:
        """Persist the output volume + mute state (footnote: saved by the
        volume popup on every change)."""
        cfg = self._load_config()
        cfg["lastVolume"] = float(volume)
        cfg["lastMuted"] = bool(muted)
        self._save_config()

    def get_last_play_mode(self) -> int:
        """Restore the last-session playback mode (a ``PlayMode`` int)."""
        return int(self._load_config().get("lastPlayMode", 0))  # PlayMode.SINGLE

    def set_last_play_mode(self, mode: int) -> None:
        """Persist the playback mode chosen from the footer mode menu."""
        cfg = self._load_config()
        cfg["lastPlayMode"] = int(mode)
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

    # ── Persistent: Playlist Cache ──────────────────────────

    def get_playlist_cache(self) -> "dict[str, Any]":
        """Read the playlist cache JSON (scan results + likes)."""
        try:
            with open(self._playlist_cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def set_playlist_cache(self, data: "dict[str, Any]") -> None:
        """Write the playlist cache JSON."""
        with open(self._playlist_cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_playlist_root_dirs(self) -> "list[str]":
        """Return the cached root directories for playlist scanning."""
        return self.get_playlist_cache().get("root_dirs", [])

    def toggle_playlist_like(self, path: str) -> bool:
        """Toggle the liked state of a song in the playlist cache.

        Returns the new liked state (True = liked).
        """
        cache = self.get_playlist_cache()
        songs: list[dict] = cache.get("songs", [])
        for song in songs:
            if song.get("path") == path:
                song["liked"] = not song.get("liked", False)
                result = song["liked"]
                break
        else:
            return False
        cache["songs"] = songs
        self.set_playlist_cache(cache)
        return result

    # ── Persistent: Play Queue (播放列表记忆) ──────────────

    def get_play_queue(self) -> "dict[str, Any]":
        """Return the saved play queue: {"songs": [...], "index": int}."""
        data = self._load_config().get("playQueue", {})
        return data if isinstance(data, dict) else {}

    def set_play_queue(self, songs: "list[dict[str, Any]]", index: int) -> None:
        """Persist the play queue and current index."""
        cfg = self._load_config()
        cfg["playQueue"] = {"songs": songs, "index": index}
        self._save_config()
