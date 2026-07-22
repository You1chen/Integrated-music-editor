# LRC Maker — 集成歌曲编辑器

> PyQt6 桌面歌词打轴工具。Web 版 [`magic-akari/lrc-maker`](https://github.com/magic-akari/lrc-maker) 的 Python 移植，并在桌面端能力上做了大量增强。

## 一句话简介

一款**逐行跟随音频打时间戳**的 LRC 歌词制作工具。加载歌曲 → 边听边敲空格 → 导出标准 LRC 文件。支持翻译编辑、AI 辅助翻译、音频元数据编辑、封面裁剪等完整工作流。

---

##  强大之处

### 极致打轴效率

- **空格键一秒打点** — 选中歌词行，听到对应位置按空格，时间戳写入，光标自动跳到下一行。全程键盘操作，双手无需离开键盘
- **27 个可绑定快捷键** — 上下选行、快进快退、变速播放、拆分合并、撤销重做……全部支持自定义覆盖
- **反应时间补偿** — 可配置 0~500ms 的延迟补偿，自动从打点时间中扣除人类反应延迟
- **变速打轴** — 把难听清的部分放到 0.37x 慢放，精确卡点；简单段落 2.72x 快速通过

### AI 辅助翻译

- **API 自动翻译** — 对接任意 OpenAI 兼容接口（DeepSeek、Kimi 等），一键将歌词翻译为中文
- **模式匹配** — 粘贴含翻译的 LRC 文本，自动按时间戳匹配到当前歌词行，支持增量/覆写两种模式
- **提示词生成** — 自动构建翻译提示词，一键复制到剪贴板，配合模型聊天网站使用
- **API 密钥加密存储** — 通过 Windows DPAPI 加密，密钥只在当前用户当前机器可解密

### 完整的桌面体验


- **封面图管理** — 自动读取 MP3/FLAC 内嵌封面，支持浏览外部图片并交互式裁剪（矩形/方形/圆形三种模式）
- **音频元数据编辑** — 通过 mutagen 直接读写 ID3（MP3）和 VorbisComment（FLAC/Ogg）标签
- **拖放加载** — 直接把音频文件或歌词文件拖进窗口即可开始工作
- **滚动歌词首页** — 类似音乐 App 的歌词展示，支持原词/翻译/双语三种模式，点击歌词跳转播放

### 智能细节

- **奈奎斯特采样游标** — 时间戳显示根据精度和播放速率自适应刷新频率，既不闪烁也不延迟
- **自动关联同名文件** — 加载音频时自动查找同目录下同名的 `.lrc` 文件
- **草稿自动保存 + 崩溃恢复** — 每次修改即时写入草稿文件，异常退出后下次启动可恢复
- **撤销/重做（100 步）** — 递归深度拷贝快照，任何操作均可回退
- **10 色主题 + 自定义取色** — 全局 QSS 动态生成，亮色/暗色/跟随系统三模式

---

## 代码结构精巧之处

### 架构总览

```
main.py                     # 启动入口，组装所有部件
src/
├── core/                   # 核心层 — 零 Qt Widgets 依赖
│   ├── constants.py        #   全局枚举与常量（单一真相源）
│   ├── lrc_parser.py       #   LRC ↔ 结构化数据（纯函数，零依赖）
│   ├── lrc_state.py        #   歌词状态机 + undo/redo
│   ├── audio_manager.py    #   QMediaPlayer 封装
│   ├── config_manager.py   #   JSON 持久化 + 会话内存
│   ├── crypto_utils.py     #   Windows DPAPI 加密
│   └── keybinding.py       #   快捷键匹配引擎
│
└── ui/                     # 界面层
    ├── main_window.py      #   总控制器：持有共享状态，组装全部信号线
    ├── content_stack.py    #   页面路由 + 全局 QSS 主题引擎
    └── synchronizer/       #   打轴页面子包（5 个模块）
```

### 精妙设计点

#### 1. 中心辐射式信号架构

`MainWindow` 持有四个共享对象（ConfigManager、LrcStateManager、AudioManager、KeyBindingManager），所有 UI 组件通过 `main_window.xxx` 访问，彼此之间不直接耦合。全部跨组件通信走 PyQt6 信号/槽：

```
LrcStateManager.state_changed
  ├──→ MainWindow._save_state()          草稿自动保存
  ├──→ SynchronizerPage._refresh_rows()  行高亮刷新
  ├──→ EditorPage._update_from_state()   编辑器同步
  └──→ AudioControls                     精度更新
```

组件之间零直接调用，替换任一组件不影响其他。

#### 2. 状态管理的 Redux 思想

`LrcStateManager` 是歌词数据的**单一真相源**。所有修改必须通过其方法完成，每个方法在修改前自动保存快照到 undo 栈：

```python
def next_(self, audio_time: float) -> None:
    self._push_undo()                    # 修改前存档
    self.lyric[idx].time = audio_time    # 执行修改
    self.select_index += 1
    self.state_changed.emit()            # 通知 UI
```

这不是简单的 getter/setter，而是借鉴了 Redux reducer 模式 — 状态变化是可预测、可追溯、可回退的。

#### 3. 动态信号连接

只有打轴页面活跃时，高频的 `current_time_changed` 信号才连接到 `LrcStateManager.refresh()`。切换到其他页面时自动断开，避免无意义的刷新计算：

```python
def _on_sync_page_changed(self, active: bool) -> None:
    if active:
        self.audio_manager.current_time_changed.connect(...)
    else:
        self.audio_manager.current_time_changed.disconnect(...)
```

#### 4. 自适应游标采样

`CursorLabel` 实现了奈奎斯特-香农采样定理：根据时间精度和播放速率动态切换更新策略。低精度（秒级）时用 2Hz 定时器轮询，省 CPU；高精度（毫秒级）时自动切换到音频引擎的 60fps 信号推送，无延迟。阈值公式：`2×[1,10,100,1000][fixed]×rate > 60` 时走信号，否则走定时器。

#### 5. 零依赖 LRC 解析器

`lrc_parser.py` 不依赖任何 Qt 模块，是纯 Python 函数式设计：

```python
parse(text: str, options: TrimOptions) -> LrcState    # 纯函数
stringify(state: LrcState, options: FormatOptions) -> str  # 纯函数
```

可独立测试、独立复用、独立打包。

#### 6. 快捷键系统的双向匹配

`KeyBindingManager` 的 `Ctrl` 修饰键采用严格双向匹配：绑定 `Ctrl+S` 不会被 `Ctrl+Shift+S` 触发。Shift 则在 Ctrl 组合键时收紧、非 Ctrl 时宽松（允许额外 Shift 不干扰匹配）。这套逻辑精确复刻了 Web 版的行为，避免了桌面端常见的快捷键冲突。

#### 7. 顶层无边框 Toast

`ToastOverlay` 不是普通的 QWidget，而是一个 `WindowStaysOnTopHint` + `FramelessWindowHint` + `WA_ShowWithoutActivating` 的工具窗口。这意味着 **Toast 通知可以出现在模态对话框之上**，而不会抢走焦点。通过事件过滤器跟踪主窗口位置，始终悬浮在右上角。

#### 8. 加密的安全分层

API 密钥使用 Windows DPAPI 逐字段加密。不是把整个配置文件加密（那会导致读取全部配置时必须解密），而是每个敏感字段独立 base64 编码的加密 blob。通过 ctypes 直接调用 `crypt32.dll`，零外部依赖。

#### 9. 草稿跟随源文件

歌词草稿不放在固定的 AppData 目录，而是放在**源文件旁边**：LRC 文件目录优先，其次 MP3 目录，最后才是 AppData 兜底。这样草稿与源文件一同移动/备份/删除，不会产生"僵尸草稿"。Session Draft Registry 机制确保退出时精准清理，即使崩溃也能在下次启动时扫尾。

#### 10. 主题系统的对比度自适应

`content_stack.py` 的 QSS 引擎不是简单的模板替换。它实现了 WCAG 亮度算法：先对 sRGB 做 gamma 校正转为线性 RGB，计算相对亮度，再通过对比度公式决定前景用黑色还是白色。10 种预设色 + 自定义取色，任意主题色都能保证文字可读。

---

## 快速开始

```bash
# 安装依赖
uv sync

# 启动
uv run main.py

##或者
#gcc -o main.exe laucher.c
```

`soundfile` 为可选依赖（无它时波形图显示平线）。如需波形功能，Windows 上推荐：

```bash
pip install soundfile
```

---

##  页面一览

| 页面 | 说明 |
|------|------|
| **主页** | 封面展示 + 滚动歌词轴（原词/翻译/双语、点击跳转） |
| **歌词制作** ⭐ | 核心打轴页面：逐行时间戳、翻译编辑、模式匹配、导入导出 |
| **编辑元信息** | 音频 ID3/VorbisComment 标签编辑 + 封面图裁剪（矩形/方形/圆形） |
| **设置** | 主题、快捷键自定义、反应时间补偿、LRC 输出格式等全部偏好 |
| **编辑器** | 纯文本 LRC 查看/编辑（通过拖放歌词文件进入，不在导航栏显示） |

---

## ⌨️ 核心快捷键

| 快捷键 | 功能 |
|--------|------|
| `Space` | 打时间戳 / 切换播放暂停 |
| `↑` `↓` `W` `S` | 上下选行 |
| `←` `→` `A` `D` | 快退/快进 5 秒 |
| `Ctrl` + `↑` `↓` | 加减速播放 |
| `R` | 重置播放速率 |
| `Backspace` | 删除当前行时间戳 |
| `Ctrl+D` | 拆分歌词行 |
| `Ctrl+C` | 复制歌词行 |
| `Ctrl+S` | 保存覆写源文件 |
| `Ctrl+Shift+S` | 导出/另存 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |
| `Ctrl+T` | 切换翻译模式 |
| `Esc` | 取消选中行 |
| `?` | 帮助对话框 |

> 全部 27 个快捷键可在设置页面自定义。

---

## 🔧 技术栈

| 技术 | 用途 |
|------|------|
| PyQt6 | UI 框架（Widgets + Multimedia） |
| numpy | 音频波形降采样 |
| soundfile | 音频解码 |
| mutagen | 音频元数据读写 |
| openai | AI 翻译 API |
| ctypes | Windows DPAPI 加密 |
| QPainter | 波形图 + 封面裁剪预览手绘 |
| QSS | 全局动态主题样式表 |

---

##  License

MIT
