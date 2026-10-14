# CSDN 20 篇 · 视觉设计系统（Master）

> 来源：`ui` skill（ui-ux-pro-max）`--design-system` 检索结果 + `--domain color` / `--domain chart` 补充检索
> 用途：本批 20 篇文章的**封面**与**正文示意图**统一视觉规范
> 检索主意图：`technical blog article cover developer infrastructure cloud native`
> 落地方式：`csdn_figure.py html`（封面，本地 HTML→PNG）；`csdn_figure.py diagram`（示意图，Mermaid→PNG）

## 一、全局基调

- **风格**：Swiss Modernism 2.0 —— 网格系统、理性、数学化间距、清晰层级。
  理由：技术博客/文档类的强适配（skill 明确把 documentation / architecture 列为其 Best For）。
- **排版骨架**：12 列网格、数学化间距、非对称但克制。
- **明暗**：正文示意图用 **light**（CSDN 正文默认白底，深色图会割裂阅读）；封面用**深色终端风**（在信息流里对比度更强、更像"技术"）。
- **字体**（封面与图内统一）：标题 `Inter` / `Public Sans`，代码与数字 `JetBrains Mono` / `Consolas`。中文字体回落 `Microsoft YaHei`。
- **反模式（禁止）**：堆 Emoji 当图标、堆无信息量的装饰图、用别人的截图当插图。
  （策略文档 1.7 已实测：配图数量与阅读无关，图只服务完读率。）

## 二、封面规范（统一模板，换主色 + 赛道标识）

| 项 | 规格 |
|---|---|
| 画布 | 1200 × 640（CSDN 封面推荐比例 ≈ 1.875:1） |
| 底 | 深色 `#0F172A`，右下角一层 6% 白网格线（Swiss 网格暗示） |
| 主色条 | 左侧 8px 竖条，颜色 = 赛道主色（见下表） |
| 赛道标签 | 左上角小字 chip：`CLOUD NATIVE` / `GOLANG` / `MYSQL` / `REDIS` |
| 标题 | 主标题 44px/600，`#F8FAFC`；最多 2 行，超长自动缩号 |
| 副题 | 关键词行 20px/500，赛道主色 |
| 角标 | 右下角 `<你的昵称>` 16px `#94A3B8` |
| **禁忌** | 不使用 CSDN 会剥掉的 `style=`（那是正文规则；封面是本地 HTML 渲染，不受限） |

## 三、四赛道配色（Accent = 赛道主色）

| 赛道 | Accent | 深底 | 浅底（图内） | 语义 |
|---|---|---|---|---|
| 云原生 K8s | `#326CE5`（K8s 官方蓝） | `#0F172A` | `#EFF4FF` | 集群/调度/资源 |
| Go | `#00ADD8`（Go 官青） | `#0F172A` | `#E9F9FE` | 运行期/并发 |
| MySQL | `#00758F`（MySQL 深青）+ 警示 `#E48E00` | `#0F172A` | `#EAF4F7` | 锁/执行计划/复制 |
| Redis | `#DC382D`（Redis 红） | `#0F172A` | `#FDEDEC` | 缓存/内存/淘汰 |

> 统一语义色沿用 skill 的 `--color-destructive: #DC2626`（错误/告警）、
> `--color-muted-foreground: #94A3B8`（次要说明）；正文表格图用 light 主题。

## 四、正文示意图规范（Mermaid，light 主题）

- **只用三类图**：流程图（排查顺序）、时序图（锁/复制关系）、状态或架构图（数据流）。
- **每篇 1~2 张**，只放"能替代一大段文字"的图；数据对比优先用 **markdown 表格**（可复制、精确，见 skill chart 域的 Bar Chart 指引：类别 ≤15 用条形图、必须直接标数值、不可仅靠颜色区分）。
- 图内字号 15px，代码/命令用等宽。
- 图必须**自绘**（Mermaid 源码在 `exp/fig-*.mmd`），不引用他人图表。

## 五、可访问性与交付前检查（继承 skill checklist）

- [ ] 正文图 light 底 + 深字，对比 ≥ 4.5:1
- [ ] 图上不靠颜色单独承载语义（配文字标签）
- [ ] 不放 Emoji 当图标
- [ ] 封面标题前 30 字自带完整语义（流里被截断也能读懂）
