# 开源打包说明（内部文档，随包一起走）

> 本文记录「csdn-publish skill → 开源项目」的打包过程：文件清点、目录结构设计、
> 纳入/剔除决策、脱敏记录与待确认清单。**对外使用者不需要读这份文件**，
> 看 `README.md`（中文）/ `README.en.md`（English）即可。

---

## 一、源材料清点

### 1.1 Skill 文档（`~/.workbuddy/skills/csdn-publish/`，2026-10-13 版为最新）

| 文件 | 作用 |
|---|---|
| `SKILL.md` | 技能主文档：文首「第 0 条」（遇滑块停手等真人）为最高优先级规则；往下是快捷指令表、发布/定时发布/体检/一键三连/私信群发/选题调研的全部作业规程、已知限制、排错表、「改代码时务必守住的坑」 |
| `COMMANDS.md` | `/csdn-publish help` 的输出源：用户全部可用说法 → 底层命令映射 |
| `CONFIG.md` | `/csdn-publish config` 的输出源：全部可配置参数逐一列举（job 字段、config.json、命令行开关、优先级） |
| `*.bak-20261009 / *.bak-20261013` | 历史备份，**剔除**（不是交付物） |

> 注：项目内 `skills/csdn-publish/` 副本原是 2026-10-09 旧版，比用户级已安装副本旧 4 天
> （缺 `get_url_message` 行）。本包以**最新版**为准放入 `skills/csdn-publish/`，
> 修复了「安装器会装上旧文档」的问题。

### 1.2 工具代码（项目根，44 个 .py）

| 文件 | 作用 | 纳入？ |
|---|---|---|
| `publish_csdn.py` | 发布引擎：login/check/inspect/config/publish；风控闸门 `risk_gate()`、崩溃自愈、栏目硬闸门（rc=7）都在这里 | ✅ |
| `csdn_job.py` | 声明式任务执行器（prepare/status/publish/read），换主题只改 JSON | ✅ |
| `csdn_stats.py` | 阅读数据采集 + 复盘报告（只读，不发布不改动） | ✅ |
| `csdn_retro.py` | 单批复盘：probe/build/analyze/compare 四子命令 | ✅ |
| `csdn_rank.py` | 跨批横评（2026-10-14 新增） | ✅ |
| `csdn_lint.py` | 发布前体检（error 拦发布 / warn 只提示） | ✅ |
| `csdn_research.py` | 按关键词搜集高阅读素材（被 csdn_job 复用） | ✅ |
| `csdn_refs.py` | 逐篇独立搜素材 + 全池去重 | ✅ |
| `csdn_search.py` | 热榜选题调研 → md 报告 | ✅ |
| `csdn_hot.py` | 热榜取数 + 爆款风格指纹 | ✅ |
| `csdn_interact.py` | 一键三连（赞/藏/评） | ✅ |
| `csdn_message.py` | 私信群发（硬闸门 --yes/--wait） | ✅ |
| `csdn_top_urls.py` | 今日 Top4 链接写进私信模板 | ✅ |
| `csdn_figure.py` | Mermaid 矢量图 / 可商用配图 | ✅ |
| `csdn_image.py` | 表格/终端输出渲染成图 + 传 CSDN 图床 | ✅ |
| `make_cover.py` / `make_cover_pro.py` | 封面生成（渐变 / 设计感） | ✅ |
| `csdn_drafts.py` | 草稿箱计数（验收依据） | ✅ |
| `csdn_draft_clean.py` | 草稿/定时发布列表与删除 | ✅ |
| `csdn_draft_check.py` | 草稿插图存活校验 | ✅ |
| `csdn_schedule_drafts.py` | 草稿批量改定时发布（不动正文） | ✅ |
| `csdn_preview_dump.py` | 探针：预览区真实 DOM 与计算样式 | ✅ |
| `_probe_delivery.py` | 探针：私信逐人送达复核（只读，SKILL.md 引用） | ✅ |
| `_probe_category.py` | 探针：回读文章真实栏目归属（只读） | ✅ |
| `_probe_column.py` | 探针：抓专栏页文章列表（只读；专栏 ID 参数化） | ✅（脱敏） |
| `probe_writable.py` | 诊断：目录可写性（只读） | ✅ |
| `analyze_excl.py` | 一次性分析：剔除指定文章后重算 | ✅（脱敏） |
| `test_robustness.py` / `test_launch_paths.py` | 离线自测 33 + 25 项 | ✅ |
| `test_category_gate.py` / `test_schedule.py` | 栏目闸门 / 排期离线自测 | ✅ |
| `exp/new_experiment.py` | 「每篇一个实测」三件套脚手架（SKILL.md 引用） | ✅ |
| `_publish_20.py` / `_upload_embed_20.py` / `_gen_covers_20.py` / `_render_diagrams_20.py` / `_extract_mmd_20.py` / `_audit_redis_categories.py` / `_fix_redis_categories.py` | 某一批 20 篇的一次性脚本，含账号特有数据 | ❌ 剔除 |
| `exp/agent_verifier.py` / `exp/probe_article_truth.py` / `exp/rag_experiment.py` / `exp/vector_sanity.py` 及 exp/ 下数据文件 | 与本 skill 文档面无关的实验/用户资产 | ❌ 剔除 |

### 1.3 配置与模板

| 文件 | 作用 | 处置 |
|---|---|---|
| `config.json` | 选择器/阈值/接口全部配置；**无密钥**（只有 cookie 名字白名单） | ✅ 原样 |
| `requirements.txt` | playwright>=1.40、Pillow>=10.0 | ✅ 原样 |
| `*.bat` ×6、`install-skill.ps1` | 启动器/装机/登录/装技能 | ✅ 原样（CRLF 保持，.gitattributes 加固） |
| `templates/start.txt` | 文章开头横幅 | ✅ 脱敏为占位符 |
| `templates/message.txt` | 私信正文（含编号槽位） | ✅ 脱敏为占位符 |
| `templates/comment.txt` | 旧版一次性评论文案 | ✅ 原样 |
| `retro/config.json` | 复盘口径 | ✅ 脱敏（去环境特有路径、清用户基线） |
| `jobs/job.all.json`、`jobs/job.independent.json` | 两个可跑通的示例任务（SKILL.md 点名） | ✅ 脱敏 coverFooter |
| `jobs/` 其余 9 个 | 用户实际任务文件 | ❌ 剔除（.gitignore 规则同款） |
| `topics/pool40-*.json` | 用户选题池 | ❌ 剔除（README 说明格式） |
| `log/known-issues.md` | 已知坑（工程知识，SKILL.md 多处引用） | ✅ 脱敏纳入 |
| `log/README.md` | 日志索引约定 | ✅ 清洗纳入（个人运行历史剔除） |
| `log/` 其余 | 逐次运行日志 | ❌ 剔除 |
| `design-system/csdn-20/MASTER.md` | 封面设计规范 | ✅ 脱敏纳入 |
| `docs/redis-series/` | 用户的 Redis 系列内容策划 | ❌ 剔除 |
| `README.md`（旧，41KB） | 原项目自用 README | ❌ 替换为新的双语 README |
| `CLEANUP.md` / `E2E-VERIFY-*.md` / `MESSAGE-FULL-LIST-*.md` / `PATH-AUDIT-*.md` / `PORT-REPORT-*.md` | 个人工作记录 | ❌ 剔除 |
| `.profile/` `.venv/` `.pw-browsers/` `.tmp/` `.workbuddy/` `output/` `posts/` `hot_search/` `covers/` `img/` | 登录态/环境/运行时/用户内容 | ❌ 剔除（.gitignore 覆盖） |

### 1.4 敏感信息核查结论

- **无任何密钥/token/cookie 值**进入本包（`.profile/` 整体剔除；`config.json` 只有 cookie 名称白名单）。
- 硬编码个人标识已全部参数化：账号 ID `2501_9…（账号 ID）`、昵称「「用户昵称」」、
  好友博客链接、专栏 ID `<专栏ID>`、原作者机器上的两个项目绝对路径。

---

## 二、目录结构设计与职责

```
csdn-publisher-oss/            ← 打包产物（目录名为临时名，项目名称待确认）
├── *.py                       # 工具代码（平铺，脚本间用相对路径互找，不挪动）
├── *.bat / *.ps1              # Windows 启动器与装机脚本（CRLF）
├── config.json                # 唯一配置文件（选择器/阈值/接口）
├── requirements.txt
├── jobs/                      # 任务配置目录（保留 2 个示例）
├── templates/                 # 发布时拼接的素材（横幅/私信正文/评论）
├── retro/config.json          # 复盘口径（与 csdn_retro.py 一对）
├── log/                       # 索引约定 + 已知坑（逐次日志不入库）
├── skills/csdn-publish/       # 技能文档（install-skill.ps1 的安装源；__TOOL_DIR__ 占位）
├── design-system/             # 封面设计规范
├── exp/new_experiment.py      # 实验脚手架
├── exp/examples/              # 文档示例的输入文件（让示例命令可直接跑）
├── README.md / README.en.md   # 双语说明
├── CONTRIBUTING.md / CHANGELOG.md / LICENSE / .gitignore / .gitattributes
└── docs/packaging-notes.md    # 本文件
```

设计原则：

1. **不改脚本间的相对路径关系**（`sys.path.insert(0, 脚本所在目录)`、`ROOT = 脚本目录`），
   所以代码全部平铺在根，目录职责与原项目一致——「结构整理」只做增删，不做搬家。
2. **`.gitignore` 精确到「保留示例、剔除用户数据」**：`jobs/*` + 两个 `!` 例外；
   `log/*` + 两个 `!` 例外。
3. **`.gitattributes` 把 `.bat`/`.ps1` 钉死 CRLF**（LF 的 .bat 在 cmd 里会直接解析失败），
   源码文档钉 LF。
4. **`exp/examples/` 让文档里的示例命令开箱可跑**（Mermaid/表格/终端输出三个示例输入）。

---

## 三、脱敏与参数化记录（逐条）

| 文件 | 原文 | 改为 |
|---|---|---|
| `skills/csdn-publish/SKILL.md` | <原作者机器上的项目绝对路径>（8 处） | `__TOOL_DIR__`（install-skill.ps1 安装时替换为实际路径） |
| 同上 | `2501_9…（账号 ID）` / 「「用户昵称」」 | `2501_9xxxxxxx` / 「示例昵称」 |
| 同上 | 测试命令多写一层 `csdn-publisher\` 的笔误 | 修正为 `.\.venv\Scripts\python.exe test_*.py` |
| 同上 | 示例引用 `exp\fig-rag-flow.mmd` 等不随包发布的文件 | 改指 `exp\examples\*`（随包发布） |
| `skills/csdn-publish/COMMANDS.md` | 同类绝对路径 | `__TOOL_DIR__` |
| `templates/start.txt` | 真实昵称 + 5 条专栏链接 | `<你的昵称>` / `<你的账号ID>/category_<分类ID>` |
| `templates/message.txt` | 4 条真实文章链接 + 好友链接 | `1.url`~`4.url` 槽位 + `<好友昵称>：https://blog.csdn.net/<好友账号ID>` |
| `jobs/job.all.json`、`jobs/job.independent.json` | `coverFooter: 「用户昵称」` | `<你的昵称>` |
| `retro/config.json` | `snapshots` 含一条原作者机器上的绝对路径；`baselines` 6 条用户基线；`refBatches: ["2026-10-09"]` | 只留 `output/stats/snapshots`；基线清空（保留注释说明按需追加）；`refBatches: []` |
| `log/known-issues.md` | `UserName=2501_9…（账号 ID）`；`chromium-1243`；引用不随本包发布的 `_verify_message.py` | `<你的账号ID>`；`chromium-<版本号>`；改为描述「迷你 profile 副本」做法本身 |
| `log/README.md` | 用户 18 行运行历史 | 保留约定说明，历史表清空 |
| `design-system/csdn-20/MASTER.md` | 昵称 | `<你的昵称>` |
| `_probe_column.py` | `DEFAULT_COL = "<专栏ID>"`（用户专栏 ID） | `""` + 注释说明从专栏页 URL 取 |
| `analyze_excl.py` | `DEFAULT_EXCLUDE` 写死用户文章标题 | `r""`（用 `--exclude` 指定） |
| `exp/examples/verify-output.txt` | 原作者机器上的旧工作区路径 | `<项目根目录>\exp\_selftest_ws` |

未做任何功能性改动：所有 `.py` / `config.json` / `.bat` / `.ps1` 除上表所列外**逐字节相同**。

---

## 四、待确认清单（2026-10-14 23:5x 已全部确认并落地）

| # | 事项 | 落地结果 |
|---|---|---|
| 1 | 项目名称 | **csdn-publisher** |
| 2 | 开源许可证 | **MIT**（Copyright (c) 2026 朱昱先） |
| 3 | 作者与版权归属 | **朱昱先**（Zhu Yuxian） |
| 4 | 目标托管平台 | **GitHub** — <https://github.com/ZYX200603/csdn-publisher>（公开仓库） |
| 5 | 运行环境与依赖版本 | Windows 10/11 + Python 3.10+ + Chrome + playwright>=1.40 + Pillow>=10.0（源材料实证） |
| 6 | 是否推送远端仓库 | **已推送**（用户 2026-10-14 确认：新建公开仓库） |
| 7 | CHANGELOG / CONTRIBUTING | 保留（用户未要求删除） |
| 8 | `__TOOL_DIR__` 占位机制 | 保留（install-skill.ps1 安装时替换为实际路径） |
