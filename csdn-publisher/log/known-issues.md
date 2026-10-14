# 已知重大问题（CSDN 工具链）

> 这里记的是**踩过、且会反复咬人**的问题。执行任何操作遇到重大问题也往这里加，
> 方便下次先看这里再动手。每条都写清：现象 / 根因 / 正确处置。

---

## 1. 浏览器 `.profile` 脏锁 —— 会被误判成"Chrome 没装"

**现象**（症状很有误导性）：

```
channel=chrome 启动失败（TargetClosedError），回退到自带 chromium
无法启动浏览器。请任选其一：
  1) 安装 Chrome 并确认 config.json 里 browser_channel = "chrome"
  2) 执行: python -m playwright install chromium
原始错误: Executable doesn't exist at ...\.pw-browsers\chromium-<版本号>\chrome-win64\chrome.exe
```

看起来像"Chrome 没装 / 要装 chromium"，**其实 Chrome 装了**。真实原因是
`.profile\lockfile` 被上一轮**残留的 chrome 进程**占着。

**根因**：脚本崩溃或被中断时，chrome 子进程不会自动退干净，锁文件留在那里；
此时新启动的 chrome 会"连得上但几秒后自己退出"，表现为
`launch()` 成功、下一次 `page.goto()` 抛 `TargetClosedError`。
而且因为 `channel=chrome` 失败后代码会回退到**从来没装过的自带 chromium**，
最终报出来的错误完全指向错误的方向。

**正确处置**（`csdn_interact.py` 已内置，其它脚本需手工做）：

```powershell
# ① 只杀本项目 profile 的 chrome，别用 taskkill /IM chrome.exe（会连你日常的 Chrome 一起杀）
Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
  Where-Object { $_.CommandLine -like '*csdn-publisher\.profile*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
# ② 删掉锁文件
Remove-Item -LiteralPath .profile\lockfile -Force
```

**❌ 别踩的坑（实测结论，很重要）**：

1. **不要在会话进行中反复 force-kill chrome。** 实测"每次启动前都强杀"会让 profile 进入
   残缺状态，后续每次启动都可能几秒后自己退出。正确策略是
   **干净启动一次 → 会话内一直复用**；中途死了先优雅 `close()`、等 2~3 秒让旧进程退干净
   再起，**只有在起不来的时候才强清**。
2. **同一进程内重启浏览器时，必须先清心跳**（`.browser-heartbeat.json`）。
   心跳记的是 **Python 进程的 pid**，只要脚本还在跑就"永远活着"。
   `2026-10-08 13:47` 之前 `heartbeat_alive()` 没排除自己，于是 preflight 会看到
   「锁文件存在 + pid 还活着」→ 判定被"另一个进程"占用而拒绝启动，
   而那个 pid 就是自己 —— 这条会把"死了就重启并重试同一篇"的整条恢复链卡死。
   **已在 `publish_csdn.heartbeat_alive()` 里修掉**（`pid == os.getpid()` 视为非外部占用）。
3. 但**真的别的会话占着时，绝不能强清**（见第 7 条）。

对照实测：一次干净的会话连续 16 次导航、40 秒全程存活；
而"每步都强杀"的那一轮，17 次里挂了 5 次。

---

## 7. ⚠️ 两个会话同时跑 CSDN 工具会互抢 `.profile`（最容易误判成"环境坏了"）

**现象**：浏览器"启动成功但几秒后自己退出"、`TargetClosedError` 反复出现、
`⛔ profile 正被另一个进程占用（pid=…，启动于 …）`，而且**时好时坏**——
同样的命令刚刚还跑得好好的，几分钟后就连续挂 4~6 次。

**根因**：本项目**所有工具共用同一个 `.profile`**（里面是登录态）。
同时开两个会话/两个脚本跑 CSDN 工具时，两边的 chrome 会抢同一个用户目录，
互相把对方的浏览器搞死。实测 `2026-10-08 13:41`、`13:43` 那几轮"连续退出 4 次"
就是撞上了另一个会话（它的 python pid 写进了心跳，preflight 报的 pid 是**它**，不是我）。

**正确处置**：
- **串行跑**：同一时间只允许一个会话用 `.profile`。跑之前先看有没有别的会话在跑。
- 遇到 `⛔ profile 正被另一个进程占用` **不要强清**，那会把对方正在跑的浏览器杀掉，
  两边的运行都坏掉。等对方跑完再继续。
- `csdn_interact.py` 已内置这个判断（`other_owner_alive()`）：发现是别的会话占用就
  **拒绝启动并如实报告**，不会硬抢。

**代价**：因为这条，2026-10-08 这轮**没能做真正的点击实测**——只完成了 `--dry-run`
全链路验证（候选筛选 / 状态回读 / 评论框定位 / 去重 / 留档都跑通了）。
真跑之前请先确认没有第二个会话在动 CSDN。

**想两边同时跑：用「迷你 profile 副本」（2026-10-08 私信群发实测可行）**。
「迷你 profile 副本」的做法（私信群发实测可行）：把**登录态相关的几个文件**复制到一个独立目录
（`Local State` + `Default/Network/Cookies` + `Default/Preferences` + `Default/Secure Preferences`，
**几 MB，不是整个 455MB**），再把这三个全局指过去：

```python
pc.PROFILE_DIR    = ROOT / ".tmp" / "vprofile"
pc.LOCKFILE       = pc.PROFILE_DIR / "lockfile"
pc.HEARTBEAT_PATH = pc.PROFILE_DIR / ".browser-heartbeat.json"   # 别污染共享心跳
```

这样 `kill_lingering_browser()` / `preflight_profile()` 只认自己的目录，两边互不干扰，
也**不会误报对方占用**。私信群发的全部验证（名单抓取 / 选择器演练 / `--dry-run`）
就是在另一个会话正在发布文章的同时用这个办法跑通的。cookie 是 SQLite、Chrome 允许多读者，
所以复制时对方正在跑通常也没问题；复制失败就等它停下再复制一次。

---

## 8. ⚠️ 私信「已发送」是**乐观渲染**，真状态可能是「陌生人限额」拦下（最会骗人的一条）

**现象**：私信脚本当场逐条报 `sent`（输入框清空了、会话里也能看到本条原文），
但过一会儿再打开那个会话，**自己的消息不见了**，只剩一条系统提示：

```
24小时内最多与5个陌生人发送消息
```

**根因**：CSDN 私信是**先乐观渲染**消息气泡、再由服务端结果覆盖。
被风控拦下时，气泡会被**替换成系统提示**。所以任何"看第一眼就判成功"的检测都会误报
（2026-10-08 实测：18 个粉丝当场全部报 sent，事后逐条回查**只有 15 个真送到**）。

**判据不是"粉丝还是关注"，而是"对方跟你有过历史会话没有"**：
有历史会话（大多数粉丝都发过"感谢关注"之类）→ 正常送达；
没有历史会话的陌生人 → 受「24 小时内 5 个」限额，**粉丝一样会被拦**。
这条限额是**账号级**的，用光之后当天再发陌生人也不会成功，24 小时后重置。

**正确处置**（`csdn_message.py` 已内置）：

1. 点发送后**先等一个沉淀时间**（`message.defaults.sendSettleSec`，默认 3.5 秒）再下结论；
2. 把会话内的**系统气泡**也算拦截信号（`.pure-text / .system-prompt` 里匹配 `strangerHints`
   「陌生人 / 最多与」），命中就记 `failed_stranger_limit`，报告里明确写「**没送到**」；
3. 陌生人限额**不整轮停**（很多人有历史会话照样能收到），但**连续 3 次**命中就停止本轮
   （`message.defaults.strangerStreak`），别把额度耗在必然失败的人身上。

**事后审计工具**：`_probe_delivery.py` —— 逐个打开会话，按"里面还有没有我发的文章链接"
判定 送达 / 被拦，只读、不发消息。**发完想确认真实到达率就跑它**，别信当场那份日志。

---

## 9. ⚠️ 点赞/收藏是**开关**，点之前不读状态就会「取消」用户已有的赞

**事故（2026-10-08 14:54 首次真实发送）**：`do_like()` 直接点 `li#is-like`，
**点之前没有读当前状态**，把用户**已经赞过**的文章点成了取消。收藏同理。

**铁律：任何点击之前，先读 `#is-like-imgactive` / `#is-collection-imgactive` 是否可见，
可见就是"已做"，绝不再点。** 这两个锚点是实测出来的（未做时 `display:none`）。

同批踩到的另外三条：

| 控件 | 真实行为 |
|---|---|
| 收藏 | 点第一下只弹出**「添加收藏夹」弹窗**，必须在弹窗里再点那一行右侧的**「收藏」按钮**才算成功；而且这个弹窗会**一直盖住底部工具栏**，不处理掉后面所有点击都被挡（当次运行的点赞就是这么被挡掉的） |
| 评论 | 入口 `a.go-side-comment` 没问题，但要用 **Playwright 原生 click（可信事件）**；输入框是 `textarea#comment_content`（placeholder「说说你的看法，或者提个问题」） |
| 点赞控件 | CSDN 在做 A/B 测试，页面上有**两套**：底部工具栏 `li#is-like` 和正文顶部 `div.read-count-box.is-like.like-ab-new`（`#is-like-img-new`） |

### 收藏弹窗的真实结构（2026-10-08 连做三个探针才拿到，**别再猜 class**）

```
#csdn-collection.csdn-collection-theme-default        ← 弹窗根（fixed, z=99999）
  ├ .csdn-collection-mask
  └ .csdn-collection-container
      ├ .csdn-collection-header     「添加收藏夹」 + span.csdn-collection-close(x)
      └ .csdn-collection-body
          ├ .csdn-collection-folder  span.csdn-collection-btn「创建收藏夹」+ input[placeholder=收藏夹标题]
          └ ul.csdn-collection-items
              └ li.csdn-collection-item
                  ├ .csdn-collection-item_title > span.title-m   「我的收藏夹」
                  │   （下面一行小字是「39条内容 ・ 私密」）
                  └ span.collect-btn   「收藏」                    ← **必须点这个**
```

**两个把我坑了三轮的细节**：

1. **容器 class 不是 `.el-dialog` / `.collect-box` / `[role=dialog]`，而是 `#csdn-collection`。**
   照那套猜的结果是 `found=False`，而截图里弹窗明明开着 —— 于是我连续三轮把
   "选择器不对"误报成了**"弹窗没出现"**，还据此把收藏判定成"平台限额"。
   **教训：找不到元素时不要先入为主判断"平台没弹"，要用"从已知文案往上爬祖先链 +
   抓 position:fixed 大浮层"的方式把真实 DOM 抓下来。**
2. **确认按钮必须用 Playwright 原生 click。** 用 JS `el.click()` 点 `span.collect-btn`
   **毫无反应**（弹窗不关、状态不变、没有 toast）。原生 click 一次就成。

**修复后的实测结果**（2026-10-08 15:57）：`收藏: done — 已收藏到「我的收藏夹」（弹窗二次确认）`，
收藏数 12 → 13，且另一篇因"页面显示已收藏"正确走了 `skipped_already`（没有二次点击造成反取消）。

**用户口径**：点「我的收藏夹」那一行、或点它右边的「收藏」按钮都可以；账号下只有一个收藏夹。
**收藏不限额**（点赞才限），所以收藏失败时先怀疑选择器/点击方式，别归因到限额。

**还有一条没解决的问题（重要）**：事故之后复测，**两套点赞控件 × 四种点击方式
（原生 click / 鼠标坐标 / JS click / 点 li 本身）全部无效** —— 按钮点得到、
`elementFromPoint` 命中正确、**但状态不变、也没有任何 toast**。
这不像"点错元素"，更像**平台侧静默拒绝（限流 / 自动化拦截）**。
再遇到**不要反复试**（点赞是开关，反复点可能互相抵消），
先人工在浏览器里确认能否手动点赞，再决定是否等冷却。

**旧代码还有个更坏的毛病**：状态没变时记的是 `done_unverified`（"可能仍在处理"），
而它属于"已完成" → 这两篇**永远不会被重试**，等于把没做成的事当成做成了。
现已改成：状态没变一律记 **`failed`**（可用 `--retry-failed` 重试）。

**已按用户要求实现"熔断跳过"**（2026-10-08）：
"每天的点赞数有限制，发现点不了之后直接跳过即可，其他两个功能同理，
遇到异常直接跳过，写进 log 即可。"

所以现在：某个操作一旦 `blocked` 或点了没反应，就给该操作打上熔断标记，
**本轮剩余篇目直接记 `skipped_op_disabled` 跳过它**（不再浪费 15~30 秒/篇去试），
整轮不会因此中止（其它操作照做），并把原因写进 `log/<run>.md` 的「重大问题」。
`skipped_op_disabled` **不定性** → 下一次运行（新的一天、额度重置）会自然重试。

---

## 2. CSDN 全站没有"按发布时间倒序"的文章列表

想找"刚发布的文章"时**不要再去试这些入口了**，实测全部无效：

| 入口 | 实测结果 |
|---|---|
| `www.csdn.net` 首页 | 首屏只有极客头条**资讯**；tab 是领域分类（后端/运维/人工智能/DeepSeek…），不是时间排序 |
| `blog.csdn.net/nav/<频道>` | tab 只有 `推荐 / 付费专栏 / VIP文章`；首条实际发布于 **24 天前** |
| `blog.csdn.net/rank/list` | 榜单行里**没有时间字段**；累积热度，老文霸榜 |
| 搜索页「最新」排序 | **是坏的**：`s=new` 返回 2022~2025 的文章，比「综合」`s=0` 还旧 |

**唯一可用的**：搜索页 `so.csdn.net/so/search?q=<kw>&t=blog&s=0`（**综合**排序）。
它的结果列表**自带发布日期**（精度到天），所以在列表层就能筛"近 N 天"，
不必逐篇进详情页试错。

---

## 3. 详情页有两套模板，约 45% 读不到发布时间

实测 60 篇候选里 **27 篇(45%)** 读不到发布时间，同时也读不到作者资料
（`.profile-intro-rank-information` 不存在）——这是 VIP/付费/笔记类页面模板不同。

`csdn_interact.py` 的处置：**读不到发布时间就跳过、不三连**（按约定），
并记为 `skipped_no_time`（已定性，不再重复打开）。

另外 `.time` 有两种形态，改解析时别只认一种：
- `于 2026-09-14 08:00:00 发布` ← 有发布时间，可用
- `已于 2026-09-29 01:53:14 修改` ← **只有修改时间**，当拿不到发布时间处理

---

## 4. 搜索页综合排序里营销号/内容农场占比很高

实测抓到的一批"近期文章"作者：`超级简历WonderCV`(36粉)、`斯迈尔齿科`(46粉)、
`L oiey`(**1479 篇原创 / 35 粉**)。这些号写文只为 SEO，**不会回你三连**，互动等于白做。

`csdn_interact.py` 的三道过滤（都能在 `config.json` 的 `interact` 段关掉/调整）：
1. `excludeAuthorNameKeywords` —— 作者名含"简历/齿科/有限公司…"直接跳过
2. `farmFilter` —— **原创数 ≥ 150 且粉丝 < 500** 判为内容农场
   （真正的新人是"20 篇原创 / 36 粉"，农场是"1479 篇 / 35 粉"）
3. `excludeTitleKeywords` —— 标题含"征文/榜单/福利…"跳过

---

## 5. 自带 chromium 从来没装过

`.pw-browsers\chromium-<版本号>\` 不存在，所以 `config.json` 的 `browser_channel: "chrome"`
是**唯一可用**的启动路径。一旦 chrome 起不来，代码回退到自带 chromium 必然再失败，
并把错误信息掩盖成"浏览器没装"。修 chrome 的问题，不要真的去 `playwright install chromium`。

---

## 6. PowerShell 里跑含中文的 `python -c` 会被乱码/引号坑

实测 `python -c "...中文..."` 在 pwsh 里会因为引号与编码被拆坏
（报"无法将"未读到"项识别为 cmdlet"）。**要写中文脚本就写成 .py 文件再跑**，
别用 `-c` 内联。

---

## 10. 互动/私信链路没有风控闸门 —— 评论被滑块吞掉，却记成 `done_unverified`

**现象**（2026-10-08 实测，用户全程没看到滑块）：

```
[16:21:01] 评论: done_unverified — 输入框已清空，但**没在评论区找到这条评论**（校验片段「「Connection别乱建」）
```

同一篇隔一会儿再复核：`❌ 没找到… → failed`；页面评论数始终 **0 条评论**，刷新后也没有。
登录态正常（cookie `UserName=<你的账号ID>`），感悟确实写进了 `textarea#comment_content`。

**根因**：点「发表评论」后 CSDN 弹出**滑块安全验证**（「请完成安全验证 / 请拖动滑块完成拼图」），
按钮停在「提交中…」，评论根本没提交；而 `do_comment()` 里**没有任何风控识别**，
脚本只看到"输入框被清空"，于是记成 `done_unverified`（属"已完成"→ 永不重试）。

**为什么用户看不到滑块**：`do_comment` 点完提交只回读约 9 秒就收尾，整轮结束立刻 `b.close()`
把浏览器关掉 —— 滑块弹窗在屏幕上只存在几秒，人不在那一刻盯着就完全错过。

**核对结论（改代码前先认这个事实）**：全项目**只有 `publish_csdn.py`** 调用
`risk_gate()` / `detect_risk()`；**`csdn_interact.py`（评论/点赞/收藏）和
`csdn_message.py`（私信）都没有接入**。所以"遇到安全验证会走 `pc.risk_gate()` 停下来等你点"
这句话，对这两条链路**不成立**。

**正确处置（用户 2026-10-08 定的最高优先级规则，详见 SKILL.md 文首「第 0 条」）**：

1. 任何操作认出滑块/安全验证 → **立刻停手**：不再点击、不再提交、不再翻页、**绝不关浏览器**；
2. 截图 `*-risk-blocked.png` + 响铃 + 往 `output/NEEDS-HUMAN.txt` 追加 + **明确喊用户来看浏览器**；
3. **无限期等真人处理**：`risk.waitTimeoutSec`、`interact.maxMinutes`、每篇 15~30 秒节奏、
   排期时间——**统统不许抢先结束等待**，更不许"跳过这篇继续下一篇"；
4. **绝不自作主张过验证码**，也**绝不把它记成 `done` / `done_unverified`**：没发出去就是没发出去；
5. 代码待办：把 `pc.risk_gate()` 接进 `csdn_interact.do_comment()`（点赞/收藏、私信同理），
   等待期间**保持浏览器打开**，让用户真的能看到滑块。

**补发前必看**：被这样误记的那条在库里仍是 `done_unverified`（不是 `failed`），
直接重跑会被当成"已做过"跳过 —— 先跑 `--verify-comments` 把它降级成 `failed`，再
`--send --pending <同一清单> --retry-failed`。

---

## 9. 私信：会话里残留的历史系统提示会把"已送达"误判成 failed_dm_limit（2026-10-10，已修）

- **现象**：给 57 位粉丝群发，脚本当场把 7 人判成 `failed_dm_limit`
  （「24小时内最多与5个陌生人发送消息」/「去设置防止陌生人打扰」）。
  用户逐个核对会话 → **7/7 都有这条消息**，是**互关**，根本不受陌生人限额约束。
  复核脚本 `_probe_delivery.py` 逐个打开会话也判 7/7 送达。
- **根因**：`send_one()` 的发后判定是「扫会话里有没有系统拦截文案」，
  而这些文案是**上一批群发残留在会话里的历史气泡**（实测某个会话里躺了 2 条），
  不是本次发送的结果。同名文案重复出现时，旧代码还会把它当成"本次被拦"。
- **已修（2026-10-10）**：判定改为
  ① 发送前先留基线（会话条数 / 是否已含本条标记 / 已有哪些系统提示）；
  ② **主判据 = 本次正文标记（`/details/<id>`，从 message.txt 抽取）在不在会话里**，
     且能证明是这次新加进来的（条数变多 或 发送前压根没有这个标记）——与 `_probe_delivery.py`
     复核口径**完全一致**；
  ③ 只有标记没命中时，才看「**本次新增**」的系统提示（`Counter` 差集，不是 set，
     因为同一个会话里可能有两条一模一样的提示）。
  纯函数 `judge_sent()` / `judge_final()` 已抽出，可离线测试（不发消息）。
- **自测**：给自己连发两次同内容，第一次基线「0 条 / 无标记」→ sent；
  第二次基线「1 条 / 已含标记 / 历史系统提示 2 条」→ 仍正确判 sent（旧代码在这里必判失败）。
- **教训**：当场日志的 `sent` 和 `failed` 都不是结论。**报"被拦"之前必须复核**，
  尤其是用户说"我查了都收到了"的时候 —— 他看的是真实会话，比脚本判定可信。

## 10. 封面裁剪窗的残留遮罩会拦住「保存为草稿」（2026-10-10，已修）

- **现象**：批量存草稿 10 篇，其中 2 篇（第 2、10）在**最后一步**失败：
  `TimeoutError: Locator.click: Timeout 30000ms exceeded`，报
  `<div class="vue-image-crop-upload">… intercepts pointer events`。
  日志前面全都正常（「封面已确认上传」「标签已填」「命中保存为草稿按钮」），
  看着像随机失败——错误截图里能看见「图片编辑」弹窗**还开着**，盖住了底部按钮。
- **根因**：`set_cover()` 点完「确认上传」后，CSDN 的 `.vue-image-crop-upload` 是
  **异步关闭**的。快的几百 ms 就没了，慢的时候（网络/风控）一直挂在 DOM 里
  ——它是 `position:fixed` 遮罩，会拦住后面**所有**点击。
  旧兜底只把残留节点 `pointerEvents='none'`，**这不够**：Playwright 的 actionability
  用的 hit-test 仍会判"被拦截"。
- **已修（2026-10-10，`publish_csdn.py`）**：
  ① `set_cover()` 等窗消失从 12s 延长到 20s；
  ② 还不消失就设 `display:none` + `visibility:hidden`，**真正移出布局**（不只是 pointer-events）；
  ③ `dismiss_overlays()` 里加一层"清残留裁剪遮罩"，并在**保存草稿前**再调一次（多一道保险）；
  ④ 保存草稿的 `btn.click()` 包 try/except：被拦就清浮层再重试一次，避免整篇白跑。
- **补发**：`csdn_job.py publish jobs/xxx.json --only 2 10`（`--only` 同时支持 prepare/publish），
  2/2 成功。
- **教训**：**「异步关窗」的元素不能只睡固定时长或只改 CSS 属性**——
  要么真的等到它消失，要么主动把节点移出布局。判定"浮层已关"要用
  `getClientRects().length`（参与布局）而不是 `display` 单看一次。

## 11. ⚠️⚠️ 栏目名对不上号时会「静默留空」——稿子照存、栏目是空的（2026-10-10，已加硬闸门）

- **现象（本文档最会骗人的一条）**：job.json 里 37 篇全写着 `"category": "redis"`，
  `manifest.json` / `final/*.md` 的 front matter 也全是 `category: redis`，
  发布日志一路 `✅ 成功`、内容管理页计数也在涨 —— **但文章在 CSDN 上根本没进 redis 栏目**。
  整条链路没有一个地方报错。
- **根因（`publish_csdn.py::select_categories`）**：
  栏目名必须与账号**真实的专栏名完全一致**（含大小写）。名字对不上时旧代码只做两件事：
  ```python
  log(f"栏目「{name}」不在你的专栏列表里，可选: {have}")
  ok_all = False
  continue            # ← 跳过，但继续走完剩下的流程
  ...
  return ok_all       # ← 返回值调用方压根不看
  ```
  而调用处 `run_publish_flow()` 是裸调用：`select_categories(page, art.get("category"))`。
  于是**「栏目没勾上」不阻断保存**，草稿照存、rc=0、日志只有一行不起眼的 warning。
  更隐蔽的是 `write_front_matter()` 仍会写 `category: redis` —— **本地文件与线上状态静默分叉**，
  事后翻本地产物只会越看越觉得"明明填了"。
- **为什么以前没人发现**：栏目归属**无法从公开页回读**（文章页 HTML 里没有 category 字段，
  侧栏 `category_*` 链接是账号全部专栏、不可信），唯一可靠来源是发布弹窗的「栏目回读」日志，
  而那条日志恰恰没被当成验收条件。
- **已修（2026-10-10，`publish_csdn.py`）**：
  ① 新增 `CategoryGateAbort` 异常；`select_categories()` 选完**比对回读值**，
     目标栏目没全勾上就 `raise`（并在异常里带上「可选栏目: [...]」清单，方便直接照抄改配置）；
  ② `cmd_publish()` 捕获它 → `rc=7`，**明确"内容没提交"**，与内容错误(1)/平台限制(3/4)/风控(5) 区分；
  ③ `csdn_job.py` 的结果汇总表加上 `7` 的中文解释；
  ④ 文档（`skills/csdn-publish/CONFIG.md` 退出码章节）补 `7`。
- **教训**：**"外部系统回读值"必须当作验收条件，不能只当日志打出来。**
  凡是"写出去以后要能被外部系统证实"的字段（栏目、标签、定时时间、封面），
  都要有 `写入 → 回读 → 比对 → 不一致就中止` 的闭环；否则本地看着全对，线上是空的。
- **排查口诀**：怀疑栏目没进去时，**别信本地 md 和 manifest**，只看发布日志里
  `栏目「xxx」-> clicked` / `栏目回读: [...]` 两行；回读里没有目标名 = 没进去。

## 12. 栏目名到底叫什么：`category` 只能填账号里真实存在的专栏名

- 本号实测的真实专栏名（**全小写/含空格**，照抄，别无脑首字母大写）：
  `redis`、`mysql`、`技术栈学习笔记`、`算法题目解析`、`GO项目分享`。
- 反例：写 `Redis`（大写 R）→ 不在列表里 → 就是第 11 条那个静默留空。
- **不确定就先跑一次**：日志里 `栏目「xxx」不在你的专栏列表里，可选: [...]` 会打印全量可选名，
  或跑 `publish_csdn.py --dry-run` 看回读。**别拿正文横幅里的显示文案当专栏名**
  （横幅写的是「Redis」「Go 项目」「技术栈」，与真实专栏名大小写/用词都对不上）。

