# LRC Maker — Integrated Song Editor

> A PyQt6 desktop lyrics tool for Windows: real-time timestamping, translation editing (with AI assist), audio metadata editing, playlist management, and LRC import/export.

## What It Does

Create timestamped LRC files by following audio playback in real time: load a song, tap the spacebar as each line is sung, export a standard LRC file. Around that core it also covers translation editing, AI-assisted translation, audio metadata editing, cover-art cropping, and a scannable music library.

---

## Highlights

### Efficient Timestamping

- **Spacebar to stamp** — Select a lyric line, press Space at the moment you hear it, and the timestamp is written; the selection advances to the next line. Entirely keyboard-driven.
- **30 customizable shortcuts** — Line navigation, timestamp offsets, seek, variable-speed playback, split/merge, undo/redo. Every binding can be overridden on the Preferences page.
- **Reaction time compensation** — A configurable 0–500 ms offset is subtracted from each stamp to compensate for human reaction latency (default 100 ms).
- **Optional seek-back verification** — After stamping, playback can jump back to the new timestamp after a configurable delay so you can hear whether it landed right.
- **Variable-speed stamping** — Slow tricky sections down or speed simple passages up; rate changes are persisted across sessions when enabled.

### AI-Assisted Translation

- **Automatic API translation** — Talks to any OpenAI-compatible endpoint through the `openai` client, with multiple named, encrypted API profiles.
- **Pattern matching** — Paste LRC text that already contains translations; the tool maps them onto the current lines by timestamp. Lines that already have a translation are skipped unless overwrite mode is selected.
- **Prompt generation** — Builds a ready-to-paste translation prompt for chat-based model websites and copies it to the clipboard.
- **Encrypted API key storage** — Every field of an API profile is encrypted separately with Windows DPAPI, so it is only decryptable by the current user on the current machine.

### Full Desktop Experience

- **Waveform visualization** — Audio is decoded with numpy + soundfile, reduced to a peak envelope at a fixed resolution (1200 buckets), and painted with QPainter: the unplayed part in the foreground colour at low alpha, the played part in the theme colour. Decoding runs on a background thread with an LRU cache of 6 songs; click or drag to seek.
- **Cover art management** — Reads embedded cover art from MP3/FLAC automatically. Browse an external image and crop it interactively (rectangle, square, circle).
- **Audio metadata editing** — Reads and writes ID3 (MP3) and VorbisComment (FLAC/Ogg) tags directly via mutagen, and can rename the file from its metadata.
- **Music library** — Scans a folder tree for MP3s, keeps a cache with incremental refresh (files unchanged by mtime + size are reused), browses it as a collapsible tree, filters with search and a "liked" toggle, and imports songs into the play queue.
- **Play queue and play modes** — Five modes (single, sequential, loop, single-loop, shuffle), a right-side sliding queue drawer with virtualised rows, and a queue, mode, volume and mute that survive a restart.
- **Drag-and-drop loading** — Drop an audio file or a lyric file onto the window to start working immediately.
- **Scrollable lyrics home page** — A music-app-style lyrics display with three modes (original, translation, bilingual); click any line to seek.
- **Expanded lyric editor** — A large-window editor with its own transport controls and Ctrl+F find/replace (regular expressions).

### Smart Details

- **Auto-match same-name files** — When audio loads, the tool looks for a `.lrc` or `.txt` file with the same stem next to it and loads it automatically.
- **Draft restore on exit** — The lyric draft lives at one fixed path in AppData. It is read once at startup and immediately consumed (deleted); during a session everything stays in memory; on exit the draft is written again when the preference is on.
- **Undo/redo (100 steps)** — Each mutation snapshots the state first, so any operation can be rolled back.
- **10 theme colors + custom picker** — Global QSS stylesheet generated dynamically, with light, dark and follow-system modes. Contrast is checked with the WCAG relative-luminance algorithm so text stays readable on any theme colour.

---

## Architecture

### Module Map

```
main.py                       # Entry point — builds the app, registers 6 pages, wires main.py-level signals
src/
├── core/                     # Core layer — no Qt Widgets (QtCore / QtMultimedia only)
│   ├── constants.py          #   Enums: InputAction, PlayMode, SyncMode, ThemeMode, PageRoute
│   ├── lrc_parser.py         #   LRC text ↔ structured data (pure functions, no Qt)
│   ├── lrc_state.py          #   Lyrics state machine + snapshot undo/redo
│   ├── audio_manager.py      #   QMediaPlayer wrapper + embedded cover extraction
│   ├── playlist_manager.py   #   Play queue + play modes + auto-advance
│   ├── config_manager.py     #   JSON persistence + in-memory session
│   ├── crypto_utils.py       #   Windows DPAPI encryption
│   └── keybinding.py         #   Keyboard matching engine
│
└── ui/                       # UI layer (PyQt6 Widgets)
    ├── main_window.py        #   Controller — shared state, signal wiring, global key filter, draft lifecycle
    ├── content_stack.py      #   Page router + global QSS theme engine
    ├── header_bar.py         #   Top navigation (5 tabs + help)
    ├── footer_bar.py         #   Bottom bar — hosts AudioControls, accepts file drops
    ├── home_page.py          #   Cover card + lyrics axis
    ├── lyric_axis_widget.py  #   Scrolling lyrics axis (3 display modes)
    ├── editor_page.py        #   Plain-text lyrics editor + metadata form
    ├── meta_editor_page.py   #   ID3 / VorbisComment editor + cover cropping + rename
    ├── playlist_page.py      #   Music library: scan, tree, search, likes
    ├── preferences_page.py   #   Settings (8 collapsible sections, incl. shortcut editing)
    ├── audio_controls.py     #   Transport bar: info zone / playback zone / toggles
    ├── playlist_panel.py     #   Queue drawer (slides in from the right, virtualised)
    ├── song_info_dialog.py   #   Per-song metadata + untimed lyrics dialog
    ├── waveform_widget.py    #   QPainter waveform with background decoding
    ├── toast_overlay.py      #   Top-right toast notifications
    └── synchronizer/         #   Synchronizer page sub-package (6 modules)
        ├── _helpers.py       #     colour helpers, WCAG contrast
        ├── _lyric_input.py   #     auto-growing lyric input box
        ├── _lyric_row.py     #     one lyric row (timestamp button + view/edit/split stack)
        ├── _translation_row.py #   translation editing row
        ├── _ai_assist.py     #     AI assist dialog, prompt building, pattern matching
        └── _expand_editor.py #     expanded editor window with find/replace
```

### Key Design Decisions

**1. Hub-and-spoke signal architecture**

`MainWindow` owns five shared objects (`ConfigManager`, `LrcStateManager`, `AudioManager`, `PlaylistManager`, `KeyBindingManager`). UI components reach them through `main_window.xxx`, with no direct coupling between components — cross-component communication goes through PyQt6 signals and slots:

```
LrcStateManager.state_changed
  ├──→ MainWindow._save_select_index()   selected row index (session memory only)
  ├──→ SynchronizerPage._refresh_rows()  row repaint
  ├──→ EditorPage._update_from_state()   editor sync
  ├──→ LyricAxisWidget._rebuild()        lyrics axis rebuild
  └──→ AudioControls.set_fixed()         timestamp precision (wired in main.py)
```

Any component can be replaced without affecting the others.

**2. Snapshot-based state management**

`LrcStateManager` is the single source of truth for lyrics data. Every mutation goes through its methods, and the ones that change state push a snapshot before applying it:

```python
def next_(self, audio_time: float) -> None:
    self._push_undo()                    # snapshot before the mutation
    self.lyric[index] = LyricLine(       # replace the line
        time=audio_time,
        text=line.text,
        translation=line.translation,
    )
    self.select_index = guard(index + 1, 0, max(0, len(self.lyric) - 1))
    self.state_changed.emit()            # notify the UI
```

The undo stack holds up to 100 snapshots, and `state_changed` is emitted only when something actually changed, so validation failures stay silent.

**3. Dynamic signal connection**

The high-frequency `current_time_changed` signal (16 ms timer, ≈62.5 fps while playing) is connected to `LrcStateManager.refresh()` only while the synchronizer page is active. Leaving the page disconnects it and restores the play mode that was active before:

```python
def _on_sync_page_changed(self, active: bool) -> None:
    if active:
        self.audio_manager.current_time_changed.connect(self.lrc_state.refresh)
        self._saved_play_mode = self.playlist.mode
        self.playlist.set_mode(PlayMode.SINGLE)
    else:
        self.audio_manager.current_time_changed.disconnect(self.lrc_state.refresh)
        self.playlist.set_mode(self._saved_play_mode)
```

**4. Zero-dependency LRC parser**

`lrc_parser.py` has no Qt imports — pure Python functions:

```python
parse(text: str, options: TrimOptions) -> LrcState          # pure function
stringify(state: LrcState, options: FormatOptions) -> str   # pure function
```

The body/translation distinction is a convention: every timestamped body line ends with exactly four spaces, and a line without that marker sharing a timestamp with a marked line is treated as its translation. Independently testable and reusable.

**5. Bidirectional shortcut matching**

`KeyBindingManager` matches `Ctrl` strictly in both directions, so a `Ctrl+S` binding is never triggered by `Ctrl+Shift+S`. `Shift` tightens when `Ctrl` is present and relaxes otherwise (an extra Shift is allowed when `Ctrl` is absent); `Alt` is only required when the binding itself specifies it. Keys are matched by `Qt.Key` code first, then by character, which avoids the common desktop shortcut conflicts.

**6. Top-level frameless toast**

`ToastOverlay` is not an ordinary `QWidget` — it is a tool window with `WindowStaysOnTopHint | FramelessWindowHint | WA_ShowWithoutActivating`, so toasts appear above modal dialogs without stealing focus. An event filter tracks the main window position so the overlay always floats at the top-right corner, and each toast dismisses itself after 3 seconds.

**7. Layered encryption**

API keys are encrypted per field with Windows DPAPI. Rather than encrypting the whole config file (which would force a decrypt to read any setting), each sensitive field is an independent base64-encoded blob. The implementation calls `crypt32.dll` through `ctypes` — no external dependency.

**8. Contrast-adaptive theme engine**

The QSS engine in `content_stack.py` is not simple template substitution: it applies the WCAG relative-luminance algorithm (sRGB gamma correction to linear RGB, weighted luminance, contrast threshold) to pick near-black or near-white foreground text. All 10 preset colors plus any custom picker colour keep text readable.

---

## Getting Started

The project is managed with [uv](https://docs.astral.sh/uv/):

```bash
uv sync                    # core dependencies
uv run main.py             # launch
```

Optional extras:

```bash
uv sync --extra waveform   # soundfile — waveform decoding (without it the waveform degrades)
uv sync --extra build      # pyinstaller — for build_release.py
```

---

## Pages

| Page | Description |
|------|-------------|
| **Home** | Cover art + scrollable lyrics axis (original / translation / bilingual, click to seek) |
| **Playlist** | Music library: folder scan, collapsible tree, search, likes, import into the play queue |
| **Synchronizer** | Core timestamping page: per-line stamps, translation editing, pattern matching, import/export |
| **Meta Editor** | Audio ID3 / VorbisComment editing + cover cropping (rectangle / square / circle) + rename |
| **Preferences** | Theme, shortcuts, reaction time, LRC output format and every other setting |
| **Editor** | Plain-text LRC viewer/editor (entered by dropping a lyric file; not in the nav bar) |

---

## Core Shortcuts

| Shortcut | Action |
|----------|--------|
| `Space` | Stamp timestamp (or toggle play/pause when no line is selected) |
| `Backspace` | Delete the timestamp on the current line |
| `0` / `-` / `=` | Reset / decrease / increase the current line's offset by 0.5 s |
| `↑` `W` `J` / `↓` `S` `K` | Move the selection up / down |
| `Home` / `End` | First / last line |
| `PageUp` / `PageDown` | Page up / down |
| `H` / `L` | Previous / next song in the queue |
| `←` `A` / `→` `D` | Seek back / forward (5 s; Shift halves it, Alt shrinks it to 0.2×) |
| `R` | Reset playback rate |
| `Ctrl+↑` `Ctrl+J` / `Ctrl+↓` `Ctrl+K` | Increase / decrease playback rate |
| `Ctrl+Enter` | Toggle play/pause (highest priority, works while editing text) |
| `Ctrl+C` | Copy the current lyric line |
| `Ctrl+D` | Split the current lyric line |
| `Delete` | Delete the selected lines |
| `Ctrl+H` | Merge adjacent selected lines |
| `Ctrl+A` | Select all |
| `Ctrl+S` | Save (overwrite the source file) |
| `Ctrl+Shift+S` | Export / save as |
| `Ctrl+T` | Toggle translation mode |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Esc` | Deselect (and close the queue drawer when it is open) |
| `?` | Show the help dialog |

Mouse: double-click a line's text to edit it, `Ctrl`+left-click toggles multi-selection, `Ctrl`+right-click appends an empty line below the clicked row, and right-click opens the row menu (edit / split / append / delete / merge).

All 30 shortcuts can be customized on the Preferences page.

---

## Tech Stack

| Technology | Role |
|------------|------|
| PyQt6 ≥6.5 | UI framework (Widgets + Multimedia) |
| numpy ≥1.24 | Audio waveform downsampling |
| mutagen ≥1.48 | Audio metadata read/write |
| openai ≥2.0 | AI translation API client |
| soundfile ≥0.12 (optional) | Audio decoding for the waveform |
| PyInstaller ≥6.0 (optional) | Building `dist/lrc-maker.exe` |
| ctypes | Windows DPAPI encryption |
| QPainter | Waveform and cover-crop preview rendering |
| QSS | Global dynamic theme stylesheet |

---

## License

MIT
