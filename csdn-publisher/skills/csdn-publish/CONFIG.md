# CSDN 工具：全部可配置参数（一一列举）

> 这份文件是 `/csdn-publish config` 的输出源 —— 改这里，`config` 就跟着变。
> 命令行看同一份内容：`.\csdn.bat config`（或 `python publish_csdn.py config`）。
> **开头「⭐ 最常用的几个」就是最该改的那几项**（三种发布方式在最前面）；
> 参数分四层：**任务文件顶层** → **单篇文章** → **字段取值语法** → **config.json**。
> 最后两节是命令行开关和优先级规则（不改文件也能改行为）。

---

## ⭐ 最常用的几个（90% 的情况只改这里）

### 1. 三种发布方式 —— `publish.on`，一次只写一个

```jsonc
// ① 直接发布（立即公开）
"publish": { "on": "yes" }

// ② 存草稿（真实提交但只进草稿箱，标签/摘要/栏目照样填好）
"publish": { "on": "no" }

// ③ 定时发布（到点自动公开；时间必须在「当前 +4 小时 ~ +6 天」之间）
"publish": { "on": "schedule", "schedule": "2026-10-08 16:30" }
//   schedule 也能写 "auto"（自动挑你自己数据里阅读最好的时段）
//   "+26h" / "+3d"（相对现在）；多篇想错开再加 "scheduleGap": "1d"
```

- 只写 `"schedule": "…"`、不写 `on`，也按定时走。
- 命令行等价物：`--draft` 存草稿、`--schedule "+26h"` 定时、`--dry-run` 只演练不提交。
- 想改**已经存进草稿箱**的稿子：`python csdn_schedule_drafts.py --at "2026-10-08 16:30"`（不动正文）。

### 2. 发什么主题、一次几篇

```jsonc
"search": { "keywords": ["Go 切片", "goroutine 泄漏"], "minViews": 3000 },
"count": 3
```

`search.keywords` 和 `search.minViews` **必须你自己定**（我不会替你猜）；`count` 是篇数，
也可以改用 `articles` 逐篇写。

### 3. 另外三个最常改的

| 想改什么 | 写在哪 | 例子 |
|---|---|---|
| 每篇标签 | 单篇/顶层 `tags` | `"tags": ["Go", "并发"]`（手写最贴题；auto 是启发式） |
| 要不要封面 | 单篇/顶层 `cover` | `""` = **不要封面**；`"file:xx.png"` = 指定图；不写这个键 = 自动生成 |
| 栏目 | 单篇/顶层 `category` | `""` = **不放入任何栏目**；字符串或数组，最多 3 个 |

> 下面是全部参数，按「任务文件 → 单篇文章 → 字段语法 → config.json → 命令行 → 优先级」逐项列全。

---

## 0. 两个配置文件在哪

| 文件 | 位置 | 管什么 |
|---|---|---|
| 任务文件 | `__TOOL_DIR__\job.<主题>.json` | 这次发什么主题、几篇、标签/封面/摘要、**发布方式** |
| 运行配置 | `__TOOL_DIR__\config.json` | 浏览器、默认标签、**页面选择器** |

`config.json` **只需要写你想改的键**，缺的会自动与内置默认值合并（`selectors` 是逐个选择器补的，所以你只写要校准的那一个也行）。`job.*.json` 可以随便加 `_` 开头的键当注释，代码会忽略。

---

## 一、任务文件顶层（`job.<主题>.json`）

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `search.keywords` | 字符串数组 | **必填** | 搜索关键词，多个就多个；决定素材来源与标签 auto 的权重 |
| `search.minViews` | 整数 | **必填** | 最低阅读量门槛，低于它的素材不算 |
| `search.wantSources` | 整数 | `6` | 最多抓几篇达标素材的正文 |
| `search.pages` | 整数 | `2` | 每个关键词最多翻几页（CSDN 前端分页不可靠，多给 keywords 更有效） |
| `count` | 整数 | `0` | 简写：展开成 N 篇全 auto 的文章（与 `articles` 二选一） |
| `articles` | 数组 | `[]` | 逐篇配置（写了它就不用 `count`） |
| `maxTags` | 整数 | `7`（config.json 的 `max_tags`） | 每篇标签上限 |
| `prependFile` | 路径 | `""` | 文章开头固定横幅，写 `templates\start.txt`（任务文件里的相对路径按 `jobs\` 解析，裸文件名会自动去 `templates\` 找） |
| `prependTrim` | `true`/`false` | `true` | 清理横幅尾部的空行和 `---` 分隔线 |
| `coverFooter` | 字符串 | `""` | 自动生成封面时右下角小字（如你的博客名） |
| `title` | 见第三节 | 不写 = auto | **所有文章**的标题默认值（单篇可覆盖） |
| `tags` | 见第三节 | 不写 = auto | 所有文章的标签默认值 |
| `cover` | 见第三节 | 不写 = auto 生成 | 所有文章的封面默认值 |
| `coverTheme` | `blue`/`purple`/`teal`/`orange`/`rose`/`dark` | 按篇轮换 | 自动封面的配色 |
| `summary` | 见第三节 | 不写 = auto | 所有文章的摘要默认值 |
| `category` | 字符串 / 数组 | `""` | 所有文章的栏目默认值；`""` = 不放入任何栏目，最多 3 个 |
| `publish.on` | `"yes"` / `"no"` / `"schedule"` | 不写退回 `dryRun`/`draft` | `yes` 立即公开；`no` 只进草稿箱；`schedule` 定时发布 |
| `publish.schedule` | `"auto"` / `"2026-10-08 16:30"` / `"+26h"` / `"+3d"` / 数组 | `"auto"` | 定时时间；**只写 schedule、不写 on 也按定时走** |
| `publish.scheduleGap` | `1d` / `6h` / `90m` / `0` | `1d` | `schedule=auto` 且多篇时，篇与篇的间隔 |
| `publish.dryRun` | `true`/`false` | `true` | 只在**没写 `on`** 时生效：true = 填完不提交 |
| `publish.draft` | `true`/`false` | `false` | 只在**没写 `on`** 时生效：true = 存草稿 |
| `_任意名` | 任意 | — | 注释键，代码忽略（例：`"_说明": "..."`） |

> 顶层同名键 = 所有文章的默认值，**唯一例外是 `content`**（它不参与回落，否则 N 篇会共用一份正文）。

---

## 二、`articles[]` 里每篇能写的

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `slug` | 字符串 | `article-<序号>` | 正文文件名 `posts/<slug>.md`，也是成品文件名 |
| `id` | 字符串 | — | `slug` 的别名 |
| `title` | 见第三节 | auto | 标题 |
| `content` | `auto` / `file:路径` / 字面值 | `auto` | 正文来源；auto = `posts/<slug>.md`（要你自己写） |
| `tags` | 见第三节 | auto | 标签（推荐直接写 `["Go","并发"]`） |
| `cover` | 见第三节 | auto 生成 | 封面 |
| `coverTheme` | 六种配色 | 按篇轮换 | 自动封面配色 |
| `summary` | 见第三节 | auto | 摘要 |
| `category` | 字符串 / 数组 | 顶层 `category` | 栏目，最多 3 个 |
| `search` | 对象 | 顶层 `search` | 这一篇单独用另一组搜索条件 |
| `_任意名` | 任意 | — | 注释键（例：`"_进度": "第 2 篇"`） |

---

## 三、六个字段的取值语法（`title`/`content`/`tags`/`cover`/`summary`/`category` 通用）

| 写法 | 含义 |
|---|---|
| `"auto"` | 自动推导（含义见下表） |
| `"file:路径"` | 从文件读；相对路径按 **job 文件所在目录** 解析 |
| 其他字符串 | 字面值直接用（`tags` 可用 `"a,b"` 逗号分隔，也可直接用 JSON 数组） |
| `null` / `""` | 只有 `cover`/`category` 有"留空 = 不要"的语义 |

| 字段 | auto 的含义 |
|---|---|
| `title` | 取正文 front-matter 的 `title`，没有就取正文第一个 `# 一级标题` |
| `content` | ★ 脚本**不会写文章**：auto 只生成骨架，必须你把正文写进 `posts/<slug>.md` |
| `tags` | 按「素材文章标签 × 搜索关键词」加权推导（启发式，可能不贴题；在意质量就手写） |
| `cover` | 按标题生成渐变封面，每篇自动换配色。**`""`/`null`/`none` = 不要封面**，不写这个键才是 auto |
| `summary` | 取正文纯文本前 110 字（注意会把一级标题也带进去） |
| `category` | 留空或 auto = **不放入任何栏目**（合法且常用） |

---

## 四、`config.json`（浏览器 / 默认值 / 选择器）

### 4.1 顶层

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `browser_channel` | `"chrome"` / `"chromium"` | `"chrome"` | 用系统 Chrome（无需 `playwright install`）；启动失败会自动回退自带 chromium |
| `headless` | `true`/`false` | `false` | 有头模式（方便你看过程 / 扫码登录） |
| `urls.home` | URL | `https://www.csdn.net/` | 登录与登录态检测入口 |
| `urls.editor` | URL | `https://editor.csdn.net/md/` | 编辑器入口 |
| `login_cookies` | 字符串数组 | `["UserName","UserToken","UserInfo"]` | 判定"已登录"的 cookie 名 |
| `default_tags` | 字符串数组 | `["Python"]` | 标签 auto **推不出来时**的兜底（最好别用到，手写 tags 更稳） |
| `default_category` | 字符串 | `""` | 默认栏目 |
| `max_tags` | 整数 | `5`（本机 config.json 里是 7） | 标签数量上限，超出 CSDN 会拒 |
| `step_wait_ms` | 整数（毫秒） | `1200` | 每步之间的等待，网络慢就调大 |

### 4.1.1 `browser`（浏览器健壮性 · 2026-10-08 新增）

治两个反复出现的现象：①「Chrome 未正常关闭，是否恢复」→ 下次 profile 被锁、浏览器起不来；
②「是否翻译此页面」气泡盖在编辑器上**拦截点击**，表现成"元素点不到"。

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `cleanStaleProfile` | `true`/`false` | `true` | 启动前清理上次崩溃留下的 profile 锁（`lockfile`/`SingletonLock`）。**只在确认没有活着的浏览器占用时才删**（靠心跳文件判断），有人在用会拒绝启动而不是硬删 |
| `disableTranslate` | `true`/`false` | `true` | 关掉 Chrome 内置翻译（启动参数 + 页面级兜底关闭）。**翻译气泡是"点不到元素"的隐形元凶之一** |
| `disableCrashBubble` | `true`/`false` | `true` | 关掉「未正常关闭，是否恢复」气泡 |
| `hideAutomationInfoBar` | `true`/`false` | `true` | 关掉「正在被自动化软件控制」信息栏 |
| `launchRetries` | 整数 | `2` | channel 启动失败时的额外重试次数 |
| `launchRetryWaitMs` | 整数（毫秒） | `2500` | 重试之间的等待；每次重试前会再清一遍锁和残留进程 |
| `killLingeringOnLaunch` | `true`/`false` | `true` | 启动前杀掉"用着本项目 `.profile` 的残留 Chrome"。**必须开**：残留进程占着 `user_data_dir` 时，Chrome 走单例握手直接退出，Playwright 只报 `TargetClosedError` —— 不清进程，重试多少次都必然失败。只杀命令行里带本项目 `.profile` 的进程，绝不动你自己的 Chrome |
| `heartbeat` | `true`/`false` | `true` | 写 `.browser-heartbeat.json`（含 pid），用来判断锁是陈旧的还是真的有人在用。**本进程自己写的心跳不算"被占用"**，否则第一次启动失败后的重试会被自己拦住 |

### 4.1.2 `risk`（风控闸门 · 2026-10-08 新增）

> ⛔ **第 0 条（最高优先级）**：任何操作一旦认出滑块/安全验证，就**立刻停手、无限期等真人处理**；
> `waitTimeoutSec`、`maxMinutes`、每篇之间的节奏间隔、排期时间等**任何时间限制都不许抢先结束等待**，
> 也不许"跳过这篇继续下一篇"。详见 SKILL.md 文首「第 0 条」。

CSDN 的安全验证是**风控不是 bug**：识别出来 → 停下来 → **真人点一下** → 自动续跑。
本工具**绝不自动过验证码**（自动化过码会让风控升级，代价远大于手动点 10 秒）。

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `onDetection` | `"wait"` / `"abort"` | `"wait"` | `wait`=停下等你手动完成验证，检测到通过后原地续跑（**默认，也是最高优先级行为**）；`abort`=直接放弃（无人值守/任务队列用，退出码 5，**只能由用户显式指定，agent 不得代他决定**） |
| `waitTimeoutSec` | 整数（秒） | `300` | 等人处理的最长时间，超时按放弃处理。⚠️ **与第 0 条尚未对齐**：按规则它不该抢先结束等待，属**待改项**；现状下真超时了就当"没做完"，**绝不许记成成功** |
| `pollMs` | 整数（毫秒） | `1500` | 检测验证是否通过的轮询间隔 |
| `beep` | 整数 | `3` | 命中后响几声提示（Windows 用 `winsound`，其他平台忽略失败） |
| `checkBeforeStep` | `true`/`false` | `true` | 每步动手前都检查一次页面，防止在上一步间隙被拦 |

命中时会在 `output/NEEDS-HUMAN.txt` 追加一条提示（人不在终端也能看到），并截图 `*-risk-blocked.png`。

**作用范围（重要）**：目前**只有发布链路**（`publish_csdn.py`）接了这个闸门。
**评论/点赞/收藏（`csdn_interact.py`）和私信（`csdn_message.py`）都没有接入**——
跑这两条链路时 agent 要自己盯住页面，一发现滑块/安全验证就按第 0 条停手、把用户喊来、等到底。

### 4.1.3 `crash`（崩溃自愈 · 2026-10-08 新增）

浏览器异常关闭（`TargetClosed` / 进程没了 / 恢复气泡）时自动重新拉起再跑一遍。

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `onCrash` | `"restart"` / `"abort"` | `"restart"` | `restart`=自动重启浏览器续跑（退出码 6 表示重试用尽仍失败）；`abort`=放弃 |
| `maxRestarts` | 整数 | `2` | 整条链路最多重启几次（`0` 等于不重启） |
| `restartWaitSec` | 整数（秒） | `5` | 重启之间等待，给 CSDN 服务端落草稿留时间 |

**为什么重跑是安全的**：内容没提交成功时草稿箱不会留下半成品，而 CSDN 编辑器会恢复上次内容，
所以重跑不会变成重复发布。**注意**：只有"浏览器没了"类异常才重启；
选择器找不到元素这种业务错误不会触发重启（白等没意义），直接报错退出码 1。

### 4.2 `selectors`（每个是一组选择器，**按顺序取第一个可见的**）

| 键 | 默认值 | 说明 |
|---|---|---|
| `title` | `input.article-bar__title` | 标题输入框（默认 `display:none`） |
| `title_display` | `.article-bar__title-display` | 标题占位区，**必须先点它**才能填标题 |
| `editor` | `pre.editor__inner` | 正文编辑器（Cledit 的 contenteditable，不是 CodeMirror） |
| `import_file` | `#import-markdown-file-input` | 隐藏 file input，用 `set_input_files` 导入正文 |
| `publish_button` | `button.btn.btn-publish` | 编辑器上的「发布文章」（另有 `button.simulation-button` 同名，别选错） |
| `save_draft` | `button.btn.btn-save` | 编辑器上的「保存草稿」（**不带**标签/摘要，正式流程不用它） |
| `save_draft_dialog` | `button.button.btn-b-normal.ml16:has-text('保存为草稿')` | 发布弹窗里的「保存为草稿」，存草稿走这个 |
| `tag_button` | `button.tag__btn-tag:has-text('添加文章标签')` | 标签不是自由文本框，要先点开浮层 |
| `tag_input` | `input[placeholder*='请输入文字搜索']` | 标签浮层里的搜索框，输入后**回车**添加 |
| `category_trigger` | `.form-entry:has-text('分类专栏') .el-select` | 栏目下拉（本工具改走 JS 点 label，见 `tag__option-chk`） |
| `confirm_publish` | `button.btn-b-red` | 弹窗里红色的「发布文章」 |
| `cover_input` | `input.el-upload__input` | 封面上传入口 |
| `cover_confirm` | `.vicp-operate-btn` | 裁剪窗的「确认上传」（**是 div 不是 button**） |
| `summary_input` | `.modal__publish-article textarea.el-textarea__inner` | 摘要输入框（选填） |
| `schedule_button` | `.publish-article-modal__footer button:has-text('定时发布')` | 弹窗底部的「定时发布」（点它才会请求可选区间接口） |
| `schedule_dialog` | `.el-dialog.publish-timer-dialog` | 定时发布子弹窗 |
| `schedule_date_input` | `.publish-timer-dialog input[placeholder*='选择日期']` | 选择日期，可直接 `fill()` + 回车 |
| `schedule_time_input` | `.publish-timer-dialog input[placeholder*='选择时间']` | 选择时间：**选完日期才解除禁用**，只有 15 分钟一档 |
| `schedule_confirm` | `.publish-timer-dialog button.btn-b-red` | 子弹窗里真正提交的「定时发布」 |
| `schedule_cancel` | `.publish-timer-dialog button.btn-b-normal` | 子弹窗的「取 消」（文案中间有空格，`has-text('取消')` 匹配不到） |

> CSDN 改版时：`inspect` 导出真实结构 → 改这里的值，**不要改代码**。

### 4.3 `interact`（一键三连 · 点赞/收藏/评论）

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `commentFile` | 路径 | `templates/comment.txt` | 评论文案来源。**改这个文件即可换话术**，两行原文原样发出（含换行） |
| `keywords` | 字符串数组 | 16 个赛道词 | 搜索页候选池的关键词。**加词是扩大候选池最有效的手段** |
| `excludeTitleKeywords` | 字符串数组 | 征文/榜单/福利… | 标题命中就跳过 |
| `excludeAuthors` | 字符串数组 | `[]` | 作者名**精确**排除 |
| `excludeAuthorNameKeywords` | 字符串数组 | 简历/齿科/有限公司… | 作者名**含**这些词就跳过（专治营销号） |
| `defaults.days` | 整数 | `7` | 发布时间窗口（天）。列表日期只到天，进详情页再用 `.time` 精确校验 |
| `defaults.maxFans` | 整数 | `10000` | 作者粉丝上限。0 = 不限。**大 V 看不到你的通知** |
| `defaults.pagesPerKw` | 整数 | `3` | 每个关键词翻几页 |
| `defaults.delayMinSec` / `delayMaxSec` | 整数 | `15` / `30` | 每篇之间随机停多少秒（防风控）。**别调小** |
| `defaults.maxMinutes` | 整数 | `45` | 单轮时间上限，0 = 不限。到点就停并如实报"实做 X/目标 N" |
| `defaults.commentLimitStreak` | 整数 | `3` | 评论连续被限流几次后，本轮不再尝试评论 |
| `defaults.minBodyChars` | 整数 | `400` | **正文少于这么多字就只做赞/藏、不评论**（读不到正文 ≈ 写不出真感悟） |
| `farmFilter.origMin` / `fansBelow` | 整数 | `150` / `500` | 内容农场判据：原创 ≥ `origMin` **且** 粉丝 < `fansBelow` → 跳过 |
| `urls.search` | URL 模板 | `...&s=0`（综合） | **别改成 `s=new`**：实测搜索页的「最新」排序是坏的 |
| `selectors.*` | 选择器数组 | 见 `config.json` | 三连按钮 / 评论框 / 发布时间 / 作者资料。**按顺序取第一个可用的** |

**三连按钮的状态锚点**（"点之前先读真实状态"就靠它们，别改坏）：

| 操作 | 容器 | 未做 | 已做 |
|---|---|---|---|
| 点赞 | `li#is-like` | `#is-like-img`(display:block) | `#is-like-imgactive`(可见) |
| 收藏 | `li.tool-item.is-collection` | `#is-collection-img` | `#is-collection-imgactive`(可见) |
| 评论 | `li.tool-item-comment > a.go-side-comment` | 面板 `#pcCommentBox` 隐藏 | 输入框 `.comment-box textarea` |
| 关注 | `.tool-attend` | `tool-bt-attend` | `tool-unbt-attend` |

> 交互状态存在 `output/interact/interact.db`（SQLite，按「文章 × 操作」两维记账），
> 不是"这篇文章处理过没有"——某篇点赞成功、评论被限流拦下，下次跑只补评论，不会重点赞。

---

### 4.4 `message`（私信群发 · 给关注的人 / 粉丝发同一条私信）

| 参数 | 取值 | 默认 | 作用 |
|---|---|---|---|
| `file` | 路径 | `templates/message.txt` | 私信正文来源。**改这个文件即可换内容**，纯文本原样发送（含换行） |
| `maxChars` | 整数 | `500` | 单条上限（= 页面上的 `x/500` 计数）。**超了直接拒绝发送**，不截断 |
| `pendingTtlMin` | 整数 | `120` | `--wait` 存下的待确认名单多久过期；过期后 `--yes` 会重新抓名单 |
| `blockHints` | 字符串数组 | 发送频繁/被拉黑/拒收… | 命中页面提示就**停止本轮群发**（不硬撞风控） |
| `limitHints` | 字符串数组 | 陌生人 / 对方尚未回复… | 私信限额提示，实测两种：①「24小时内最多与5个陌生人发送消息」②「对方尚未回复你，你只能发送1条消息」。命中记 `failed_dm_limit`（**这条没送到**） |
| `urls.chat` | URL 模板 | `im.csdn.net/chat/{user}` | 私信会话页。实测可直接用 URL 打开某个人的会话 |
| `urls.fans_page` / `follow_page` | URL 模板 | `blog.csdn.net/{me}?type=sub&subtype=fans\|follow` | **只用来**①确认这是我自己的主页 ②读头部「N 粉丝 / N 关注」做**对账**。它的列表 DOM 写死 20 条，**不要拿它当名单来源** |
| `fansApi.base` | URL | `mp-action.csdn.net/interact/wrapper/pc/fans/v1/api` | ★ **名单的真正来源**。在个人主页里用 `fetch()` 发起（带登录 cookie） |
| `fansApi.fans` / `following` | 字符串 | `getFansOffsetList` / `getFollowOffsetList` | 两个接口名。请求形如 `{base}/{fans}?pageSize=N&username=<我>` |
| `fansApi.minPageSize` / `maxPageSize` | 整数 | `200` / `2000` | 一次最少/最多要多少条。**`pageSize` 是唯一旋钮**（没有 offset/page 参数），调大就能取全；超出总数会自动截断 |
| `fansApi.headroom` | 整数 | `50` | 按主页总数要多留的余量 |
| `fansApi.retries` / `retryWaitMs` | 整数 | `3` / `2500` | 失败退避重试。**必须留着重试**：短时间高频打这个接口会被网关在 TLS 层掐断（`ssl3_read_bytes tlsv1 alert internal error`），之后连正常请求也连着失败 |
| `fansApi.timeoutMs` | 整数 | `20000` | 单次接口请求超时 |
| `defaults.delayMinSec` / `delayMaxSec` | 整数 | `5` / `12` | 两条私信之间随机停多少秒（防风控）。**别调小** |
| `defaults.maxPages` | 整数 | `10` | **只在接口不可用、退回 DOM 抓取时**才有意义（那个视图固定 20 条、没有分页器） |
| `defaults.sendVerifySec` | 整数 | `12` | 点发送后最多等几秒确认「输入框清空 + 消息里出现本条」 |
| `defaults.sendSettleSec` | 整数 | `3500` | 点发送后**先等这么久再下结论**（毫秒）。CSDN 会乐观渲染气泡、几秒后被风控系统提示替换；太快判定会把「没送到」误报成「已发送」 |
| `defaults.retryTransientOnce` / `retryWaitSec` | 布尔 / 整数 | `true` / `8` | 只有**原因不明、多半是瞬时**的失败（`failed_unknown` / `failed_no_input` / `failed_fill`）才隔 8 秒重试一次。平台明确拒绝的（限额 / 风控）**不重试**——重试只会白撞风控，应该用 `--skip-sent` 下次补发 |
| `defaults.limitStreak` | 整数 | `3` | 连续几次私信限额就停止本轮。限额按「有没有历史会话 / 对方回没回你」判，不该一命中就全停（很多粉丝有历史会话照样能收到） |
| `selectors.chat_input` | 选择器数组 | `textarea#messageText` … | 私信输入框（首选 `#messageText`） |
| `selectors.send_button` | 选择器数组 | `.send button` … | 发送按钮；都找不到就回退按 **Enter**（页面提示「按下Enter发送内容」） |
| `selectors.fans_tab` / `following_tab` | 字符串数组 | `["粉丝"]` / `["关注的人"]` | 个人主页「关注/订阅/互动」下的二级页签文本 |

**两个跟别处不一样的地方**：

1. **不写 `--yes` / `--wait` 就什么都不发**（唯一例外：`--dry-run` 只填框不发送），
   且 **`--limit` 必须每次指定**（数字或 `all`）——
   这个功能没有"默认帮你发"的路径，`config.json` 里也刻意不设默认人数。
2. **二级页签必须用 JS `click()`**：它的 `<label>` 宽高为 0，`locator.click()` 必然超时
   （和发布弹窗里的「栏目」是同一类坑）。

**名单走接口，不再有 20 人上限**（2026-10-09 改造）：粉丝/关注都从 `fansApi.base` 下的
`getFansOffsetList` / `getFollowOffsetList` 取（在个人主页里用 `fetch()` 发起，自动带登录 cookie），
`pageSize` 调大就能一次取全，实测 **41 粉丝 41/41、208 关注 208/208**。
主页 DOM 那个列表视图**写死 20 条**（没有分页器 / 加载更多），所以它**只用来**确认身份 + 读总数对账；
DOM 抓取只在接口不可用时才作为兜底（那时最多 20 条，并会明确告警）。对账不上就报 `list-cap-*`，
**宁可说没抓全，也不静默少发**。

**去重（两层）**：同一次运行内自动按用户名去重（粉丝 ∩ 关注，实测 249 → 210）；
跨运行要加 `--skip-sent` 才跳过「以前成功发过私信」的人（默认不跳，保证 `--limit all` = 全部粉丝）。

> 记录在 `output/message/message.db`（SQLite：`runs` 每次运行 + `sends` 每个收件人），
> 报告在 `output/message/latest.md`，**这次没送到**的人另存 `output/message/failed.json`
> （补发：`--limit all --yes --skip-sent`）。**按你的要求默认不做整批去重跳过**——
> 同一个人重复跑会重复收到，库里只留档、不拦你。

---

## 五、命令行开关（不改配置文件也能改行为）

```powershell
cd __TOOL_DIR__

# 登录态 / 页面结构 / 参数清单
.\csdn.bat login                                  # 扫码登录（只能你本人做）
.\csdn.bat check                                  # 检查登录态
.\csdn.bat inspect                                # 导出编辑器结构（校准选择器用）
.\csdn.bat config                                 # 打印这份参数清单

# 单篇发布
.\csdn.bat publish posts\demo.md --dry-run        # 填完不提交
.\csdn.bat publish posts\demo.md --draft          # 只进草稿箱
.\csdn.bat publish posts\demo.md --schedule "+26h"  # 定时发布
.\csdn.bat publish posts\demo.md --force          # 体检报错也照发
.\csdn.bat publish posts\demo.md --no-lint        # 跳过体检

# 浏览器健壮性（2026-10-08 新增；这几个开关 login/check/inspect/publish 都能用）
.\csdn.bat publish posts\demo.md --on-risk wait    # 遇到滑块停下来等你点，点完自动续跑（默认）
.\csdn.bat publish posts\demo.md --on-risk abort   # 遇到滑块直接放弃，交给任务队列重跑（无人值守）
.\csdn.bat publish posts\demo.md --on-crash restart # 浏览器崩了自动重启续跑（默认）
.\csdn.bat publish posts\demo.md --on-crash abort   # 崩了就放弃
.\csdn.bat publish posts\demo.md --max-restarts 3   # 最多自动重启几次
.\csdn.bat publish posts\demo.md --no-clean-profile # 启动前不清理陈旧 profile 锁

# 声明式任务（首选）
.\.venv\Scripts\python.exe csdn_job.py                             # 列出所有任务文件
.\.venv\Scripts\python.exe csdn_job.py prepare jobs\job.all.json   # 搜素材+骨架+封面
.\.venv\Scripts\python.exe csdn_job.py status  jobs\job.all.json   # 完成度+体检+排期表
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --only 1 --dry-run
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --schedule auto
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --allow-stale-sources

# 把草稿箱里现成的稿子批量改定时（不动正文）
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-08 16:30" --dry-run
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "+30h" --only 1 3 5 --limit 2

# 看/删 草稿箱与排期稿
.\.venv\Scripts\python.exe csdn_draft_clean.py --tabs              # 有哪些标签页
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 全部 --raw     # 看两个时间
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 草稿箱 --delete 1 --match 标题片段

# 体检（离线）
.\.venv\Scripts\python.exe csdn_lint.py posts\xxx.md --tags Go,并发 -k Go --json

# 素材 / 封面
.\.venv\Scripts\python.exe csdn_research.py -k "云原生" --min-views 1000 --want 8 --pages 2
.\.venv\Scripts\python.exe make_cover.py -t "标题" -s "副标题" --theme purple -o covers\c.png

# 阅读复盘
.\csdn_stats.bat --pages 2 --top 20 --window-days 30
.\csdn_stats.bat --report-only

# 私信群发（正文: templates\message.txt，原样发送）
.\csdn_message.bat --to follower --limit 20 --wait          # 只列名单，一条都不发
.\csdn_message.bat --to follower --limit 20 --yes           # 按确认过的名单真发
.\csdn_message.bat --to follower --limit all --yes --skip-sent  # 只补发没送到的（跳过以前已成功的）
.\csdn_message.bat --to following,follower --limit all --wait
.\csdn_message.bat --to follower --limit 1 --dry-run        # 只演练：填框+截图，不点发送
.\csdn_message.bat --stats                                  # 历史发送记录
```

| 工具 | 开关 | 默认 | 作用 |
|---|---|---|---|
| `csdn_job.py publish` | `--only N [N…]` | 全部 | 只处理指定序号的文章 |
| | `--dry-run` / `--no-dry-run` | 未写 `on` 时 `dryRun` | 强制演练 / 强制真提交 |
| | `--draft` | — | 强制存草稿 |
| | `--schedule WHEN` | 配置里的值 | 强制定时发布（覆盖配置） |
| | `--force` | — | 体检 error 也照发 |
| | `--no-lint` | — | 完全跳过体检 |
| | `--allow-stale-sources` | — | 素材缓存与搜索条件不一致时也继续（标签会退回默认值） |
| `publish_csdn.py publish` | `--draft` `--schedule` `--dry-run` `--force` `--no-lint` | — | 同上（单篇版） |
| | `--on-risk wait\|abort` | `wait` | 遇到安全验证：等人点完续跑 / 直接放弃 |
| | `--on-crash restart\|abort` | `restart` | 浏览器异常关闭：自动重启续跑 / 放弃 |
| | `--max-restarts N` | 2 | 最多自动重启几次 |
| | `--no-clean-profile` | — | 启动前不清理陈旧 profile 锁 |
| `csdn_schedule_drafts.py` | `--at WHEN` | **必填** | 定时时间 |
| | `--dry-run` | — | 只填不提交 |
| | `--only N…` / `--limit N` | 全部 | 只处理第 N 篇 / 最多几篇 |
| `csdn_draft_clean.py` | `--tab 名字` | `草稿箱` | 草稿箱 / 全部 / 已发布 / 审核中 |
| | `--tabs` / `--raw` | — | 列出标签页 / 打印整行原文（看定时发布的两个时间） |
| | `--delete N` `--match 子串` `--expect-time 时间` | — | 删除（带防误删校验） |
| `csdn_lint.py` | `--tags` `--summary` `--cover` `-k` `--json` | — | 覆盖字段再体检 / 输出 JSON |
| `csdn_research.py` | `-k`（必填，可重复） `--min-views` `--want` `--pages` `--out` | 1000 / 8 / 2 | 素材搜集 |
| `make_cover.py` | `-t` `-s` `-o` `--theme` `--footer` `--list-themes` | blue | 生成封面 |
| `csdn_stats.bat` | `--pages` `--report-only` `--top` `--window-days` `--keep-open` `--quiet` | 全部 / 15 / 7 | 阅读复盘 |
| `csdn_message.bat` | `--to following\|follower\|following,follower`（**必填**）<br>`--limit N\|all`（**必填**）<br>`--wait` / `--yes`（**二选一，必填**）<br>`--file` `--dry-run` `--max-pages` `--skip-sent` `--stats` | 无默认人数 | 私信群发：`--wait` 只列名单并存下来，`--yes` 才真发（优先复用待确认名单）；**不写 `--yes`/`--wait` 什么都不发** |
| `csdn_hot.py` | `--limit` `--tab 榜单名` `--fingerprint URL…` `--trend` `--out` | 50 / 综合热榜 | 热榜取数 + 爆款风格指纹；`--trend` 是离线的选题雷达 |
| `csdn_search.py` | `--q 关键词` `--limit` `--deep N` `--no-deep` `--my-pages N` `--out-dir` `--profile` `--focus 词…` `--exclude 词…` `--all` | 自动取 Top 主题词 / 30 / 5 / 3 / go-backend | 选题调研：赛道过滤（核心/学习/弱相关/无关）+ 热度&收藏双榜 → 精读拆结构 → 账号交叉 → `hot_search/<关键词>-<日期>.md` |
| `csdn_image.py` | `render --table/--text --out --title --footnote --theme --lines` / `upload --files… --out --wait` | light / 10s | 正文插图：表格或终端输出→PNG；上传拿 CSDN 图床 URL |
| `csdn_figure.py` | `diagram --code --out --title --note --theme` / `stock --q/--url --license --download --out` | default | 矢量示意图（Mermaid）；可商用配图（搜索 API 本机不通，走直链） |
| `csdn_draft_check.py` | `articleId [articleId…]` | — | 校验草稿里的正文长度与插图是否真的存下来 |
| `exp/new_experiment.py` | `slug --title "…" [--force]` | — | 建"每篇一个实测"的实验三件套（README/run.py/numbers.md） |

---

## 六、优先级与硬性限制（容易踩）

1. **命令行 > 配置文件**：`--dry-run` 绝不提交，`--draft` 强制存草稿，`--schedule` 强制定时。
2. **`publish.on` > `publish.dryRun`/`draft`**：写了 `on` 就不再问、不再看那两个。
3. **`schedule` 非空就按定时走**：此时 `on: "no"`（存草稿）会让位，日志里会明确提示。
4. **顶层是默认值**：`title`/`tags`/`cover`/`coverTheme`/`summary`/`category` 单篇可覆盖；`content` 例外。
5. **定时发布的硬限制**：时间必须落在**当前时间后 4 小时 ~ 约 6~7 天**之间，选择器只有 **15 分钟一档**；越界会在**启动浏览器之前**中止并打印排期表。
6. **发文额度**：`定时发布` 和公开发布一样**占额度**（草稿不占）；额度看编辑器底部「今日发文额度还有 N 篇」。用尽时会弹窗提示，脚本报 `⛔ 被 CSDN 拒绝`（rc=3），不会留下任何东西。
7. **体检**：error（空标题、骨架正文、正文 <500 字、没标签、标签超限、封面文件不存在、和自己已发布文章撞标题）会**拦下发布**；warn（标题没数字/没命中关键词、没封面、摘要过长、不在流量高峰等）只提示，`--force` 只跳过拦截。
8. **两件事必须你定，我不会替你猜**：`search.keywords`、`search.minViews`。登录扫码也只能你本人做。
9. **退出码（看 `rc=` 就知道该怎么办）**：`0` 成功 / `1` 硬错误（选择器变了、元素找不到）/ `2` 结果未确认（看截图）/ `3` 平台拒绝（多半是发文额度）/ `4` 频率限制（等一会儿重跑）/ `5` 遇到安全验证没解决（**内容没提交**，重跑即可）/ `6` 浏览器反复异常关闭（已自动重启过仍失败）。
10. **安全验证不要自动过（第 0 条，最高优先级）**：命中滑块/点选时**立刻停手、无限期等真人处理**，`waitTimeoutSec`/`maxMinutes`/节奏间隔/排期时间都**不许抢先结束等待**，更不许"超时就跳过"或把没做成的事记成成功；`onDetection: "wait"` 是默认也是有意的 —— 自动化过码会让风控升级。无人值守要的是**用户显式**指定 `abort` + 任务队列重跑，不是自动打码。
11. **profile 锁只在确认没人用时才清**：`cleanStaleProfile` 靠心跳文件里的 pid 判断；如果那个 pid 还活着（另一个脚本/浏览器正在用这个 profile），脚本会明确报错并**不动任何文件**，不会把你的登录态搞坏。
