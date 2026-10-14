# CSDN 工具：全部可用说法

> 这份文件是 `/csdn-publish help` 的输出源——改这里，help 就跟着变。
> 说法不用背原话，意思到了就行；下面每一条都给了对应的底层命令，方便你或我直接跑。

## 三个快捷指令

| 你发 | 我做什么 |
|---|---|
| `/csdn-publish help` | 把下面这份清单原样回给你，**不执行任何操作** |
| `/csdn-publish config` | **先给你最常用的三个发布方式**（直接发布 / 存草稿 / 定时发布），再列全部可配置参数（job 字段 / config.json / 命令行开关 / 优先级），等价于跑 `.\csdn.bat config` |
| `/csdn-publish choice` | 立刻弹出选项菜单让你点，**不先解释一堆** |
| `/csdn-publish`（不带参数） | 等同 `choice` |

---

## 一、阅读复盘（只读，不动你博客上的任何东西）

| 你可以这么说 | 我会做什么 | 底层命令 |
|---|---|---|
| 跑一下阅读复盘 / 看看我博客最近的阅读量 | 全量采集 + 出报表 + **读报告给你讲结论**（不是甩个文件） | `csdn_stats.bat` |
| 哪篇在涨 / 这几天哪几篇跑得好 | 重点讲「本次新增排行」和阅读速度（需距上次采集 ≥1 小时） | `csdn_stats.bat` |
| 下一篇写什么方向 / 我该写什么 | 读报表的「主题词表现」「低阅读文章」给选题建议，并说明依据 | `csdn_stats.bat` |
| 这个月我写得怎么样 | 分组对比口径改成最近 30 天，避免文章年龄偏差 | `csdn_stats.bat --window-days 30` |
| 只看看最近几篇 / 快点看一眼 | 只采第一页，快 | `csdn_stats.bat --pages 1` |
| 别联网了，用上次的数据再看一遍 | 不采集，用已有快照重出报表 | `csdn_stats.bat --report-only` |
| 排行榜多列点 | 排行榜从 15 行加到 N 行 | `csdn_stats.bat --top N` |

**产物**：`output\stats\latest.md`（报告）、`snapshots\*.json`（历史快照）、`history.csv`（时序，画曲线用）。

**三条时间上的注意事项**：

1. **第一次跑只建立基准**，没有增量和速度。
2. **「发布后 24 小时阅读」要按时跑**：只认「发布后 24h±8h」窗口内的快照，错过就永远是 `—`。想让这个指标有意义，就在发文后一天左右跑一次。
3. **速度指标要间隔 ≥1 小时**才算，否则不外推（3 分钟 +2 阅读折成「每天」是 960，纯属误导）。

---

## 二、发文章

| 你可以这么说 | 我会做什么 |
|---|---|
| 帮我发 3 篇 xx 方向的文章 | 搜素材 → 我写原创 → 生成封面 → 发布（全自动，只改 JSON） |
| 学习 CSDN 上阅读量 1000 以上的文章，整合成原创 | 同上，`minViews` 设成 1000 |
| 同步博客到 CSDN | 发布流程 |
| 先存草稿箱，别公开 | 把 `publish.on` 设成 `no`，标签/摘要/栏目照样填好，只进草稿箱 |
| 明天早上再发 / 先别发 | 只 prepare（搜素材+建骨架+生成封面），不发布 |
| 看看那几篇准备得怎么样了 | `csdn_job.py status`：完成度 + 体检结果 |
| 把草稿箱里的文章全部改成 x 月 x 日 xx:xx 发布 | 逐个打开草稿（**不动正文/标签/摘要**）→ 定时发布 → 回读校验；标题核对不一致的跳过不发 | `csdn_schedule_drafts.py --at "2026-10-08 16:30"` |
| 草稿箱里都有啥 / 排期成功没有 / 删掉某个草稿 | 列「草稿箱 / 全部」列表（`--raw` 能看到排期稿的两个时间），删除带标题校验防误删 | `csdn_draft_clean.py --tab 草稿箱` |
| 最近什么题材火 / 我该写什么方向 | 抓综合热榜（热度 + 收藏双排）、精读 5 篇拆结构指纹、和自己账号交叉，出 md 报告到 `hot_search/` | `csdn_search.py` |
| 换主题：改成写 Flink | 复制 `jobs\job.all.json` 到 `jobs\` 下改名，只改 `keywords` |
| 给文章配图 / 把表格做成图 | 表格或终端输出渲染成 PNG → 传到 CSDN 图床 → 写进 markdown（**无封面也照样能插图**） | `csdn_image.py render` + `upload` |
| 图不够精美 / 想要流程图 | **矢量示意图**（Mermaid 流程图·时序图·架构图），无需联网、可精确表达你的数据 | `csdn_figure.py diagram` |
| 从网上找可商用配图 | 只要 CC0/CC-BY/Unsplash/Pexels 这类可商用源，并写图注署名；**不搬别人文章里的图** | `csdn_figure.py stock` |
| 这篇文章的数据哪来的 / 能不能复现 | 建/跑实验三件套（README+run.py+numbers.md），文章里的数字必须能一条命令复现 | `exp/new_experiment.py <slug>` |
| 最近什么题材火 / 我该写什么方向 | 抓热榜 + 拆爆款风格指纹；积累几天后用 `--trend` 看连续在榜的题材 | `csdn_hot.py` / `--trend` |

**有两件事必须你定，我不会替你猜**：`search.keywords`（搜索关键词）和 `search.minViews`（最低阅读量）。
另外**登录扫码只能你本人做**——登录态失效时我会让你跑 `.\csdn.bat login`。

**注意每日发文额度**：编辑器底部会显示「今日发文额度还有 N 篇」。一次要多发几篇的话我会先 `--only 1` 试一篇。

---

## 三、发布前体检

| 你可以这么说 | 我会做什么 | 底层命令 |
|---|---|---|
| 这篇发出去有没有问题 | 跑体检，列出硬伤（error）和建议（warn） | `csdn_lint.py posts\xxx.md` |
| 帮我检查这几篇 | 同上，逐篇跑 | `csdn_lint.py <md>` × N |
| 把标签也一起算上检查 | 用你给的标签/摘要/关键词覆盖文稿里的值再检查 | `csdn_lint.py <md> --tags a,b -k xx` |
| 我要把这些结果接进别的东西 | 输出 JSON | `csdn_lint.py <md> --json` |

**体检是自动的**：正常 `publish` 时我会自己跑一遍，硬伤会**在启动浏览器之前**拦下，不会白跑流程。只有你明确说「这些不是问题，照发」我才会加 `--force`。

**error（拦下发布）**：标题空/不足 5 字、正文空/还是骨架/不足 500 字、没标签、标签超上限、封面文件不存在、**和自己已发布过的文章撞标题**。
**warn（只提示）**：标题没数字/偏短/超 60 字/没命中关键词/含连续问号、没封面、摘要为空或过长、标签在标题正文里都没出现、当前不在流量高峰。

---

## 四、一键三连（点赞 / 收藏 / 评论）

| 你可以这么说 | 我会做什么 | 底层命令 |
|---|---|---|
| 给 50 篇文章点赞 | 按「近 7 天 + 小作者」挑 50 篇逐篇点赞，每篇之间随机停 15~30 秒 | `csdn_interact.py --ops like --count 50` |
| 给 50 篇收藏 | 同上，只收藏 | `--ops save --count 50` |
| **一键三连 50 篇** | **我逐篇读正文写一段感悟**（开头「大佬您好，昵称」，约 100 字），**先给你审**，你确认后才发：同一篇上依次做 **赞 → 藏 → 评论** | 见下方四步 |
| 先看会挑哪些文章，别真点 | 只判定与记录，**绝不点击**（且不写去重库，否则一次演练就把整批标成做过了） | `--ops 三连 --count 50 --dry-run` |
| 上次没做完的接着做 | 去重库按「文章 × 操作」记账，已成功的不再动；直接再跑一次即可 | 同上，去掉 `--dry-run` |
| 看看做过多少了 | 打印存量统计 + **粉丝最少的作者**（这些才会回你三连） | `--stats` |
| 评论框长什么样 / 评论发不出去 | 勘察评论框真实结构（只开面板，不输入不提交） | `--probe-comment <文章URL>` |

### 评论是「读正文写感悟」，四步走

脚本不会写文章，所以评论必须由 agent 读正文后写。**不要再用固定文案刷评论**
（50 条一模一样的话术正是平台判定"低质重复"的靶子）。

```powershell
# ① 抓正文摘要，产出待写评论清单（不发任何东西、不点任何按钮）
.\csdn_interact.bat --prepare-comments --ops 三连 --count 50
#    → output/interact/pending-<runid>.json   机器清单
#    → output/interact/pending-<runid>.md     人读版（标题+小标题+正文摘要）
#    → 感悟写到同名 pending-<runid>.comments.json：{"<article_id>": "感悟正文"}

# ②（我做的事）逐篇读正文，写约 100 字感悟。只写正文，开头由脚本补

# ③ 给你审：把最终评论整条打出来（含「大佬您好，昵称」），并标出偏长/偏短/漏写
.\csdn_interact.bat --preview output\interact\pending-<runid>.json

# ④ 你确认后再发
.\csdn_interact.bat --send --pending output\interact\pending-<runid>.json
```

四条硬规矩：

1. **开头固定「大佬您好，{昵称}」**，由脚本用**作者真实昵称原样**拼上
   （实测有 `❀͜͡傀儡师`、`L oiey` 这种名字，也照用不改）；拿不到名字才退回「博主」。
2. **不提"我已一键三连"**：只写内容感悟，像个真读者，不像来换互动的。
3. **读不到正文就只做赞和藏、不评论**（`minBodyChars`，默认 400 字）——
   实测约 45% 的 VIP/付费页既没有发布时间也抓不到正文，硬夸容易翻车。
   **读不到作者昵称也一样**（粉丝数会一起缺失，农场/规模过滤等于失效）。
4. **没写感悟的文章不会发评论**（记为 `skipped_no_comment`，**不定性**：补写好再跑就会补发）。

**审阅时可以整篇丢弃**：在感悟文件里给那一篇写 `"__DROP__"`，赞/藏/评论都不做。
用在**列表层看不出来、读到正文才发现**的营销号上——实测抓到过卖课引流文
（"全套视频教程 / 资料如何领取"）和企业营销号（通篇产品叙事 + 咨询电话）。

**候选来自两个源**（`feedUrls` 可调）：

| 源 | 特点 |
|---|---|
| 搜索页「综合」排序 | **列表自带日期**，能先按天数筛；但新鲜文章被内容农场淹没（26 篇里 12 篇是 1000~6400 篇原创 / 几十粉） |
| 推荐流 / 热榜（`nav/back-end`、`nav/ai`、`rank/list`） | **真实开发者多**（800~7000 粉那种）；行里没有日期，只能进详情页判新旧，所以排在搜索候选之后 |

**赛道初筛**（`trackKeywords` / `trackExcludeKeywords`）：标题必须命中赛道词、且不命中排他词。
专治关键词撞车——`Gin` 会抓到 **NCCL 的 GIN**（GPU-Initiated Networking）、
`后端` 会抓到「数字后端」（芯片）和「前后端分离」。注意只是**粗筛**：
GIN 同时是 Go 框架 / PostgreSQL 索引 / NCCL 网络库的缩写，光看标题分不清。

**篇数没有默认值**：`--count` 必须每次由你指定，脚本里不预设。

**候选从哪来（实测结论，别改回去）**：CSDN **没有**按时间倒序的文章列表——
首页 tab 是领域分类、频道页只有「推荐/付费专栏/VIP文章」、热榜行里没有时间字段、
搜索页的「最新」排序是坏的（`s=new` 返回 2022~2025 的文章，比「综合」还旧）。
唯一能用的是**搜索页「综合」排序（`s=0`）**，好处是**结果列表自带日期**，
能在列表层就筛"近 N 天"，不必逐篇进详情页试错。

**三道过滤**（都在 `config.json` 的 `interact` 段，可关可调）：

1. 作者粉丝 ≥ `maxFans`（默认 1 万）→ 跳过。**大 V 收到几百条通知看不到你**，互动等于白做。
2. **内容农场**：原创数 ≥ 150 篇但粉丝 < 500 → 跳过。实测抓到过「1479 篇原创 / 35 粉」的 SEO 号，
   也抓到过 `超级简历WonderCV`(36粉)、`斯迈尔齿科`(46粉) 这类营销号。
3. 作者名含"简历/齿科/有限公司…"、标题含"征文/榜单/福利…" → 跳过。

**约 45% 的文章读不到发布时间**（VIP/付费/笔记类模板不同），按约定**直接跳过不三连**，
并记为已定性，不会每轮重复打开。

**限流怎么处理**：
- 评论撞限流 → **只跳过评论，赞和藏照做、不等待**；连续 3 次被拦 → 本轮剩余篇目不再尝试评论。
- 赞/藏撞限流 → **整轮立刻收手**（这两个比评论敏感得多，硬刷风险高）。

**每条都会留档**：`log/YYYY-MM-DD-HHmm-interact.md`（逐篇明细 + 操作×状态统计 + 重大问题），
索引追加到 `log/README.md`。**动手前先看 [`known-issues.md`](../../../csdn-publisher/log/known-issues.md)**——
那里记着"浏览器 profile 脏锁会被误判成 Chrome 没装"这类会反复咬人的坑。

---

## 五、私信群发（给「我关注的人 / 我的粉丝」发同一条私信）

| 你可以这么说 | 我会做什么 | 底层命令 |
|---|---|---|
| 私信给我的粉丝 / 给我 20 个粉丝发私信 | **只先列名单**：把「将要发给谁」摆给你看，**一条都不发** | `csdn_message.py --to follower --limit 20 --wait` |
| 就按这个名单发 | 复用上一步 `--wait` 存下的那份名单真发（发出去的就是你看过的那批人） | 同上，把 `--wait` 换成 `--yes` |
| 发给关注的人 / 两个都发 | 名单来源换成「关注的人」/ 两者合并去重 | `--to following --limit 20 --wait`<br>`--to following,follower --limit 20 --wait` |
| 不限量，全都发（**所有粉丝**） | 名额改成 all，名单走接口一次取全（不再受 20 条上限） | `--limit all` |
| 只补发上次没送到的 | 跳过「以前已经成功发过」的人 | 再加 `--skip-sent` |
| 先别真发，看选择器对不对 | 只把正文填进输入框并截图，**绝不点发送** | `--to follower --limit 1 --dry-run` |
| 之前都发给谁了 | 打印每次运行 + 每个收件人的累计记录 | `--stats` |
| 把今天阅读量最高的 4 篇链接写进私信模板 | 筛今天发布的文章、按阅读量**降序取前 4**，链接按顺序覆盖 `message.txt` 的 `url` 槽位（旧链接自动被新的替换）；默认优先复用今天已有的快照，快照不可用才自动重采 | `csdn_top_urls.py` |

**正文来自 `templates\message.txt`**：纯文本、**原样发送，不做任何个性化替换**（不替换昵称、不加称呼）。
单条上限 **500 字**（页面右下角有 `x/500` 计数），超了脚本**直接拒绝发送**，不会偷偷截断。

**正文里的 `1.url`~`4.url` 槽位可以一键刷新**：说「把今天阅读量最高的文章链接写进私信模板」或直接跑
`csdn_top_urls.py`，会把当天发布的文章按阅读量**从高到低**取前 4 篇写进槽位（已有链接整块覆盖，
阅读量高的排 1）。刷新后正文 = 固定文案 + 当天 Top4 链接，**发送前你会看到新正文**；
正文字符数由脚本打印把关（>500 会被 `csdn_message.py` 拒发）。

**两道硬闸门（不是提示，是拒绝执行）**：

1. **不写 `--yes` / `--wait` 就什么都不发**——不存在"默认帮我发出去"这种路径。
   唯一例外是 **`--dry-run`**：只把正文填进输入框并截图、**绝不点发送**，可以单独用。
2. **`--limit` 必须每次由你指定**（`20` 或 `all`），脚本里**没有默认人数**。

**名单从哪来（2026-10-09 改造：不再有 20 人上限）**：走 CSDN 自己的名单接口
`mp-action.csdn.net/interact/wrapper/pc/fans/v1/api/getFansOffsetList`（粉丝）/
`getFollowOffsetList`（关注的人），在个人主页里用 `fetch()` 发起、带上登录 cookie。
`pageSize` 调大就能一次取全，实测 **41 粉丝 41/41、208 关注 208/208**。

> 以前为什么只能发 20 个：名单是从个人主页「粉丝」**列表 DOM** 里抓的，而那个视图
> **写死 20 条**（没有分页器、没有「加载更多」、滚到底也不涨，点头部那两个数字也进同一个视图）。
> 站点自己拉列表用的就是上面那个接口、只是它只请求了 `pageSize=20` —— 所以"只能 20 个"
> 其实是**我们抓错了地方**，不是 CSDN 只给我们看 20 个。DOM 抓取现在只作为接口不可用时的兜底，
> 走兜底路径时会明确告警"最多 20 条"。

**对账**：接口不给服务端总数（`total` 字段恒 0），所以拿主页头部的「N 粉丝 / N 关注」对账，
对不上就打印告警（`list-cap-*`）并写进报告——**宁可告诉你没抓全，也不静默少发**：

```
⚠ 名单没抓全（抓到多少就发多少，不会拿别人凑数）：
    · 粉丝 主页显示 41 人，只抓到 20 人
```

**去重（两层）**：
- **单次运行内**：同一个人同时在「粉丝」和「关注的人」里（实测 208 关注 + 41 粉丝 = 249 → 去重后 210 人）
  只发一次；接口返回重复也按用户名兜住。**默认就做，不用你管。**
- **跨多次运行**：加 `--skip-sent` 才跳过「以前已经成功发过私信」的人（默认不跳，
  保证 `--limit all` 就是发给全部粉丝）。**想只补发上次没送到的，就用它**：

```powershell
.\csdn_message.bat --to follower --limit all --wait               # 先看名单
.\csdn_message.bat --to follower --limit all --yes                # 全部粉丝都发（不受 20 上限）
.\csdn_message.bat --to follower --limit all --yes --skip-sent    # 只补发没送到的
```

**⚠ 另一个限制：私信限额（两种，都意味着「没送到」）。**

1. 「**24小时内最多与5个陌生人发送消息**」——看**对方跟你有过历史会话没有**，**账号级**，24 小时后重置；
2. 「**对方尚未回复你，你只能发送1条消息**」——对方没回过你时只能发 1 条，得等对方回你。

两者都**跟他在粉丝还是关注列表里无关**——实测 30 人里被拦 10 人，**粉丝一样有份**。
所以"以后只发粉丝"**并不能**避开它；能正常收到的是那些**跟你有历史会话 / 回过你**的人。

所以脚本**不会把"当场显示成功"当作送达**：CSDN 会先把消息乐观渲染出来、几秒后若被拦就
**替换成系统提示**（"输入框清空 + 出现本条"当场成立，事后整条消失）。现在的判定是等一个
沉淀时间再看，出现这类系统提示就记 `failed_dm_limit`，报告里明确写**没送到**。

**发完想确认真实到达率**（推荐每次都跑，只读、不发消息）：

```powershell
.\.venv\Scripts\python.exe _probe_delivery.py        # 逐个会话回查：送达 / 被拦
```

我会把**真实到达数**告诉你（例："18 条里 15 条真送到，3 条被陌生人限额拦下"），
而不是把脚本当场那句 `sent` 当成送达。

**发送怎么走**：直接开会话页 `https://im.csdn.net/chat/<用户名>` → 写入 `textarea#messageText`
→ 点发送按钮（找不到按钮就回退按 Enter）。发完用**两个信号**校验：输入框已清空 **且**
消息列表里出现本条原文的片段；只满足一个就记 `sent_unverified` 让你人工瞄一眼——**不谎报成功**。

**风控与边界（重要）**：

- 每条之间默认随机停 **5~12 秒**（`config.json` → `message.defaults.delayMinSec/delayMaxSec`），**别调小**。
- 页面弹「发送频繁 / 对方拒收 / 已被拉黑」这类提示时**立刻停本轮**，不硬撞风控。
- CSDN 私信设置里有「**屏蔽群发信息**」开关——群发私信是平台敏感行为，被举报会有限流/封号风险。
  **正文自己把关，别发广告**；这个工具只负责"把你要发的话发出去"。
- **不自动过验证码（最高优先级）**：一旦遇到滑块/安全验证，**立刻停手、无限期等你本人处理**，
  任何时间限制（超时、节奏、排期）都不许抢先结束等待。⚠️ 核对代码发现：**私信链路目前并没有接入那道闸门**
  （全项目只有发布链路 `publish_csdn.py` 有），所以跑私信时我会自己盯页面，看到验证立刻停下喊你。

**每次都会留档**：`output\message\latest.md`（名单 + 逐条状态 + **未送达清单** + 过程记录）、
`output\message\message.db`（SQLite：每次运行 + 每个收件人）、`output\message\pending.json`（`--wait` 存下的待确认名单）、
`output\message\failed.json`（这次**没送到**的人，可用来补发）。

**某个粉丝发失败了怎么办**（分三类，别一锅端）：

| 失败类型 | 脚本怎么做 | 你该怎么做 |
|---|---|---|
| `failed_dm_limit`（陌生人限额 / 对方尚未回复） | **不重试**（重试只会白撞风控），连续 3 个就停本轮，剩下的标 `stopped_stranger_limit` | 等额度重置（陌生人限额 24h）或用 `--skip-sent` 下次补发 |
| `failed_freq` / `failed_block`（频率 / 拉黑 / 拒收） | **立刻停本轮**，不硬撞 | 隔几小时再跑 |
| `failed_unknown` / `failed_no_input` / `failed_fill`（原因不明，多半是页面没就绪） | 隔 8 秒**自动重试一次**（`message.defaults.retryTransientOnce/retryWaitSec`），成功则记 `sent_on_retry` | 还失败就看报告的说明 + 截图 |

---

## 六、维护与排错

| 你可以这么说 | 我会做什么 |
|---|---|
| 登录失效了 / 一直跳登录页 | 提示你跑 `.\csdn.bat login`（**扫码必须你本人做**） |
| CSDN 改版了，找不到发布按钮 | `inspect` 导出真实页面结构，改 `config.json` 的选择器（不改代码） |
| 素材不够 / 搜不到达标的文章 | 降低 `minViews`、加更多 `keywords` |
| 浏览器起不来了 / 报"Executable doesn't exist" | **先别去装 chromium**：十有八九是 `.profile\lockfile` 脏锁。查 `log/known-issues.md` 第 1 条 |
| 一键三连跑一半浏览器挂了 | 脚本会自己清理残留并**重试同一篇**；连续挂 4 次才中止本轮（属环境问题，不是被平台拦） |
| **跑一半弹滑块 / 安全验证** | **最高优先级：立刻停手，无限期等你手动拖滑块**（任何超时/节奏/排期都不许抢先结束等待）。我会响铃 + 截图 + 写 `output\NEEDS-HUMAN.txt`，并明确喊你「现在去看浏览器」。无人值守只能由你显式指定 `--on-risk abort`。**评论/点赞/收藏和私信链路还没接这道闸门，我会自己盯页面看到就停** |
| 私信发不出去 / 提示发送频繁 | 脚本已经**主动停本轮**并把提示原文记进报告；等几小时再跑，别连着重试 |
| 私信名单抓成 0 个人 | 先看是不是 `--to following`（你关注列表本来是 0）；再看报告的 `list-api-*`（接口不可用）和 `list-cap-*`（没抓全）；最后人工开一次粉丝页确认能打开 |
| 某篇正文抓到 0 字 | 换一篇素材（该文容器特殊） |

---

## 七、我不会做的

- **给自己刷阅读量 / 给自己点赞收藏**：违反平台规则，按现在的 CSDN 架构也刷不动，这类脚本我不写。
  给**别人**的文章三连是真实互动，按你的要求做了——但它确实是平台风控盯的行为，
  所以默认每篇停 15~30 秒、遇限流立刻收手、每轮留档。**别把节奏调快。**
- **代替你扫码登录**：登录态等同于账号密码，只能你本人操作。
- **替你猜关键词和阅读量门槛**：这两项是你对"发什么"的判断，必须你定。
- **替你猜"做多少篇"**：`--count` 每次由你下任务时指定，脚本里没有默认值。
- **把复盘数据说得比实际更确定**：样本不足、间隔太短、窗口没命中时，报告里会明确写「暂不折算」「—」，我不拿别的时点冒充。
- **把"没抓到成功提示"的评论说成发成功了**：提交后没有明确信号时记为 `done_unverified` 并提示你人工看一眼。
  但**只要是滑块/安全验证拦下的，一律按最高优先级处理**：立刻停手等你处理、留证据，
  并且**绝不许记成"已完成"**——2026-10-08 的教训就是评论被滑块吞掉、页面没有任何提示，
  却被记成 `done_unverified`（属"已完成"→ 永不重试），等于谎报成功。
- **替你伪装群发私信**：私信正文就是 `templates\message.txt` 的**原文**，我不做昵称替换、不做话术轮换
  来规避风控，也不自动过验证码；平台一拦就停，并把拦截原文留档。

---

## 附：底层命令速查

```powershell
cd __TOOL_DIR__

# 阅读复盘
.\csdn_stats.bat                          # 全量采集 + 报表
.\csdn_stats.bat --report-only            # 不联网重出报表
.\csdn_stats.bat --pages 2 --top 20       # 只采 2 页，排行榜 20 行
.\csdn_stats.bat --window-days 30         # 分组对比看最近 30 天

# 发布
.\.venv\Scripts\python.exe csdn_job.py                       # 列出所有任务文件
.\csdn.bat check                                             # 确认登录态
.\csdn.bat config                                            # 打印全部可配置参数
.\.venv\Scripts\python.exe csdn_job.py prepare jobs\job.all.json   # 搜素材+建骨架+封面
.\.venv\Scripts\python.exe csdn_job.py status  jobs\job.all.json   # 完成度 + 体检
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json   # 按 publish.on 发/存草稿
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --only 1 --dry-run  # 只演练
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --force             # 跳过体检拦截
.\.venv\Scripts\python.exe csdn_job.py publish jobs\job.all.json --no-lint           # 完全不体检
# 任务文件都在 jobs\ 下；只写文件名（job.all.json）脚本也会去 jobs\ 找

# 体检（离线，不开浏览器）
.\.venv\Scripts\python.exe csdn_lint.py posts\xxx.md
.\.venv\Scripts\python.exe csdn_lint.py posts\xxx.md --tags Docker,容器 -k Docker --json

# 草稿箱 / 排期
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-08 16:30" --dry-run
.\.venv\Scripts\python.exe csdn_schedule_drafts.py --at "2026-10-08 16:30"
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 草稿箱 --raw
.\.venv\Scripts\python.exe csdn_draft_clean.py --tab 草稿箱 --delete 1 --match 标题片段
.\.venv\Scripts\python.exe csdn_draft_check.py <articleId>      # 校验草稿里的正文与插图

# 一键三连（点赞 / 收藏 / 评论）
#   ★ 评论是「读正文写感悟」两阶段，别再用固定文案刷评论
.\csdn_interact.bat --prepare-comments --ops 三连 --count 50   # ① 抓正文摘要出清单（不发）
#   ② agent 把感悟写进 output/interact/pending-<runid>.comments.json
.\csdn_interact.bat --preview output\interact\pending-<runid>.json   # ③ 打出来给你审
.\csdn_interact.bat --send --pending output\interact\pending-<runid>.json   # ④ 你确认后发
.\csdn_interact.bat --ops like --count 50                    # 只点赞 50 篇（不需要写感悟）
.\csdn_interact.bat --ops save --count 30                    # 只收藏 30 篇
.\csdn_interact.bat --ops 三连 --count 50 --dry-run           # 只判定，不点击、不写库
.\csdn_interact.bat --stats                                  # 存量统计 + 粉丝最少的作者
.\csdn_interact.bat --probe-comment <文章URL>                 # 勘察评论框结构（不提交）
#   可调: --days 7 --max-fans 10000 --keywords "Go,Redis" --pages-per-kw 3
#         --delay-min 15 --delay-max 30 --max-minutes 45 --retry-failed
#   库:   output/interact/interact.db（按「文章 × 操作」去重）
#   日志: log/YYYY-MM-DD-HHmm-interact.md ｜ 已知坑: log/known-issues.md

# 私信群发（正文: templates\message.txt，原样发送不做替换）
.\csdn_message.bat --to follower --limit 20 --wait        # 只列名单并存下来，一条都不发
.\csdn_message.bat --to follower --limit 20 --yes         # 按上面确认过的名单真发
.\csdn_message.bat --to following --limit 20 --wait       # 发给「我关注的人」
.\csdn_message.bat --to following,follower --limit all --wait   # 两个都发，不限量
.\csdn_message.bat --to follower --limit 1 --dry-run      # 只演练选择器：填框+截图，不点发送
.\csdn_message.bat --stats                                # 历史发送记录
#   --to:   following（我关注的人）/ follower（我的粉丝）/ following,follower（两个都发）
#   --limit: 数字 或 all（必须指定；0 也当 all）
#   闸门:  不写 --yes/--wait 什么都不会发；每条随机间隔 5~12 秒
#   库:   output/message/message.db ｜ 报告: output/message/latest.md
#   待确认名单: output/message/pending.json（--yes 会优先复用它，保证发的是你看过的那批人）

# 私信正文链接刷新（今天阅读 Top4 → templates\message.txt，按阅读量降序）
.\.venv\Scripts\python.exe csdn_top_urls.py            # 默认优先复用今天已有的快照（秒出）
.\.venv\Scripts\python.exe csdn_top_urls.py --fresh    # 强制重新联网采集后再写
.\.venv\Scripts\python.exe csdn_top_urls.py --dry-run  # 只预览，不写文件
.\.venv\Scripts\python.exe csdn_top_urls.py --top 4    # 取前几篇（默认 4，与模板槽位一致）

# 热榜 / 选题调研
.\.venv\Scripts\python.exe csdn_search.py                       # 关键词自动取本期 Top 主题词
.\.venv\Scripts\python.exe csdn_search.py --q "AI Agent"         # 指定关键词（决定文件名）
.\.venv\Scripts\python.exe csdn_search.py --limit 30 --deep 5    # 精读 5 篇正文拆结构
.\.venv\Scripts\python.exe csdn_search.py --no-deep --my-pages 0 # 只抓榜单，最快
.\.venv\Scripts\python.exe csdn_search.py --focus Raft etcd      # 临时加赛道词（默认画像 go-backend）
.\.venv\Scripts\python.exe csdn_search.py --exclude 鸿蒙          # 临时排掉某些领域
.\.venv\Scripts\python.exe csdn_search.py --all                  # 无关热点也全列出来
#   报告：hot_search/<关键词>-<日期>.md ｜ 原始数据：hot_search/raw/ ｜ 历史索引：hot_search/index.json
#   赛道档位：核心（Go/Redis/MySQL/K8s…）/ 学习（AI/Agent/RAG）/ 弱相关（NAS 上跑 Docker）/ 无关（ATE/CAD）
#   精读与结构指纹只在相关文章里算；报告末节给「按 Go 后端排的 AI 学习路线」

# 热榜 / 插图 / 实测
.\.venv\Scripts\python.exe csdn_hot.py --limit 30                # 全站综合热榜
.\.venv\Scripts\python.exe csdn_hot.py --trend                   # 选题雷达（离线）
.\.venv\Scripts\python.exe csdn_hot.py --fingerprint <文章URL>    # 拆爆款风格指纹
.\.venv\Scripts\python.exe csdn_image.py render --table exp\rag-chunk-recall\numbers.md --out img\a.png
.\.venv\Scripts\python.exe csdn_image.py upload --files img\a.png --out img\urls.json
.\.venv\Scripts\python.exe exp\new_experiment.py <slug> --title "实验标题"

# 素材 / 封面
.\.venv\Scripts\python.exe csdn_research.py -k "云原生" --min-views 1000
.\.venv\Scripts\python.exe make_cover.py -t "标题" --theme purple -o covers\c.png
```

`csdn_job.py publish` 的新开关要写在**任务文件后面**（`publish jobs\job.all.json --force`）。
`publish --help` 会被当成"没给任务文件"，直接列出任务列表——想看帮助用 `python csdn_job.py --help`。
