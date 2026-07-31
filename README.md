# LRC Maker — Integrated Song Editor

> A PyQt6 desktop lyrics synchronization tool. Python port of [`magic-akari/lrc-maker`](https://github.com/magic-akari/lrc-maker), enhanced with desktop-native capabilities.

## What It Does

A tool for creating timestamped LRC lyrics files by following audio playback in real time. Load a song, tap the spacebar as each line is sung, and export a standard LRC file. Covers the full workflow: translation editing, AI-assisted translation, audio metadata editing, and cover art cropping.

---

## Highlights

### Efficient Timestamping

- **Spacebar to stamp** — Select a lyric line, press Space when you hear the corresponding moment, and the timestamp is written; the cursor auto-advances to the next line. Entirely keyboard-driven.
- **27 customizable shortcuts** — Line navigation, seek, variable-speed playback, split/merge, undo/redo. All bindings can be overridden in preferences.
- **Reaction time compensation** — Configurable 0--500 ms delay automatically subtracted from each timestamp to offset human reaction latency.
- **Variable-speed stamping** — Slow down tricky sections to 0.37x for pinpoint accuracy; speed through simple passages at 2.72x.

### AI-Assisted Translation

- **Automatic API translation** — Connects to any OpenAI-compatible endpoint (DeepSeek, Kimi, etc.) to translate lyrics to Chinese in one click.
- **Pattern matching** — Paste LRC text containing translations; the tool maps them to current lyric lines by matching timestamps. Supports incremental and overwrite modes.
- **Prompt generation** — Automatically builds a translation prompt, ready to copy to clipboard for use with chat-based model websites.
- **Encrypted API key storage** — Keys are encrypted per-field via Windows DPAPI. Decryptable only by the current user on the current machine.

### Full Desktop Experience

- **Waveform visualization** — Audio decoded with numpy + soundfile, rendered as a dual-channel waveform (grey background + theme-colored progress overlay) via QPainter.
- **Cover art management** — Reads embedded cover art from MP3/FLAC files automatically. Supports browsing external images with an interactive crop dialog (rectangle, square, circle modes).
- **Audio metadata editing** — Reads and writes ID3 (MP3) and VorbisComment (FLAC/Ogg) tags directly via mutagen.
- **Drag-and-drop loading** — Drop an audio or lyrics file onto the window to start working immediately.
- **Scrollable lyrics home page** — A music-app-style lyrics display with three modes (original, translation, bilingual); click any line to seek audio.

### Smart Details

- **Nyquist-Shannon cursor sampling** — The timestamp display adapts its update strategy based on precision and playback rate: low-precision polling at economical rates, high-precision signal-driven updates at 60 fps.
- **Auto-match same-name files** — When an audio file is loaded, the tool automatically looks for a `.lrc` file with the same stem in the same directory.
- **Auto-save drafts with crash recovery** — Every state change is written to a draft file immediately. Orphaned drafts from crashed sessions are cleaned up on next launch.
- **Undo/redo (100 steps)** — Deep-copy snapshots allow any operation to be rolled back.
- **10 theme colors + custom picker** — Global QSS stylesheet generated dynamically. Light, dark, and follow-system modes. All theme colors guarantee readable text via WCAG luminance calculation.

---

## Architecture

### Module Map

```
main.py                     # Entry point — assembles all components
src/
├── core/                   # Core layer — zero Qt Widgets dependency
│   ├── constants.py        #   Enums & constants (single source of truth)
│   ├── lrc_parser.py       #   LRC text ↔ structured data (pure functions)
│   ├── lrc_state.py        #   Lyrics state machine + undo/redo
│   ├── audio_manager.py    #   QMediaPlayer wrapper
│   ├── config_manager.py   #   JSON persistence + session memory
│   ├── crypto_utils.py     #   Windows DPAPI encryption
│   └── keybinding.py       #   Keyboard matching engine
│
└── ui/                     # UI layer
    ├── main_window.py      #   Controller — holds shared state, wires all signals
    ├── content_stack.py    #   Page router + global QSS theme engine
    └── synchronizer/       #   Synchronizer page sub-package (5 modules)
```

### Key Design Decisions

**1. Hub-and-spoke signal architecture**

`MainWindow` owns four shared objects (`ConfigManager`, `LrcStateManager`, `AudioManager`, `KeyBindingManager`). All UI components access them via `main_window.xxx` with zero direct coupling between components. Cross-component communication flows exclusively through PyQt6 signals and slots:

```
LrcStateManager.state_changed
  ├──→ MainWindow._save_state()          auto-save draft
  ├──→ SynchronizerPage._refresh_rows()  row highlight refresh
  ├──→ EditorPage._update_from_state()   editor sync
  └──→ AudioControls                     precision update
```

Any component can be replaced without affecting others.

**2. Redux-style state management**

`LrcStateManager` is the single source of truth for all lyrics data. Every mutation goes through its methods, each of which snapshots state onto the undo stack before applying changes:

```python
def next_(self, audio_time: float) -> None:
    self._push_undo()                    # snapshot before mutation
    self.lyric[idx].time = audio_time    # apply mutation
    self.select_index += 1
    self.state_changed.emit()            # notify UI
```

This is not a simple getter/setter layer — it follows the Redux reducer pattern. State transitions are predictable, traceable, and reversible.

**3. Dynamic signal connection**

The high-frequency `current_time_changed` signal (~60 fps) connects to `LrcStateManager.refresh()` only when the synchronizer page is active. Switching to any other page disconnects it automatically, preventing wasted refresh cycles:

```python
def _on_sync_page_changed(self, active: bool) -> None:
    if active:
        self.audio_manager.current_time_changed.connect(...)
    else:
        self.audio_manager.current_time_changed.disconnect(...)
```

**4. Adaptive cursor sampling**

`CursorLabel` implements the Nyquist-Shannon sampling theorem. At low precision (seconds), it polls at 2 Hz to save CPU. At high precision (milliseconds), it switches to the audio engine's 60 fps signal push for zero latency. The threshold formula: when `2 * [1, 10, 100, 1000][fixed] * rate > 60`, use signals; otherwise, use a timer.

**5. Zero-dependency LRC parser**

`lrc_parser.py` has no Qt imports — pure Python functions:

```python
parse(text: str, options: TrimOptions) -> LrcState       # pure function
stringify(state: LrcState, options: FormatOptions) -> str # pure function
```

Independently testable, reusable, and packable.

**6. Bidirectional shortcut matching**

`KeyBindingManager` uses strict bidirectional matching for the `Ctrl` modifier: a `Ctrl+S` binding is never triggered by `Ctrl+Shift+S`. The `Shift` modifier tightens when `Ctrl` is present and relaxes otherwise (extra Shift is allowed when `Ctrl` is absent). This precisely replicates the original web app's behavior and avoids common desktop shortcut conflicts.

**7. Top-level frameless toast**

`ToastOverlay` is not an ordinary `QWidget` — it is a tool window with `WindowStaysOnTopHint | FramelessWindowHint | WA_ShowWithoutActivating`. This means toast notifications appear above modal dialogs without stealing focus. An event filter tracks the main window position so the overlay always floats at the top-right corner.

**8. Layered encryption**

API keys are encrypted per-field via Windows DPAPI. Rather than encrypting the entire config file (which would require decryption to read any setting), each sensitive field is an independent base64-encoded encrypted blob. The implementation calls `crypt32.dll` directly through `ctypes` — zero external dependencies.

**9. Draft-follows-source-file**

Lyric drafts are not confined to a fixed AppData directory. They are placed next to the source file: LRC directory first, then MP3 directory, with AppData as the fallback. This means drafts move, back up, and delete together with the source files — no orphaned drafts. A Session Draft Registry file tracks all draft paths for precise cleanup on exit and orphan recovery on next launch.

**10. Contrast-adaptive theme engine**

The QSS engine in `content_stack.py` is not simple template substitution. It implements the WCAG relative luminance algorithm: sRGB gamma correction to linear RGB, weighted luminance calculation, then a contrast threshold to choose black or white foreground text. All 10 preset colors plus any custom picker color guarantee readable text.

---

## Getting Started

```bash
# Install dependencies
pip install PyQt6 numpy soundfile mutagen openai

# Launch
python main.py
```

`soundfile` is optional (waveform widget shows a flat line without it). On Windows:

```bash
pip install soundfile
```

---

## Pages

| Page | Description |
|------|-------------|
| **Home** | Cover art display + scrollable lyrics axis (original / translation / bilingual modes, click to seek) |
| **Synchronizer** | Core timestamping page: per-line time stamps, translation editing, pattern matching, import/export |
| **Meta Editor** | Audio ID3 / VorbisComment tag editing + cover art cropping (rectangle / square / circle) |
| **Preferences** | Theme, custom shortcuts, reaction time compensation, LRC output format, and all other settings |
| **Editor** | Plain-text LRC viewer/editor (entered via drag-and-drop of lyrics files; not shown in the nav bar) |

---

## Core Shortcuts

| Shortcut | Action |
|----------|--------|
| `Space` | Stamp timestamp / toggle play-pause |
| `Up` `Down` `W` `S` | Previous / next line |
| `Left` `Right` `A` `D` | Seek backward / forward 5 seconds |
| `Ctrl` + `Up` `Down` | Increase / decrease playback rate |
| `R` | Reset playback rate |
| `Backspace` | Delete timestamp on current line |
| `Ctrl+D` | Split lyric line |
| `Ctrl+C` | Copy lyric line |
| `Ctrl+S` | Save (overwrite source file) |
| `Ctrl+Shift+S` | Export / save as |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo |
| `Ctrl+T` | Toggle translation mode |
| `Esc` | Deselect current line |
| `?` | Show help dialog |

> All 27 shortcuts can be customized on the Preferences page.

---

## Tech Stack

| Technology | Role |
|------------|------|
| PyQt6 | UI framework (Widgets + Multimedia) |
| numpy | Audio waveform downsampling |
| soundfile | Audio decoding |
| mutagen | Audio metadata read/write |
| openai | AI translation API client |
| ctypes | Windows DPAPI encryption |
| QPainter | Waveform + cover crop preview rendering |
| QSS | Global dynamic theme stylesheet |

---

## License
 
MIT
