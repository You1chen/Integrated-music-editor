# LRC Maker — 集成歌曲编辑器

> PyQt6 桌面歌词工具（Windows）：实时打轴、翻译编辑（含 AI 辅助）、音频元信息编辑、歌单管理、LRC 导入导出。

## 一句话简介

一款**逐行跟随音频打时间戳**的 LRC 歌词制作工具：加载歌曲 → 边听边敲空格 → 导出标准 LRC 文件。围绕这条主线，还覆盖翻译编辑、AI 辅助翻译、音频元数据编辑、封面裁剪与可检索的媒体库。

---

## 强大之处

### 极致打轴效率

- **空格键一秒打点** — 选中歌词行，听到对应位置按空格，时间戳写入，选中自动跳到下一行。全程键盘操作，双手无需离开键盘
- **30 个可绑定快捷键** — 上下选行、时间戳偏移、快进快退、变速播放、拆分合并、撤销重做……全部可在设置页自定义覆盖
- **反应时间补偿** — 可配置 0~500ms 的延迟补偿，自动从打点时间中扣除人类反应延迟（默认 100ms）
- **可选的跳转验证** — 打点后可按设定延迟回跳到新时间戳，当场听出卡点准不准
- **变速打轴** — 难点慢放，简单段落快速通过；开启记忆后倍速跨会话保留

### AI 辅助翻译

- **API 自动翻译** — 通过 `openai` 客户端对接任意 OpenAI 兼容接口，支持多个具名且加密的 API 配置
- **模式匹配** — 粘贴含翻译的 LRC 文本，按时间戳自动匹配到当前歌词行；**已有翻译的行默认跳过**，除非选择覆写模式
- **提示词生成** — 自动构建翻译提示词并复制到剪贴板，配合模型聊天网站使用
- **API 密钥加密存储** — 每条配置的每个字段都单独用 Windows DPAPI 加密，只在当前用户当前机器可解密

### 完整的桌面体验

- **波形可视化** — numpy + soundfile 解码音频，按固定分辨率（1200 个采样桶）压成峰值包络，用 QPainter 绘制：未播放部分用前景色低透明度、已播放部分用主题色覆盖。解码在后台线程进行，按路径 LRU 缓存 6 首；点击或拖拽即可跳转
- **封面图管理** — 自动读取 MP3/FLAC 内嵌封面，支持浏览外部图片并交互式裁剪（矩形/方形/圆形三种模式）
- **音频元数据编辑** — 通过 mutagen 直接读写 ID3（MP3）和 VorbisComment（FLAC/Ogg）标签，并可按元信息重命名文件
- **媒体库** — 扫描目录树中的 MP3 并缓存，支持增量刷新（mtime + size 未变的文件直接复用缓存），以可折叠树浏览，支持搜索与「喜欢」筛选，并可导入播放队列
- **播放队列与播放模式** — 五种模式（单次/顺序/循环/单曲循环/随机），右侧滑出的队列抽屉（行虚拟化），队列、模式、音量与静音均跨会话保留
- **拖放加载** — 直接把音频文件或歌词文件拖进窗口即可开始工作
- **滚动歌词首页** — 类似音乐 App 的歌词展示，支持原词/翻译/双语三种模式，点击歌词跳转播放
- **展开大窗口编辑器** — 大输入框 + 独立播放控件，支持 Ctrl+F 正则查找替换

### 智能细节

- **自动关联同名文件** — 加载音频时，自动查找同目录下同名的 `.lrc` / `.txt` 并载入
- **退出时保存草稿** — 歌词草稿固定存放在 AppData 的单一路径：启动时读取一次并立即消费（删除）；会话中全部在内存；退出时按偏好写回
- **撤销/重做（100 步）** — 每次修改前先存档快照，任何操作均可回退
- **10 色主题 + 自定义取色** — 全局 QSS 动态生成，亮色/暗色/跟随系统三模式；用 WCAG 相对亮度算法校验对比度，任意主题色都保证文字可读

---

## 代码结构

### 模块地图

```
main.py                       # 启动入口 — 建应用、注册 6 个页面、接好 main.py 层信号
src/
├── core/                     # 核心层 — 不依赖 Qt Widgets（仅 QtCore / QtMultimedia）
│   ├── constants.py          #   枚举：InputAction、PlayMode、SyncMode、ThemeMode、PageRoute
│   ├── lrc_parser.py         #   LRC ↔ 结构化数据（纯函数，无 Qt）
│   ├── lrc_state.py          #   歌词状态机 + 快照式 undo/redo
│   ├── audio_manager.py      #   QMediaPlayer 封装 + 内嵌封面提取
│   ├── playlist_manager.py   #   播放队列 + 播放模式 + 自动切歌
│   ├── config_manager.py     #   JSON 持久化 + 会话内存
│   ├── crypto_utils.py       #   Windows DPAPI 加密
│   └── keybinding.py         #   快捷键匹配引擎
│
└── ui/                       # 界面层（PyQt6 Widgets）
    ├── main_window.py        #   总控制器 — 共享状态、信号线、全局按键过滤、草稿生命周期
    ├── content_stack.py      #   页面路由 + 全局 QSS 主题引擎
    ├── header_bar.py         #   顶部导航（5 个 Tab + 帮助）
    ├── footer_bar.py         #   底部栏 — 承载播放控件、接收文件拖放
    ├── home_page.py          #   封面卡片 + 歌词轴
    ├── lyric_axis_widget.py  #   滚动歌词轴（三种显示模式）
    ├── editor_page.py        #   纯文本歌词编辑器 + 元信息表单
    ├── meta_editor_page.py   #   ID3 / VorbisComment 编辑 + 封面裁剪 + 重命名
    ├── playlist_page.py      #   媒体库：扫描、树状浏览、搜索、喜欢
    ├── preferences_page.py   #   设置页（8 个可折叠栏目，含快捷键编辑）
    ├── audio_controls.py     #   播放控件：信息区 / 播放区 / 开关区
    ├── playlist_panel.py     #   播放队列抽屉（右侧滑出，行虚拟化）
    ├── song_info_dialog.py   #   单曲信息弹窗（元信息 + 无时间戳歌词）
    ├── waveform_widget.py    #   QPainter 波形图 + 后台解码
    ├── toast_overlay.py      #   右上角 Toast 通知
    └── synchronizer/         #   打轴页子包（6 个模块）
        ├── _helpers.py       #     颜色工具、WCAG 对比度
        ├── _lyric_input.py   #     自适应高度歌词输入框
        ├── _lyric_row.py     #     歌词行（时间戳按钮 + 查看/编辑/拆分三态栈）
        ├── _translation_row.py #   翻译编辑行
        ├── _ai_assist.py     #     AI 辅助对话框、提示词、模式匹配
        └── _expand_editor.py #     展开大窗口 + 查找替换
```

### 精妙设计点

#### 1. 中心辐射式信号架构

`MainWindow` 持有五个共享对象（ConfigManager、LrcStateManager、AudioManager、PlaylistManager、KeyBindingManager），所有 UI 组件通过 `main_window.xxx` 访问，彼此之间不直接耦合。全部跨组件通信走 PyQt6 信号/槽：

```
LrcStateManager.state_changed
  ├──→ MainWindow._save_select_index()   选中行号（仅会话内存）
  ├──→ SynchronizerPage._refresh_rows()  行重绘
  ├──→ EditorPage._update_from_state()   编辑器同步
  ├──→ LyricAxisWidget._rebuild()        歌词轴重建
  └──→ AudioControls.set_fixed()         时间精度（在 main.py 中接线）
```

组件之间零直接调用，替换任一组件不影响其他。

#### 2. 快照式状态管理

`LrcStateManager` 是歌词数据的**单一真相源**。所有修改必须通过其方法完成，会改动状态的方法在修改前先存档快照：

```python
def next_(self, audio_time: float) -> None:
    self._push_undo()                    # 修改前存档
    self.lyric[index] = LyricLine(       # 替换该行
        time=audio_time,
        text=line.text,
        translation=line.translation,
    )
    self.select_index = guard(index + 1, 0, max(0, len(self.lyric) - 1))
    self.state_changed.emit()            # 通知 UI
```

undo 栈最多保留 100 份快照；`state_changed` 只在状态**确实变化**时发射，参数非法或没有改动的分支静默返回。

#### 3. 动态信号连接

只有打轴页面活跃时，高频的 `current_time_changed` 信号（16ms 定时器，播放中约 62.5fps）才连接到 `LrcStateManager.refresh()`。离开页面时断开，并恢复进入前的播放模式：

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

#### 4. 零依赖 LRC 解析器

`lrc_parser.py` 不依赖任何 Qt 模块，是纯 Python 函数式设计：

```python
parse(text: str, options: TrimOptions) -> LrcState          # 纯函数
stringify(state: LrcState, options: FormatOptions) -> str   # 纯函数
```

正文与翻译靠约定区分：每个带时间戳的正文行末尾恰好 4 个空格；与带标记行时间戳相同、但自己不带标记的行，就是它的翻译。可独立测试、独立复用。

#### 5. 快捷键系统的双向匹配

`KeyBindingManager` 的 `Ctrl` 修饰键采用严格双向匹配：绑定 `Ctrl+S` 不会被 `Ctrl+Shift+S` 触发。Shift 则在 Ctrl 组合键时收紧、非 Ctrl 时宽松（允许额外 Shift 不干扰匹配）；Alt 只在绑定自身要求时才校验。按键先比 `Qt.Key` 码、再比字符，避免桌面端常见的快捷键冲突。

#### 6. 顶层无边框 Toast

`ToastOverlay` 不是普通的 QWidget，而是一个 `WindowStaysOnTopHint` + `FramelessWindowHint` + `WA_ShowWithoutActivating` 的工具窗口。这意味着 **Toast 通知可以出现在模态对话框之上**，而不会抢走焦点。通过事件过滤器跟踪主窗口位置，始终悬浮在右上角；每条 3 秒后自动消失。

#### 7. 加密的安全分层

API 密钥使用 Windows DPAPI 逐字段加密。不是把整个配置文件加密（那会导致读取任何配置都必须解密），而是每个敏感字段独立 base64 编码的加密 blob。通过 ctypes 直接调用 `crypt32.dll`，零外部依赖。

#### 8. 主题系统的对比度自适应

`content_stack.py` 的 QSS 引擎不是简单的模板替换：它实现了 WCAG 相对亮度算法（sRGB gamma 校正为线性 RGB → 加权亮度 → 对比度阈值），据此在前景选近黑或近白。10 种预设色 + 自定义取色，任意主题色都能保证文字可读。

---

## 快速开始

本项目用 [uv](https://docs.astral.sh/uv/) 管理依赖：

```bash
uv sync                    # 核心依赖
uv run main.py             # 启动
```

可选依赖：

```bash
uv sync --extra waveform   # soundfile — 波形解码（没有它波形图形降级）
uv sync --extra build      # pyinstaller — build_release.py 打包用
```

---

## 页面一览

| 页面 | 说明 |
|------|------|
| **主页** | 封面展示 + 滚动歌词轴（原词/翻译/双语、点击跳转） |
| **歌单** | 媒体库：扫描文件夹、可折叠树、搜索、喜欢、导入播放队列 |
| **歌词制作** | 核心打轴页面：逐行时间戳、翻译编辑、模式匹配、导入导出 |
| **编辑元信息** | 音频 ID3/VorbisComment 标签编辑 + 封面图裁剪（矩形/方形/圆形）+ 重命名 |
| **设置** | 主题、快捷键自定义、反应时间补偿、LRC 输出格式等全部偏好 |
| **编辑器** | 纯文本 LRC 查看/编辑（通过拖放歌词文件进入，不在导航栏显示） |

---

## 核心快捷键

| 快捷键 | 功能 |
|--------|------|
| `Space` | 打时间戳（未选中行时则是切换播放/暂停） |
| `Backspace` | 删除当前行时间戳 |
| `0` / `-` / `=` | 以当前行时间戳为基准重置 / 减少 / 增加 0.5 秒偏移 |
| `↑` `W` `J` / `↓` `S` `K` | 上 / 下移动选中行 |
| `Home` / `End` | 首行 / 末行 |
| `PageUp` / `PageDown` | 上翻页 / 下翻页 |
| `H` / `L` | 队列中上一首 / 下一首 |
| `←` `A` / `→` `D` | 快退 / 快进（基准 5 秒；Shift 减半，Alt 缩到 0.2 倍） |
| `R` | 重置播放速率 |
| `Ctrl+↑` `Ctrl+J` / `Ctrl+↓` `Ctrl+K` | 加速 / 减速播放 |
| `Ctrl+Enter` | 切换播放/暂停（全局最高优先级，编辑文本时也有效） |
| `Ctrl+C` | 复制当前歌词行 |
| `Ctrl+D` | 拆分当前歌词行 |
| `Delete` | 删除选中行 |
| `Ctrl+H` | 合并相邻选中行 |
| `Ctrl+A` | 全选 |
| `Ctrl+S` | 保存覆写源文件 |
| `Ctrl+Shift+S` | 导出/另存 |
| `Ctrl+T` | 切换翻译模式 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Esc` | 取消选中（队列抽屉打开时先关抽屉） |
| `?` | 帮助对话框 |

鼠标：双击行文本进入编辑，`Ctrl`+左键切换多选，`Ctrl`+右键在被点击那一行下方追加空行，右键弹出该行菜单（编辑 / 拆分 / 追加 / 删除 / 合并）。

全部 30 个快捷键均可在设置页自定义。

---

## 技术栈

| 技术 | 用途 |
|------|------|
| PyQt6 ≥6.5 | UI 框架（Widgets + Multimedia） |
| numpy ≥1.24 | 音频波形降采样 |
| mutagen ≥1.48 | 音频元数据读写 |
| openai ≥2.0 | AI 翻译 API 客户端 |
| soundfile ≥0.12（可选） | 波形解码 |
| PyInstaller ≥6.0（可选） | 打包 `dist/lrc-maker.exe` |
| ctypes | Windows DPAPI 加密 |
| QPainter | 波形图 + 封面裁剪预览手绘 |
| QSS | 全局动态主题样式表 |

---

## License

MIT
