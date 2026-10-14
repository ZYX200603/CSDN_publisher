---
name: csdn-publish
description: CSDN 博客运营六件事：①按声明式 JSON 往 CSDN 批量发布文章（搜集高阅读量素材、写原创、生成封面、自动填标题/标签/摘要/栏目）；②**定时发布 / 排期**——`publish.on=schedule` 排期，或把草稿箱里现成的稿子批量改成定时发布（不动正文），并识别「发文额度」这类平台拦截；③**阅读数据复盘**——采集自己账号的真实阅读/点赞/收藏数据、存快照、算真实新增与阅读速度，按发布时段/标题特征/主题词聚合出「哪类文章真的有人读」；④**发布前体检**——空标题、骨架正文、和自己撞标题、标签超限等硬伤拦下发布；⑤**一键三连**——`/csdn-publish like`、`comment`、`save` 分别点赞/评论/收藏别人的文章，`like,comment,save count=50` 就是给 50 篇一键三连（只挑近 7 天、粉丝 1 万以下的小作者，自带内容农场过滤与「文章×操作」去重库；**评论走「读正文写感悟」两阶段：先抓正文摘要出清单 → agent 逐篇写约 100 字感悟、脚本自动补开头「大佬您好，昵称」→ 先给用户审 → 确认后才发**；读不到正文或没写感悟的只做赞和藏，评论撞限流只跳过不等待）；⑥**私信群发**——`/csdn-publish message following`、`message follower`、`message following,follower` 把 `templates\message.txt` 的原文私信给「我关注的人 / 我的粉丝」，`limit 20` / `limit all` 指定人数，**不写 `--yes`/`--wait` 什么都不发**（`--wait` 先列名单等你确认）。另外提供**全部可配置参数清单**（`/csdn-publish config`，等同 `.\csdn.bat config`）。当用户要求"发文章到 CSDN""学习 CSDN 优秀文章写原创""明天发 3 篇 xx 方向的文章""同步博客到 CSDN""把草稿箱里的文章全部改成定时发布""定时 x 月 x 日 xx:xx 发布""跑一下阅读复盘""看看我博客最近的阅读量""哪篇文章在涨""下一篇该写什么方向""查查我的文章数据""帮我看看这篇发出去有没有问题""给文章点赞/评论/收藏""一键三连 N 篇""私信给我的粉丝""给关注的人发消息""群发私信""有哪些参数可以改"时使用。工具在 __TOOL_DIR__，用 Playwright 驱动真实浏览器复用已登录态，不接触也不存储用户密码；只读取和发布，不伪造流量。
whenToUse: 用户提到往 CSDN 发文章、批量发文、定时发布/排期、把草稿箱的稿子改发布时间、参考 CSDN 热门文章写作、生成文章封面，或提到 CSDN 阅读量/阅读复盘/文章数据/哪篇涨了/下一篇选题方向/发布前检查文章问题，或要给 CSDN 文章点赞/评论/收藏/一键三连若干篇，或想看有哪些参数可以改（config）时。
user-invocable: true
---

# CSDN 发布（声明式任务）

## ⛔ 第 0 条（最高优先级）：遇到滑块/安全验证 → 立刻停下，无限期等真人

**这条优先级高于本技能其余一切规则，包括下面所有的时间限制、节奏、超时和"跳过"逻辑。**
任何操作都适用：发布、定时发布/改排期、评论、点赞、收藏、私信、采集、复盘。

1. **一认出滑块或安全验证就立刻停手**：不再点击、不再提交、不再翻页、不切页面，
   **绝不关闭浏览器**。当前这一步就停在这里，不往下走。
2. **把浏览器留在原地**，截图 `*-risk-blocked.png`、响铃、往 `output/NEEDS-HUMAN.txt`
   追加一条，并**明确对用户说一句「现在去看浏览器窗口，手动把滑块拖完」**。
   用户压根没看到验证弹窗 = 这条规则等于没执行。
3. **无限期等真人处理，任何时间限制都不许抢先结束等待**：`risk.waitTimeoutSec`、
   `interact.maxMinutes`、每篇 15~30 秒的节奏、排期时间、以及任何"超时就放弃 / 跳过 /
   静默收尾"的分支，**统统让位于这一条**。宁可整轮挂在那里等人回来，也不许
   "跳过这篇继续下一篇"，更不许把没发出去的东西记成做完了。
4. **绝不自作主张过验证码**，绝不换选择器/换入口绕开它，绝不因为"等太久了"就重跑绕过。
   无人值守只能由用户**显式**指定放弃模式（`--on-risk abort`），agent 不得替他决定。
5. **跑任何会写 CSDN 的操作之前，先提醒用户**：「接下来会弹出浏览器窗口，注意有没有滑块；
   出现就请你手动完成，我会停下来等你」。**不要在用户不知情的时候发起会弹验证的操作。**
6. 每次这类运行都要留档：日志里写清"哪一步撞到验证、等了多久、谁处理的、最后结果如何"。

> ⚠️ **当前实现不全，agent 必须自己兜住**：风控闸门只做在 `publish_csdn.py`（发布链路）。
> **`csdn_interact.py` 的评论/点赞/收藏链路、`csdn_message.py` 的私信链路都还没接入**
> （已核对：全项目只有 `publish_csdn.py` 调用 `risk_gate()` / `detect_risk()`）。
> 2026-10-08 实测后果：评论提交弹了滑块，脚本没停、用户没看到，评论被平台吞掉，
> 却记成 `done_unverified`（属"已完成"→ 永不重试）。
> **在代码补齐之前，跑互动/私信类任务时 agent 要自己盯住页面**（截图、读 DOM），
> 一发现滑块/安全验证，立即按上面 1~6 办：停手、把人喊来、等到底。

## 快捷指令：先看这一节

用户直接调用本技能、且没有给出具体任务时，按下面的规则**立刻**响应，
不要先反问、不要先解释工具结构：

| 用户消息里出现 | 你（agent）必须做的事 |
|---|---|
| `help`、`帮助`、`指令`、`能干什么`、`有哪些功能` | **读 `<技能目录>\COMMANDS.md`，把里面的清单原样发给用户**。不要执行任何工具、不要跑任何命令 |
| `config`、`配置`、`参数`、`有哪些能改的`、`字段有哪些` | **读 `<技能目录>\CONFIG.md`，把内容原样发给用户**：它开头第一段就是「⭐ 最常用的几个」，**先把三种发布方式（直接发布 `on:yes` / 存草稿 `on:no` / 定时发布 `on:schedule`）讲清楚，再往下给全量清单**。不要执行其他工具、不要改任何配置 |
| `choice`、`选项`、`菜单`，**或调用本技能但没带任何参数** | **立刻调用 `ask_user_question`** 弹出下面的选项菜单，不要先输出文字说明 |
| `search`、`热榜`、`找选题`、`最近什么火`、`学哪些文章`、`榜单` | 跑 `csdn_search.py`（综合热榜**热度 + 收藏双排** → 精读 5 篇拆结构指纹 → 和自己账号交叉 → 出 md 报告到 `hot_search/`），**然后把结论讲给用户**：空白主题、值得学的 3 篇、三个可写选题。不要只丢一个文件路径 |
| `like`、`comment`、`save`、`点赞`、`评论`、`收藏`、`三连`、`一键三连`，通常带 `count=N` | 跑 `csdn_interact.py`（见下节「一键三连」）。**`count` 必须由用户指定，脚本里没有默认值**；没给就问他，别自己拍一个数 |
| `message`、`私信`、`群发`、`给粉丝发`、`给关注的人发`，通常带 `following` / `follower` / `limit N` | 跑 `csdn_message.py`（见下节「私信群发」）。**`--limit`（几个人）必须由用户指定**，没给就问他；**用户没明确说"直接发"就一律先用 `--wait` 列名单**，等他确认后再 `--yes` |
| `get_url_message`、`刷新私信链接`、`把今天阅读量最高的文章链接写进私信模板` | 跑 `csdn_top_urls.py`：筛 `publishTime` 为今天的文章、按阅读量**降序取前 4**，链接按顺序写进 `templates\message.txt` 的编号槽位（`1.url`→`1.<链接>`，已有链接整块覆盖；默认优先复用今天已有的快照，快照不可用才自动重采，`--fresh` 强制重采、`--dry-run` 只预览）。跑完把 4 条链接念给用户，并报告正文字符数（私信上限 500） |

`choice` 的菜单就用这一组（单选，推荐项放第一个）：

```json
[{"id": "task", "header": "要做什么", "question": "这次想做哪件事？", "options": [
  {"label": "跑阅读复盘（推荐）", "description": "全量采集真实阅读数据、出报表，并给你讲结论"},
  {"label": "只看看现有数据，不采集", "description": "用已有快照重新出报表，不联网"},
  {"label": "发新文章到 CSDN", "description": "我先问你主题关键词和阅读量门槛，再搜素材写原创并发布"},
  {"label": "发布前体检", "description": "检查本地文稿有没有会拦下发布的硬伤"},
  {"label": "一键三连别人的文章", "description": "点赞/评论/收藏 N 篇（我先问你 N 是多少）"},
  {"label": "给粉丝/关注的人发私信", "description": "内容取 templates\\message.txt；我先用 --wait 列名单给你确认，再发"},
  {"label": "查看任务进度", "description": "看已有发布任务的完成度和体检结果"},
  {"label": "看全部可配置参数（config）", "description": "job 字段、config.json、命令行开关一次列全"},
  {"label": "看全部可用说法（help）", "description": "把 COMMANDS.md 的完整清单发给你"},
  {"label": "其他（我直接说）", "description": "不走菜单，你直接描述需求"}
]}]
```

用户选了「发新文章」→ 接着按标准作业流程走，并且**必须**先问 `keywords` 和 `minViews`
（这两项不能替用户猜）。选了「发布前体检」→ 问是哪一篇（或哪几篇）。

识别时要宽容：`/csdn-publish help`、`/csdn-pulish help`（拼错）、
`csdn-publish 帮助`、`帮我看下指令` 都算 help。

## 工具与常规流程

工具在 `__TOOL_DIR__`，**任务文件都在它的 `jobs\` 子目录下**（`jobs\job.all.json` …）。
**首选 `csdn_job.py`**：任务写在 `job*.json` 里，
换主题只改 JSON 不改代码，各有独立缓存目录 `output/job/<文件名>/`。
命令里写 `jobs\job.all.json` 或只写 `job.all.json` 都能找到（脚本会到 `jobs\` 兜底查找）；
任务里的相对路径按 `jobs\` 目录解析，开头横幅统一在 `templates\start.txt`。

```powershell
cd __TOOL_DIR__
.\.venv\Scripts\python.exe csdn_job.py                     # 列出所有任务文件
.\csdn.bat check                                           # 必须先确认登录态
.\.venv\Scripts\python.exe csdn_job.py prepare jobs\job.all.json    # 搜素材+建骨架+生成封面
#   ↓ 这一步由你（agent）把正文写进 posts\*.md
.\.venv\Scripts\python.exe csdn_job.py status  jobs\job.all.json    # 看每篇完成度
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json    # 按 publish.on 自动决定发/存草稿
```

不知道用哪个任务文件就跑不带参数那句列出全部。新主题从 `jobs\job.all.json` 复制到 `jobs\` 下。
`job.all` 共用一套 search，`job.independent` 每篇独立 search。

## 发还是存草稿：`publish.on`（一行开关，决定后不再问用户）

```json
"publish": { "on": "yes" }     // 直接发布到博客
"publish": { "on": "no" }      // 只保存到 CSDN 草稿箱，不公开
```

- `yes` → 真实提交并公开。`no` → 真实提交但只进草稿箱，**标签/摘要/栏目照样填好**
  （走完整弹窗后点弹窗里的「保存为草稿」，不是编辑器那个不带字段的保存草稿）。
- **这两个值都代表"用户已经拍板"，直接执行，不要再回来问一句"要不要正式发"**。
  只有 `on` 缺失或写法无法识别时才退回 `publish.dryRun` / `publish.draft`。
- 命令行开关优先于 job 配置：`--dry-run` 绝不提交；`--draft` 强制存草稿。日常不用加这两个开关。
- 想先验证一遍再发：临时用 `--only 1 --dry-run`，核对完把 `on` 设成 `yes` 跑全量。
- **草稿模式的 `05-draft-saved.png` 里发布弹窗往往还开着**，那是 CSDN 弹窗关得慢，
  不代表失败——真正的验收方式是草稿箱计数 +1（内容管理页「草稿箱(N)」），
  实测点完 3 秒截图时弹窗仍在，但草稿已经入库。
- **存草稿也会被平台拦下，别信"草稿已保存"这句日志**：实测连发两篇时第二篇弹
  `.el-message` toast「**文章频繁发布，请稍后再试**」，保存被拒、界面却什么都不缺。
  现在 `publish_csdn.py` 会在存草稿后 4 秒内轮询并识别这类提示：
  频率限制返回 **rc=4**（`csdn_job.py` 会等 `retryWaitSec`（默认 120 秒）**自动重试一次**），
  额度用尽返回 **rc=3**；跑完还会自动读一次「草稿箱(N)」计数当验收依据。
  **多篇连发要留间隔**，别一秒钟点两次。
- **不要假设重复跑会覆盖旧草稿**：实测同一篇文章连跑两次存草稿，一次是原地更新
  （计数不变、时间戳变新），另一次却新增了一篇（草稿箱 1 → 2）。CSDN 这边行为不
  确定，所以同一篇别反复跑；跑完务必去看草稿箱计数，多出来的自己删。
  **2026-10-08 补测：连续两次都是"新增"**（草稿箱 3 → 4 → 5），所以现在的标准流程是
  「存 → 列草稿箱（`csdn_draft_clean.py --tab 草稿箱`）→ 按**标题 + 时间**双重校验删掉旧的那份」。
  删的时候一定带上 `--expect-time`，否则列表顺序一变就可能删错。
- 想确认封面上没上，看日志有没有 `已提交封面图片` / `封面已确认上传` 两行；
  弹窗里「添加封面」那一行会被标签浮层挡住，要看就得看 `03-publish-dialog.png`
  （Tag 浮层还没弹出来的那一张）。

## 定时发布：`publish.on = "schedule"` + `publish.schedule`

```json
"publish": {
  "on": "schedule",          // 也可以只写 schedule，不写 on 也按定时走
  "schedule": "auto",        // auto / "2026-10-09 19:30" / "+26h" / ["auto","+50h"]
  "scheduleGap": "1d"        // 多篇自动排期时，篇与篇间隔（默认 1 天）
}
```

- `auto` = 第一篇取**你自己复盘数据里阅读最好的时段**（没数据就 19:00），对齐整点，
  且不早于 CSDN 允许的最早时间；多篇按 `scheduleGap` 顺延。
- 命令行也可以临时定：`--schedule "+26h"` / `--schedule "2026-10-09 19:30"` / `--schedule auto`。
- **硬限制（脚本开浏览器前就校验，越界直接中止、时间表打印出来）**：CSDN 只允许
  **当前时间后 4 小时 ~ 约 6~7 天**（接口 `getScheduledReleaseTime` 实测 `start=now+4h`、
  `end=now+6d`），且时间选择器只有 **15 分钟一档**（脚本自动对齐）。
- `status` 会把排期表打出来（不联网、不开浏览器）。
- 被拦时的正确反应：CSDN 弹 `.el-message-box`「当前已达发文上限，可开通会员提升额度」时，
  脚本报 `⛔ 被 CSDN 拒绝`（单篇 rc=3）。**这是账号额度限制，不是故障**，
  草稿箱/内容管理页都不会留下东西——别去 `--force`，那是给体检用的。
- 排期成功的文章在内容管理页会有**两个时间**（「预计于北京时间 … 发布」+ 提交时间），
  没有单独的「定时发布」标签页；排期提交后**立刻计入「全部/已发布」计数**，
  不会停在「审核中/未通过」，别以为它已经发出去了。
- **定时发布占发文额度**（草稿不占）：额度看编辑器底部 `.publish-quota-tip`
  「今日发文额度还有 N 篇」，实测 1 篇扣 1 个。用尽时弹 `.el-message-box`
  「当前已达发文上限」，脚本报 `⛔ 被 CSDN 拒绝`（rc=3）。
- 想改时间：内容管理页点「修改定时发布」，改完重新走审核。
- **把草稿箱里现成的稿子批量改成定时**（不动正文/标签/摘要，最常用）：

  ```powershell
  .\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-08 16:30" --dry-run
  .\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-08 16:30"
  ```

  它自己从内容管理页取 `articleId`，用 `editor.csdn.net/md?articleId=<id>` 打开草稿，
  核对标题一致才提交（不一致就跳过，绝不发错稿）。
  查/删排期稿用 `csdn_draft_clean.py --tab 全部 --raw`（能看到两个时间）。

## 正文插图 + 每篇一个实测（这一轮新增的两条流水线）

**插图**（热榜爆款配 4~45 张图，我们以前是 0 张）：

```powershell
# ① 首选：矢量示意图（Mermaid）—— 流程图/时序图/架构图，比手绘表格图精美一个档次
.\.venv\Scripts\python.exe csdn_figure.py diagram --code exp\examples\rag-flow.mmd --out img\rag-flow.png --title "检索链路"
#    首次会自动把 mermaid.js 缓存到 img/vendor/，之后不依赖网络；支持 default/dark/neutral/forest
# ② 数据表继续用正文里的 markdown 表格（可复制、更精确），不要为了好看把数据做成图
# ③ 手绘风图表/终端输出（要"像截图"时用）
.\.venv\Scripts\python.exe csdn_image.py render --table exp\examples\chunk-recall-numbers.md --out img\a.png --title "标题"
.\.venv\Scripts\python.exe csdn_image.py render --text exp\examples\verify-output.txt --out img\b.png --title "终端输出"
# ④ 传到 CSDN 图床拿 URL，再自己写进 markdown
.\.venv\Scripts\python.exe csdn_image.py upload --files img\a.png img\b.png --out img\urls.json
# ⑤ 存完草稿验一次图有没有活着
.\.venv\Scripts\python.exe csdn_draft_check.py <articleId>
```

**配图只用可商用的源，绝不用别人文章里的图表/截图**（版权 + 信息可能对不上 + 常带水印）。
本机网络实测（2026-10-08）：图库**搜索 API 全不通**（`api.openverse.org`、`commons.wikimedia.org`
超时/重置），但**图片 CDN 直链通**（`images.unsplash.com`、`images.pexels.com`、`picsum.photos` 均 200）。
所以照片只能走 `csdn_figure.py stock --url <直链>`；技术文优先用示意图，照片最多当开头氛围图并署名。

**开头横幅 / 正文里写 HTML 的铁律：CSDN 会把 `style="..."` 全部剥掉。**
实测保留：`align="center"`、`<center>`、`<b>`/`<strong>`、`<br>`、`<hr>`、
`<font color size>`（老标签反而活着）、以及 markdown 原生的 `>` 引用块卡片、
`**粗体**`、`[链接](url)`、emoji、`@[toc]`；被剥：任何 `style=`、圆角/阴影/渐变。
**改完横幅一定要读回真实渲染**：`python csdn_preview_dump.py <articleId>` 会打印预览区的
DOM 与计算样式（这个工具就是靠它发现"内联样式全被剥掉"的）。
现行 `templates\start.txt` = 引用块卡片 + 彩色品牌行 + 一行 5 个栏目链接（比旧的 6 行文本干净得多）。

三个坑：① 编辑器的 `input.cfw-file-input` **只上传不插图**，要自己把 URL 写进 markdown；
② 图床 URL 在**华为云 OBS 的 JSON 响应**的 `data.imageUrl` 里（脚本已抓）；
③ **一个会话最多传 3 张**，多了分几次跑。

**每篇一个实测**：`python exp/new_experiment.py <slug> --title "…"` 建三件套
（README / run.py / numbers.md），跑完把真实数字填进 `numbers.md` 就能出图。
结论必须分三档写：**现象（可复现数字）/ 已排除的解释 / 尚未证实的猜测** ——
这一轮我先把"1024 塌缩＝输入被截断"写成了结论，靠前缀对比实验才纠正过来。
发布前顺手跑一次 `csdn_hot.py --trend`（离线）看题材是不是连续在榜。

---

## 一键三连（点赞 / 收藏 / 评论）

```powershell
cd __TOOL_DIR__
# ★ 评论 = 「读正文写感悟」两阶段，**不要再用固定文案刷评论**
.\csdn_interact.bat --prepare-comments --ops 三连 --count 50   # ① 抓正文摘要出清单（不发）
#   ② agent 逐篇读正文，把 ~100 字感悟写进
#      output/interact/pending-<runid>.comments.json = {"<article_id>": "感悟正文"}
.\csdn_interact.bat --preview output\interact\pending-<runid>.json   # ③ 打出来给用户审
.\csdn_interact.bat --send --pending output\interact\pending-<runid>.json   # ④ 用户确认后发
.\csdn_interact.bat --ops like --count 50                  # 只点赞（不需要写感悟）
.\csdn_interact.bat --ops save --count 30                  # 只收藏
.\csdn_interact.bat --ops 三连 --count 50 --dry-run         # 只判定，不点击、不写库
.\csdn_interact.bat --stats                                # 存量统计 + 粉丝最少的作者
.\csdn_interact.bat --probe-comment <文章URL>               # 勘察评论框（只开面板，不提交）
```

### 评论的五条硬规矩（用户 2026-10-08 定的，别自作主张改）

1. **开头固定「大佬您好，{昵称}」**——只写感悟，**开头由脚本用作者真实昵称拼上**，
   而且是**原样使用**（实测有 `❀͜͡傀儡师`、`L oiey`、`爬一手好线杆` 这种名字，照用不改）；
   拿不到名字才退回「博主」。别自己去改写作者名。
2. **不提"我已一键三连"**：只写内容感悟，像个真读者，不像来换互动的。
3. **读不到正文就只做赞和藏、不评论**（`minBodyChars`，默认 400 字）。
   实测约 45% 的 VIP/付费页既没有发布时间也抓不到正文——**没有正文就没有感悟**，
   硬夸必然翻车。
4. **必须先给用户审再发**（`--preview`）：评论发到别人文章下不可撤回。
5. **撞到滑块/安全验证 → 立刻停手、无限期等真人，别当成功**（= 文首第 0 条，优先级最高）。
   实测 2026-10-08：点「发表评论」后 CSDN 弹滑块，按钮停在「提交中…」、评论数还是 0、
   没有任何 toast，评论被平台吞掉；而互动链路没有风控闸门，于是记成 `done_unverified`。
   **发评论前先提醒用户"注意浏览器窗口可能弹滑块"**；一旦发现验证就停在这里等人，
   不许跳过、不许重试绕过、不许把它记成"已完成"。

**感悟怎么写才算合格**：抓住正文里**一个具体的点**（某个 API 行为、某个坑、某段实现顺序），
说清"它解开了我什么疑惑 / 我打算怎么用"，约 100 字。**避免"写得很详细，学习了"这类空话**——
50 条空话和 50 条复制粘贴在平台看来是一回事。

**没写感悟的文章不会发评论**（记 `skipped_no_comment`，**不定性**：补写好再跑就会补发）。
`templates/comment.txt` 只在旧的一次性模式里用，已不是评论正文来源。

`--count` **没有默认值**，必须用户每次指定。用户说 "like,comment,save count=50" 就是
`--ops like,comment,save --count 50`。

### 候选从哪来（实测钉死，别走回头路）

用户最初的要求是"从**热度榜或主页**刷新出来的、**刚发布**的文章"。实测这个前提**不成立**：

| 入口 | 实测结果 |
|---|---|
| `www.csdn.net` 首页 | 首屏只有极客头条**资讯**；tab 是领域分类（后端/运维/人工智能/DeepSeek…），不是时间排序 |
| `blog.csdn.net/nav/<频道>` | tab 只有 `推荐 / 付费专栏 / VIP文章`；首条实际发布于 **24 天前** |
| `blog.csdn.net/rank/list` | 榜单行里**没有时间字段**；累积热度，老文霸榜 |
| 搜索页 `s=new`（最新） | **是坏的**：返回 2022~2025 的文章，比 `s=0`（综合）还旧 |

- **推荐流 + 热榜**做候选：60 篇里 48h 内只有 **1 篇(1.7%)**，且 **45% 读不到发布时间**。
  按 48h 筛凑 50 篇要预读约 3000 篇 ≈ 200 分钟 → **不可行**。
- **唯一能用的是搜索页「综合」排序（`s=0`）**：258 篇候选里近 7 天 12 篇(4.7%)，
  而且**结果列表自带日期**，能在列表层就筛"近 N 天"，不必逐篇进详情页试错。

所以现行策略是 **搜索页综合排序 + 近 7 天 + 小作者优先**（用户已确认）：
"新鲜"在 CSDN 供给上做不到 48h，但"**对方会不会回你三连**"其实还取决于作者规模——
**大 V 收到几百条通知根本看不到你，小作者每条赞和评论都会看**。

### 三道过滤（`config.json` 的 `interact` 段，可关可调）

1. 作者粉丝 ≥ `maxFans`（默认 1 万）→ 跳过。
2. **内容农场**：原创 ≥ 150 篇但粉丝 < 500 → 跳过。实测抓到过 `L oiey`（**1479 篇原创 / 35 粉**）、
   `超级简历WonderCV`(36粉)、`斯迈尔齿科`(46粉) —— 这些号写文只为 SEO，**不会回三连**。
3. 作者名含"简历/齿科/有限公司…"、标题含"征文/榜单/福利…" → 跳过。

**约 45% 的文章读不到发布时间**（VIP/付费/笔记类模板不同，作者资料也一起缺失）：
按约定**直接跳过不三连**，记为 `skipped_no_time` 已定性，不会每轮重复打开。

### 执行细节（改代码前必须知道）

- **顺序固定 赞 → 藏 → 评论**：评论放最后，读者视角上更自然（先互动再留言），
  也保证评论里的语境和已发生的互动一致。
- **点之前先读真实状态**：`#is-like-imgactive` / `#is-collection-imgactive` 可见即"已做"，
  直接记 `skipped_already`，不重复点。
- **限流处理**：评论撞限流 → **只跳过评论，赞和藏照做、不等待**（这是用户明确要求的）；
  连续 3 次被拦 → 本轮剩余篇目不再尝试评论。**赞/藏撞限流 → 整轮立刻收手**。
- **节奏**：每篇之间随机停 15~30 秒（用户确认的档位）。**别为了出量调快**——
  批量三连是平台风控盯的行为。
- **去重是「文章 × 操作」两维**的（SQLite `output/interact/interact.db`）：某篇点赞成功、
  评论被限流拦下，下次跑**只补评论**，不会重点赞。这是敢反复跑的前提。
- **`--dry-run` 一律不写库**：否则一次演练就把整批文章标成"做过了"，真跑会被全部跳过。
- **浏览器必须"干净启动一次、会话内一直复用"**：实测在会话进行中反复 force-kill chrome
  会让 profile 进入残缺状态，之后每次启动都在几秒后自己退出（表现为
  `launch()` 成功、下一次 `goto()` 抛 `TargetClosedError`）。脚本已内置自愈与"重试同一篇"。
- 评论提交后**没抓到明确成功信号**时记为 `done_unverified` 并提示人工看一眼——
  宁可当已发（避免重复评论刷屏），也不谎报成功。

---

## 私信群发 `/csdn-publish message`（给「我关注的人 / 我的粉丝」发同一条私信）

```powershell
cd __TOOL_DIR__
.\csdn_message.bat --to follower --limit 20 --wait          # 只列名单（存 pending.json），一条都不发
.\csdn_message.bat --to follower --limit 20 --yes           # 按上面确认过的名单真发
.\csdn_message.bat --to following --limit 20 --wait         # 发给「我关注的人」
.\csdn_message.bat --to following,follower --limit all --wait   # 两个都发，不限量
.\csdn_message.bat --to follower --limit 1 --dry-run        # 只演练选择器：填框+截图，不点发送
.\csdn_message.bat --to follower --limit all --yes --skip-sent  # 只补发没送到的（跳过以前已成功的）
.\csdn_message.bat --stats                                  # 历史发送记录
```

用户说 `/csdn-publish message following`、`message follower`、`message following,follower`
就是上面这几条（**中文写法也认**：粉丝 / 关注的人 / 两个都发）。

**正文永远是 `templates\message.txt` 的原文**，纯文本、原样发送，
**不做任何个性化替换**（用户明确要求：不替换昵称、不加称呼）。单条上限 **500 字**，
超了脚本**直接拒绝发送**，不截断——用户要自己改短或拆两条。

> **模板里的链接槽位可以一键刷新**：发 `/csdn-publish get_url_message`（或直接跑
> `csdn_top_urls.py`）会把「今天发布的文章里阅读量最高的 4 篇」按从高到低写进模板的
> `1.url`~`4.url` 槽位（已有链接自动被新的覆盖）。刷新后私信正文 = 固定文案 + 当天 Top4 链接，
> **发送前用户应知晓正文变了**；字符数由脚本打印把关（>500 会被 `csdn_message.py` 拒发）。

### 三条铁律（agent 必须照做，别好心绕过）

1. **用户没说 `--limit` 就先问，别自己填**。这个功能**没有默认人数**，
   用户的原话是"每次发多少人我会给你发指令，limit 20 或者 limit all"。
2. **用户没明确说"直接发"时，一律先 `--wait`**，把名单念给他看（谁、昵称、来自粉丝还是关注），
   等他确认后再跑 `--yes`。`--yes` 会**优先复用 `--wait` 存下的 `pending.json`**，
   所以"你确认过的那批人"就是"真正收到的那批人"（默认 2 小时有效，可用 `message.pendingTtlMin` 调）。
3. **不写 `--yes` / `--wait` 时脚本什么都不做**（返回码 2）。这是硬闸门，
   唯一例外是 **`--dry-run`**：它只把正文填进输入框并截图、**绝不点发送**，可以单独用
   （校准选择器的标准手段）。**不要为了"帮用户省一步"而擅自加上 `--yes`**。

### 名单从哪来（2026-10-09 改造：**不再有 20 人上限**）

名单**默认走 CSDN 自己的名单接口**，不再从页面 DOM 里数人头：

- 粉丝：`mp-action.csdn.net/interact/wrapper/pc/fans/v1/api/getFansOffsetList?pageSize=N&username=<我>`
- 关注的人：同族的 `getFollowOffsetList`（同一套参数）
- 在个人主页里用页面内 `fetch(url, {credentials:'include'})` 发起（自动带 cookie / referer），
  一次要 `pageSize = 主页总数 + 余量`（`config.json` → `message.fansApi`，默认最少 200、最多 2000）。

**`pageSize` 是唯一旋钮**（没有 `offset`/`page` 之类翻页参数——实测 13 个候选名全无效，
传 `fanId=<x>` 反被当成过滤条件回 0 条），调大就能一次取全。实测 **41 粉丝 41/41、208 关注 208/208**
（粉丝涨到 42 时接口也跟着给 42，跟着主页实时总数走）——**所以 `--limit all` 就是全部粉丝**。

> **以前那条"最多 20 人"的结论已作废。** 个人主页「粉丝」那个列表视图**写死渲染 20 条**
> （没有分页器、没有「加载更多」、滚到底也不涨，点头部那两个统计数字进的是同一个视图），
> 我们以前是从它的 DOM 里抓的；而站点自己拉列表用的就是上面那个接口、只是它只请求了 `pageSize=20`
> ——**是我们抓错了地方，不是 CSDN 只给我们看 20 个**。
> 别再照着旧说法讲"`limit all` 也只能拿到 20 人"或"差额拿不到、只能用户自己翻"。

**对账与兜底**（接口不给服务端总数，`total` 字段恒 0/null，没用）：拿主页头部的「N 粉丝 / N 关注」对账。
① 返回条数 == 请求条数（疑似被截断）→ **自动翻倍再要一次**（封顶 `maxPageSize`）；
② 仍不够 → 报告写 `list-cap-<来源>` 并在屏幕上明确告警；③ 接口整个不可用 → 才退回旧的 DOM 抓取
（**只有那条兜底路径才最多 20 条**，且会告警）。**任何情况都不静默少发**；
名单为空时打印 `⛔ 名单是空的`（含"本来 0 粉丝 / 接口没通 / `--skip-sent` 全剔掉 / 登录态失效"四条可能），
**不发送、也不拿别的页面用户凑数**。

DOM 兜底路径的坑（保留备用）：列表是**异步渲染**的，直接开 `?type=sub&subtype=fans` 只有壳，
**必须点一下二级页签**才出数据；二级页签的 `<label>` 宽高为 0，`locator.click()` 必然超时，
**必须 JS `click()`**（和发布弹窗里的「栏目」同一类坑）。

**探测时踩到的坑（改代码别踩）**：短时间高频打这个接口（探测阶段一口气 30+ 请求）会被网关在
**TLS 层**掐断——`APIRequestContext.get: write EPROTO ... ssl3_read_bytes:tlsv1 alert internal error`，
之后连正常请求也连着失败，容易误判成"pageSize 超限"。歇 1 分钟、请求控制在 1~2 个/次即恢复
（页面内 `fetch` 6 连发全部 200）。所以正式实现**一次名单只发 1~2 个请求 + 退避重试**
（`fansApi.retries`/`retryWaitMs`，默认 3 次 / 2.5s 起），**不写"循环翻页"的代码**。

### 身份识别（踩过一次坑，别再踩）

自己是谁**只能靠 cookie `UserName` / `UN`**（账号 ID，形如 `2501_9xxxxxxx`）；
`UserNick` 是昵称（例「示例昵称」），`UserInfo` 只是一串 hash。
主页头部显示的是**昵称**、URL 用的是**账号 ID**，两个都要拿来校验。

**曾经因为只解 `UserInfo` 而拿到 None**，URL 就退化成 `blog.csdn.net/?type=sub&subtype=fans`，
那一页照样有一堆 `blog.csdn.net/<user>` 链接（推荐作者），**结果把首页的推荐作者当成了"我的粉丝"**
（还煞有介事地列出了 46 个人）。现在的防线有三层，**改代码时不要删**：

1. 解析不出账号 ID → **中止**，绝不猜；
2. 主页 404 / 头部统计对不上 → **放弃抓取**（宁可为空，也不拿别的页面凑数）；
3. 抓到的人数与头部总数**对账**，不足就在输出和报告里报出来。

顺带一个容易迷惑的点：`blog.csdn.net/SapphireFox37`（1,057 原创 / 674 粉丝）**不是这个账号**，
浏览器里可能同时存在另一个账号的 cookie。判断"我是谁"一律以 `UserName` cookie
＋ 主页头部（**别把"151 原创 / 33 粉丝 / 27 关注"当硬校验**——那是 2026-10-08 的快照，
粉丝/关注数天天在变，对账一律以主页实时数为准）。

### 发送怎么走（选择器已实测钉死）

- 会话页可以直接用 URL 打开：**`https://im.csdn.net/chat/<用户名>`**（例 `.../chat/2503_92624912`），
  用户名就是 `blog.csdn.net/<用户名>` 里的那一段。
- 输入框 **`textarea#messageText`**（页面右下角有 `0/500` 计数）；发送按钮是 **`div.send` 里的 `button`**
  （文案「发送」），找不到就回退 **按 Enter**（页面提示「按下Enter发送内容」）。
- **成功判定（2026-10-10 重写，详见 `log/known-issues.md` 第 9 条）**：
  ⚠ 以前那版**把 7 个明明收到消息的人全判成"被拦"**，用户当场核对才发现，别再照旧说法讲。
  - 旧口径「会话里有没有系统拦截文案」是**错的**：那些文案多半是**上一批群发残留在会话里的
    历史气泡**（实测某个会话里躺了 2 条），不是本次发送的结果。
  - 现在的主判据是「**本次正文标记在不在会话里**」——标记 = 正文里的文章 ID
    （`/details/<id>`，取不到才退回开头 14 字），与 `_probe_delivery.py` 复核**同口径**：
    ① 发送前留基线（条数 / 是否已含该标记 / 已有哪些系统提示）；
    ② 沉淀 `sendSettleSec`（默认 3.5 秒）后标记仍命中，且能证明是这次加进来的
       （条数变多 或 发送前压根没有这个标记）→ `sent`；
    ③ 只有标记**没命中**时，才去看「**本次新增**」的系统提示（跟基线做 `Counter` 差集，
       不是 set——同一会话可能有两条一模一样的提示）→ `failed_dm_limit` / `failed_freq` / `failed_block`。
  - 判定已抽成纯函数 `judge_sent()` / `judge_final()`，**改完可以离线自测**
    （构造 before/after 字典即可，不必真发消息）。
- **自测手段：发给自己**——把 `pending.json` 的 users 改成自己的账号 ID 再 `--yes`，
  真实链路又不打扰任何人；测完 `DELETE FROM sends WHERE user='<自己>'` 清掉测试记录。
- **私信限额（关键，有两种，别只认一种）**：文案在 `message.limitHints`，可调：
  1. 「**24小时内最多与5个陌生人发送消息**」——对方跟你没有历史会话时的**账号级**限额，
     当天用光后当天再发陌生人也不会成功，**24 小时后重置**；
  2. 「**对方尚未回复你，你只能发送1条消息**」——对方没回过你时最多只能发 1 条，
     得等对方回你。

  ⚠ **看到这些文案 ≠ 这次被拦**：它们可能是会话里残留的**历史气泡**（上一批群发留下的）。
  脚本只在「会话里查不到本条 **且** 该文案是本次新增」时才记 `failed_dm_limit`
  （那才意味着这条确实没送到）。

  **一条更硬的常识**：**互关 / 有历史会话的人根本不受"陌生人限额"约束**。
  用户说"都是我互关，怎么可能失败"时——他大概率是对的，**先复核、别拿脚本判定跟用户争**。
  （2026-10-10 实测：判定脚本说 7 人被拦，逐个打开会话 7/7 消息都好好躺在会话里。）
  判据跟"他在粉丝还是关注列表里"无关。脚本对它**不整轮停**（有历史会话的人照样能收到），
  但**连续 3 次**命中就停本轮（`message.defaults.limitStreak`；
  **这个 3 实测偏保守**——全量群发时可临时调到 40，跑完改回。用户明确讨厌"半路停"，宁可多试）。
- **限流/拦截**：页面弹「发送频繁 / 对方拒收 / 已被拉黑」等文案 → 记 `failed_freq` / `failed_block`
  并**立刻停止本轮**（剩余的人记 `stopped_platform`）。**不要连着重试**，等几小时。
- **失败重试（只对"原因不明"的）**：`failed_unknown` / `failed_no_input` / `failed_fill` → 隔 8 秒
  **自动重试 1 次**（`message.defaults.retryTransientOnce` / `retryWaitSec`，成功记 `sent_on_retry`）；
  **平台明确拒绝的（限额 / 风控）不重试**——重试只白撞风控，应该用 `--skip-sent` 下次补发。
- **节奏**：每条之间随机停 5~12 秒（用户确认的档位，`message.defaults` 可调）。**别调小**。

### 发完要复核（别信当场那份日志 —— sent 和 failed 都不算数）

`_probe_delivery.py`（只读、不发消息）逐个打开会话，按"**里面还有没有你发的文章链接**"
判定 送达 / 被拦（它和发送判定用的是同一个标记，口径一致）：

```powershell
.\.venv\Scripts\python.exe _probe_delivery.py           # 只查粉丝（默认）
.\.venv\Scripts\python.exe _probe_delivery.py --all     # 查所有发过的人
.\.venv\Scripts\python.exe _probe_delivery.py --users=a,b,c   # 只复核指定的几个人
```

- **报给用户时必须说真实到达数**（例："57 条全部送达"或"18 条里 15 条真送到，3 条被拦"），
  不能把脚本当场的 `sent` 直接当成送达 —— **也不能把当场的 `failed` 直接当成被拦**。
- ⚠ **被判失败的人更要逐个复核**（`--users=`）：2026-10-10 那次 7 个 `failed_dm_limit`
  复核后 **7/7 都送到了**，而我当时直接拿它向用户汇报"被平台拦下"，属于虚假结论。
  **未经 probe 复核，不准在任何报告里写"X 人被平台拦下"。**
- 复核名单默认来自数据库里"成功过的人"，**默认覆盖不到失败的那批**——
  所以"普复核 N 人全送达"这句话本身并不能证明失败的那批真的失败。

### 留档与产物

| 产物 | 内容 |
|---|---|
| `output/message/latest.md` | 本次名单 + 逐条状态 + 过程记录 |
| `output/message/message.db` | SQLite：`runs`（每次运行）+ `sends`（每个收件人、状态、时间） |
| `output/message/pending.json` | `--wait` 存下的待确认名单（`--yes` 优先复用它） |
| `output/message/failed.json` | 这次**没送到**的人（配合 `--limit all --yes --skip-sent` 补发） |
| `output/dm-delivery.json` | `_probe_delivery.py` 的逐人复核结果（送达 / 被拦 + 命中的系统提示） |

**⚠ `--skip-sent` 补不了"本轮没轮到的人"（2026-10-10 踩坑）**：它跳的是**数据库里历史上
成功过的人**，不是"今天还没发的人"。首轮中途停手后直接 `--skip-sent` → 57 人只剩 7 人
（那 30 个"今天没轮到但历史上有会话"的人被跳过了，而这批恰恰最容易送达）。
**正确做法**：重新 `--wait` 抓名单 → 把 `pending.json` 里「今天已发过 + 已确认被拦」剔掉 →
写回 `pending.json` → `--yes`。（key 只由 `--to` / `--limit` / 正文 hash / `skip_sent` 算，
**改 users 列表不影响 key 校验**，可以放心改。）实测这样补的 30 人 30/30 成功。

**群发中途被用户叫停 / 中途被平台打断怎么办**：已发出去的人都在 `sends` 表里
（`status like 'sent%'`）。接着发之前必须先把名单里**已发过的人剔掉**（做法见上），
否则同一人几分钟内会收到两条一样的私信。
（**用户名比对要 casefold**：实测 `Chen_LSN` ≡ `chen_lsn` 是同一个账号，大小写不同。）

**默认不做"整批去重跳过"**（用户明确要求"照发不误"）：同一个人重复跑会重复收到，
数据库只留档、不拦。**所以每次跑之前务必确认名单**——这也是 `--wait` 存在的意义。

要跳过得**显式**加 `--skip-sent`（跳过"以前成功发过私信"的人，名单来自 `db_sent_users()`）。
顺序是**先跨运行去重、再按 `--limit` 截断**，所以 `--limit 20 --skip-sent` 拿到的是 20 个"该发的人"。
`skip_sent` 也算进 `pending_key()`，避免 `--wait` 看的是 A 名单、`--yes` 发的却是 B。
同一次运行内的去重（粉丝 ∩ 关注，实测 208 + 41 = 249 → 去重 210）**默认就做**。

### 边界（不要越线）

- CSDN 私信设置里有「**屏蔽群发信息**」开关，群发私信本身是平台敏感行为，被举报会有限流/封号风险。
  **正文由用户把关**，不要主动帮他想"更不容易被举报"的规避话术。
- **不自动过验证码**：**第 0 条（最高优先级）**——认出滑块/安全验证就立刻停手、无限期等用户本人处理，任何时间限制都不许抢先结束等待（详见文首）。
  ⚠️ 核对代码发现：**私信链路并没有调用 `pc.risk_gate()`**（全项目只有 `publish_csdn.py` 调用 `risk_gate()`/`detect_risk()`），所以跑私信时 agent 要自己盯页面，看到验证立刻停下喊人。
- 如果用户想"群发给陌生人"（既不是粉丝也不是他关注的人），**当前不支持**——
  名单只有这两类来源；要发给具体某个人可以让他直接给用户名，我单独开一个会话页发。

---

## 运行日志与已知坑（`log/`）

**约定：每次操作跑完都在 `__TOOL_DIR__\log\` 留一份 md，遇到重大问题也写这里。**

| 文件 | 内容 |
|---|---|
| `log/YYYY-MM-DD-HHmm-<动作>.md` | 单次运行：逐篇明细（文章/作者/粉丝/原创/发布时间 × 每步状态）+ 操作×状态统计 + 重大问题 |
| `log/README.md` | 索引，每跑一轮追加一行摘要。**动手前先看这里** |
| `log/known-issues.md` | 会反复咬人的坑（现象 / 根因 / 正确处置） |

**最值得先看的一条**：浏览器报
`无法启动浏览器 … Executable doesn't exist at .pw-browsers\chromium-1243\…`
**看起来像"Chrome 没装"，其实不是**——Chrome 装了，真实原因是 `.profile\lockfile`
被上一轮残留的 chrome 进程占着；而 `channel=chrome` 失败后会回退到**从来没装过的自带
chromium**，于是报出完全指向错误方向的错误。正确处置（只杀本项目 profile 的 chrome，
别用 `taskkill /IM chrome.exe`）见 `log/known-issues.md` 第 1 条。**不要真的去 `playwright install chromium`。**

---

## 选题调研 `/csdn-publish search`（热榜 → md 报告）

```powershell
.\.venv\Scripts\python.exe csdn_search.py --q "AI Agent" --limit 30 --deep 5 --my-pages 2
```

**赛道画像（重要）**：这个账号是 **Go 后端**，AI/Agent 是"要学的增量"，其他领域（C++/嵌入式/前端/
图形/机器学习理论）再火也不写。所以命令先做赛道判定，再出报告：

| 档位 | 含义 | 例 |
|---|---|---|
| 核心 | 你现在的主战场 | Go/并发/Redis/MySQL/K8s/Docker/Linux 运维/架构/性能 |
| 学习 | 要补的增量（不限语言，但优先工程侧） | AI/Agent/RAG/MCP/大模型/向量检索 |
| 核心+学习 | 最佳甜点区 | 「Go 后端接入大模型 API 的成本对比」 |
| 弱相关 | 话题沾边但赛道不符 | 「C++ 项目里讲消息队列」「NAS 上跑 Docker」 |
| 无关 | 不写 | ATE 芯片测试、CAD、鸿蒙、Qt、机器学习理论 |

- 精读与结构指纹**只在相关文章里统计** —— 否则冷门爆款（ATE 8 个 H2、0 代码块）会把
  "该对标什么"算歪，等于对着别人的赛道练基本功。
- 「值得学的 5 篇」按**收藏率 × 热度交叠**挑，且带赛道命中词，方便判断为什么算相关。
- 报告末节给「**按 Go 后端排的 AI 学习路线**」：调用与集成 → 上下文工程（RAG 本质是检索+排序，
  和后端索引同源）→ Agent 工程化（工具调用/状态机/**输出验收**）→ 部署与成本。
- 选题一律落在赛道内，并提示差异化：**别写「AI 是什么」通稿，写「后端视角的 AI」**。

调档位/加词：

```powershell
.\.venv\Scripts\python.exe csdn_search.py --profile go-backend          # 默认
.\.venv\Scripts\python.exe csdn_search.py --focus Raft etcd 网关        # 临时加入你的赛道词
.\.venv\Scripts\python.exe csdn_search.py --exclude 鸿蒙 ArkTS           # 临时排除
.\.venv\Scripts\python.exe csdn_search.py --all                          # 无关热点也全列出来
```

赛道判定的两个坑（改代码别踩）：① 短英文词必须**按词边界**匹配 —— 子串匹配会把
`Cargo`（含 go）、`Dataify`/`main`（含 ai）判成你的赛道；② 内容管理页的列表**混着草稿**，
交叉统计前要按"标题与本地 `posts/*.md` 一致"剔除，否则会把"在写"算成"已发布"。
另外草稿标题常只写 RAG/Agent 不写 AI，所以覆盖度要按 **AI 家族**合并统计，否则会误报"空白"。

一次跑约 45 秒，产出三份东西：报告 `hot_search/<关键词>-<日期>.md`、
原始数据 `hot_search/raw/*.json`、历史索引 `hot_search/index.json`（下期的"新上榜/掉榜/连续在榜"靠它）。

设计上刻意做的几件事（都是为了不被单一指标带偏）：

1. **抓两个排序**：`热度排序` + `收藏排序`。2026-10-08 实测两张榜**重合 0 篇**——
   只看一个榜等于漏掉一半视野。且**热度 ≠ 阅读**（热度榜第 3 名只有 424 浏览，
   榜上另有 105,154 浏览却排第 22 的）。
2. **精读按"收藏率/热度交叠"挑**，不按热度挑；正文只做**结构统计**（字数/H2/代码块/表格/
   图片/数字密度/内链/问句 H2 占比），**绝不摘抄段落**（版权）。
3. **和自己账号交叉**：报告里明确写"榜单里你完全没写过的主题"（那才是增量），
   以及"你写过 N 篇、平均阅读多少"（避免重写没数据的题材）。
4. **结论分三档**：现象 / 已排除 / 尚未证实 —— 和实验流程同一套标准。

采集口径的坑（改代码别踩）：

- 榜单行文本**两个排序口径不同**：热度排序是 `… X 收藏 H 热度 作者 已关注`，
  收藏排序是 `… X 收藏 N 新增收藏 作者 关注`（**没有热度字段**）。所以
  `csdn_hot.ROWS_JS` 不能复用（它按"含热度"筛行，收藏榜会整行取空），
  `csdn_search.py` 自带 `ROWS_JS`（按"含浏览+收藏"筛）与 `parse_row()`。
- **作者名尾部要剥 `关注/已关注`**，否则会变成"小码农叔叔 已"。
- **文章真标签在 `<meta name="keywords">`**；宽泛的 `.tag-link` 会抓到侧栏的"订阅专栏/查看详情"。
- **发布页的目录是普通 `<p>`**（不是 `.toc`），不剔除会污染"开头 200 字"和段落统计，
  用 `strip_leading_toc()` 按行启发式切掉。

## 任务配置字段语法

`title` / `content` / `tags` / `cover` / `summary` / `category` 六个字段通用：

| 写法 | 含义 |
|---|---|
| `"auto"` | 自动推导，见下表 |
| `"file:路径"` | 从文件读（相对路径按任务文件所在目录解析） |
| 其他字符串 | 字面值直接用 |

| 字段 | auto 的含义 |
|---|---|
| `title` | 取正文 front-matter 的 title，没有就取正文第一个 `# 一级标题` |
| `content` | ★ **脚本不会写文章**。auto 时只生成骨架，必须由你把正文写进去 |
| `tags` | 按「素材文章标签 × 搜索关键词」加权推导 |
| `cover` | 生成渐变封面（`coverTheme` 选配色）。**留空 / `null` / `none` = 不要封面**；不写这个键才是 auto |
| `summary` | 取正文纯文本前 110 字 |
| `category` | 留空或 auto = **不放入任何栏目**（合法且常用） |

必填项：`search.keywords`（搜索关键词，可多个）和 `search.minViews`（最低阅读量）。
这是用户明确要求手填的两项，**不要替他设默认值**。

**最省事的写法**：只写 `search` + `"count": N`，会自动展开成 N 篇全 auto 的文章，
正文落在 `posts/article-1..N.md`。参考 `jobs\job.all.json`：
换主题只改 `keywords` 几个词，`count` 控制发几篇。
`"count"` 和 `"articles"` 二选一——写了 `articles` 就用它，否则按 `count` 展开。

**顶层同名字段 = 所有文章的默认值**，单篇里写了就以单篇为准。适用于
`title` / `tags` / `cover` / `coverTheme` / `summary` / `category` 六个字段。
所以 `jobs\job.all.json` 那种 `"count": N` 简写也能在顶层统一控制封面和标签，
例如顶层写 `"cover": ""` 就等于给这批文章全部关掉封面。
**唯一例外是 `content`**：它不参与回落，否则 `count: N` 会让 N 篇文章共用同一份正文。

其他可选：`prependFile`（文章开头固定横幅，写 `templates\start.txt`）、
`coverFooter`（封面右下角小字）、`maxTags`、`prependTrim`（默认 true，清理横幅尾部
多余空行和 `---` 分隔线）、顶层 `cover` / `tags` / `summary` 等默认值（见上）、
`publish.on`（yes 发布 / no 存草稿，见上）、
`publish.dryRun`、`publish.draft`（只在没写 `publish.on` 时才生效）。

每篇还能带自己的 `search` 覆盖顶层配置（不同文章搜不同主题时用）。

## 标准作业流程

1. **`.\csdn.bat check`** 确认登录态。失效就让用户跑 `login`（要扫码，你做不了）。
2. **确认用哪个任务文件**：用户说了主题就按文件名匹配，没说清就跑
   `csdn_job.py` 列出全部再问。`keywords` / `minViews` 必须由用户确定，
   **不要替他猜**。新主题从 `jobs\job.all.json` 复制一份到 `jobs\` 下改名。
3. **`csdn_job.py prepare <任务文件>`**：搜集素材、在 `posts/` 下建正文骨架
   （骨架里附了素材链接）、给能定标题的篇目生成封面。
4. **读素材写原创**：`read` 打开 `output/job/<任务名>/sources/*.md`。
   **严禁整段照搬**——CSDN 有原创声明和查重，抄段落会害用户被处罚。
   只吸收观点和结构，用自己的例子、代码、表述重写。每篇主题必须能区分开。
5. **把正文写进 `posts/<slug>.md`**，并删掉首行的 TODO 标记。
   标题写 `# 一级标题`（title 为 auto 时取用它），或直接在正文 front-matter 里写 title。
6. **`csdn_job.py status <任务文件>`** 确认全部 ✅ 且没有 ⛔（体检会连标签贴不贴题
   一起告诉你）。有 ⛔ 就按提示改，**别顺手加 `--force`**。
7. **（只在没把握时做）** `publish <任务文件> --only 1 --dry-run`，核对日志里
   「发布弹窗字段回读」的标签/封面/摘要/栏目，再用 `read_image` 看
   `output\*-04-dialog-filled.png` 确认排版。配置已经是老一套、只是换了内容，这步可跳。
8. **发布**：确认 job.json 里 `publish.on` 是用户想要的值（`yes` 公开 / `no` 进草稿箱），
   然后跑 `publish <任务文件>`。
   **不要再问用户"是否正式发布"**：`publish.on` 就是他的答复，问一遍等于让他重复表态。
   **体检拦下时的正确反应是改内容或说明原因，不是直接 `--force`。**
9. **回报**：`output\*-05-result.png`（草稿模式是 `*-05-draft-saved.png`）和最终 URL。
10. **发完 24 小时左右**（用户下次来找你时顺手做）：跑一次 `.\csdn_stats.bat`，
    用 `latest.md` 里的「发布后 24 小时阅读」和「本次新增阅读排行」告诉用户
    这篇到底跑得怎么样，下一篇该往哪个主题词靠。

## 发布前体检（已自动接入，不用额外操作）

`publish` 和 `status` 都会自动跑一遍 `csdn_lint.py`，分两级：

- **error = 拦下发布**（只拦确定性硬伤）：标题空/不足 5 字、正文还是骨架、
  正文非空白字数不足 500、一个标签都没有、标签超过 `maxTags`、封面文件不存在、
  **和自己已发布过的文章撞标题**。被拦时**不会启动浏览器、不会提交任何内容**。
  确认无害加 `--force` 照发；`--no-lint` 完全跳过。
- **warn = 只提示，不拦**：标题没数字 / 偏短 / 超 60 字 / 没命中搜索关键词 /
  含连续问号（`？？？`）、没封面、摘要为空或过长、标签在标题正文里都没出现、
  当前不在流量高峰（这条会带上**你自己数据里表现最好的发布时段**）。

「撞标题」不是假想：该账号实测有 20 篇同名《Docker居然还能这么用？？？》，
阅读量 9~336 不等——同题文章互相分流，体检就是拦这个。
**这条规则依赖 `output/stats/` 里的快照**（跑过一次 `.\csdn_stats.bat` 才会生效），
快照不存在时它会静默跳过，别以为"没报就是没撞"。

**体检提示要念给用户听，尤其是 `tag-irrelevant`**：标签 auto 是启发式，
实测会把 Docker 文章推出 `['云原生','运维']` 这种不贴题的标签。

单篇自查（不联网、不启动浏览器）：

```powershell
.\.venv\Scripts\python.exe csdn_lint.py posts\docker-basics.md --tags Docker,容器 -k Docker
```

exit code 1 = 有 error。改完文字想快速复检就用它，不用跑整条发布链路。

## 阅读数据复盘 `csdn_stats.py`（只读，不发布）

它回答"到底哪类文章真的有人读"。只打开创作中心「内容管理」页读数据，
**不发布、不修改、不刷量**。

```powershell
.\csdn_stats.bat                    # 采集 + 出报表（全量约 8 页，1 分钟内）
.\csdn_stats.bat --pages 2          # 只采前 2 页，快速看一眼
.\csdn_stats.bat --report-only      # 不联网，用已有快照重新出报表
.\csdn_stats.bat --window-days 14   # 分组对比口径改成最近 14 天
```

**每次都存快照**，所以第一次跑只是基准，之后每次跑都会给出**真实新增阅读**和
**当前阅读速度（阅读/天）**。想要「发布后 24 小时阅读」这个最公平的横向指标，
就在文章发出后 24 小时左右再跑一次——脚本只认落在「发布后 24h ±8h」窗口里的快照，
宁缺毋滥，没有就显示 `—`。

产物在 `output/stats/`：

| 文件 | 内容 |
|---|---|
| `snapshots/<时间>.json` | 每次采集的原始数据（可回溯、可对账） |
| `history.csv` | 长期时序（长格式），方便自己画曲线 |
| `latest.md` | 复盘报告：新增排行、累计排行、24/72h、时段、标题特征、主题词、低阅读文章 |

**怎么用**：写下一篇之前先看 `latest.md` 的「五、主题词表现」（哪类主题平均阅读高）
和「六、零阅读/低阅读文章」（哪些标题白写了）；发完之后定期跑，看新增排行里谁在带量。

⚠️ 横向对比有年龄偏差：老文章天然累计更多。所以「四、分组对比」默认只统计
**最近 7 天发布**的文章，窗口内不足 3 篇时回退到全部并在报告里明确标注——别把
回退后的数字当成结论。

## 已知限制

- **每日发文额度**：编辑器底部会显示「今日发文额度还有 N 篇」（剩 0 时才变红说用完）。
  能走到最后一步但点发布被拒，**是账号限制不是故障**。一次要发的篇数别超过 N。
- **存草稿不占发文额度**，草稿箱里是一篇标签/摘要/栏目都填好的完整文章，改天点发布即可。
- **栏目最多选 3 个**，只从账号已有专栏里选（不新建，避免垃圾专栏）。
- **发布链路已跑通**：2026-10-07 一次连续发布 5 篇全部成功（`05-result.png` 显示
  「发布成功！正在审核中」）。仍建议发布后看一眼 `output\*-05-result.png`。
- `csdn_research.py` 的 `--pages` 翻页不可靠（CSDN 前端分页），多给 `keywords` 代替。
- 标签 auto 是启发式，可能挑出不贴题的素材标签。在乎质量就手写 `tags`
  （体检的 `tag-irrelevant` 就是专门盯这个的）。
- **`csdn_stats.py` 只有「内容管理」页能看到的字段**（阅读/点赞/评论/收藏/发布时间），
  没有标签和栏目维度——那要从每篇文章详情页取，151 篇逐页打开太慢，暂未做。
  想做标签维度分析，就手工跑几次 `csdn_research.py` 或自己补数据。
- **复盘数据的横向对比天生有年龄偏差**：老文章累计更多。报告里已经用「只统计最近
  7 天」来缓解，但别把「累计阅读排行」当成质量排名——那是时间排名。

## 浏览器健壮性：弹滑块 / 浏览器异常关闭（2026-10-08 新增）

发布链路以前只做「防自动化检测」（`--disable-blink-features=AutomationControlled`），
**没有任何风控识别，也没有崩溃恢复**，所以一旦弹验证码或浏览器崩了就整条链路作废。
现在 `publish_csdn.py` 内置两件事，默认都开：

**① 风控闸门（`risk`）——识别滑块就停下来等人点，点完自动续跑。**
命中判定用三段证据：结构（`.geetest_*`/`.yidun_*`/`.verify-*`/`*captcha*`/验证 iframe）、
文案（只在弹窗/遮罩里找「安全验证/拖动滑块/人机验证」等，**绝不扫整页**，否则帮助文档和
侧栏广告会误报）、URL（`sec.csdn.net`、`*captcha*`）。
命中后：截图 `*-risk-blocked.png` + 响铃 + 打印大字提示 + 往 `output/NEEDS-HUMAN.txt` 追加一条，
然后**轮询等你手动点完**，检测到通过就原地继续。

> ⛔ **永远不要试图自动过验证码。** 滑块是行为风控，自动化过码会让验证升级，
> 成本远大于让真人点 10 秒。无人值守要的是 `--on-risk abort` + 任务队列重跑。

**等待不设上限（按文首「第 0 条」，优先级最高）**：`risk.waitTimeoutSec`、
`interact.maxMinutes` 这类超时**不得用来抢先结束等待**，也不许"超时就跳过这篇"。
⚠️ 代码现状与这条规则**尚未对齐**——`waitTimeoutSec` 默认 300 秒仍是硬超时，
超时按放弃处理（退出码 5）；这是**待改项**。在改掉之前，撞到验证时要盯着日志，
别让它超时退出；真超时了就当"没做完"，绝不当成功。

**这道闸门目前只在发布链路里**：`csdn_interact.py`（评论/点赞/收藏）和
`csdn_message.py`（私信）**都没有接入**（全项目只有 `publish_csdn.py` 调用
`risk_gate()`/`detect_risk()`）。跑这两条链路时，按文首第 0 条由 agent 自己盯页面兜住。

**② 崩溃自愈（`crash`）——分级恢复，不是无脑重启。**
这一条是 2026-10-08 实测推翻了初版设计才定下来的，**别改回去**：

- **`TargetClosedError` 不等于"浏览器崩了"。** 实测 CSDN 风控会对**编辑器页关掉标签页**，
  而浏览器进程完全健康（同一份探针里：CSDN 首页能稳 14 秒，编辑器页有时 1 秒内被关）。
  初版把两者混为一谈、去重启整个浏览器：多花十几秒、还可能撞 profile 锁 —— **方向是错的**。
  现在先分级：能在同一个 context 里拿到可用页面 → **原地新开 page 续跑**；
  连 context 都不可用 → 才重启浏览器。日志会明确写是哪一种。
- **`ctx.pages` 问不出进程死活。** 浏览器进程已经死了，Playwright 的本地对象还在，
  `ctx.pages` 照样正常返回。**唯一可靠判据是真去 `new_page()` / `page.evaluate()`**，
  所以存活判断用 `page_recoverable()`，`Browser.alive()` 只能当参考。
- **`is_browser_dead()` 只负责"是不是浏览器断开这类错"**：选择器找不到元素这种业务错误返回
  False，直接报错（退出码 1）不浪费重试；它**不负责**区分标签页/进程。
- **启动前必须清残留 Chrome。** 一次启动失败或崩溃会留下十几个 chrome 子进程占着
  `user_data_dir`；Chrome 的单例机制看到已有实例，就"在已有会话中打开"然后立刻退出，
  Playwright 只报一句 `TargetClosedError` —— **不清进程，重试多少次都必然失败**。
  实测：清掉 8~9 个残留后，同一个 profile 立刻恢复正常。只杀命令行里带本项目 `.profile`
  的进程，绝不动用户自己的 Chrome。
- **风控是成波来的。** 实测同一套代码在一次连续访问过密之后会连续失败三次（每次都
  `TargetClosedError`），隔一两分钟再跑就完全正常。所以"连着失败"先想想是不是刚才跑太密，
  别急着怀疑代码。

**③ 顺手治了「是否翻译此页面」气泡。**
启动参数就关掉 Chrome 内置翻译（`--disable-features=Translate,TranslateUI,TranslateNewUX`
+ `--lang=zh-CN`），页面级还有一层兜底（递归 shadow DOM 点关闭 / 移除 `translate-notice`）。
**这个气泡是"元素点不到"的隐形元凶之一** —— 它盖在编辑器上拦点击，日志里只会看到超时。
实测日志里出现过 `已关闭干扰气泡: translate-remove`，说明这层兜底**真的在干活**。
同一层兜底还会关掉「未正常关闭，是否恢复」气泡（只点"不恢复"，绝不点"恢复"）。

**测试**（离线、不启浏览器、不联网，共 58 项）：

```powershell
& __TOOL_DIR__\.venv\Scripts\python.exe test_robustness.py    # 33 项
& __TOOL_DIR__\.venv\Scripts\python.exe test_launch_paths.py  # 25 项
```

`test_robustness.py` 覆盖：陈旧锁判定（活进程不删/死进程才删/**自己写的心跳不算占用**/开关能关）、
风控识别四种情形、崩溃与业务错误的区分、闸门 wait/abort/超时/等待中浏览器被关掉、命令行开关覆盖。
`test_launch_paths.py` 用**真实调用形参**跑通启动失败路径：清锁→重试→回退 chromium→成功、
全都起不来→可操作的报错+清心跳、被别的活进程占用→拒绝且不删锁、chromium 没装→不空转重试、
每次重试前都清过残留。改了 `risk`/`crash`/`browser` 相关代码就跑这两套。

> 写这两套测试时踩过一个坑：只把异常类名映射到结论的"纯函数测试"会**给出虚假的安全感**
> —— 真正错的是调用方的判断逻辑（把标签页被关当成进程死）。所以 `test_launch_paths.py`
> 特意用真实形参跑 `launch()`，而不是只测 `is_browser_dead()` 的返回值。

## 排错

| 现象 | 处理 |
|---|---|
| 未登录 / 重定向到 passport | 让用户跑 `.\csdn.bat login` |
| **浏览器起不来 / 报 `Executable doesn't exist at .pw-browsers\chromium-…`** | **别去装 chromium**。十有八九是 `.profile\lockfile` 脏锁（Chrome 其实装了）。处置见 `log/known-issues.md` 第 1 条：只杀本项目 profile 的 chrome，再删 lockfile。**现在启动前会自动清这种陈旧锁**（`browser.cleanStaleProfile`），日志里会打「已清理陈旧 profile 锁」 |
| **右上角弹「Chrome 未正常关闭，是否恢复」** | 这是上次崩溃污染了 profile。现在启动参数已关掉这个气泡，并且启动前会清锁；若仍出现，看日志里有没有「⛔ profile 正被另一个进程占用」——那是真有别的浏览器在用，别硬删 |
| **弹「是否翻译此页面」/ 元素点不到** | 翻译气泡拦截点击。启动参数已关掉翻译，页面级还有兜底；确认日志里没出现 `已关闭干扰气泡: translate-close` 的话再排查选择器 |
| **跑一半弹滑块/安全验证** | **第 0 条（最高优先级）：立刻停手、无限期等真人拖滑块**。日志里会有「⚠ 遇到 CSDN 安全验证」+ `output/NEEDS-HUMAN.txt` + `*-risk-blocked.png`；**任何时间限制都不许让它提前放弃**（`waitTimeoutSec` 目前仍是硬超时，属待改项；真超时了按"没做完"处理，**不许当成功**）。只有用户显式 `--on-risk abort` 才放弃（退出码 5，内容没提交）。**互动链路（评论/点赞/收藏）和私信链路都还没接这道闸门 → agent 必须自己盯页面，看到滑块立刻停手并喊用户** |
| **浏览器跑到一半异常关闭** | 先看日志说是哪一种：**「标签页被关掉了…正在新开页面续跑」= 浏览器其实没死**（多半是 CSDN 风控关了编辑器页），原地新开 page 继续，正常几秒就恢复；只有日志说「浏览器异常关闭，正在自动恢复」才是真的重启。两种都失败到底才报退出码 6，看 `output/*-crash.png` |
| **连着好几次都是浏览器异常关闭** | 先别改代码：实测风控是**成波**来的，连续访问过密会连挂三次，隔一两分钟再跑就正常。也确认下是不是有另一个会话在用同一个 `.profile`（日志会打「⛔ profile 正被另一个进程占用（pid=…）」） |
| 一键三连跑到一半浏览器挂了 | 脚本会自己清理残留并**重试同一篇**；连续挂 4 次才中止本轮（属环境问题，不是被平台拦） |
| 找不到元素 | `inspect` 导出结构，改 `config.json` 的 `selectors`，**别改代码**（先确认不是翻译气泡/验证页挡的） |
| 素材不够 | 降低 `minViews`、加更多 `keywords` |
| 提示素材缓存过期 | 改了 `keywords`/`minViews`，重跑 `prepare`（只改 `pages`/`wantSources` 不阻塞） |
| 某篇正文抓到 0 字 | 该文容器特殊，跳过换一篇 |
| 三连候选凑不够 count | 正常：实测搜索页综合排序里近 7 天只占约 5%，且还要过内容农场/粉丝数三关。**加 `keywords` 或放宽 `--days`**，脚本会如实报"实做 X/N" |
| 评论发不出去 | 平台限流或等级限制。按约定跳过即可；想看真实结构跑 `--probe-comment <URL>` |
| 发布被拒 | 看截图底部有没有额度红字 |
| **浏览器起不来 / 截图没生成 / 读不到草稿箱计数，但看不出为什么** | 先用 `probe_writable.py` 分清是**环境写不进去**还是工具坏了。2026-10-08 实测本工作区出现过：`<另一台机器上的旧路径>` 根可写、但 `csdn-publisher\` 及其下所有子目录（`.profile`/`output`/`.tmp`/`posts`/`jobs`/`log`）**一律不可写**（连追加已存在文件都 PermissionError）。这种状态下 `launch_persistent_context` 会以一行看不懂的 Chromium stderr 失败、截图会静默失败、连新加的 `NEEDS-HUMAN.txt` 都写不出来 |
| 上面那种情况怎么办 | ① 最快：把会话切到**完全权限**（full access）——只影响本会话；② 怀疑是 ACL 就去跑 `diagnose-windows-sandbox-acl` 那个 skill 的一条命令；③ 验证：重跑 `probe_writable.py`，全 OK 才算好了。**注意**：ACL 修复脚本可能判 `NOT_THIS_CLASS`（目录权限和所有者都没问题、`writeDac`/`writeOwner` 都可用），那就不是 ACL 能修的，别反复跑 |

### 关于"浏览器弹翻译 / 弹恢复"的两个环境细节

- **「是否翻译此页面」不是 CSDN 的弹窗，是 Chrome 的**，它在 shadow DOM 里，
  普通选择器点不到，而且会**盖在编辑器上拦截点击** —— 日志表现成"找不到元素/点击超时"。
  已在启动参数（`--disable-features=Translate,TranslateUI,TranslateNewUX` + `--lang=zh-CN`）
  和页面级兜底（`KILL_BUBBLES_JS` 递归 shadow DOM）两层关掉。
- **「Chrome 未正常关闭，是否恢复」** 说明上一次是崩溃退出的，`profile` 里的
  `lockfile`/`SingletonLock` 会留给下一次，导致启动失败。已做成启动前自动清理
  （靠 `.browser-heartbeat.json` 里的 pid 判断是不是真有人在用；有人在用就报错不动手）。

## 改代码时务必守住的坑

实测踩出来的，别改掉：

1. **标题框默认 `display:none`**，必须先点 `.article-bar__title-display`。
2. **正文是 Cledit 的 `pre.editor__inner`（contenteditable），不是 CodeMirror**；
   写入用 `#import-markdown-file-input` + `set_input_files`，别逐字敲。
3. **判断导入成功要比对内容，不能比"有没有变化"**——CSDN 会恢复上次草稿。
4. **千万不要按 Escape**：关的是整个发布弹窗，字段全白填。
5. **填写顺序：封面 → 摘要 → 栏目 → 标签**。标签浮层会一直留在页面上挡住下面的
   控件，必须放最后。
6. **封面「确认上传」是 `div.vicp-operate-btn`，不是 button**。
7. **栏目选项的 `<label>` 宽高为 0**，`locator.click()` 必然超时，必须 JS
   `label.click()`；结果从 `input[name=categories]` 回读。
8. 两个「发布文章」：编辑器 `button.btn.btn-publish`、弹窗 `button.btn-b-red`；
   另有 `button.simulation-button` 同名，别选错。
9. **CSDN 对非浏览器客户端返回 HTTP 521**，一切 CSDN 数据都必须走浏览器。
10. **md 可能带 UTF-8 BOM**，读取一律 `utf-8-sig`，否则 front-matter 被当成正文。
11. **CSDN 会记住上次选过的标签**，填标签前必须先清空历史标签，否则会混进无关标签。
12. **「内容管理」列表页的行是 `.article-list-item-mp`**，不是 `.article-list-item`
    （后者是表头）。每行：标题 `.list-item-title a`（href 是编辑器地址，`articleId=` 后面
    就是文章 id）、`.article-list-item-time`、指标在 `.article-list-item-info
    .item-info-left p` 里，**顺序固定是 阅读/点赞/评论/收藏**、正式地址取
    `a[href*="blog.csdn.net"][href*="/article/details/"]`。
13. **采集翻页必须等第一行的 articleId 变了再抓**（`csdn_stats.py` 的
    `wait_first_id_change`）。它和 `csdn_research.py` 一样是前端分页，翻页按钮
    `button.btn-next` 点了之后列表是异步换的；不等就直接 `evaluate`，会把上一页
    的数据重复采一遍。翻页器是 `ul.el_mcm-pager li.number`，最大页码就是总页数。
14. **快照时间戳是自己写的 ISO 格式**（`2026-10-07T22:02:20`），解析一律用
    `datetime.fromisoformat`。早期只认页面的空格格式 `%Y-%m-%d %H:%M:%S`，
    结果 `gap_h` 恒为 None，整个「本次新增」区块被静默清空、24/72h 也永远填不上
    ——**这类"解析失败就当没数据"的静默降级最难查，宁可抛错也别默默返回 None**。

## 维护

```powershell
.\csdn.bat inspect                            # 导出编辑器结构
.\csdn.bat publish posts\demo.md --dry-run    # 导出发布弹窗结构
```

按 `output\inspect.json` 和 `output\publish-dialog.json` 里的真实 class 名改
`config.json` 的 `selectors`。`jobs\job.all.json` 与 `jobs\job.independent.json` 都是可直接跑通的示例。
