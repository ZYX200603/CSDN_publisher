# CSDN Publisher —— 用真实浏览器自动化运营 CSDN 博客的工具集

用 Playwright 驱动真实 Chrome，复用**你自己的登录态**，把「发文章 → 看数据 → 复盘 → 互动」这条运营链路自动化。

> CSDN 没有对外开放的发布接口（对非浏览器客户端一律返回 HTTP 521），所以本工具走「自动化真实编辑器 UI」路线：
> **不接触、不存储你的密码**，登录态只保存在本机 `.profile/` 目录（等同于账号密码，切勿外传）。

---

## ⚠️ 合规与风险提示（必读）

- 本工具**只读取和发布你自己的内容**，不伪造流量、不刷量。
- **严禁自动过验证码**：任何操作认出滑块/安全验证，工具会停下来等真人处理；人也应该盯住浏览器窗口。自动化过码会升级风控。
- 批量发布/互动/私信都是平台敏感行为，**账号安全第一**：不要高频连发，私信正文由你自己把关。
- 自动化发布属于灰色地带，**风险自负**。只用来发你自己的原创内容——CSDN 有原创声明和查重，严禁整段照搬素材。

---

## 一、功能说明

| # | 能力 | 入口 | 说明 |
|---|---|---|---|
| 1 | **声明式批量发布** | `csdn_job.py` | 只写 `jobs/job.*.json`（搜什么关键词、阅读量门槛、几篇、发布方式），`prepare` 搜素材+建正文骨架+生成封面 → 你把正文写进 `posts/*.md` → `publish` 自动填标题/标签/摘要/封面/栏目并提交 |
| 2 | **定时发布 / 排期** | `publish.on="schedule"` / `csdn_schedule_drafts.py` | 支持 `auto`（取你自己复盘数据里最好的时段）/ 绝对时间 / `+26h` 相对时间；也可把**草稿箱里现成的稿子**批量改成定时发布（不动正文） |
| 3 | **阅读数据复盘** | `csdn_stats.py` | 只读采集「内容管理」页的阅读/点赞/评论/收藏，存快照、算真实新增与阅读速度，按发布时段/标题特征/主题词聚合出报告 |
| 4 | **批次复盘 / 跨批横评** | `csdn_retro.py` / `csdn_rank.py` | 单批复盘（同龄对比 + 24h 外推 + 线上核验）与跨批排名；口径统一「发布后 16~32h 窗口内最接近 24h 的快照」 |
| 5 | **发布前体检** | `csdn_lint.py` | 空标题、骨架正文、字数不足、标签超限、**和自己已发文章撞标题**等硬伤直接拦下发布（不启动浏览器） |
| 6 | **一键三连** | `csdn_interact.py` | 按「近 7 天 + 小作者优先」挑文章批量点赞/收藏/评论；自带内容农场过滤与「文章×操作」去重库；评论走「读正文写感悟」两阶段，先给你审再发 |
| 7 | **私信群发** | `csdn_message.py` | 给「我关注的人 / 我的粉丝」发 `templates/message.txt` 原文；**不写 `--yes`/`--wait` 就什么都不发**（硬闸门） |
| 8 | **选题调研** | `csdn_search.py` / `csdn_hot.py` | 热榜双排序（热度+收藏）→ 精读拆结构指纹 → 和你账号交叉 → 出 md 报告 |
| 9 | **封面与插图** | `make_cover*.py` / `csdn_figure.py` / `csdn_image.py` | 渐变/设计感封面；Mermaid 矢量示意图；表格/终端输出渲染成图并上传 CSDN 图床 |
| 10 | **草稿箱管理** | `csdn_drafts.py` / `csdn_draft_clean.py` / `csdn_draft_check.py` | 草稿计数验收、列表/删除（带标题+时间双重校验）、草稿插图存活校验 |

---

## 二、运行环境与依赖

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | **Windows 10/11** | 启动器是 `.bat`；选择器与浏览器行为在 Windows + Chrome 上实测 |
| Python | **3.10 或更高** | 只用标准库 + 下面两个第三方包 |
| 浏览器 | **Google Chrome**（已安装并登录过任意 Google 账号无所谓） | 工具用 `channel=chrome` 启动；也可 `python -m playwright install chromium` 用自带浏览器 |
| playwright | `>=1.40` | 驱动真实浏览器 |
| Pillow | `>=10.0` | 画封面和插图 |

> 不需要 CSDN 开放平台账号、不需要 API Key。登录态 = 你本机 Chrome 的 cookie。

---

## 三、安装

### 方式 A：一键安装（推荐）

在项目根目录（本 README 所在目录）双击 / 运行：

```bat
setup.bat
```

它会依次：找 Python → 建 `.venv` → 装 `playwright` + `Pillow`（默认源失败自动换清华镜像）→ 检查 Chrome。

然后**首次登录**（之后不用再登录）：

```bat
login.bat
```

弹出 Chrome 窗口，扫码或账密登录 CSDN，工具自动保存登录态并关闭浏览器。

确认登录态有效：

```bat
.\csdn.bat check
```

### 方式 B：手动安装

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe publish_csdn.py login
.\.venv\Scripts\python.exe publish_csdn.py check
```

> 本文档所有命令都默认在**项目根目录**执行；Python 一律用 `.\.venv\Scripts\python.exe`。
> `csdn.bat` / `csdn_stats.bat` / `csdn_message.bat` / `csdn_interact.bat` 只是同一调用的薄包装。

---

## 四、快速开始：发一篇文章

```powershell
# 0. 看看有哪些现成任务文件（两个内置示例）
.\.venv\Scripts\python.exe csdn_job.py

# 1. 确认登录态
.\csdn.bat check

# 2. 复制示例任务，改成你的主题（换主题只改 keywords / minViews）
Copy-Item jobs\job.all.json jobs\job.mytopic.json
#    编辑 jobs\job.mytopic.json：search.keywords / search.minViews / count / publish.on

# 3. 搜素材 + 建正文骨架 + 生成封面
.\.venv\Scripts\python.exe csdn_job.py prepare jobs\job.mytopic.json

# 4. ★ 把正文写进 posts\*.md（脚本不写文章，只建骨架；删掉首行 TODO 标记）

# 5. 看完成度 + 发布前体检结果
.\.venv\Scripts\python.exe csdn_job.py status jobs\job.mytopic.json

# 6. 发布（publish.on = yes 直接公开 / no 存草稿 / schedule 定时）
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.mytopic.json
```

**发布方式就一行开关**（job 文件里）：

```jsonc
"publish": { "on": "yes" }       // 直接公开
"publish": { "on": "no" }        // 只进 CSDN 草稿箱（标签/摘要/栏目照样填好）
"publish": { "on": "schedule", "schedule": "auto", "scheduleGap": "1d" }   // 定时
```

发完 24 小时左右跑一次复盘，看这篇到底跑得怎么样：

```powershell
.\csdn_stats.bat                 # 采集 + 出报表（约 1 分钟）
type output\stats\latest.md      # 报告：新增排行 / 24h 阅读 / 时段 / 主题词表现
```

---

## 五、各能力使用示例

> 标注：🌐 = 需要浏览器 + 登录态；💻 = 纯本地离线；🖥 = 需要 Chrome 但无需登录。

### 5.1 单篇发布 / 定时发布（底层入口）

```powershell
# 🌐 直接发一篇（发布弹窗字段回读校验，栏目对不上会中止 rc=7）
.\.venv\Scripts\python.exe publish_csdn.py publish posts\demo.md

# 🌐 26 小时后定时发布
.\.venv\Scripts\python.exe publish_csdn.py publish posts\demo.md --schedule "+26h"

# 💻 只导出发布弹窗结构（校准选择器用）
.\.venv\Scripts\python.exe publish_csdn.py inspect
```

### 5.2 把草稿箱现成的稿子批量改成定时发布

```powershell
# 🌐 先看不提交
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-20 16:30" --dry-run --only 1
# 🌐 真改（逐个打开草稿 → 核对标题一致才提交，绝不改正文）
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-20 16:30"
```

### 5.3 草稿箱 / 排期稿管理

```powershell
# 🌐 列草稿箱（只读）；--raw 能看到排期稿的两个时间
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 草稿箱 --raw
# 🌐 删（必须带标题子串 + 时间，防误删）
.\.venv\Scripts\python.exe csdn_draft_clean.py --delete IDX --match 标题子串 --expect-time "2026-10-20 16:30"
# 🌐 存完草稿验一次插图是否存活
.\.venv\Scripts\python.exe csdn_draft_check.py <articleId>
```

### 5.4 阅读复盘 / 批次复盘 / 跨批横评

```powershell
# 🌐 全量采集 + 报表（--pages 2 只采前 2 页；--report-only 用旧快照重出报表）
.\csdn_stats.bat --pages 2

# 💻 单批复盘（probe 子命令 🌐 线上核验）
.\.venv\Scripts\python.exe csdn_retro.py probe
.\.venv\Scripts\python.exe csdn_retro.py build
.\.venv\Scripts\python.exe csdn_retro.py analyze
.\.venv\Scripts\python.exe csdn_retro.py compare 2026-10-12 2026-10-13

# 💻 跨批横评（--coupon 标注投流量券的批次，默认不进主统计）
.\.venv\Scripts\python.exe csdn_rank.py --since 2026-10-08 --until 2026-10-14
```

> 复盘每天只改 `retro/config.json` 里的 `day`（或 `--day`），**不要为某一天新建脚本副本**。

### 5.5 发布前体检（单篇自查）

```powershell
# 💻 不联网、不开浏览器；exit code 1 = 有 error（会拦下发布）
.\.venv\Scripts\python.exe csdn_lint.py posts\docker-basics.md --tags Docker,容器 -k Docker
```

### 5.6 一键三连（点赞/收藏/评论）

```powershell
# 🌐 评论两阶段：先抓正文摘要出清单（不发任何东西）
.\csdn_interact.bat --prepare-comments --ops 三连 --count 50
#     ↓ 把约 100 字感悟写进 output\interact\pending-<runid>.comments.json
# 🌐 打出来给你审
.\csdn_interact.bat --preview output\interact\pending-<runid>.json
# 🌐 你确认后才发
.\csdn_interact.bat --send --pending output\interact\pending-<runid>.json

# 🌐 只点赞 / 只收藏（count 没有默认值，必须每次指定）
.\csdn_interact.bat --ops like --count 50
.\csdn_interact.bat --ops save --count 30
# 💻 只判定不点击
.\csdn_interact.bat --ops 三连 --count 50 --dry-run
```

### 5.7 私信群发

```powershell
# 🌐 只列名单，一条都不发（硬闸门：不写 --yes/--wait 就什么都不做）
.\csdn_message.bat --to follower --limit 20 --wait
# 🌐 按你确认过的名单真发
.\csdn_message.bat --to follower --limit 20 --yes
# 🌐 补发没送到的（跳过以前已成功的人）
.\csdn_message.bat --to follower --limit all --yes --skip-sent
# 💻 只看历史发送记录
.\csdn_message.bat --stats
```

正文永远是 `templates\message.txt` 的原文，单条上限 500 字，超了脚本直接拒发。
`csdn_top_urls.py` 可把「今天发布的文章里阅读量最高的 4 篇」链接写进模板的 `1.url`~`4.url` 槽位：

```powershell
.\.venv\Scripts\python.exe csdn_top_urls.py --dry-run    # 只预览
.\.venv\Scripts\python.exe csdn_top_urls.py              # 真写（优先复用今天已有的快照）
```

### 5.8 选题调研

```powershell
# 🌐 热榜双排序 → 精读 5 篇拆结构指纹 → 和你账号交叉 → 报告落 hot_search/
.\.venv\Scripts\python.exe csdn_search.py --q "AI Agent" --limit 30 --deep 5 --my-pages 2
```

### 5.9 封面与插图

```powershell
# 💻 渐变封面 / 设计感封面（--list-themes 看配色）
.\.venv\Scripts\python.exe make_cover.py -t "一文搞懂 Agent 记忆机制" -s "从短期记忆到向量检索" -o posts\c1.png
.\.venv\Scripts\python.exe make_cover_pro.py -t "标题" --theme purple -o posts\c2.png

# 🖥 Mermaid 矢量示意图（需要 Chrome、无需登录；首次运行需网络缓存 mermaid.js 到 img\vendor\，之后离线可用）
.\.venv\Scripts\python.exe csdn_figure.py diagram --code exp\examples\rag-flow.mmd --out img\flow.png --title "检索链路"

# 💻 表格 / 终端输出渲染成图（示例输入在 exp\examples\）
.\.venv\Scripts\python.exe csdn_image.py render --table exp\examples\chunk-recall-numbers.md --out img\t1.png --title "三种检索器的召回率"
.\.venv\Scripts\python.exe csdn_image.py render --text exp\examples\verify-output.txt --out img\t2.png --title "验收器真实输出"

# 🌐 上传 CSDN 图床拿 URL（一个会话最多传 3 张，多的分几次跑）
.\.venv\Scripts\python.exe csdn_image.py upload --files img\t1.png img\t2.png --out img\urls.json
```

### 5.10 每篇一个实测（实验三件套）

```powershell
# 💻 建 README / run.py / numbers.md 三件套；跑完把真实数字填进 numbers.md 就能出图
.\.venv\Scripts\python.exe exp\new_experiment.py <slug> --title "…"
```

---

## 六、配置项

配置分三层，**改版时优先改配置、不改代码**：

### 6.1 `config.json` —— 选择器 / 阈值 / 接口（全局）

| 段 | 管什么 |
|---|---|
| `browser` | 浏览器健壮性：清陈旧 profile 锁、关翻译气泡、启动重试、心跳 |
| `risk` | 风控闸门：认出滑块就停下来等真人（**只做在发布链路**） |
| `crash` | 崩溃自愈：分级恢复（标签页被关 → 原地新开 page；进程真死 → 重启） |
| `selectors` | 发布弹窗的全部选择器（每组按顺序取第一个可见的） |
| `message` | 私信：正文文件、500 字上限、名单接口、限流文案、节奏、去重策略 |
| `interact` | 一键三连：关键词、三道过滤、候选来源、节奏、评论区选择器 |
| `urls` / `login_cookies` / `default_tags` / `category_names` | 入口 URL、登录态判据、默认标签、**账号真实专栏名白名单**（写错会静默留空，故发布前校验） |

完整逐项说明（含每个字段的默认值和实测结论）跑：

```powershell
.\csdn.bat config
```

或直接读 `skills\csdn-publish\CONFIG.md`。

### 6.2 `jobs/job.<主题>.json` —— 这次发什么

六个字段（`title` / `content` / `tags` / `cover` / `summary` / `category`）通用取值语法：
`"auto"` 自动推导 ｜ `"file:路径"` 从文件读 ｜ 其他字符串 = 字面值。

- `search.keywords` / `search.minViews` **必填，没有默认值**（要发什么、参考多热门的文章，由你决定）。
- `count` 与 `articles` 二选一：写 `count: N` 展开成 N 篇全 auto；写 `articles` 则逐篇控制。
- 顶层同名字段 = 所有文章的默认值（`content` 除外）。
- `publish.on`：`yes` / `no` / `schedule`（见上）。

两个内置示例 `jobs\job.all.json`（共用一套 search）和 `jobs\job.independent.json`（每篇独立 search）可直接跑通。

### 6.3 `retro/config.json` —— 复盘口径

复盘基线、同龄参照批、24h 外推参数、探针节流。**每天只改 `day` 一个字段。**

### 6.4 优先级

命令行开关 > job 文件 > `config.json` 默认值。

---

## 七、目录结构

```
csdn-publisher/
├── publish_csdn.py        # 发布引擎：login / check / inspect / config / publish（风控闸门+崩溃自愈都在这）
├── csdn_job.py            # ★ 声明式任务执行器（prepare / status / publish / read）
├── csdn_stats.py          # 阅读数据采集 + 复盘报告（只读）
├── csdn_retro.py          # 单批复盘：probe / build / analyze / compare
├── csdn_rank.py           # 跨批横评：绝对排名 + 相对排名
├── csdn_lint.py           # 发布前体检（离线）
├── csdn_research.py       # 素材搜集（被 csdn_job.py 复用）
├── csdn_refs.py           # 逐篇独立搜素材 + 全池去重（--topics 选题池 JSON）
├── csdn_search.py         # 热榜选题调研 → md 报告
├── csdn_hot.py            # 热榜取数 + 爆款风格指纹
├── csdn_interact.py       # 一键三连（点赞/收藏/评论）
├── csdn_message.py        # 私信群发
├── csdn_top_urls.py       # 今日 Top4 文章链接写进私信模板
├── csdn_figure.py         # Mermaid 矢量图 / 可商用配图
├── csdn_image.py          # 表格/终端输出渲染成图 + 上传图床
├── make_cover.py          # 封面（渐变 + 标题排版）
├── make_cover_pro.py      # 封面（多段渐变/光晕/图标面板，设计感）
├── csdn_drafts.py         # 草稿箱计数（存草稿模式的验收依据）
├── csdn_draft_clean.py    # 草稿/定时发布列表与删除
├── csdn_draft_check.py    # 草稿插图存活校验
├── csdn_schedule_drafts.py# 草稿批量改定时发布（不动正文）
├── csdn_preview_dump.py   # 探针：读预览区真实 DOM 与计算样式
├── _probe_delivery.py     # 探针：私信逐人送达复核（只读）
├── _probe_category.py     # 探针：回读文章真实栏目归属（只读）
├── _probe_column.py       # 探针：抓专栏页实际文章列表（只读，需传专栏 ID）
├── probe_writable.py      # 诊断：目录到底能不能写（只读）
├── analyze_excl.py        # 一次性分析：剔除指定文章后重算主题词/时段
├── test_*.py              # 离线自测（见「八、测试」）
├── config.json            # 选择器 / 阈值 / 接口配置
├── requirements.txt       # Python 依赖
├── setup.bat / login.bat  # 一键装机 / 首次扫码登录
├── csdn*.bat              # 四个主命令的启动器（自动用 .venv）
├── install-skill.ps1      # 把 skills/csdn-publish 装成 AI 技能（见「十」）
├── jobs/                  # 任务配置（两个内置示例；你的 job.*.json 不进仓库）
├── templates/             # start.txt 文章横幅 / message.txt 私信正文 / comment.txt 旧版评论文案
├── retro/config.json      # 复盘口径配置
├── log/                   # README.md 索引约定 + known-issues.md 已知坑
├── skills/csdn-publish/   # 技能文档：SKILL.md（作业规程）/ COMMANDS.md（全部说法）/ CONFIG.md（全部参数）
├── design-system/         # 封面设计规范（MASTER.md）
├── exp/                   # new_experiment.py 实验三件套脚手架 + examples/ 示例输入
├── posts/                 # 你的 Markdown 文稿（运行时生成，不进仓库）
├── output/                # 运行时产物：素材/封面/快照/数据库/截图（不进仓库）
├── .profile/              # 浏览器登录态 = 账号密码，绝不外传（不进仓库）
└── .venv/                 # Python 虚拟环境（不进仓库）
```

---

## 八、测试（全部离线，不开浏览器、不联网）

```powershell
.\.venv\Scripts\python.exe test_robustness.py     # 33 项：陈旧锁/风控识别/崩溃分级/闸门等待
.\.venv\Scripts\python.exe test_launch_paths.py   # 25 项：真实形参跑启动失败路径
.\.venv\Scripts\python.exe test_category_gate.py  # 栏目硬闸门四种场景
.\.venv\Scripts\python.exe test_schedule.py       # 排期解析与越界校验
```

改了 `risk` / `crash` / `browser` / 排期相关代码后，跑前两套。

---

## 九、常见问题（FAQ）

**Q：浏览器起不来，报 `Executable doesn't exist at .pw-browsers\chromium-…`？**
A：**别去装 chromium**。十有八九是 `.profile\lockfile` 脏锁（Chrome 其实装了）。现在启动前会自动清陈旧锁（日志打「已清理陈旧 profile 锁」）；还不行就只杀本项目 profile 的 chrome 进程再删 lockfile，**别用 `taskkill /IM chrome.exe`**（会连你日常的 Chrome 一起杀）。

**Q：右上角弹「Chrome 未正常关闭，是否恢复」/「是否翻译此页面」？**
A：启动参数已关掉这两个气泡，页面级还有兜底。若仍出现，看日志有没有「⛔ profile 正被另一个进程占用」——那是真有别的会话在用，别硬删。

**Q：跑一半弹滑块/安全验证？**
A：**立刻停手、无限期等真人拖滑块**，不要跳过、不要重试绕过、不要把它记成成功。只有显式 `--on-risk abort` 才放弃。⚠️ 互动链路和私信链路**还没接**这道闸门，跑那两类任务时请自己盯住浏览器窗口。

**Q：发布被拒 / 弹「当前已达发文上限」？**
A：这是账号额度限制，不是故障（草稿不占额度，定时发布占）。看编辑器底部「今日发文额度还有 N 篇」，一次别超过 N。

**Q：栏目没进专栏 / 发布成功但栏目是空的？**
A：`category` 只能填账号里**真实存在的专栏名**（大小写要一致）。日志里 `栏目「xxx」不在你的专栏列表里，可选: [...]` 会打印全量可选名；发布弹窗回读不符会直接中止（rc=7）。也可以跑 `_probe_category.py` 回读线上真实归属。

**Q：私信发出去一半停了 / 有人没收到？**
A：已发的人都在 `output/message/message.db` 的 `sends` 表里。正确补发 = 重新 `--wait` 抓名单 → 把 pending.json 里「今天已发过 + 已确认被拦」的人剔掉 → `--yes`。`--skip-sent` 跳的是「历史上成功过的人」，补不了「本轮没轮到的人」。**别信当场的 sent/failed 判定**，跑 `_probe_delivery.py` 逐个复核后再下结论。

**Q：一键三连候选凑不够 count？**
A：正常：搜索页综合排序里近 7 天只占约 5%，还要过三道过滤。加 `--keywords` 或放宽 `--days`，脚本会如实报「实做 X/N」。

**Q：浏览器起不来 / 截图没生成 / 读不到草稿箱计数，但看不出为什么？**
A：先跑 `probe_writable.py` 分清是**环境写不进去**还是工具坏了——两种现象的日志很像，但处置完全不同。

**Q：为什么没有「按发布时间倒序」的文章列表？**
A：CSDN 全站没有（首页 tab 是领域分类、频道页首条 24 天前、榜单没有时间字段、搜索「最新」排序是坏的）。唯一可用的是搜索页**综合**排序 `s=0`，结果列表自带日期。详见 `log/known-issues.md` 第 2 条。

更多「会反复咬人的坑」见 [`log/known-issues.md`](log/known-issues.md)。

---

## 十、作为 AI 技能使用（可选）

`skills/csdn-publish/` 是一份完整的 AI 智能体作业规程（SKILL.md 含全部铁律、流程与踩坑记录）。

```powershell
# 装进 WorkBuddy 的用户级技能目录（会把 __TOOL_DIR__ 替换成实际路径）
powershell -NoProfile -ExecutionPolicy Bypass -File install-skill.ps1
```

其他智能体框架：把 `skills/csdn-publish` 整个目录复制到你所用框架的技能目录即可
（SKILL.md 里的 `__TOOL_DIR__` 需替换成项目实际路径）。

---

## 十一、已知限制

- **每日发文额度**：账号级限制，能走到最后一步但点发布被拒就是额度用尽（rc=3）。
- **栏目最多选 3 个**，只从账号已有专栏里选（不新建）。
- `csdn_stats.py` 只有「内容管理」页字段（阅读/点赞/评论/收藏/发布时间），没有标签和栏目维度。
- 复盘横向对比天生有年龄偏差：报告默认只统计最近 7 天发布的文章，不足 3 篇会回退并明确标注。
- 标签 auto 是启发式，可能挑出不贴题的素材标签；在乎质量就手写 `tags`。
- `csdn_research.py` 的 `--pages` 翻页不可靠（CSDN 前端分页），多给 `keywords` 代替。

---

## 十二、许可证与致谢

- **许可证**：[MIT](LICENSE)（Copyright (c) 2026 朱昱先）
- **作者 / 版权归属**：朱昱先
- **托管平台**：GitHub — <https://github.com/ZYX200603/csdn-publisher>

致谢：本项目的实现站在这些开源工作的肩膀上——

- [Playwright](https://playwright.dev)（Apache-2.0）：浏览器自动化
- [Pillow](https://python-pillow.org)（MIT-CMU）：图像处理
- [Mermaid](https://mermaid.js.org)（MIT）：矢量示意图渲染（首次使用时按需缓存到 `img/vendor/`）
