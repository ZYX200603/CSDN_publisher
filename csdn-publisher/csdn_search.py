#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/csdn-publish search` —— CSDN 热榜选题调研，产出一份可执行的 md 报告。

它回答的不是"今天什么火"，而是"**我该学哪几篇、学什么、下一篇写什么**"。所以报告里
除了榜单，还有：按"收藏率/评论率"挑出的精读清单、Top 5 的结构指纹区间（字数/H2/代码块/
表格/图片/数字密度/内链）、主题词分布、和你自己账号的交叉（已写过 vs 空白），
以及和上期的对比（新上榜/掉榜/连续在榜）。

    python csdn_search.py                          # 关键词自动取本期 Top 主题词
    python csdn_search.py --q "AI Agent"            # 指定关键词（决定文件名）
    python csdn_search.py --limit 30 --deep 5       # 抓前 30 条，精读 5 篇
    python csdn_search.py --no-deep                 # 不抓正文（快 3 倍）
    python csdn_search.py --my-pages 0              # 跳过"和我账号交叉"（不再采自己的列表）

产物：
    hot_search/<关键词>-<YYYY-MM-DD>.md     报告（给人看）
    hot_search/raw/<关键词>-<时间戳>.json   原始数据（全字段，供对账/趋势）
    hot_search/index.json                   历史索引（下期的"新上榜/连续在榜"靠它）

为什么是"热度排序 + 收藏排序"两个视角：热度是平台加权（实测第 22 名有 22,425 浏览，
第 3 名只有 422 浏览），而收藏更接近"读者认为值得反复看"。两个都抓才不会被单一指标带偏。
"""

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import csdn_hot as hot  # noqa: E402
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT_DIR = ROOT / "hot_search"
RAW_DIR = OUT_DIR / "raw"
INDEX = OUT_DIR / "index.json"

# 行抓取：不能用 csdn_hot.ROWS_JS —— 它按"含热度"筛行，而收藏排序的行没有热度字段
# （只有"X 浏览 Y 评论 Z 收藏 N 新增收藏 作者 关注"），会整行取空。
ROWS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/article/details/"]')) {
    const title = clean(a.innerText);
    if (title.length < 5 || /榜单规则/.test(title)) continue;
    if (seen.has(a.href)) continue;
    seen.add(a.href);
    let el = a, item = '';
    for (let i = 0; i < 7 && el; i++) {
      el = el.parentElement;
      if (!el) break;
      const t = clean(el.innerText);
      if (/浏览/.test(t) && /收藏/.test(t) && t.length < 420) item = t;   // 两个排序都有这两项
    }
    out.push({href: a.href, title, item});
  }
  return out;
}
"""

# 点击"热度排序 / 收藏排序"
SORT_JS = r"""
(label) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const el = [...document.querySelectorAll('a,li,div,span,button')]
    .filter(e => clean(e.innerText) === label && e.children.length <= 1).pop();
  if (!el) return null;
  el.click();
  return clean(el.innerText);
}
"""

# 详情页结构（比 csdn_hot 的指纹多要：真标签、内链数、阅读数、发布时间）
DETAIL_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const box = document.querySelector('#content_views') || document.querySelector('article');
  if (!box) return null;
  const imgs = [...box.querySelectorAll('img')].filter(i => (i.src || '').startsWith('http'));
  const links = [...box.querySelectorAll('a')].map(a => a.href || '');
  const meta = document.querySelector('meta[name="keywords"]');
  return {
    title: clean((document.querySelector('h1.title-article') || document.querySelector('h1') || {}).innerText),
    text: box.innerText || '',
    h2: [...box.querySelectorAll('h2')].map(h => clean(h.innerText)),
    codeBlocks: box.querySelectorAll('pre').length,
    tables: box.querySelectorAll('table').length,
    images: imgs.length,
    links: links.length,
    innerLinks: links.filter(u => /blog\.csdn\.net.*\/article\/details\//.test(u)).length,
    // 文章真标签在 meta keywords 里（宽泛的 .tag-link 会抓到侧栏"订阅专栏/查看详情"）
    tags: meta ? (meta.getAttribute('content') || '').split(',').map(s => s.trim()).filter(Boolean).slice(0, 12) : [],
    collections: [...document.querySelectorAll('.blog-tags-box a')]
                   .map(a => clean(a.innerText))
                   .filter(t => t && !['订阅专栏', '查看详情', '收起'].includes(t)).slice(0, 8),
    publishTime: clean((document.querySelector('.time') || {}).innerText || ''),
    readCount: clean((document.querySelector('.read-count') || {}).innerText || ''),
  };
}
"""

# 榜单行文本解析（两个排序口径不同，必须分开认）
#   热度排序： 1 标题 790 浏览 22 评论 24 收藏 34760 热度 作者名 已关注
#   收藏排序： 1 标题 1469 浏览 2 评论 394 收藏 66 新增收藏 作者名 关注
ROW_RE = re.compile(
    r"^(?P<rank>\d{1,3})\s+(?P<title>.*?)\s*"
    r"(?P<views>[\d.,]+)\s*浏览\s*(?P<comments>\d+)\s*评论\s*"
    r"(?P<favorites>[\d.,]+)\s*收藏\s*(?P<metric>[\d.,]+)\s*(?P<kind>热度|新增收藏)\s*"
    r"(?P<author>.*?)\s*(?:已关注|关注)?\s*$")

# 目录行（发布页的目录是普通 <p>，不是 .toc，会污染"开头 200 字"和段落统计）
TOC_LINE = re.compile(r"^\s*(目录|(?:第?[一二三四五六七八九十百]+[、.．章节)）])|\d+(?:\.\d+)*\s*[、.．]?|"
                      r"[（(]\d+[)）])")


def parse_row(item):
    """把一行榜单文本解析成结构化字段；两个排序口径都能认。"""
    m = ROW_RE.match((item or "").strip())
    if not m:
        return None
    kind = m.group("kind")
    return {
        "rank": int(m.group("rank")),
        "title": m.group("title").strip(),
        "views": num(m.group("views")),
        "comments": num(m.group("comments")),
        "favorites": num(m.group("favorites")),
        "heat": num(m.group("metric")) if kind == "热度" else 0,
        "new_favorites": num(m.group("metric")) if kind == "新增收藏" else 0,
        "author": re.sub(r"\s*(?:已关注|关注)\s*$", "", m.group("author") or "").strip(),
        "sort_kind": kind,
    }


def strip_leading_toc(text):
    """去掉正文开头的目录块（启发式：连续短行/编号行，直到出现真正的长段落）。"""
    lines = (text or "").split("\n")
    for i, ln in enumerate(lines[:100]):
        s = ln.strip()
        if len(s) >= 40 and not TOC_LINE.match(s):
            return "\n".join(lines[i:])
    return text

# 主题词表（榜单标题里最常出现的题材；够用即可，不在多）
THEME_WORDS = [
    "AI", "Agent", "智能体", "大模型", "RAG", "MCP", "LLM", "GPT", "DeepSeek", "提示词",
    "Prompt", "向量检索", "知识库", "机器学习", "深度学习",
    "C++", "Java", "Python", "Rust", "Go", "JavaScript", "TypeScript", "C#", "PHP",
    "Docker", "K8s", "Kubernetes", "Linux", "Nginx", "云原生", "运维", "微服务",
    "Redis", "MySQL", "PostgreSQL", "MongoDB", "SQL", "数据库", "消息队列", "Kafka",
    "Spring", "MyBatis", "Vue", "React", "前端", "后端", "算法", "数据结构", "LeetCode",
    "面试", "架构", "性能优化", "网络", "操作系统", "设计模式", "安全", "鸿蒙", "HarmonyOS",
    "嵌入式", "单片机", "物联网", "测试", "Git", "编译器",
]

# "AI 家族"：统计自己覆盖度时按家族合并（草稿标题里可能只写 RAG/Agent，不写 AI）
AI_FAMILY = ("AI", "Agent", "智能体", "大模型", "RAG", "MCP", "LLM", "GPT", "DeepSeek",
             "提示词", "Prompt", "向量检索", "知识库")

SERIES_RE = re.compile(r"[【\[]([^】\]]{2,22})[】\]]")
QUESTION_HINTS = ("为什么", "怎么", "如何", "是什么", "什么是", "能不能", "要不要",
                  "哪些", "多少", "到底", "？", "?")

# 赛道画像：决定"哪些热点跟你有关"。
# 三档：core = 你现在的主战场（Go 后端/数据库/中间件/云原生）；learn = 你要补的增量（AI 应用与工程）；
# exclude = 再火也不写的领域（别的语言/嵌入式/前端/图形/算法竞赛…）。
# 为什么必须有它：不做过滤时，冷门领域的爆款（ATE 芯片测试、CAD、C++ 标准项目）会混进
# "值得学的 5 篇"，把结构指纹区间算歪 —— 等于对着别人的赛道练基本功。
PROFILES = {
    "go-backend": {
        "desc": "Go 后端 + AI/Agent 应用（增量学习区）",
        "core": ["go", "golang", "gin", "gorm", "grpc", "微服务", "后端", "服务端", "api", "接口",
                 "网关", "分布式", "并发", "goroutine", "channel", "redis", "mysql", "postgres",
                 "mongodb", "数据库", "sql", "索引", "缓存", "消息队列", "kafka", "rocketmq",
                 "rabbitmq", "docker", "k8s", "kubernetes", "容器", "云原生", "运维", "部署", "nginx",
                 "linux", "性能优化", "调优", "架构", "系统设计", "高可用", "限流", "熔断", "监控",
                 "devops", "ci/cd", "http", "tcp", "网络编程", "面试"],
        "learn": ["ai", "agent", "智能体", "大模型", "llm", "gpt", "deepseek", "rag", "mcp",
                  "提示词", "prompt", "向量", "embedding", "推理", "模型部署", "ai应用", "ai 应用",
                  "工作流", "langchain", "多智能体", "function calling", "工具调用", "codex",
                  "claude", "cursor", "知识库", "语义"],
        "exclude": ["c++", "c语言", "c primer", "嵌入式", "单片机", "ate", "芯片", "cad", "鸿蒙",
                    "harmonyos", "arkts", "vue", "react", "小程序", "css", "html", "前端", "图形学",
                    "游戏", "opengl", "unity", "逆向", "反汇编", "汇编", "内核", "驱动", "文件系统",
                    "qt", "上位机", "labview", "机器学习", "深度学习", "神经网络", "论文", "数学建模",
                    "蓝桥杯", "爬虫", "3d", "渲染",
                    # 消费级/家庭网络，不是后端工程（实测这类会沾着 Docker/数据库 混进来）
                    "nas", "群晖", "极空间", "飞牛", "软路由", "openwrt", "路由器", "家庭网络",
                    "客厅", "电视盒子", "手机"],
    },
}


def _hit(blob, word):
    """关键词命中判定。

    纯 ASCII 词必须**按词边界**匹配：实测过子串匹配会误判 —— `Cargo` 里含 `go`、
    `Dataify` 里含 `ai`、`main` 里含 `ai`，结果 Rust/数据采集类文章被算成"你的赛道"。
    中文词继续用子串（分词不划算，误判率低）。
    """
    w = word.lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9+#./ -]*", w):
        return re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", blob) is not None
    return w in blob


def relevance(row, profile):
    """判断一篇文章跟这个赛道的关系：核心 / 学习 / 核心+学习 / 弱相关 / 无关。"""
    blob = ((row.get("title") or "") + " " + " ".join(row.get("tags") or [])).lower()
    core = [w for w in profile["core"] if _hit(blob, w)]
    learn = [w for w in profile["learn"] if _hit(blob, w)]
    excl = [w for w in profile["exclude"] if _hit(blob, w)]
    if learn:
        tier = "核心+学习" if core else "学习"
    elif core and not excl:
        tier = "核心"
    elif core and excl:
        tier = "弱相关"          # 例：C++ 项目里讲消息队列 —— 主题沾边，但语言不是你的赛道
    else:
        tier = "无关"
    return {"tier": tier, "core": core[:4], "learn": learn[:4], "exclude": excl[:3]}


RELEVANT_TIERS = ("核心", "核心+学习", "学习")


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def num(s):
    """'22,425' / '1.2万' / '' -> int"""
    if s is None:
        return 0
    t = str(s).replace(",", "").replace(" ", "").strip()
    m = re.match(r"^([\d.]+)万$", t)
    if m:
        return int(float(m.group(1)) * 10000)
    m = re.match(r"^(\d+)", t)
    return int(m.group(1)) if m else 0


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


# ---------------------------------------------------------------- 采集

def collect_rank(page, sort_label):
    """点某个排序，等渲染，抓全榜（解析在 Python 侧做，两个口径都认）。"""
    clicked = page.evaluate(SORT_JS, sort_label)
    if clicked is None:
        log(f"  没找到「{sort_label}」排序入口，跳过（用当前排序）")
    else:
        log(f"  已点「{clicked}」")
    page.wait_for_timeout(5000)
    raw = page.evaluate(ROWS_JS) or []
    rows, bad = [], 0
    for r in raw:
        parsed = parse_row(r.get("item") or "")
        if not parsed:
            bad += 1
            continue
        parsed["url"] = (r.get("href") or "").split("?")[0]
        rows.append(parsed)
    if bad:
        log(f"  ! {bad} 行没解析出来（榜单改版？把 output/rank-detail-probe.json 的思路再跑一遍）")
    rows.sort(key=lambda x: x["rank"])
    return rows


def merge_sorts(by_heat, by_fav):
    """两套排序按 URL 合并：同一篇记录它在两个排序里的位置与各自口径的数值。

    注意两个榜口径不同：热度排序给「热度」，收藏排序给「新增收藏」。
    """
    merged = {}
    for sort_name, rows in (("热度", by_heat), ("收藏", by_fav)):
        for r in rows:
            key = r["url"] or r["title"]
            m = merged.setdefault(key, dict(r))
            m[f"rank_{sort_name}"] = r["rank"]
            if sort_name == "收藏":
                m["新增收藏"] = r["new_favorites"]
                m["收藏榜收藏"] = r["favorites"]
                if not m.get("heat"):
                    m["heat"] = r["heat"]          # 收藏榜没有热度，保持 0
    out = list(merged.values())
    out.sort(key=lambda x: (x.get("rank_热度") is None, x.get("rank_热度") or 999))
    return out


def pick_deep(rows, n):
    """挑精读对象：**只在赛道相关的文章里挑**（否则会对着别人的赛道练基本功）。

    相关集合里再按"热度 Top15 × 收藏率 Top15"交错取 n 篇，两类信号都覆盖。
    """
    if n <= 0:
        return []
    pool = [r for r in rows if r.get("tier") in RELEVANT_TIERS]
    if len(pool) < n:                     # 相关文章不够就放宽到"弱相关"
        pool = [r for r in rows if r.get("tier") in RELEVANT_TIERS + ("弱相关",)]
    if not pool:
        pool = rows
    hot_top = [r for r in pool if r.get("rank_热度")][:15]
    fav_top = sorted([r for r in pool if r["views"] >= 50],
                     key=lambda r: -(r["favorites"] / max(r["views"], 1)))[:15]
    picked, seen = [], set()
    for pair in zip(hot_top, fav_top):
        for r in pair:
            key = r["url"] or r["title"]
            if key not in seen:
                seen.add(key)
                picked.append(r)
            if len(picked) >= n:
                return picked
    for r in pool:
        key = r["url"] or r["title"]
        if key not in seen:
            seen.add(key)
            picked.append(r)
        if len(picked) >= n:
            break
    return picked[:n]


def scan_local_drafts():
    """本地 posts/*.md 的标题（= 草稿箱里那些还没发的稿子），用于账号交叉时区分"已发布/在写"。"""
    out = []
    for p in sorted((ROOT / "posts").glob("*.md")):
        if p.name.startswith("_"):
            continue
        try:
            head = p.read_text(encoding="utf-8-sig")[:600]
        except Exception:
            continue
        m = re.search(r"^title:\s*(.+)$", head, re.M)
        if not m:
            m = re.search(r"^#\s+(.+)$", head, re.M)
        if m:
            out.append({"title": m.group(1).strip().strip('"\''), "file": p.name})
    return out


def deep_one(page, row):
    """打开文章详情页，拆结构指纹（只统计结构，不摘抄段落）。"""
    page.goto(row["url"], wait_until="domcontentloaded")
    page.wait_for_timeout(3500)
    data = page.evaluate(DETAIL_JS)
    if not data:
        return None
    data["text"] = strip_leading_toc(data.get("text") or "")   # 目录不是正文
    fp = hot.fingerprint(data)                       # 复用同一套指标定义，避免两份实现漂移
    fp["内链"] = data.get("innerLinks")
    fp["标签"] = data.get("tags") or []
    fp["收录于"] = data.get("collections") or []
    fp["发布时间"] = data.get("publishTime") or ""
    fp["页内阅读数"] = num(data.get("readCount"))
    fp["url"] = row["url"]
    fp["作者"] = row.get("author") or ""
    fp["榜单热度"] = row.get("heat")
    fp["榜单浏览"] = row.get("views")
    fp["榜单收藏"] = row.get("favorites")
    fp["榜单评论"] = row.get("comments")
    fp["收藏率"] = pct(row.get("favorites") or 0, row.get("views") or 0)
    return fp


def collect_my_titles(page, max_pages):
    """采自己账号最近的文章（复用 csdn_stats 的采集器）。"""
    if max_pages <= 0:
        return []
    try:
        import csdn_stats as st
    except Exception as e:
        log(f"  拿不到 csdn_stats: {e.__class__.__name__}")
        return []
    try:
        rows = st.collect_all(page, max_pages=max_pages, quiet=True)
        log(f"  采到自己 {len(rows)} 篇文章（最近 {max_pages} 页）")
        return rows
    except Exception as e:
        log(f"  采自己的文章失败: {e.__class__.__name__}: {e}")
        return []


# ---------------------------------------------------------------- 分析

def theme_stats(rows):
    themes, series = {}, {}
    for r in rows:
        t = r["title"]
        low = t.lower()
        for w in THEME_WORDS:
            if w.lower() in low:
                themes[w] = themes.get(w, 0) + 1
        for m in SERIES_RE.findall(t):
            s = m.strip()
            if 2 <= len(s) <= 22:
                series[s] = series.get(s, 0) + 1
    return (dict(sorted(themes.items(), key=lambda kv: -kv[1])),
            dict(sorted(series.items(), key=lambda kv: -kv[1])))


def top_keyword(themes, series, rows, fallback="热榜"):
    """给文件名挑关键词：主题词优先，其次专栏系列名，最后兜底。"""
    if themes:
        top = next(iter(themes))
        if themes[top] >= 2:
            return top
    if series:
        top = next(iter(series))
        if series[top] >= 2:
            return top
    return fallback


def stat_block(fps, key):
    vals = [f.get(key) for f in fps if isinstance(f.get(key), (int, float))]
    if not vals:
        return None
    vals.sort()
    return {
        "p25": vals[int(len(vals) * 0.25)] if len(vals) > 1 else vals[0],
        "median": statistics.median(vals),
        "p75": vals[min(int(len(vals) * 0.75), len(vals) - 1)],
        "min": vals[0], "max": vals[-1],
    }


def load_index(path):
    """历史索引要跟着 --out-dir 走（否则冒烟测试会污染正式索引）。"""
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def history_compare(entries, title_key="title"):
    """用上一条索引记录算新上榜 / 掉榜 / 连续在榜。"""
    if not entries:
        return None
    prev = entries[-1]
    prev_titles = {x[title_key]: x for x in prev.get("top", []) if x.get(title_key)}
    return prev, prev_titles


def continuity(entries, rows, times=3):
    """最近 N 期里出现过的标题 → 出现次数（连续在榜 = 真趋势）。"""
    counts = {}
    for e in entries[-times:]:
        for x in e.get("top", []):
            t = x.get("title")
            if t:
                counts[t] = counts.get(t, 0) + 1
    return {r["title"]: counts.get(r["title"], 0) + 1 for r in rows if counts.get(r["title"])}


def my_coverage(mine, themes):
    """自己写过的主题词覆盖情况：篇数 + 平均阅读。"""
    out = {}
    for w in themes:
        hit = [m for m in mine if w.lower() in (m.get("title") or "").lower()]
        if hit:
            views = [int(m.get("views") or 0) for m in hit]
            out[w] = {"篇数": len(hit), "平均阅读": round(sum(views) / len(views)),
                      "最高阅读": max(views)}
        else:
            out[w] = {"篇数": 0, "平均阅读": 0, "最高阅读": 0}
    return out


# ---------------------------------------------------------------- 报告

def slugify(kw):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "-", kw.strip())
    return re.sub(r"-+", "-", s).strip("-") or "热榜"


def suggest_topics(rows, themes, mine, deep):
    """给 3 个可写选题：尽量挑"榜单热 + 我没写过/写得少"的主题词。"""
    my_by_theme = my_coverage(mine, themes) if mine else {}
    cand = []
    for w, n in themes.items():
        have = (my_by_theme.get(w) or {}).get("篇数", 0) if mine else 0
        gain = n * (1.0 / (1 + have))      # 榜单席位多、自己写得少 → 优先
        cand.append((gain, w, n, have))
    cand.sort(reverse=True)
    picks = []
    for _, w, n, have in cand[:3]:
        sample = next((r for r in rows if w.lower() in r["title"].lower()), None)
        rate = next((r for r in sorted(rows, key=lambda r: -(r["favorites"] / max(r["views"], 1)))
                     if w.lower() in r["title"].lower()), None)
        bench = rate or sample
        picks.append({
            "主题词": w, "榜单席位": n, "我写过": have,
            "对标文章": (bench or {}).get("title", ""),
            "对标链接": (bench or {}).get("url", ""),
            "对标指标": (f"浏览 {(bench or {}).get('views')} / 收藏 {(bench or {}).get('favorites')}"
                        f" / 收藏率 {pct((bench or {}).get('favorites') or 0, (bench or {}).get('views') or 0)}"),
        })
    return picks


def build_report(kw, collected_at, rows, deep_fps, themes, series, mine, drafts, idx_entries,
                 limit, deep_n, my_pages, profile, themes_all=None, show_all=False):
    L = []
    A = L.append
    prev = idx_entries[-1] if idx_entries else None
    cont = continuity(idx_entries, rows)
    covered = my_coverage(mine, themes) if mine else {}
    draft_cov = my_coverage(drafts, themes) if drafts else {}
    rel_rows = [r for r in rows if r.get("tier") in RELEVANT_TIERS]
    weak_rows = [r for r in rows if r.get("tier") == "弱相关"]
    off_rows = [r for r in rows if r.get("tier") == "无关"]
    themes_all = themes_all or themes

    A(f"# CSDN 热榜选题报告 · {kw} · {collected_at[:10]}")
    A("")
    A(f"> 采集时间：{collected_at} ｜ 榜单：**全站综合热榜**（热度排序 + 收藏排序两个视角）")
    A(f"> 赛道画像：**{profile['desc']}** ｜ 榜单口径：72h 热度、时更")
    A("> 数据来源：`blog.csdn.net/rank/list`（浏览器实采）｜ 精读与指纹**只在赛道相关文章里统计**")
    A("")

    # 一、速览
    A("## 一、本期速览（只列与你赛道相关的）")
    A("")
    total_fav = sum(r["favorites"] for r in rows)
    total_views = sum(r["views"] for r in rows)
    have_heat = [r for r in rows if r.get("rank_热度")]
    have_fav = [r for r in rows if r.get("rank_收藏")]
    overlap = {r["url"] for r in have_heat} & {r["url"] for r in have_fav}
    A(f"- 全榜 **{len(rows)} 篇**（热度榜 {len(have_heat)} + 收藏榜 {len(have_fav)}，两榜重合 {len(overlap)} 篇）；"
      f"其中**与你相关 {len(rel_rows)} 篇**、弱相关 {len(weak_rows)} 篇、无关 {len(off_rows)} 篇")
    A(f"- 相关文章合计浏览 {sum(r['views'] for r in rel_rows):,}、收藏 {sum(r['favorites'] for r in rel_rows):,}"
      f"（全榜合计浏览 {total_views:,}、收藏 {total_fav:,}）")
    if len(overlap) <= max(2, len(have_heat) // 10):
        A("- ⚠️ **两个排序几乎不重叠**：热度榜和收藏榜是两批完全不同的文章 —— 只看一个榜会漏掉一半视野")
    top_themes = "、".join(f"{k} {v} 篇" for k, v in list(themes.items())[:8]) or "—"
    A(f"- 你的赛道里主题分布：{top_themes}")
    A("")
    A("| 热度榜 | 收藏榜 | 赛道 | 标题 | 浏览 | 评论 | 收藏 | 收藏率 | 热度 |")
    A("|---:|---:|---|---|---:|---:|---:|---:|---:|")
    for r in rel_rows[:limit]:
        hit = "、".join((r.get("rel", {}).get("core") or []) + (r.get("rel", {}).get("learn") or []))
        A(f"| {r.get('rank_热度') or '—'} | {r.get('rank_收藏') or '—'} | {r['tier']} | "
          f"[{r['title'][:40]}]({r['url']}) | {r['views']:,} | {r['comments']} | {r['favorites']} | "
          f"{pct(r['favorites'], r['views'])} | {r['heat']:,} |")
    A("")
    A("> 命中词（判断依据，前 12 条）：")
    for r in rel_rows[:12]:
        hit = "、".join((r.get("rel", {}).get("core") or []) + (r.get("rel", {}).get("learn") or []))
        if hit:
            A(f"> - {r['title'][:34]} → {hit}")
    A("")
    if off_rows or weak_rows:
        A(f"**榜上很火但不在你赛道（{len(off_rows) + len(weak_rows)} 篇，不建议投入）**：")
        show = (off_rows + weak_rows) if show_all else (off_rows + weak_rows)[:10]
        for r in show:
            excl = "、".join(r.get("rel", {}).get("exclude") or [])
            A(f"- {r['title'][:52]}" + (f"（{excl}）" if excl else ""))
        if not show_all and len(off_rows) + len(weak_rows) > len(show):
            A(f"- …另有 {len(off_rows) + len(weak_rows) - len(show)} 篇（加 `--all` 全列）")
        A("")

    # 二、值得学的
    A(f"## 二、最值得学的 {len(deep_fps)} 篇（**只在你的赛道里挑**，按收藏率/热度交叠）")
    A("")
    if not deep_fps:
        A("（本次未抓正文：`--no-deep` 或抓取失败）")
    for i, f in enumerate(deep_fps, 1):
        A(f"### {i}. [{f['title'][:50]}]({f['url']})")
        A("")
        A(f"- **赛道**：{f.get('赛道', '—')}"
          + (f"（命中 {'、'.join(f.get('赛道命中') or [])}）" if f.get("赛道命中") else ""))
        A(f"- 榜单表现：热度 {f['榜单热度']:,}（热度榜第 {f.get('rank_热度') or '—'}）｜浏览 {f['榜单浏览']:,}"
          f"｜收藏 {f['榜单收藏']}（收藏率 {f['收藏率']}）｜评论 {f['榜单评论']}｜作者 {f['作者'][:16]}")
        A(f"- 结构：{f['字数(非空白)']} 字 ｜ H2 {f['二级标题']} 个（问句占比 {f['问句标题占比']}）"
          f"｜代码块 {f['代码块']} ｜表格 {f['表格']} ｜图片 {f['图片']} ｜数字密度 {f['每千字数字']}/千字"
          f"｜内链 {f['内链']} ｜段落 {f['段落数']}（均长 {f['平均段落长度']} 字）")
        if f.get("标签"):
            A(f"- 标签：{'、'.join(f['标签'][:8])}")
        if f.get("收录于"):
            A(f"- 被收录于：{'、'.join(f['收录于'][:6])}")
        A(f"- 开头 200 字：{str(f['开头 200 字'])[:160]}…")
        A("")

    # 三、指纹区间
    A("## 三、结构指纹区间（**只看你赛道的这几篇**，写作时照着靠）")
    A("")
    if deep_fps:
        A("| 指标 | 最小 | P25 | 中位数 | P75 | 最大 |")
        A("|---|---:|---:|---:|---:|---:|")
        for key in ("字数(非空白)", "二级标题", "代码块", "表格", "图片", "段落数",
                    "平均段落长度", "每千字数字", "内链"):
            b = stat_block(deep_fps, key)
            if b:
                A(f"| {key} | {b['min']} | {b['p25']} | {b['median']} | {b['p75']} | {b['max']} |")
        qs = [f["问句标题占比"] for f in deep_fps if f.get("问句标题占比") != "—"]
        A("")
        A(f"- 问句式 H2 占比样本：{('、'.join(qs)) or '—'}（实测爆款多为 0%，"
          "即**小标题用陈述句+数字**更常见）")
        A("- 这一节为什么必须过滤赛道：不做过滤时，ATE 芯片测试（8 个 H2、0 代码块）和 CAD 这类"
          "冷门爆款会把区间算歪 —— 那是在对着别人的赛道练基本功。")
    else:
        A("（未抓正文）")
    A("")

    # 四、主题词
    A("## 四、主题词分布与连续性（只统计你赛道的文章）")
    A("")
    A("| 主题词 | 相关席位 | 占比 | 连续在榜 |")
    A("|---|---:|---:|---:|")
    for w, n in list(themes.items())[:15]:
        A(f"| {w} | {n} | {pct(n, max(len(rel_rows), 1))} | {cont.get(w, '—')} |")
    A("")
    if cont:
        A("**连续在榜（最近 3 期出现过）**：")
        for t, c in sorted(cont.items(), key=lambda kv: -kv[1])[:8]:
            A(f"- ×{c} {t[:56]}")
        A("")
    else:
        A("（暂无历史快照，首次采集只能给出本期绝对值；多跑几期才看得出趋势）")
        A("")
    off_themes = {k: v for k, v in (themes_all or {}).items() if k not in themes}
    if off_themes:
        A("**平台在推、但不在你赛道（知道了就行，别投入）**："
          + "、".join(f"{k} {v}" for k, v in list(off_themes.items())[:10]))
        A("")

    # 五、账号交叉
    A("## 五、和你账号的交叉"
      + (f"（已发布最近 {my_pages} 页 / {len(mine)} 篇" + (f"，本地草稿 {len(drafts)} 篇" if drafts else "")
         + "）" if mine or drafts else ""))
    A("")
    if mine:
        A("| 主题词 | 赛道席位 | 已发布 | 已发平均阅读 | 在写（草稿） | 结论 |")
        A("|---|---:|---:|---:|---:|---|")
        for w, n in list(themes.items())[:12]:
            c = covered.get(w) or {"篇数": 0, "平均阅读": 0, "最高阅读": 0}
            d = (draft_cov.get(w) or {}).get("篇数", 0)
            if c["篇数"] == 0 and d:
                verdict = f"已在写（{d} 篇草稿），发完看数据"
            elif c["篇数"] == 0:
                verdict = "空白区，值得试"
            elif c["平均阅读"] >= 150:
                verdict = "有基础，可加重"
            else:
                verdict = "写过但数据一般"
            A(f"| {w} | {n} | {c['篇数']} | {c['平均阅读'] or '—'} | {d or '—'} | {verdict} |")
        blank = [w for w, n in themes.items()
                 if (covered.get(w) or {}).get("篇数", 0) == 0 and not (draft_cov.get(w) or {}).get("篇数")]
        writing = [w for w, n in themes.items() if (draft_cov.get(w) or {}).get("篇数")]
        A("")
        if writing:
            A(f"- **已在写（草稿箱）**：{'、'.join(writing)}")
        fam_draft = any(any(k.lower() in (d.get("title") or "").lower() for k in AI_FAMILY)
                        for d in drafts) if drafts else False
        if fam_draft:
            blank = [w for w in blank if w not in AI_FAMILY]
        A(f"- **完全空白**（既没发布、也没在写）：{'、'.join(blank[:12]) or '（无）'}"
          + ("（AI 家族相关词已排除，见下条）" if fam_draft else ""))
        # AI 家族合计：草稿标题里常只写 RAG/Agent，单看关键词会误判成"空白"
        if any(w in themes for w in AI_FAMILY):
            pub = sum(1 for m in mine
                      if any(k.lower() in (m.get("title") or "").lower() for k in AI_FAMILY))
            drf = sum(1 for m in drafts
                      if any(k.lower() in (m.get("title") or "").lower() for k in AI_FAMILY))
            seats = sum(v for k, v in themes.items() if k in AI_FAMILY)
            A(f"- **AI 家族合计**（AI/Agent/智能体/大模型/RAG/MCP/DeepSeek…）：赛道内 {seats} 席；"
              f"你已发布 {pub} 篇、在写 {drf} 篇")
            if drf and pub == 0:
                A("  → 结论：不是「完全空白」，而是**已起步但还没发**。把手上这两篇发出去拿到真实数据，"
                  "再决定要不要加码 —— 否则会重复写同一类内容。")
    else:
        A("（本次跳过：加 `--my-pages 3` 可采自己最近的文章做交叉；"
          "或先跑 `.\\csdn_stats.bat` 建立长期数据）")
    A("")

    # 六、选题建议
    A("## 六、三个可写选题（**都落在你的赛道里**：榜单热度 × 你的空白度）")
    A("")
    ai_theme = next((w for w in themes if w.lower() in ("ai", "agent", "大模型", "智能体", "llm")), None)
    for i, p in enumerate(suggest_topics(rows, themes, mine, deep_fps), 1):
        A(f"**{i}. 主题：{p['主题词']}**（赛道内 {p['榜单席位']} 席，你写过 {p['我写过']} 篇）")
        A(f"- 对标文章：[{p['对标文章'][:46]}]({p['对标链接']}) ｜ {p['对标指标']}")
        A(f"- 标题建议：用「{p['主题词']}」+ **疑问句 + 数字 + 具体现象**"
          f"（例：`{p['主题词']}为什么 X？我踩了 3 个坑`）")
        if ai_theme and p["主题词"] == ai_theme:
            A("- **你的差异化在哪（重要）**：不要跟着写「AI 是什么」这类通稿。你是 Go 后端，"
              "优势在**工程侧**：用 Go 接 Agent/MCP、给 AI 输出做验收与可观测、把向量库/推理服务"
              "接进现有后端。选题落在「后端视角的 AI」上，竞争面小得多。")
        A("- 硬要求：配 1 张结构示意图 + 1 张真实数据表/终端输出；每个结论挂一个可复现实验")
        A("")
    A("> 榜单只给方向，**标题必须自己造**：本账号有 20 篇同名《Docker居然还能这么用？？？》"
      "互相分流的教训，发布前体检里有「和自己撞标题」这一条。")
    A("")

    # 七、AI/Agent 学习路线（Go 后端视角）
    ai_rows = [r for r in rel_rows if (r.get("rel", {}).get("learn"))]
    A("## 七、AI/Agent 学习路线（按你是 Go 后端来排）")
    A("")
    if ai_rows:
        A(f"本期赛道相关文章里，AI/Agent 向的有 **{len(ai_rows)} 篇**。建议的学法不是「通读」，"
          "而是**从你已经会的东西切进去**：")
        A("")
        A("| 顺序 | 学什么 | 为什么先学它 | 本期对标文章 |")
        A("|---:|---|---|---|")
        path = [
            ("调用与集成", "把模型当成一个 HTTP 依赖：超时、重试、限流、成本核算 —— 这些你本来就会",
             ("sdk", "api", "接入", "调用", "手册"), 0),
            ("上下文工程", "RAG / 向量检索 / 切片策略，本质是「检索 + 排序」问题，和后端索引优化同源",
             ("rag", "向量", "知识库", "检索", "embedding"), 0),
            ("Agent 工程化", "工具调用、状态机、超时与失败重试、**输出验收**（你已经在写这方向的稿子）",
             ("agent", "智能体", "mcp", "工作流", "harness", "codex"), 0),
            ("部署与成本", "推理服务、并发、队列、缓存、可观测 —— 纯后端技能直接迁移",
             ("部署", "推理", "成本", "工程化", "harness"), -1),
        ]
        used = set()
        for i, (what, why, key_hints, fallback_idx) in enumerate(path, 1):
            cand = next((r for r in ai_rows if any(k in r["title"].lower() for k in key_hints)
                         and r["url"] not in used), None)
            if cand is None:
                cand = next((r for r in ai_rows if r["url"] not in used), ai_rows[min(fallback_idx, len(ai_rows) - 1)])
            used.add(cand["url"])
            A(f"| {i} | {what} | {why} | [{cand['title'][:34]}]({cand['url']}) |")
        A("")
        A("> 这份路线只依赖本期榜单里真实存在的 AI 文章；每学一块就写一篇"
          "「后端视角」的实测（和你那两篇 RAG/Agent 稿子同一个套路），比读十篇通稿有用。")
    else:
        A("（本期赛道相关文章里没抓到 AI/Agent 向的；换个时间再跑，或加 `--focus` 补关键词）")
    A("")

    # 八、对比
    A("## 八、和上期对比")
    A("")
    if prev:
        prev_titles = {x["title"] for x in prev.get("top", []) if x.get("title")}
        now_titles = {r["title"] for r in rows}
        new_in = sorted(now_titles - prev_titles)
        gone = sorted(prev_titles - now_titles)
        A(f"上期：{prev.get('collectedAt', '?')}（{prev.get('kw', '?')}，{len(prev_titles)} 篇）")
        A("")
        A(f"- **新上榜 {len(new_in)} 篇**：" + ("；".join(t[:30] for t in new_in[:6]) or "（无）"))
        A(f"- **掉榜 {len(gone)} 篇**：" + ("；".join(t[:30] for t in gone[:6]) or "（无）"))
        stay = sorted(now_titles & prev_titles)
        A(f"- 仍在榜 {len(stay)} 篇：" + ("；".join(t[:30] for t in stay[:6]) or "（无）"))
    else:
        A("（首次采集，无基线。多跑几期后这里会给出新上榜/掉榜/连续在榜）")
    A("")

    # 九、可信度
    A("## 九、数据可信度与边界")
    A("")
    A("- **热度 ≠ 阅读**：热度含平台对评论/收藏密度的加权，谈「多少人看过」只看浏览。")
    A("- **单次快照只是切片**：真正该跟的是「连续在榜」，本期已给出连续性标记。")
    A("- **榜单口径**：全站综合热榜 72h 热度、时更；不同榜更新频率不同，别把周榜当当日热点。")
    A("- **样本偏差**：榜单是「已经被推荐起来的文章」，不代表全部内容生态；冷启动好文不会出现。")
    A("- **抓正文只做结构统计**，不摘抄段落（版权）；数字对不上时以原文为准。")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="CSDN 热榜选题调研 → md 报告")
    ap.add_argument("--q", default="", help="关键词（决定文件名；不给就自动取本期 Top 主题词）")
    ap.add_argument("--limit", type=int, default=30, help="速览表里列多少条，默认 30")
    ap.add_argument("--deep", type=int, default=5, help="抓几篇正文做结构指纹，默认 5")
    ap.add_argument("--no-deep", action="store_true", help="完全不抓正文（快 3 倍）")
    ap.add_argument("--my-pages", type=int, default=3,
                    help="采自己账号最近几页做交叉（默认 3；0 = 跳过）")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--profile", default="go-backend", choices=sorted(PROFILES),
                    help="赛道画像（决定哪些热点跟你有关），默认 go-backend")
    ap.add_argument("--focus", nargs="*", default=[],
                    help="额外加入你的赛道词（例：--focus Raft etcd）")
    ap.add_argument("--exclude", nargs="*", default=[],
                    help="额外排除的领域词（例：--exclude 鸿蒙 ArkTS）")
    ap.add_argument("--all", action="store_true",
                    help="报告里也把「与你无关」的热点全列出来（默认只列计数与少量样例）")
    args = ap.parse_args()

    profile = dict(PROFILES[args.profile])
    if args.focus:
        profile["core"] = list(profile["core"]) + [w.lower() for w in args.focus]
    if args.exclude:
        profile["exclude"] = list(profile["exclude"]) + [w.lower() for w in args.exclude]
    log(f"赛道画像: {args.profile} —— {profile['desc']}")

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    index_path = out_dir / "index.json"
    raw_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        log(f"登录态: {logged}")

        page.goto(hot.RANK_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_function(
                """() => [...document.querySelectorAll('a[href*="/article/details/"]')]
                         .filter(a => (a.innerText || '').trim().length > 4).length >= 10""",
                timeout=30000)
            log("榜单已渲染")
        except Exception:
            log("等榜单超时，按当前 DOM 抓（可能不完整）")
        page.wait_for_timeout(1500)

        log("抓「热度排序」…")
        by_heat = collect_rank(page, "热度排序")
        log(f"  {len(by_heat)} 条｜第 1 名：{by_heat[0]['title'][:34] if by_heat else '—'}")
        log("抓「收藏排序」…")
        by_fav = collect_rank(page, "收藏排序")
        log(f"  {len(by_fav)} 条｜第 1 名：{by_fav[0]['title'][:34] if by_fav else '—'}")
        if by_heat and by_fav and [r["title"] for r in by_heat[:3]] == [r["title"] for r in by_fav[:3]]:
            log("  ! 两个排序的前 3 名完全一样：收藏排序可能没切换成功（报告里会照实说明）")

        rows = merge_sorts(by_heat, by_fav)
        log(f"合并去重后 {len(rows)} 篇")

        # 赛道过滤（先按标题判；抓到正文后带标签再判一次，tier 可能变化）
        for r in rows:
            rel = relevance(r, profile)
            r["tier"] = rel["tier"]
            r["rel"] = rel
        tally = {}
        for r in rows:
            tally[r["tier"]] = tally.get(r["tier"], 0) + 1
        log(f"赛道相关性: " + "、".join(f"{k} {v}" for k, v in
                                       sorted(tally.items(), key=lambda kv: -kv[1])))
        log(f"  相关（核心/学习）{sum(v for k, v in tally.items() if k in RELEVANT_TIERS)} 篇 —— "
            f"精读只从这些里挑")

        deep_fps = []
        if not args.no_deep and args.deep > 0:
            picks = pick_deep(rows, args.deep)
            log(f"精读 {len(picks)} 篇正文（只做结构统计，不摘抄）…")
            for r in picks:
                fp = deep_one(page, r)
                if fp:
                    fp["rank_热度"] = r.get("rank_热度")
                    rel2 = relevance({"title": fp["title"], "tags": fp.get("标签") or []}, profile)
                    fp["赛道"] = rel2["tier"]
                    fp["赛道命中"] = (rel2["core"] + rel2["learn"])[:5]
                    if rel2["tier"] != r["tier"]:
                        log(f"    (标签补充后修正：{r['tier']} → {rel2['tier']})")
                    r["tier"] = rel2["tier"]
                    r["rel"] = rel2
                    deep_fps.append(fp)
                    log(f"  ✓ [{rel2['tier']}] {fp['字数(非空白)']:>5} 字 / H2 {fp['二级标题']:>2} / "
                        f"代码 {fp['代码块']:>2} / 图 {fp['图片']:>2}  {fp['title'][:26]}")
                else:
                    log(f"  ✗ 取不到正文: {r['url'][-20:]}")

        mine = collect_my_titles(page, args.my_pages)
        b.close()

    drafts = scan_local_drafts()
    draft_titles = {d["title"] for d in drafts}
    moved = [m for m in mine if m.get("title") in draft_titles]
    if moved:
        # 内容管理页的列表里混着草稿（实测：刚存的两篇 AI 稿子被当成"已发布 2 篇、阅读 0"）。
        # 本地 posts/*.md 才是草稿的权威来源，按标题剔除，免得把"在写"算成"已发"。
        mine = [m for m in mine if m.get("title") not in draft_titles]
        log(f"  {len(moved)} 篇其实是草稿（标题与本地 posts/*.md 一致），已从「已发布」剔除")
    log(f"本地草稿 {len(drafts)} 篇（会与已发布分开统计）")

    # 主题词：报告与文件名都用「赛道内」的口径；全量主题只用来提示平台热点
    themes_all, series_all = theme_stats(rows)
    rel_rows = [r for r in rows if r.get("tier") in RELEVANT_TIERS]
    if rel_rows:
        themes, series = theme_stats(rel_rows)
    else:
        themes, series = themes_all, series_all
        log("! 本期没有赛道相关文章，报告会退化成全量口径")
    kw = args.q.strip() or top_keyword(themes, series, rel_rows or rows)
    collected_at = datetime.now().isoformat(timespec="seconds")
    idx_entries = load_index(index_path)

    report = build_report(kw, collected_at, rows, deep_fps, themes, series, mine, drafts,
                          idx_entries, args.limit, args.deep, args.my_pages, profile,
                          themes_all=themes_all, show_all=args.all)

    slug = slugify(kw)
    date = datetime.now().strftime("%Y-%m-%d")
    out_md = out_dir / f"{slug}-{date}.md"
    if out_md.exists():                      # 同日重复运行不覆盖
        out_md = out_dir / f"{slug}-{date}-{datetime.now():%H%M}.md"
    out_md.write_text(report, encoding="utf-8")

    raw = {"collectedAt": collected_at, "keyword": kw, "limit": args.limit,
           "rows": rows, "by_heat": by_heat, "by_fav": by_fav,
           "deep": deep_fps, "themes": themes, "series": series,
           "myArticles": mine[:80]}
    out_raw = raw_dir / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out_raw.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    entry = {"collectedAt": collected_at, "kw": kw, "report": out_md.name, "raw": out_raw.name,
             "top": [{"title": r["title"], "url": r["url"], "rank_热度": r.get("rank_热度"),
                      "heat": r["heat"], "views": r["views"], "favorites": r["favorites"]}
                     for r in rows[:args.limit]],
             "themes": themes}
    idx_entries.append(entry)
    index_path.write_text(json.dumps(idx_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"报告: {out_md}（{len(report)} 字符）")
    log(f"原始数据: {out_raw}")
    log(f"历史索引: {index_path}（累计 {len(idx_entries)} 期）")
    print(f"\n主题词 Top5: {list(themes.items())[:5]}")
    print(f"精读 {len(deep_fps)} 篇；自己账号文章 {len(mine)} 篇参与交叉")
    return 0


if __name__ == "__main__":
    sys.exit(main())
