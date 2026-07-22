# LRC Maker — 开发进度

只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！
只能追加，不准删除前面的内容！！！！！！！！！！！！！

> 记录每次重要改动的日期和内容。

---

# 心路历程：集成歌曲编辑器 · Python 桌面版

## 起点：从浏览器到桌面

"集成歌曲编辑器"（原 LRC Maker）最初是一个 Web 应用——React + TypeScript，跑在浏览器里，用 wavesurfer.js 画波形，用 localStorage 存配置。

Web 版很好，但每次打开都要重新选文件。于是有了 Python 桌面版：**一次启动，长久记忆**。

最早移植过来时，代码是"照着 React 描"的——`useReducer` 变成了 `QObject + pyqtSignal`，`localStorage` 变成了 `ConfigManager`，CSS 变量变成了 QSS 样式表。骨架是对了，但血和肉还是 Web 版的模样。

---

## 第一层：让软件有记忆

> "下次打开还在上次的目录，不需要反复调控"

软件的第一个真正进步是**偏好持久化**。我们在 `config.json` 里记录：

- 记住上次打开的 LRC 文件路径
- 记住上次打开的 MP3 文件路径
- 记住上次的播放倍速
- 默认浏览目录（初始指向 `D:/歌手`）

用户在设置页里能**直观地控制**每一项是否记住——不再是一个隐形的行为，而是可见、可选的权利。

同时，所有文件浏览对话框打开时不再傻傻地停在当前目录，而是按优先级智能定位：**上次路径 → 默认目录 → 用户目录**。

---

## 第二层：让界面有反馈

> "所有的可以点的按钮在鼠标悬停的时候都变色"

Web 版有 `:hover`，桌面版一开始却忘了。

于是 QSS 里挨个补上：

| 控件 | 没加之前 | 加了之后 |
|---|---|---|
| 通用按钮 | 平面无反应 | 悬停变主题色，边框亮起 |
| 滑块手柄 | 没有悬停态 | 白色光晕 |
| 下拉框 | 无反馈 | 边框亮起，选项高亮 |
| 复选框 | **方框看不见** | 18×18 方框，选中填主题色 |
| 输入框 | 看不清边界 | 悬停半透明，聚焦全亮 |
| 标签页 | 无反馈 | 悬停背景变化，选中高亮 |

尤其是复选框——之前全局 QWidget 背景色把 `QCheckBox::indicator` 吞掉了，只剩一个勾号没有框。补上 `QCheckBox::indicator` 样式后终于像个正常的复选框了。

---

## 第三层：让打轴不出错

> "点击第五秒的歌词时间戳会显示成零秒"

打轴是最核心的功能，也是 bug 最集中的地方。三个问题：

### Bug 1：时间戳"变小"的幻觉

选中行显示 `▶{当前音轨时间}`。音轨在 0 秒，你点 5 秒的歌词，它显示 `▶00:00.000`。用户以为时间戳被改了——其实没改，只是显示错了。

**修法**：选中行显示 `▶{该行自己的时间戳}`，没戳的行显示 `▶ `。

### Bug 2：点击时间戳不跳转

`eventFilter` 处理了点击，但返回 `False` 让事件继续传播，QListWidget 内部处理覆盖了 seek 效果。

**修法**：返回 `True` 消费事件，不再让 QListWidget 插手。

### Bug 3：按空格时间戳没变

这其实是 Bug 1 的连锁反应——时间戳确实被设置了，但因为选中行显示的是音轨时间，用户看不出来。修了 Bug 1 之后，这个"问题"就消失了。

---

## 第四层：时间戳和歌词，应该分开

> "显示的是 `[00:08.400]都合よく映されてた錯覚`，而不是 `[00:08.400]` + `都合よく映されてた錯覚`"

这是最根本的设计问题：**时间戳和歌词被混在同一个字符串里**。

在旧代码里，每一行就是一个 `QListWidgetItem`，文本是 `"  [01:23.456] 歌词内容"`。点击检测要靠 `QFontMetrics` 算像素宽度——这段字符串前面多少像素是时间戳，后面多少是正文。精确，但脆弱。

### 新的架构

```
旧：QListWidget item → "  [01:23.456] 歌词内容"（一个字符串）
新：QScrollArea → _LyricRow（QFrame）
                  ├── [01:23.456]（QPushButton，独立控件）
                  └── 歌词内容（QLabel）
```

每一行是一个 `_LyricRow` 控件，里面有两个独立的东西：

- **时间戳按钮**：`QPushButton`，自带 hover 效果
  - 普通点击 → `audio.current_time = line.time`（音轨跳转）
  - Ctrl+点击 → 弹出 `QInputDialog`（手改时间戳）
  - 没戳的显示 `[--:--.---]`（虚线占位，hover 变主题色提示可操作）
- **歌词文本**：`QLabel`
  - 点击文本区域 → 选中该行

选中行和当前播放行通过 QSS 背景色区分，不再需要拼接字符串来计算"前缀像素宽度"。

### 数据模型同步升级

`LyricLine` 从两个字段变成三个：

```python
@dataclass
class LyricLine:
    time: Optional[float] = None   # 时间戳
    text: str = ""                 # 歌词正文
    translation: str = ""          # 翻译（目前留空，后续可用）
```

`parse()` 自动识别翻译行：连续两行时间戳相同时，第二行自动合并为第一行的 `translation`。

`stringify()` 反向输出：有翻译的行自动追加一行同时间戳的翻译行。

所有 `LyricLine(...)` 构造点（快照、打戳、删戳、手动设时）全部保留 `translation` 字段。

---

## 现在的状态

| 方面 | 之前 | 现在 |
|---|---|---|
| 文件记忆 | ❌ 每次手动找 | ✅ 打开即恢复，默认 `D:/歌手` |
| 播放倍速 | ❌ 每次调回 1.0 | ✅ 记住上次的 |
| 控件悬停 | ❌ 大部分无反馈 | ✅ 全控件 hover 变色 |
| 复选框 | ❌ 看不见框 | ✅ 标准的方框勾选 |
| 打轴显示 | ❌ 时间戳和歌词混在一起 | ✅ 分开显示，时间戳是按钮 |
| 时间戳点击 | ❌ 形同虚设 | ✅ 点击跳转，Ctrl+点击编辑 |
| 翻译支持 | ❌ 数据模型不支持 | ✅ 解析时自动合并，导出时自动拆分 |
| 选中行显示 | ❌ `▶{音轨时间}` 误导 | ✅ `▶{行自己的时间戳}` |
| 代码清晰度 | ❌ QListWidget + 字符串拼接 + 像素计算 | ✅ 独立控件 + 信号/槽 |

---

---

## 第五层：编辑页并入歌词制作，按钮从右边搬到上面

> "右边按钮的空间太紧凑，被滑条挤压了，按钮位置改成上方"

这是对信息架构的一次"合并"——原本「编辑」是一个独立页面，和「打轴」并列在顶部标签栏。但实际使用中，"编辑歌词文本"只是打轴流程中的一步，不应该占据一整页。同时，右侧 `AsidePanel`（模式切换 + 导出按钮）只有 48px 宽，被波形滑块挤得毫无存在感。

### 改动前

```
标签栏：[主页] [编辑] [打轴] [设置]

打轴页布局：
┌──────────────────────┬──────┐
│                      │ 🔒   │  ← 48px 右侧栏，被滑块挤压
│   歌词列表            │ ⬇   │
│                      │      │
└──────────────────────┴──────┘
```

### 改动后

```
标签栏：[主页] [歌词制作] [设置]          ← "编辑"消失了，"打轴"改名

歌词制作页布局：
┌──────────────────────────────────────────┐
│ [翻译] [导入] [导出] [编辑]        [🔒] │  ← 宽敞的顶部工具栏
├──────────────────────────────────────────┤
│                                          │
│   歌词列表                                │  ← 全宽显示
│                                          │
└──────────────────────────────────────────┘
```

### 四个按钮的分工

| 按钮 | 功能 | 实现 |
|---|---|---|
| **翻译** | 翻译歌词（尚未实现）| 占位，点击弹出"翻译功能尚未实现" |
| **导入** | 导入 LRC 文件，形成草稿 | 文件对话框 → `parse()` → `init_from_text()` |
| **导出** | 将草稿转译导出为 LRC | `stringify()` → 用户 Browse 保存位置 |
| **另存** | 直接编辑歌词原文 | 弹出 QDialog，内含 QPlainTextEdit，保存后重新解析 |

### 为什么这样改

1. **编辑不是独立页面**：之前的"编辑"标签页里有元信息表单 + 文本框 + 导入/导出按钮。导入和导出本质上是打轴流程的前置和后置步骤，不应该分开在两个页面。
2. **右侧栏太挤**：`AsidePanel` 只有 48px 宽，两个 36×36 的图标按钮再加滑条就没了。移到顶部工具栏后，按钮有充裕空间，还可以加更多。
3. **命名更准确**：「打轴」这个叫法太技术化了，普通用户不知道是什么意思。「歌词制作」直接表达了页面的用途。

### 代码层面

- `header_bar.py`：tabs 从 4 个减为 3 个，移除 `PageRoute.EDITOR`
- `synchronizer_page.py`：`QHBoxLayout`（左 scroll + 右 aside）→ `QVBoxLayout`（上 toolbar + 下 scroll），移除 `AsidePanel` 依赖
- `main.py`：不再注册 `EditorPage`
- `home_page.py`：去掉「→ 编辑」按钮，简化为统一的「→ 歌词制作」入口
- `editor_page.py` 和 `aside_panel.py` 文件保留但不再被加载——代码还在，万一将来用得着

---

## 第六层：去国际化 + 保存覆写预览

> "开发阶段只需要中文，国际化是负担"
> "打好了轴，应该能一键保存回源文件"

这一层做了两件事：**砍掉负担**，**补齐链路**。

### 去国际化

之前项目支持 9 种语言（zh-CN / en-US / ja / ko-KR / pl-PL / pt-BR / sk-SK / zh-HK / zh-TW），每个 UI 文件里都有一行 `lang = get_lang(code)`，然后用 `lang["preferences"]["themeColor"]` 这样的链式取值。开发阶段只用到中文，这套机制纯粹是噪声。

**做了什么**：
- 删除 `src/i18n/` 整个目录（10 个文件）
- 6 个 UI 文件 + `main_window.py` + `header_bar.py` 全部去掉 `get_lang` 调用，UI 字符串硬编码为中文

改动后代码更直白。
```python
# 之前
lang = get_lang(main_window.current_lang_code)
self._btn_import = QPushButton(lang.get("synchronizer", {}).get("importLrc", "导入"))

# 现在
self._btn_import = QPushButton("导入")
```

### 保存 + 预览 + 覆写警告

之前导入 LRC → 打轴 → 导出，导出是"另存为"到一个新文件。但实际使用中，用户通常只想**覆写源文件**。另外，"导出"之前没有任何方式预览最终输出的样子，只能靠猜。

**新增三个功能**：

#### 保存按钮

点击"保存"，直接将当前草稿 `stringify()` 写回源 LRC 文件（`last_lrc_path`）。没有源文件时会 toast 提示"请先导入歌词文件"。

#### 预览按钮

弹出一个**只读**对话框（`QPlainTextEdit` + `setReadOnly(True)`），展示 `stringify()` 的完整输出。不能编辑，只能看。用户可以提前确认格式、小数点位数、空格等设置是否符合预期。

#### 覆写警告

首次点"保存"时弹出警告弹窗：

```
┌─────────────────────────────────────┐
│  "保存"会覆写你的源文件，            │
│  此操作不可撤销，是否预览覆写效果？   │
│                                     │
│  ☐ 不再显示此警告                    │
│                                     │
│              [ 预览 ]    [ 取消 ]    │
└─────────────────────────────────────┘
```

- 点**预览**：打开只读预览 → 关闭后弹出"确认覆写？"→ 确认则写入
- 点**取消**：什么都不做
- 勾选**不再显示**：下次点"保存"直接覆写，不再弹窗。此偏好持久化到 `config.json` 的 `showSaveWarning` 字段

设置页"文件与路径记忆"分组中也有对应的复选框 `保存时显示覆盖警告:`，用户可以随时恢复警告。

### 现在的工具栏

```
[翻译] [导入] [导出] [编辑] [保存] [预览]     ...    [🔒]
```

| 按钮 | 功能 | 实现 |
|---|---|---|
| 翻译 | 翻译歌词（尚未实现）| 占位 |
| 导入 | 导入 LRC 文件，形成草稿 | 文件对话框 → parse() → init_from_text() |
| 导出 | 另存为新文件 | stringify() → 用户 Browse 保存位置 |
| 编辑 | 直接编辑歌词原文 | QDialog + QPlainTextEdit，保存后重新解析 |
| **保存** | **覆写源 LRC 文件** | **stringify() → open(last_lrc_path, "w")** |
| **预览** | **只读查看 LRC 最终输出** | **QPlainTextEdit(setReadOnly) 弹窗** |

---
## 第七层：翻译编辑 + 模式匹配

> "每句歌词都能添加翻译，翻译单独一行显示，不带时间戳占位"

这一层实现了歌词翻译的完整流程：**可视化编辑** + **模式匹配批量导入**。

### 翻译编辑模式

点击工具栏「翻译」按钮，按钮高亮（toggle 状态），每行歌词下方出现一个可编辑的翻译行：

```
┌──────────────────────────────────────────┐
│ [01:23.456] 歌詞本文                       │  ← _LyricRow（不变）
│             ┆ 翻譯文本………………                │  ← _TranslationRow（新增）
│ [01:25.000] 下一句歌词                     │
│             ┆ 下一句翻译…………                │
└──────────────────────────────────────────┘
```

翻译行的设计：
- 左侧 105px 空白（对齐时间戳按钮宽度，不显示时间戳）
- 右侧 `QLineEdit`，斜体、主题色、底部虚线边框
- 左侧有 3px 主题色半透明竖线，与歌词行视觉区分
- 直接点击即可编辑，实时保存到草稿

再次点击「翻译」按钮取消高亮，翻译行全部隐藏，恢复纯净的歌词视图。

### 翻译的数据流

翻译内容存在 `LyricLine.translation` 字段。最终导出 LRC 时，`stringify()` 自动为有翻译的行追加一行同时间戳的翻译：

```lrc
[01:23.456]歌詞本文
[01:23.456]翻譯文本
```

没有翻译的行只输出一行，不追加空行。**此行为从第四层就内置了**——数据模型和解析器早已支持翻译合并，本层只是补上了编辑 UI。

### 模式匹配：批量导入翻译

用户在别处（如 ChatGPT、Deepl、人工翻译文件）拿到了翻译文本，想批量填入。手工逐行粘贴太低效。

点击翻译模式下出现的「模式匹配」按钮，弹出对话框：

1. 粘贴包含翻译的 LRC 文本
2. 支持两种 LRC 格式：
   - **模式 1**：`[时间戳]歌词\n[相同时间戳]翻译`（成对出现）
   - **模式 2**：`[时间戳]翻译文本`（单独一行，时间戳对应原文）
3. 点击「匹配」，系统自动比对时间戳（严格相等），将翻译填入对应行

**匹配算法**：
1. 将输入文本保存为 `{源文件名}.lrc-maker-translation-input.txt` 临时草稿
2. 正则解析输入 → 按时间戳字符串分组（`{"[01:23.456]": ["text1", "text2"], ...}`）
3. 遍历当前歌词状态，对每行翻译为空且有时间戳的行：
   - 在输入分组中查找相同时间戳
   - **2 条文本（模式 1）**：验证一条匹配歌词原文，取另一条 → 排除歧义
   - **1 条文本（模式 2）**：直接作为翻译 → 排除与原文相同的
   - 找不到匹配 → 跳过
4. 匹配完毕自动删除临时草稿（它只是一次性的中间产物）
5. Toast 反馈 `成功匹配 N 条翻译`

示例输入：
```lrc
[00:08.400]都合よく映されてた錯覚
[00:08.400]被安排好的错觉
[00:19.960]一人で勝手に期待して
[00:19.960]独自期待着
```

### 撤销行为

翻译编辑时，每次完成编辑（按 Enter 或切换焦点）才推入一次撤销快照。逐键输入阶段不推栈，避免输 5 个字占 5 个撤销槽。

### 代码层面

- `lrc_state.py`：新增 `set_translation(index, text)` 方法（推 undo + 重建 LyricLine）
- `content_stack.py`：新增 `QPushButton:checked` QSS 规则（toggle 按钮的填充高亮外观）
- `synchronizer_page.py`：
  - 新增 `_TranslationRow` 类（~90 行）：105px 占位 + QLineEdit，含 `hasFocus()` 守卫防止音频刷新覆盖编辑中的文本
  - 工具栏：翻译按钮改为 `setCheckable(True)` + 新增隐藏的「模式匹配」按钮
  - 新增 6 个方法：`_on_translate_toggle` / `_on_translation_changed` / `_on_translation_finished` / `_on_pattern_match` / `_perform_pattern_matching` / `_get_translation_input_draft_path`
  - `_rebuild_all()` 在翻译模式下为每行 `_LyricRow` 下方插 `_TranslationRow`
  - `_refresh_rows()` 追加翻译行状态更新

### 现在的工具栏

```
[翻译] [模式匹配] [导入] [导出] [编辑] [保存] [预览]     ...    [🔒]
```

「模式匹配」只在翻译模式激活时显示。

---
## 第八层：草稿覆写警告 + 智能歌词导入

> "我们不希望老是有草稿，每次关闭程序或者新导入一个lrc的时候应该弹出窗口"
> "导入歌词的时候，如果正在播放歌曲，应该弹窗询问是否寻找当前歌曲的歌词"

这一层解决了两个工作流痛点：**草稿管理失控**和**导入路径过深**。

### 草稿覆写警告

之前草稿（`.lrc-maker-draft.txt`）总是在后台自动保存，用户关闭程序或导入新 LRC 时毫无提示。草稿可能堆积在歌曲目录中，下次启动时意外覆盖了原始内容。

现在，当关闭程序或导入新 LRC 时（且当前有歌词进度），弹出四选一对话框：

```
┌──────────────────────────────────────────────┐
│  接下来，我们将要覆写掉.lrc，                │
│  请问你希望我们这么做吗？                     │
│                                              │
│  ☐ 不再显示此警告                             │
│                                              │
│   [ 确定 ] [ 不覆写且保留进度 ] [ 不覆写 ] [ 取消 ] │
└──────────────────────────────────────────────┘
```

| 选项 | 行为 |
|---|---|
| **确定** | 保存当前进度到源 LRC 文件，删除草稿 |
| **不覆写且保留进度** | 保留草稿不动（关闭时）/ 取消导入（导入时） |
| **不覆写** | 删除草稿，丢弃修改 |
| **取消** | 取消操作（不关闭 / 不导入） |

勾选"不再显示"后，下次默认执行"确定"行为（保存到源文件 + 删除草稿）。此偏好可在设置页恢复。

### 智能歌词导入

之前点击"导入"总是打开文件浏览器，用户需要手动导航到歌曲目录。如果音频已在播放，同目录下很可能就有同名的 LRC 或草稿。

现在，当音频已加载时点击"导入"：

1. 弹出 `QMessageBox`：**"你是否要寻找当前歌曲的歌词"** → [是] / [否]
2. 点击"是"→ 自动在音频文件同目录查找：
   - `{音频名}.lrc-maker-draft.txt` → 找到直接加载
   - `{音频名}.lrc` → 找到加载并设为源文件
   - 都未找到 → 询问"是否新建草稿？"（默认新建）
3. 点击"否"→ 回退到文件浏览器手动选择

在偏好设置中可以关闭智能导入功能，关闭后直接打开文件浏览器。

### 代码层面

- `config_manager.py`：新增 `get_show_draft_warning()`、`get_enable_smart_import()`、`get_draft_path()`（公开）、`delete_draft()`
- `main_window.py`：新增 `closeEvent()`（关闭拦截）、`show_draft_overwrite_dialog()`（四选一对话框）、`_save_to_source_and_delete_draft()`
- `synchronizer_page.py`：重构 `_on_import()` 为三步流程（草稿警告 → 智能导入 → 文件浏览器兜底），新增 `_file_browser_import()`、`_do_smart_import()`
- `preferences_page.py`：新增"关闭/导入时显示覆写警告"和"播放音频时智能查找歌词"两个复选框

### 现在的设置页

"文件与路径记忆"分组中新增两项：

| 选项 | 默认值 | 说明 |
|---|---|---|
| 关闭/导入时显示覆写警告 | ✅ | 关闭时自动"确定"（保存+删除草稿） |
| 播放音频时智能查找歌词 | ✅ | 关闭后用传统文件浏览器导入 |

中间的修改遗失了，目前的状态是：

## 2026-07-21 — 主题色高亮一致化 + 键盘焦点修复

### 主题色系统：透明变体 → 完全不透明

**问题**：所有悬停高亮、选中状态、时间戳按钮都使用透明主题色（alpha 后缀 `33`/`44`/`66`/`88`），与实际主题色不一致。

**修复**（[content_stack.py](src/ui/content_stack.py) + [synchronizer_page.py](src/ui/synchronizer_page.py)）：
- 全部 `{theme_color}NN` 去掉 alpha 后缀，改为完全不透明主题色
- 当背景改为不透明主题色时，文字自动切换为 WCAG 对比色（黑/白），通过 `_is_light_color()` 和新增的 `_contrast_for_theme()` 函数计算
- 新增 `contrast_text` 变量在 QSS 中复用

**涉及的选择器**（content_stack.py QSS）：
`#navTab:hover`、`#audioButton:hover`、`QPushButton:hover/pressed`、`QComboBox` 下拉项、`QSpinBox` 按钮、`QLineEdit:hover`、`QTabBar::tab:hover/selected`、`QMenu::item:selected`、`#lyricList::item:selected`、`#collapsibleHeader:hover`、`QScrollBar::handle`、`#footerBar`

### 悬停高亮覆盖所有可点击按钮

**问题**：部分按钮通过 `setStyleSheet()` 设置了内联样式表，完全覆盖全局 QSS 的 `QPushButton:hover` 规则，导致这些按钮无悬停反馈。

**修复**（5 个文件）：
| 文件 | 按钮 | 处理方式 |
|------|------|---------|
| [home_page.py](src/ui/home_page.py) | `btn_sync` | 移除内联 stylesheet，改用 `#homeSyncButton` objectName + 全局 QSS |
| [load_audio_dialog.py](src/ui/load_audio_dialog.py) | `file_btn` | 同上，`#loadAudioFileBtn` |
| [synchronizer_page.py](src/ui/synchronizer_page.py) | 空格按钮 | 修正无效的 `var(--theme-color)`（Qt QSS 不支持 CSS 变量），新增 `_restyle_space_button()` + hover 边框 |
| [preferences_page.py](src/ui/preferences_page.py) | 颜色按钮 ×10 + 自定义按钮 | 保持内联 stylesheet（需要动态颜色），增加 `QPushButton:hover` 规则（白色边框高亮） |

### 选中行时间戳文字不可见

**问题**：选中行背景 `{theme}` + 时间戳按钮透明背景 + 文字 `{theme}` → 文字与背景同色，完全看不到。

**修复**：时间戳按钮文字始终使用对比色 `contrast`（因为无论选中与否，按钮都在主题色背景之上）。

### 暂停时点击时间戳进度条不更新

**问题**：暂停时点击时间戳，`QMediaPlayer.setPosition()` 生效了但 AudioControls 的滑块和时间显示不更新（因为 UI timer 在暂停时停止）。

**修复**（[audio_controls.py](src/ui/audio_controls.py) + [main.py](main.py)）：
- 新增 `AudioControls.on_current_time_changed(time)` — 仅在暂停时更新滑块和时间显示
- `main.py` 中连接 `audio_manager.current_time_changed → audio_controls.on_current_time_changed`

### 选中歌词后空格被其他控件拦截（核心修复）

**问题链路**：
1. 用户选中歌词行 → `select_index` 正确设置
2. 用户点击播放按钮 → **焦点转移到播放按钮的 QPushButton**
3. 用户按空格 → 事件发给播放按钮（不是 SynchronizerPage）→ 触发 `clicked` → 暂停/播放而非打轴

**修复**（[main_window.py](src/ui/main_window.py)）：

1. **事件过滤器升级为应用全局**：
   ```python
   # 之前：self.installEventFilter(self) → 只拦截发给 MainWindow 自身的事件
   # 之后：QApplication.instance().installEventFilter(self) → 拦截所有子控件事件
   ```

2. **`eventFilter` 增加选歌词时的拦截逻辑**：
   - 当前页面是打轴页 **且** 有歌词被选中时：
     - 空格（SYNC）→ 强制调用 `sync_page._on_sync()` 打轴，无视焦点
     - 音频快捷键（快进/快退/速率/切换播放）→ 直接吞掉，彻底禁止
   - 无歌词选中时 → 照常走原有逻辑

3. **[synchronizer_page.py](src/ui/synchronizer_page.py)**：
   - `_on_row_clicked` 增加 `self.setFocus()` 确保点击行后焦点回到打轴页
   - 移除 `keyPressEvent` 中冗余的 `_BLOCKED_AUDIO_ACTIONS` 守卫（由应用过滤器统一处理）
   - 修复 `SYNC` 处理器中重复的 `event.accept()` 调用

### 涉及文件

```
修改：src/ui/content_stack.py        — 全局 QSS：透明主题色→不透明 + contrast_text + homeSyncButton/loadAudioFileBtn 规则
修改：src/ui/synchronizer_page.py    — 行样式、时间戳按钮文字、空格按钮样式、_contrast_for_theme()、setFocus()、_restyle_space_button()
修改：src/ui/main_window.py          — 事件过滤器升级为 QApplication 全局 + 选歌词时拦截逻辑
修改：src/ui/audio_controls.py       — 新增 on_current_time_changed() 暂停时更新显示
修改：src/ui/home_page.py            — btn_sync 移除内联 stylesheet
修改：src/ui/load_audio_dialog.py    — file_btn 移除内联 stylesheet
修改：src/ui/preferences_page.py     — 颜色按钮增加 hover 规则
修改：main.py                        — 连接 current_time_changed → audio_controls
修改：PROGRESS.md                    — 本文件
```

---

## 2026-07-21 — 快捷键系统

### 新增功能

**6 个新快捷键**（均可在设置页自定义）：

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+C` | 复制选中歌词行到下方（同文本+时间戳） |
| `Ctrl+D` | 拆分当前歌词行 |
| `Ctrl+S` | 保存覆写源文件 |
| `Ctrl+Shift+S` | 导出/另存 |
| `Ctrl+T` | 切换翻译编辑模式 |
| `Ctrl+L` | 预览 LRC 输出 |
| `Ctrl+R` | 加载音频文件 |

**鼠标操作增强：**
- **双击**歌词行文本区域 → 进入编辑模式
- **Ctrl+点击**歌词行文本区域 → 在其下方追加空行

**智能快捷键上下文：**
- 歌词编辑页选中行时：音频快捷键（`←` `→` `R` `L` `A` `D` `H`）自动屏蔽
- 按 `Esc` 取消选中后：音频快捷键恢复，空格键切换为播放/暂停
- 其他页面不受影响

### Bug 修复

1. **Ctrl+Shift+S 被误识别为 Ctrl+S** — `keybinding.py` 中 Shift 匹配逻辑修正：Ctrl 组合键时 Shift 改为双向严格匹配
2. **Ctrl+T 只亮按钮不触发功能** — `synchronizer_page.py` 中 `toggle()` 改为手动调用 `_on_translate_toggle()`
3. **音频快捷键导致崩溃** — `audio_manager.py` 中 `step()` 的 modifiers 类型判断从 `hasattr(__contains__)` 改为 `isinstance(dict)`

### 快捷键自定义系统

- **设置页新增「快捷键」栏目**（默认折叠），展示全部 27 个动作，按 5 个分组排列
- **点击编辑** → 弹出捕获对话框，按任意组合键即时预览
- **清除/重置** → 单条或全部恢复默认
- 自定义快捷键持久化到 `config.json`，重启后保留

### 设置页 UI 改进

- 所有 8 个设置栏目改为**可折叠**（`_CollapsibleGroup` 组件）
- 常用栏目默认展开，低频栏目默认折叠

### 涉及文件

```
修改：src/core/constants.py        — 新增 7 个 InputAction 值
修改：src/core/keybinding.py       — 重构：标签、分组、序列化、用户覆盖层
修改：src/core/lrc_state.py        — 新增 copy_line() 方法
修改：src/core/audio_manager.py    — 修复 step() 类型检测 bug
修改：src/core/config_manager.py   — 新增 get_keybindings()/set_keybindings()
修改：src/ui/main_window.py        — 启动时加载自定义快捷键
修改：src/ui/synchronizer_page.py  — 快捷键处理、双击/Ctrl+点击、条件音频转发
修改：src/ui/preferences_page.py   — 可折叠栏目、快捷键编辑 UI
修改：STRUCTURE.md                 — 同步更新结构文档
新增：PROGRESS.md                  — 本文件
```

---

## 2026-07-21 — AI 辅助翻译功能（22 次迭代）

### 概述

新增"AI 辅助"翻译功能，作为"模式匹配"对话框的子功能。涉及 5 个文件，累计 +1042/-69 行。

---

### 一、AI 辅助翻译 — 核心功能

**入口**：翻译模式 → 模式匹配弹窗 → "AI辅助" 按钮

**弹窗结构**（QDialog + QStackedWidget，三页）：

1. **选项页** — "模型聊天网站" / "API 自动" 两个入口按钮
2. **模型聊天网站页** — "生成并复制提示词" 按钮 + DeepSeek Chat / Kimi 快捷链接
3. **API 自动页** — 多模型配置管理 + 一键翻译

**提示词生成逻辑** (`_build_prompt_text()`)：
- 提取全部有时间戳且文本非空的行，排除元信息标签
- 格式：`[时间戳]歌词正文\n...\n\n请帮我翻译歌词，翻译给出和原文相同的时间戳…`

**相关提交**：`5c9a609`, `b251559`

---

### 二、API 自动翻译

**完整流程**：
1. 生成提示词 → 后台线程调用 API → 结果保存到 `{stem}_translation.txt`
2. 弹窗确认"翻译完成" → [查看] 按钮打开可编辑结果对话框（Consolas 13pt，匹配编辑器风格）
3. 结果对话框提供 [复制] [填入模式匹配] 按钮
4. API 自动**不处理模式匹配** — 仅提供翻译结果，匹配由用户手动完成

**API 调用细节**：
- `urllib.request` 在后台线程执行
- 15 秒看门狗定时器防止连接挂死
- 失败时显示诊断信息（掩码 Key、URL、Model、排查提示）

**相关提交**：`f84485b`, `79694a3`, `0b02d6f`, `c48ca58`, `de9ec54`, `eb9b330`

---

### 三、多模型 API 配置管理

**存储**：`%AppData%/Roaming/lrc-maker/config.json`（项目目录外，不上传 GitHub）

**加密**：Windows DPAPI (`CryptProtectData` / `CryptUnprotectData`)，零额外依赖
- 新建 `src/core/crypto_utils.py`（纯 ctypes 调用 `crypt32.dll`）
- 加密字段：`name`, `url`, `api_key`, `model`

**配置结构**：`apiConfigs` 列表，每项为独立的命名配置
- 支持旧版单 `apiConfig` 自动迁移
- 模型列表每行：[名称+URL信息] [翻译] [测] [✕]
  - `[测]` — 测试连接
  - `[✕]` — 删除配置

**"测试并保存"按钮**：测试与保存合并为原子操作 — 测试失败不保存

**相关提交**：`b251559`, `c1830be`, `b2a274f`, `0e5be7f`, `8d19706`, `40ec932`

---

### 四、API 配置表单布局修复

**问题**：QSS 样式表触发了 QLineEdit 的私有渲染路径，导致布局引擎计算的 `minHeight`/`maxHeight` 被忽略，Label 无法与输入框对齐。

**解决过程**：
1. 尝试使用 `QFormLayout` — Label 对齐难以用数值精确控制
2. 尝试 QSS 设置 `min-height`/`max-height` — 对 QSS 设定了的 QLineEdit 无效
3. 终局方案：所有输入框改用 `setFixedHeight(INPUT_H)`，去掉 QLineEdit 上的全部 QSS
4. 布局改用 `_row(label_text, input_widget)` 辅助函数 — 创建 `QHBoxLayout`：`[80px 固定宽度 Label | stretch 输入框]`

**相关提交**：`b066ed8`, `b068e6e`

---

### 五、Toast 弹窗修复

**问题**：Child widget 上的 `WA_TranslucentBackground` 在 Windows 上无效 → 启动时出现黑色方块；Toast 被模态弹窗遮挡看不到。

**解决**：
- `ToastOverlay` 改为顶级无边框窗口（`Tool | FramelessWindowHint | WindowStaysOnTopHint`）
- 通过 `installEventFilter` 监听主窗口移动/缩放 → 自动重新定位
- 默认 `hide()`，有 toast 时才 `show()` — 避免透明背景问题
- AI 流程中的关键错误提示改用 `QMessageBox` 确保在模态弹窗之上可见

**相关提交**：`362d7b4`, `b7066c5`, `96f32f5`, `243dbea`

---

### 六、键盘输入修复

**问题**：弹窗中输入框无法键入 `L`、`A`、`H` 等按键 — `MainWindow.eventFilter` 作为全局事件过滤器拦截了这些键（用于音频快进/快退/速率）。

**解决**：在 `eventFilter` 中检查焦点控件类型 — 若为 `QLineEdit`、`QPlainTextEdit`、`QTextEdit`，放行事件不做拦截。

**相关提交**：`c1830be`

---

### 七、歌词行编辑模式修复

**问题**：双击歌词行进入编辑模式后，点击其他行切换选中时，旧行仍处于编辑状态。

**解决**：`_refresh_rows` 新增 `_prev_select_idx` 追踪，当 `select_index` 变化时对旧行调用 `exit_edit_mode()`（同时退出编辑模式和拆分模式）。

**相关提交**：`7461ae2`

---

### 八、模式匹配填充动画自动滚动

**功能**：模式匹配逐行填充翻译时，滑条自动跟随当前行向下滚动，确保始终能看到正在被填充的行。

**实现**：在 `_fill_one`（QTimer 驱动的动画函数）中调用 `self._scroll_to_row(idx)`。

**相关提交**：`8091d42`

---

### 九、UI 细节优化

- **Emoji → 纯文字**：所有按钮上的 emoji 图标替换为简短中文文字，避免不同平台渲染差异
- **按钮样式统一**：模型列表行的 [翻译] [测] [✕] 按钮统一风格，不再只有"翻译"有颜色

**相关提交**：`d479f74`, `e4625f0`

---

### 新增文件

| 文件 | 用途 |
|------|------|
| `src/core/crypto_utils.py` | Windows DPAPI 加密/解密（base64 编码，JSON 安全存储）|

### 修改文件

| 文件 | 主要变更 |
|------|---------|
| `src/ui/synchronizer_page.py` | AI 辅助弹窗、提示词生成、API 调用、翻译结果流、多模型管理 UI |
| `src/core/config_manager.py` | `apiConfigs` 多模型配置存储（加密）、旧格式迁移 |
| `src/ui/main_window.py` | 全局事件过滤器 — 文本输入控件放行键盘事件 |
| `src/ui/toast_overlay.py` | 改为顶级置顶窗口，支持模态弹窗上层显示 |

### 涉及文件

```
修改：src/ui/synchronizer_page.py    — AI 辅助弹窗、提示词生成、API 调用、翻译结果流、多模型管理 UI
修改：src/core/config_manager.py     — apiConfigs 多模型配置存储（加密）、旧格式迁移
修改：src/ui/main_window.py          — eventFilter 文本输入控件放行键盘事件
修改：src/ui/toast_overlay.py        — 改为顶级置顶窗口，支持模态弹窗上层显示
新增：src/core/crypto_utils.py       — Windows DPAPI 加密/解密（base64 编码，JSON 安全存储）
```

### 后续待办

- [ ] API 自动可考虑每次调用输入密码验证
- [ ] 可添加更多 AI 聊天网站链接

---

## 2026-07-22：API 自动翻译修复 —— urllib → openai 库 + QThread

### 背景

AI 辅助翻译（API 自动）功能上线后发现连接始终不成功——不是报错，而是一直卡住无响应。经过排查，根因有两个层面。

### 根因分析

#### 第一层：urllib.request 在 Windows 上调用 DeepSeek API 失败

最初的 API 调用使用标准库 `urllib.request`：

```python
req = urllib.request.Request(url, data=body, headers={...})
urllib.request.urlopen(req, timeout=180)
```

而用户手动写的 `test.py` 使用 `openai` 库却能成功：

```python
client = OpenAI(api_key=..., base_url="https://api.deepseek.com")
client.chat.completions.create(model=..., messages=[...])
```

对比后发现：`openai` 底层用 `httpx`，`urllib` 的默认行为（无 User-Agent、SSL 差异等）导致 DeepSeek API 网关拒绝连接。

**修复**：三处 API 调用全部从 `urllib.request` 改为 `openai.OpenAI`，新增 `_extract_base_url()` 辅助函数把完整 URL（如 `https://api.deepseek.com/v1/chat/completions`）转为 `openai` 需要的 base_url（`https://api.deepseek.com`）。

#### 第二层：threading.Thread + httpx 在 Windows 上死锁

切换 `openai` 库后，极简命令行版（主线程）能跑通，但 GUI 版（`threading.Thread` 后台线程）依然卡死。原因是 `httpx`（openai 底层 HTTP 库）在 Windows 非主线程中存在兼容性问题——同步 `create()` 调用无限阻塞，不抛异常也不返回。

**修复**：创建 `_ApiWorker(QThread)` 类替代 `threading.Thread`，QThread 是 Qt 的原生线程，在 Windows 上和 `httpx` 兼容。三处 API 调用（翻译、模型列表测、配置表单测）全部改为 `_ApiWorker`，通过 `pyqtSignal` 把结果传回主线程。

#### 第三层：openai 默认超时 10 分钟

`openai.OpenAI()` 底层 `httpx` 默认超时 600 秒。不加 `timeout` 参数时，请求一旦卡住就等 10 分钟。

**修复**：翻译接口设 `timeout=180.0`（长提示词需要时间），测试接口设 `timeout=10.0`。

### 附加修复

| 问题 | 修复 |
|------|------|
| 翻译文件名用错歌名（`Yellow Star Beats_translation.txt`） | `_save_translation_txt` 优先取当前音频文件名，不再依赖可能过时的 LRC 路径 |
| "填入模式匹配"按钮报错 `NameError: name 'txt_path' is not defined` | `txt_path` 从 `_show_done` → `_show_result` → `_fill_pattern_match` 逐层传参 |
| API Key 明文在异常日志中出现过长 | 错误提示中 Key 只显示前 8 位 + `****` + 后 4 位 |

### 新增文件

| 文件 | 用途 |
|------|------|
| `test_api_connectivity.py` | 独立的 API 连通性测试工具（GUI + 命令行双模式），使用 `openai` 库 |

### 修改文件

| 文件 | 主要变更 |
|------|---------|
| `src/ui/synchronizer/_ai_assist.py` | urllib → openai 库；threading.Thread → _ApiWorker(QThread)；超时参数；翻译文件名修复；txt_path 传参修复 |
| `test_api_connectivity.py` | threading.Thread → _TestWorker(QThread) |

**相关提交**：`a676fb2`, `96bc806`

