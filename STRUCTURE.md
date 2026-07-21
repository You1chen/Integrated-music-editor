# LRC Maker — 结构索引

> PyQt6 桌面歌词编辑与打轴工具。Web 版 `magic-akari/lrc-maker` 的 Python 移植。

## 外部依赖

| 依赖 | 用途 |
|------|------|
| `PyQt6` (≥6.11) | UI 框架：Widgets、Multimedia、信号/槽 |
| `numpy` (≥2.5) | 音频波形降采样与峰值包络计算 |
| `soundfile` | 解码音频文件为原始采样数据（波形图），可选依赖；缺失时波形图降级为平线 |

---

## 模块树

```
main.py                         # 启动脚本：组装所有部件，启动事件循环
│
└── src/
    ├── core/                    # 核心层 — 不依赖 Qt Widgets（部分依赖 QtCore/QtMultimedia）
    │   ├── constants.py         #   枚举 & 常量：PageRoute、InputAction、SyncMode…
    │   ├── lrc_parser.py        #   LRC 文本 ↔ 结构化数据（纯函数，无 Qt 依赖）
    │   ├── lrc_state.py         #   歌词运行时状态机（reducer + undo/redo）
    │   ├── audio_manager.py     #   QMediaPlayer 封装（属性访问器 + 信号）
    │   ├── config_manager.py    #   JSON 持久化 + 会话级内存数据
    │   └── keybinding.py        #   键盘映射：QKeyEvent → InputAction，支持用户自定义
    │
    └── ui/                      # 界面层 — PyQt6 Widgets
        ├── main_window.py       #   QMainWindow：持有所有共享对象，组装信号线
        ├── content_stack.py     #   QStackedWidget 页面路由 + QSS 主题引擎
        ├── header_bar.py        #   顶部导航栏（主页/歌词制作/设置）
        ├── footer_bar.py        #   底部栏（承载 AudioControls + 文件拖放）
        ├── home_page.py         #   主页：使用引导与帮助
        ├── editor_page.py       #   编辑器：元信息表单 + 纯文本编辑器（拖放歌词后跳转至此）
        ├── synchronizer_page.py #   ⭐ 打轴页面：逐行打时间戳的核心交互
        │                        #     内含 _LyricRow / _TranslationRow 两个私有组件
        │                        #     支持双击编辑、Ctrl+点击追加、快捷键复制/拆分等
        ├── preferences_page.py  #   设置页：主题/颜色/格式/行为偏好 + 快捷键自定义
        │                        #     所有设置栏目均可折叠（_CollapsibleGroup）
        ├── audio_controls.py    #   播放控制条（按钮/时间线/速率滑块/波形）
        ├── waveform_widget.py   #   自定义 QPainter 波形图（numpy + soundfile）
        ├── toast_overlay.py     #   Toast 通知队列（自动 3 秒消失）
        ├── load_audio_dialog.py #   加载音频对话框（文件选择 / URL 输入）
        ├── aside_panel.py       #   侧边面板（同步模式切换 + 导出按钮）
        └── cursor_label.py      #   实时时间戳光标（Nyquist–Shannon 采样更新）
```

---

## 架构：Signal-Slot 驱动的 MVC 变体

整个应用围绕 `MainWindow` 组装，它持有四个共享对象（无所有权的组件通过 `main_window.xxx` 访问），通过 PyQt6 信号/槽机制在它们之间传递数据。

```
MainWindow （QMainWindow）
  ├── config: ConfigManager              ← JSON 文件持久化（含自定义快捷键）
  ├── lrc_state: LrcStateManager        ← 歌词状态（单点真相）
  ├── audio_manager: AudioManager       ← 音频播放
  ├── keybinding_manager: KeyBindingManager ← 键盘映射（支持用户覆盖默认值）
  │
  ├── HeaderBar                          → page_requested → ContentStack.set_page()
  ├── ContentStack (QStackedWidget)      → sync_page_active_changed → 动态连接音频
  │   ├── [0] HomePage
  │   ├── [1] EditorPage                  （拖放歌词文件或从其他页面跳转访问，不在导航栏显示）
  │   ├── [2] SynchronizerPage
  │   └── [3] PreferencesPage
  ├── FooterBar                          → 承载 AudioControls + 文件拖放
  │   └── AudioControls                  → 播放按钮 / 时间线 / 速率 / 波形
  │       └── WaveformWidget             → numpy+QPainter 波形
  └── ToastOverlay                       → 右上角通知
```

---

## 核心模块详解

### 1. `lrc_parser.py` — LRC 解析器（零依赖纯函数）

将 LRC 文本与结构化数据互相转换：

- `parse(text, TrimOptions)` → `LrcState { info: dict, lyric: List[LyricLine] }`
  - 正则匹配 `[mm:ss.xx]` 时间标签和 `[key:value]` 元信息标签
  - 连续两行同时间戳 → 第二行合并为 translation
- `stringify(LrcState, FormatOptions)` → LRC 文本
  - `FormatOptions` 控制：空格（space_start/space_end）、精度（fixed=0~3）、换行符、翻译行输出
- `convert_time_to_tag(seconds, fixed)` — 时间 → 标签字符串
- `guard(value, min, max)` — 安全钳位

### 2. `lrc_state.py` — 状态管理器（类似 Redux Reducer）

`LrcStateManager(QObject)` 持有全部运行时歌词数据，所有修改通过其方法完成：

| 方法 | 对应 ActionType | 作用 |
|------|----------------|------|
| `init_from_text(text, options, select)` | PARSE | 从文本初始化（替换全部状态） |
| `parse(text, options)` | PARSE | 重新解析文本（保留 select_index） |
| `refresh(audio_time)` | REFRESH | 根据音频位置更新 current_index / next_index |
| `next_(audio_time)` | NEXT | 给当前行打时间戳，select 跳到下一行 |
| `set_time(time)` | TIME | 修改当前选中行的时间戳 |
| `set_text(index, text)` | — | 修改歌词文本 |
| `split_line(index, positions)` | — | 在指定字符位置拆分行 |
| `append_line(after_index)` | — | 在指定行后插入空行（继承时间戳） |
| `copy_line(index)` | — | 复制指定行到其下方（同文本+时间戳，不含翻译） |
| `insert_lines(after_index, texts, time)` | — | 在指定行后批量插入歌词行 |
| `set_translation(index, text)` | — | 设置单行翻译文本 |
| `set_translations_batch(translations)` | — | 批量设置翻译（单步 undo） |
| `set_info(name, value)` | INFO | 设置元数据（ti/ar/al/length…） |
| `select(fn)` | SELECT | 移动选中行 |
| `deselect()` | — | 取消行选中（select_index = -1） |
| `delete_time()` | DELETE_TIME | 删除当前行的时间戳 |
| `undo()` / `redo()` | — | 撤销/重做（最多 100 步快照） |
| `stringify(options)` | — | 序列化为 LRC 文本 |
| `update_format_options(options)` | — | 更新序列化格式选项 |
| `get_state(callback)` | GET_STATE | 传递当前状态给回调 |

每次修改方法调用后发射 `state_changed` 信号 → UI 自动刷新 + 草稿自动保存。

### 3. `audio_manager.py` — 音频管理

`AudioManager(QObject)` 封装 `QMediaPlayer` + `QAudioOutput`，提供与原 Web 版一致的 API：

- **属性访问器**：`src`、`duration`、`paused`、`playback_rate`（读写）、`current_time`（读写）
- **播放控制**：`toggle()`、`set_source(url)`、`step(modifiers, offset, target)` — step 支持 Alt（×0.2）和 Shift（×0.5）修饰，接受 `Qt.KeyboardModifier` 或 dict
- **信号**：
  - `current_time_changed(float)` — ~60fps 定时器触发（播放时）或 seek 时单次触发
  - `state_changed(AudioStateData)` — PAUSE_CHANGED / DURATION_LOADED / RATE_CHANGED
  - `error_occurred(str)` — 中文错误消息
  - `duration_changed(float)`

### 4. `config_manager.py` — 配置持久化

- **持久化数据** → `%AppData%/lrc-maker/config.json`（preferences、lastLrcPath、lastMp3Path、lastPlaybackRate、keybindings）
- **歌词草稿** → 源文件同目录下的 `{stem}.lrc-maker-draft.txt`，随 `state_changed` 自动保存
- **会话数据** → 仅内存（audioSrc、syncMode、selectIndex 等）
- **Session Draft Registry** → `session_drafts.txt` 记录本会话所有草稿路径，用于退出时批量清理和崩溃恢复

快捷键存储格式（config.json 的 keybindings 字段）：
```json
{
  "save": [{"code": 83, "ctrl": true, "shift": false, "alt": false}],
  "export": [{"code": 83, "ctrl": true, "shift": true, "alt": false}]
}
```

### 5. `keybinding.py` — 键盘系统

`KeyBindingManager` 维护 `Dict[InputAction, List[KeyBinding]]`，通过 `get_matched_action(QKeyEvent)` 匹配键盘事件：

- Ctrl/Cmd：严格双向匹配；Shift：Ctrl 组合键时双向匹配（防止 `Ctrl+Shift+S` 误匹配 `Ctrl+S`），否则宽松匹配；Alt：仅 binding 指定时才要求匹配
- 按键匹配优先级：`Qt.Key` code → 字符 key string
- `DEFAULT_BINDINGS` 定义了 27 个动作的默认快捷键
- `ACTION_LABELS` — 每个动作的中文标签（如 `SAVE` → `"保存"`）
- `ACTION_GROUPS` — 按 5 个分组排列（打轴/歌词编辑/工具栏/音频控制/通用），供设置页展示
- `KeyBinding.to_dict()` / `from_dict()` — 序列化为 JSON 持久化
- `keybinding_to_string(binding)` — 格式化为可读字符串（`Ctrl+Shift+S`、`Space`、`↑` 等）
- `KeyBindingManager` 支持用户覆盖层：`set_user_binding()` / `reset_user_binding()` / `reset_all()`

### 6. `synchronizer_page.py` — 核心打轴页面

最复杂的 UI 组件（~1600 行），内含三个内部类：

- **`_LyricInput`**：自适应高度的多行歌词输入框，Enter 提交/Shift+Enter 换行/Ctrl+Z 本地撤销
- **`_LyricRow`**：每行 = 时间戳按钮（105px） + 三态显示栈
  - View 模式（QLabel）：只读显示
  - Edit 模式（QLineEdit）：内联编辑文本
  - Split 模式（QTextEdit）：`//` 标记拆分，黄色闪烁高亮
  - **双击**文本区域 → 进入编辑模式
  - **Ctrl+点击**文本区域 → 追加空行
  - 右键菜单：编辑/拆分/追加
  - 信号：`seek_requested`、`edit_requested`、`row_clicked`、`lyric_text_changed`、`lyric_split_done`、`append_requested` 等
- **`_TranslationRow`**：翻译编辑行（左侧 105px 占位 + QLineEdit），仅在翻译模式下显示

SynchronizerPage 的键盘处理：
- 优先匹配打轴 + 编辑专用动作（SYNC、COPY_LINE、SPLIT_LYRIC 等）
- `SYNC`（空格键）：选中行时打时间戳，未选中行时切换播放/暂停
- 工具栏动作（SAVE、EXPORT、TRANSLATE、PREVIEW、LOAD_AUDIO）无论选中状态均可触发
- Esc 取消选中 → 切换至"音频模式"（`select_index == -1`），此时音频快捷键（上下左右、R 等）正常工作
- 选中歌词时，音频快捷键被屏蔽，避免与打轴操作冲突

快捷键速查：

| 快捷键 | 功能 | 需选中行 |
|--------|------|---------|
| `Space` | 打时间戳 / 切换播放 | 视情况 |
| `Ctrl+C` | 复制当前行（同时间戳） | ✓ |
| `Ctrl+D` | 拆分当前行 | ✓ |
| `↑` `↓` `W` `S` `J` `K` | 上下移动选中行 | — |
| `Backspace` `Delete` | 删除时间戳 | ✓ |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 | — |
| `Ctrl+S` | 保存覆写源文件 | — |
| `Ctrl+Shift+S` | 导出/另存 | — |
| `Ctrl+T` | 切换翻译模式 | — |
| `Ctrl+L` | 预览 LRC 输出 | — |
| `Ctrl+R` | 加载音频文件 | — |
| `Esc` | 取消选中（恢复音频快捷键） | — |

### 7. `preferences_page.py` — 设置页面

包含 8 个**可折叠**设置栏目：

| 栏目 | 默认状态 | 内容 |
|------|---------|------|
| 关于 | 展开 | 版本号、GitHub 链接 |
| 主题模式 | 展开 | 跟随系统/亮色/暗色 |
| 主题颜色 | 展开 | 预设颜色 + 自定义取色 |
| 显示 | 展开 | 波形、虚拟空格键、内置播放器 |
| 文件与路径记忆 | 折叠 | 草稿/歌词/音频记忆、浏览目录、速率记忆、覆盖警告、智能导入 |
| 打轴辅助 | 折叠 | 自动跳转验证、跳转延迟 |
| 歌词输出格式控制 | 折叠 | 时间精度、空格控制、实时预览 |
| 快捷键 | 折叠 | 分组展示全部 27 个动作，支持编辑/清除/重置，即时保存 |

- **`_CollapsibleGroup`**：可复用折叠组件，点击标题栏带 ▼/▶ 箭头切换
- **`_KeyCaptureDialog`**：快捷键捕获对话框，按下按键实时显示，支持清除/确认

---

## 信号连接表

所有信号在 `MainWindow._connect_signals()` 中组装：

| 信号源 | 信号 | 接收者 | 时机/作用 |
|--------|------|--------|-----------|
| `HeaderBar` | `page_requested(int)` | `ContentStack.set_page()` | 导航切换 |
| `ContentStack` | `sync_page_active_changed(bool)` | `MainWindow._on_sync_page_changed()` | 动态连接/断开 `current_time → refresh` |
| `AudioManager` | `current_time_changed(float)` | `LrcStateManager.refresh()` | 仅在打轴页活跃时连接 |
| `AudioManager` | `state_changed(AudioStateData)` | `FooterBar → AudioControls` | 播放/暂停/速率 UI 更新 |
| `AudioManager` | `error_occurred(str)` | `ToastOverlay.show_toast()` | 错误提示 |
| `AudioManager` | `duration_changed(float)` | `MainWindow._on_duration_loaded()` | 自动写入 `info["length"]` |
| `LrcStateManager` | `state_changed()` | `MainWindow._save_state()` | 自动保存草稿 |
| `LrcStateManager` | `state_changed()` | `SynchronizerPage._refresh_rows()` | 刷新行高亮/时序 |
| `LrcStateManager` | `state_changed()` | `EditorPage._update_from_state()` | 同步编辑器内容 |
| `LrcStateManager` | `state_changed()` | `AudioControls` (via lambda in `main.py`) | 更新波形/时间显示的 fixed 精度 |

---

## 核心数据流

### 打轴流程

```
用户按空格键
  → SynchronizerPage.keyPressEvent()
    → KeyBindingManager.get_matched_action() → InputAction.SYNC
      → 若 select_index == -1：切换播放/暂停
      → 否则：
        → lrc_state.next_(audio_manager.current_time)
            ├── 当前选中行写入音频时间
            ├── select_index += 1
            └── 发射 state_changed
                ├── _refresh_rows() → 重绘行选中/高亮状态
                ├── _save_state() → 草稿自动保存
                └── _scroll_to_row() → 滚动跟随
```

### 翻译模式匹配流程

```
用户点击 "模式匹配" 按钮
  → 粘贴包含翻译的 LRC 文本
    → 解析输入：按时间标签分组文本
    → 遍历 lyric 行，查找匹配时间戳
      → Pattern 1（2 条文本）：一条匹配歌词文本 → 另一条作为翻译
      → Pattern 2（1 条文本）：直接使用（跳过与歌词相同的）
    → 主线程 QTimer 逐行写入 state.lyric[index].translation + emit state_changed（带动画效果）
    → Toast 反馈匹配数量
```

### 音频加载 → 联动

```
AudioManager.set_source(url)
  → QMediaPlayer 异步加载
    → duration_changed(duration)
        ├── MainWindow: lrc_state.set_info("length", ...)
        ├── AudioControls: 更新时间线范围 + 显示波形
        └── ToastOverlay: "音频已载入"
```

### 偏好设置更新

```
PreferencesPage._save_prefs()
  → MainWindow.update_preferences(prefs)
      ├── ConfigManager.set_preferences() → 持久化 JSON
      ├── lrc_state.update_format_options() → 影响 stringify 输出
      ├── content_stack.apply_theme() → 重建 QSS 样式表 + QPalette
      └── 相关 UI 组件响应（fixed 精度、波形可见性等）
```

### 快捷键自定义流程

```
用户点击快捷键「编辑」按钮
  → _KeyCaptureDialog 弹出
    → 用户按下组合键 → eventFilter 捕获 → 实时显示
    → 点击「确认」
      → KeyBindingManager.set_user_binding(action, [new_binding])
      → ConfigManager.set_keybindings(overrides) → 持久化到 config.json
      → _refresh_shortcut_labels() → 刷新设置页显示
    → 重启后 MainWindow 加载 config.json → KeyBindingManager(user_overrides=...)
```

### 文件拖放

```
用户拖文件到 FooterBar
  → _handle_file_drop(path)
      ├── 音频 (mp3/flac/wav/ogg/…)
      │     → config 记忆路径
      │     → audio_manager.set_source()
      │
      └── 歌词文件 (txt/lrc)
            → config 记忆路径
            → lrc_state.parse() → 状态更新
            → 跳转到编辑器页
```

---

## 页面路由

```
PageRoute.HOME         = 0  →  HomePage           （帮助引导）
PageRoute.EDITOR       = 1  →  EditorPage          （文本编辑器，拖放歌词文件时跳转至此，不在导航栏显示）
PageRoute.SYNCHRONIZER = 2  →  SynchronizerPage    （打轴核心）
PageRoute.PREFERENCES  = 3  →  PreferencesPage     （设置）
```

HeaderBar 只显示 3 个 Tab：主页、歌词制作、设置。EditorPage 在 `main.py` 中已注册，通过 FooterBar 拖放歌词文件或代码中 `set_page(PageRoute.EDITOR)` 跳转访问。

---

## 会话草稿生命周期

```
会话开始
  ├── rememberDraft 开启 → config.get_lyric() 读取上次草稿 → init_from_text()
  │
会话中
  ├── state_changed → _save_state() → config.set_lyric() 写入草稿文件
  ├── 草稿路径注册到 session_drafts.txt
  │
关闭窗口 / 导入新歌词
  ├── closeEvent: _closing = True → init_from_text("") → cleanup_session_drafts()
  └── 导入前: delete_draft() → init_from_text("") → 删旧草稿 → 加载新内容
```
