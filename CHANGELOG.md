# 更新记录 / Changelog

> 时间线依据 `log/README.md` 的运行索引整理；**2026-10-07 之前的历史待补充**。
> Timeline reconstructed from the run index in `log/README.md`; history before
> 2026-10-07 is **(待补充 / to be supplied)**.

## [2026-10-14] - 2026-10-14

### 新增 / Added

- `csdn_rank.py`：跨批横评（绝对排名 + 相对排名），与 `csdn_retro.py` 分工明确；
  口径统一「发布后 16~32h 窗口内最接近 24h 的快照」，投流量券批次用 `--coupon` 标注、默认不进主统计。
  (`csdn_rank.py`: cross-batch absolute + relative ranking; coupon batches flagged and
  excluded from main stats by default.)

### 修复 / Fixed

- `csdn_retro.py::cmd_compare` 同龄参照段元组解包 bug。
- 隔离坏快照污染 `latest.json` 的问题（40 篇异常快照），重抓修复。

## [2026-10-13] - 2026-10-13

### 新增 / Added

- `/csdn-publish get_url_message` 快捷指令（`csdn_top_urls.py`）：把今天发布的文章里
  阅读量最高的 4 篇链接按顺序写进 `templates/message.txt` 的编号槽位，发送前报告正文字符数（500 上限把关）。
- 草稿箱入库 40 篇选题（`jobs/job.pool40-b*.json`、`topics/` 选题池），`csdn_refs.py`
  支持「一篇一个搜索关键词」逐篇搜素材并全池去重。
- 40 篇选题全部通过撞题预检后入池。

### 变更 / Changed

- `install-skill.ps1` / 技能指令表同步：SKILL.md 快捷指令表、COMMANDS.md、CONFIG.md
  增加 `get_url_message` 行（改前备份 `*.bak-20261013`）。

## [2026-10-12] - 2026-10-12

### 新增 / Added

- 「增量释放窗口」规律首次采到完整证据：凌晨发布 → 睡醒后（发布后第一个活跃白天）
  阅读明显增长；快照位建议 08:00 / 12:30 / 16:30 / 22:30。

## [2026-10-10] - 2026-10-10

### 修复 / Fixed

- 私信发送判定重写：主判据改为「本次正文标记（`/details/<id>`）是否在会话里且为本次新增」，
  系统提示只看「本次新增」的（Counter 差集）。旧口径曾把 7 个已送达的人误判成被拦。
  判定逻辑抽成纯函数 `judge_sent()` / `judge_final()`，可离线自测。
- 封面裁剪窗残留遮罩拦住「保存为草稿」：等待 12s→20s，仍不消失则 `display:none` +
  `visibility:hidden` 移出布局，保存前再清一次浮层，点击被拦时清浮层重试一次。

## [2026-10-09] - 2026-10-09

### 新增 / Added

- 私信名单改造：不再从个人主页 DOM 抓（写死 20 条），改走 CSDN 名单接口
  `getFansOffsetList` / `getFollowOffsetList`（`pageSize` 调大即取全，实测 41/41、208/208），
  接口不可用才回退 DOM 并告警；对账用主页头部实时总数。
- `_probe_delivery.py`：逐个打开会话复核真实送达（只读）。
- 端口移植报告与 E2E 验证记录入库。

### 修复 / Fixed

- 心跳占用判定排除自身 pid（`pid == os.getpid()` 视为非外部占用）——此前会把
  「自己还活着」当成「被别的进程占用」而拒绝启动，卡死崩溃恢复链。

## [2026-10-08] - 2026-10-08

### 新增 / Added

- **风控闸门（`risk`）**：发布链路识别滑块/安全验证即停手、截图、响铃、写
  `output/NEEDS-HUMAN.txt`，等真人点完自动续跑。绝不自动过码。
- **崩溃自愈（`crash`）**：分级恢复——`TargetClosedError` 先尝试同 context 新开 page，
  连 context 都不可用才重启浏览器；启动前清残留 Chrome（只杀本项目 profile 的进程）。
- 关掉 Chrome「是否翻译此页面」「未正常关闭是否恢复」两个气泡（启动参数 + 页面级兜底）。
- 一键三连（`csdn_interact.py`）：点赞/收藏/评论；点赞收藏点击前必读状态防「反取消」；
  收藏弹窗真实结构（`#csdn-collection` + 原生 click）；评论改「读正文写感悟」两阶段；
  熔断跳过（`skipped_op_disabled` 不定性，次日自然重试）。
- 私信群发（`csdn_message.py`）：硬闸门（不写 `--yes`/`--wait` 什么都不发）、
  两种私信限额识别、乐观渲染沉淀时间、失败只对「原因不明」重试一次。
- 离线测试两套：`test_robustness.py`（33 项）、`test_launch_paths.py`（25 项）。

## [2026-10-07] - 2026-10-07

### 新增 / Added

- 发布链路跑通：一次连续发布 5 篇全部成功（`05-result.png` 显示「发布成功！正在审核中」）。
- 声明式任务执行器 `csdn_job.py`（`prepare` / `status` / `publish`）+ 素材搜集
  `csdn_research.py` + 封面生成 `make_cover.py` + 发布前体检 `csdn_lint.py`。
