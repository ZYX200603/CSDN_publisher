#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布前体检 —— 在文章上传之前，把「会明显损害阅读量、或会被 CSDN 拒掉」的问题挑出来。

分两级，边界很清楚：

  * **error（会拦下发布）**：确定性的硬伤。空白标题、还是骨架的正文、500 字以下的
    短文、一个标签都没有、标签超过上限、**和自己已经发过的文章撞标题**。
    这些不是"风格问题"，是发出去就亏、或者直接发不出去。
  * **warn（只提示，不拦）**：启发式建议。标题里没数字、没命中搜索关键词、
    连续问号这种噪声标点、没封面、摘要过长、当前时段不在流量高峰……
    它们只是"值得看一眼"，最终由你决定。

它不联网、不发布、不改任何东西，纯本地检查。发布时由 csdn_job.py 自动调用。

CLI 用法（单篇自查）:
    python csdn_lint.py posts\\docker-basics.md
    python csdn_lint.py posts\\docker-basics.md --tags Docker,容器 --summary "..."
    python csdn_lint.py posts\\docker-basics.md --json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402

ERROR, WARN = "error", "warn"

# 阈值集中放这里，想调只改这一块
MIN_TITLE = 5            # 短于这个长度算硬伤
SHORT_TITLE = 10         # 短于这个长度给提示
LONG_TITLE = 60          # 长于这个长度给提示（列表页和搜索结果会截断）
MIN_BODY_ERROR = 500     # 正文非空白字数下限（低于此判 error）
MIN_BODY_WARN = 1500     # 低于此只是提示
SHORT_SUMMARY = 30
LONG_SUMMARY = 160
PEAK_HOURS = set(range(6, 10)) | set(range(12, 15)) | set(range(18, 24))

# ---- 结构体检（面向 GEO / 可读性）：全部只提示，不拦发布 ----
# 依据：RAG 切片基本按「二级标题 + 首段」取块，读者也是先扫标题再读第一句。
# 这些阈值是启发式（不是任何平台规则），觉得太严太松就改这一块。
STRUCT_LEAD_CHARS = 200         # 开头多少字之内必须给结论
STRUCT_THROAT_CHARS = 300       # 开头多少字之内查"铺垫句"
STRUCT_CAPSULE_MAX = 120        # 二级标题下首段（答案胶囊）建议不超过这么多字
STRUCT_CAPSULE_MIN = 20         # 短于这个字数的句子不算"给了结论"
STRUCT_QUESTION_RATIO = 0.4     # 二级标题里问句占比下限
STRUCT_MIN_H2 = 3               # 二级标题少于这个数就不查问句占比
STRUCT_MIN_DIGITS_PER_1K = 8    # 每千字至少几个具体数字（自定基线）
STRUCT_PARA_MAX = 300           # 单段超过这个字数算"文字墙"
STRUCT_PARA_WALL_N = 3          # 出现几段超长段落才提示

QUESTION_HINTS = ("为什么", "怎么", "如何", "是什么", "什么是", "能不能", "要不要",
                  "哪些", "多少", "到底", "？", "?")
# 2026-10 热榜实测：头部爆款的二级标题问句占比是 0%，但都"够具体"
# （带数字/英文术语/原因·方案·清单这类词）。所以判定标准改成
# 「问句 或 具体」的合计占比，避免把好标题误判成差标题。
SPECIFIC_HINTS = ("原因", "方案", "清单", "对比", "步骤", "场景", "边界", "误区",
                  "成本", "代价", "拐点", "指标", "表", "坑", "流程", "原则", "证据")
THROAT_HINTS = ("上一篇文章", "上一篇", "在上一篇", "前面我们", "前面提到", "本文将",
                "接下来我们", "下面我们", "我们先来看", "众所周知", "话不多说",
                "废话不多说")

# 标题里的噪声标点：连续重复的问号/感叹号/句号，或 emoji
NOISE_PUNCT = re.compile(r"([?？!！。])\1{1,}")
EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _issue(level, code, msg):
    return {"level": level, "code": code, "msg": msg}


def norm_title(t):
    """去掉标点、空白、大小写差异，用来判"是不是同一个标题"。"""
    return re.sub(r"[\s\W_]+", "", str(t or "")).lower()


def load_published_titles():
    """从 csdn_stats 的快照里读已发布文章的标题（没有就返回空）。"""
    cands = [pc.OUTPUT_DIR / "stats" / "latest.json"]
    snap_dir = pc.OUTPUT_DIR / "stats" / "snapshots"
    if snap_dir.exists():
        cands += sorted(snap_dir.glob("*.json"), reverse=True)[:1]
    for f in cands:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        arts = (d.get("snapshot") or d).get("articles")
        if arts:
            return [a.get("title", "") for a in arts if a.get("title")]
    return []


def peak_hint():
    """用你自己的数据算「哪个时段发布的文章平均阅读最高」。

    没有快照、或样本太少时返回 None —— 宁可不提示，也不瞎猜。
    """
    try:
        import csdn_stats as st
    except Exception:
        return None
    snaps = st.load_snapshots()
    if not snaps:
        return None
    arts = snaps[-1].get("articles", [])
    buckets = {}
    for a in arts:
        pub = st.parse_time(a.get("publishTime"))
        if not pub:
            continue
        b = st.hour_bucket(pub)
        buckets.setdefault(b, []).append(a["views"])
    scored = [(k, len(v), sum(v) / len(v)) for k, v in buckets.items() if len(v) >= 3]
    if not scored:
        return None
    scored.sort(key=lambda x: -x[2])
    return scored[0]


def _strip_for_structure(md):
    """去掉代码块 / 表格 / HTML / 图片，只留散文部分，供结构检查用。"""
    t = re.sub(r"```.*?```", "", md, flags=re.S)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"^\s*\|.*$", "", t, flags=re.MULTILINE)
    return t


def _h2_sections(body):
    """按二级标题切段，返回 [(标题, 该节正文)]。"""
    parts = re.split(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE)
    return [(parts[i].strip(), parts[i + 1] or "")
            for i in range(1, len(parts) - 1, 2)]


def _first_para(section_text):
    """取一节里第一段**散文**（跳过代码块 / 表格 / 引用 / 列表 / 分隔线）。

    列表要跳过：编号清单（1. 2. 3.）和项目符号（- * +）天然就长，
    它们本来就不是"答案胶囊"，拿长度去评判只会误报。
    """
    for block in re.split(r"\n\s*\n", section_text or ""):
        b = block.strip()
        if not b or b.startswith("```") or b.startswith("|") or b.startswith(">"):
            continue
        if re.fullmatch(r"[-*_]{3,}", b):
            continue
        first_line = b.splitlines()[0].strip()
        if re.match(r"^([-*+]|\d+[.、)])\s+", first_line):
            continue
        return re.sub(r"\s+", "", b)
    return ""


def _is_question(h):
    return h.rstrip().endswith(("？", "?")) or any(k in h for k in QUESTION_HINTS)


def _is_specific(h):
    """标题够不够具体：带数字、带英文术语，或含"原因/方案/清单/成本"这类词。"""
    if re.search(r"[0-9]", h) or len(re.findall(r"[A-Za-z]{2,}", h)) >= 1:
        return True
    return any(k in h for k in SPECIFIC_HINTS)


def structure_issues(title, body, summary=""):
    """结构体检：问答体 / 答案胶囊 / 可提取元素 / 事实密度。返回的 issue 全是 warn。

    这一层不判断"对不对"，只判断"能不能被整块引用、能不能被一眼看懂"。
    """
    issues = []
    title = (title or "").strip()
    plain = _strip_for_structure(body or "")
    compact = re.sub(r"\s+", "", plain)
    if len(compact) < 200:          # 太短的文章这些指标没意义
        return issues

    head = compact[:STRUCT_THROAT_CHARS]
    throat = [w for w in THROAT_HINTS if w in head]
    if throat:
        issues.append(_issue(WARN, "struct-throat-clearing",
                             f"开头 {STRUCT_THROAT_CHARS} 字里出现铺垫句 {throat}；"
                             f"读者和 AI 切片都只想要结论，建议直接删掉、开门见山"))

    lead = compact[:STRUCT_LEAD_CHARS]
    if not [s for s in re.split(r"(?<=[。！？!?])", lead) if len(s) >= STRUCT_CAPSULE_MIN]:
        issues.append(_issue(WARN, "struct-no-lead",
                             f"开头 {STRUCT_LEAD_CHARS} 字里没有一句 {STRUCT_CAPSULE_MIN} 字以上的"
                             f"完整结论句；建议第一段就把结论/正确写法写出来"))

    secs = _h2_sections(body or "")
    if len(secs) >= STRUCT_MIN_H2:
        q = sum(1 for h, _ in secs if _is_question(h))
        sp = sum(1 for h, _ in secs if _is_question(h) or _is_specific(h))
        if sp / len(secs) < STRUCT_QUESTION_RATIO:
            issues.append(_issue(WARN, "struct-few-questions",
                                 f"二级标题 {len(secs)} 个：问句 {q} 个、够具体的 {sp} 个"
                                 f"（{sp / len(secs):.0%}，建议 ≥{STRUCT_QUESTION_RATIO:.0%}）；"
                                 f"标题要么写成用户会搜的问句，要么带数字/术语/「原因·方案·清单」这类具体词"))

    fat = []
    for h, txt in secs:
        p = _first_para(txt)
        if p and len(p) > STRUCT_CAPSULE_MAX:
            fat.append((h, len(p)))
    if fat:
        sample = "；".join(f"「{h[:16]}」{n} 字" for h, n in fat[:3])
        issues.append(_issue(WARN, "struct-no-answer-capsule",
                             f"{len(fat)} 个小节的第一段超过 {STRUCT_CAPSULE_MAX} 字（{sample}）；"
                             f"先给 40~{STRUCT_CAPSULE_MAX} 字的结论，再展开讲"))
    n_table = len(re.findall(r"^\s*\|.*\|\s*$", body or "", flags=re.MULTILINE))
    n_code = len(re.findall(r"^```", body or "", flags=re.MULTILINE)) // 2
    if n_table == 0 and n_code < 3:
        issues.append(_issue(WARN, "struct-no-extractable",
                             f"全文没有表格，代码块也只有 {n_code} 个；对比表格和可复制的完整代码"
                             f"是最容易被整段引用的素材"))

    # "文字墙"只看散文：表格行已被 _strip_for_structure 去掉，
    # 项目符号/编号列表天然就长（3 条 bullet 就 300+ 字），不算墙。
    paras = []
    for block in re.split(r"\n\s*\n", plain):
        b = block.strip()
        if not b:
            continue
        if re.match(r"^([-*+]|\d+[.、)])\s+", b):
            continue
        paras.append(re.sub(r"\s+", "", b))
    walls = [len(p) for p in paras if len(p) > STRUCT_PARA_MAX]
    if len(walls) >= STRUCT_PARA_WALL_N:
        issues.append(_issue(WARN, "struct-wall-of-text",
                             f"有 {len(walls)} 段超过 {STRUCT_PARA_MAX} 字（最长 {max(walls)} 字）；"
                             f"拆小段或加小标题，长段落既难读也难切片"))

    per_1k = len(re.findall(r"\d", compact)) * 1000 / len(compact)
    if per_1k < STRUCT_MIN_DIGITS_PER_1K:
        issues.append(_issue(WARN, "struct-low-fact-density",
                             f"每千字只有 {per_1k:.1f} 个数字（基线 {STRUCT_MIN_DIGITS_PER_1K}）；"
                             f"具体数字/字节/耗时比「比较快」「很多」更容易被引用"))

    s = (summary or "").strip()
    if s and title and len(s) >= 8 and s[:8] == title[:8]:
        issues.append(_issue(WARN, "summary-not-conclusion",
                             "摘要看起来是正文开头（和标题重复）；CSDN 会把它当页面描述，"
                             "建议手写 60~160 字的结论式摘要"))
    return issues


def lint_article(title, body, summary="", tags=None, cover="", category="",
                 keywords=(), published_titles=(), max_tags=None, now=None,
                 cover_base=None):
    """返回 issue 列表，按 error 在前排序。"""
    tags = [str(t).strip() for t in (tags or []) if str(t).strip()]
    keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    published = [t for t in (published_titles or []) if t]
    max_tags = int(max_tags or pc.CFG.get("max_tags") or 7)
    now = now or datetime.now()
    issues = []

    title = (title or "").strip()
    body = body or ""
    summary = (summary or "").strip()
    plain = re.sub(r"\s+", "", body)
    body_len = len(plain)

    # ---------------- error ----------------
    if not title:
        issues.append(_issue(ERROR, "title-empty", "标题是空的"))
    elif len(title) < MIN_TITLE:
        issues.append(_issue(ERROR, "title-too-short",
                             f"标题只有 {len(title)} 个字，短到看不出主题"))

    if not plain:
        issues.append(_issue(ERROR, "body-empty", "正文是空的"))
    elif "<!-- TODO" in body or "正文待撰写" in body:
        issues.append(_issue(ERROR, "body-scaffold", "正文还是 prepare 生成的骨架，没写内容"))
    elif body_len < MIN_BODY_ERROR:
        issues.append(_issue(ERROR, "body-too-short",
                             f"正文只有 {body_len} 字（非空白），低于 {MIN_BODY_ERROR} 字，"
                             f"容易被判低质量、也拿不到推荐"))

    if not tags:
        issues.append(_issue(ERROR, "tags-empty", "一个标签都没有"))
    elif len(tags) > max_tags:
        issues.append(_issue(ERROR, "tags-too-many",
                             f"标签 {len(tags)} 个，超过上限 {max_tags} 个，CSDN 会拒绝"))

    if title and published:
        key = norm_title(title)
        for t in published:
            if key and norm_title(t) == key:
                issues.append(_issue(ERROR, "title-duplicate",
                                     f"标题和已发布的《{t}》完全相同，两篇会互相分流"))
                break

    # ---------------- warn ----------------
    if title and MIN_TITLE <= len(title) < SHORT_TITLE:
        issues.append(_issue(WARN, "title-short", f"标题只有 {len(title)} 个字，偏短"))
    if len(title) > LONG_TITLE:
        issues.append(_issue(WARN, "title-long",
                             f"标题 {len(title)} 个字，超过 {LONG_TITLE} 字，"
                             f"列表页和搜索结果里会被截断"))
    if title and not re.search(r"\d", title):
        issues.append(_issue(WARN, "title-no-digit",
                             "标题里没有数字（\"六个场景\"\"3 个反直觉\"这类数字标题通常更被点开）"))
    if title and keywords and not any(k.lower() in title.lower() for k in keywords):
        issues.append(_issue(WARN, "title-no-keyword",
                             f"标题里没有出现搜索关键词 {keywords}，搜到你的概率会低"))
    if title and NOISE_PUNCT.search(title):
        issues.append(_issue(WARN, "title-punct-noise",
                             "标题里有连续重复的标点（？？？/！！！），显得不专业"))
    if title and EMOJI.search(title):
        issues.append(_issue(WARN, "title-emoji", "标题里有 emoji，技术文章里容易被判营销号"))

    if not summary:
        issues.append(_issue(WARN, "summary-empty", "摘要是空的，列表页会显示成正文开头"))
    elif len(summary) < SHORT_SUMMARY:
        issues.append(_issue(WARN, "summary-short", f"摘要只有 {len(summary)} 个字，太短"))
    elif len(summary) > LONG_SUMMARY:
        issues.append(_issue(WARN, "summary-long",
                             f"摘要 {len(summary)} 个字，列表页只显示前 {LONG_SUMMARY} 字左右，"
                             f"建议把结论放前面"))

    if not cover:
        issues.append(_issue(WARN, "cover-missing",
                             "没有封面图。列表页/推荐位是图文卡片，没图会明显掉点击"))
    else:
        cp = Path(cover)
        if not cp.is_absolute() and cover_base:
            cp = Path(cover_base) / cp
        if not cp.exists():
            issues.append(_issue(ERROR, "cover-missing-file", f"封面文件不存在: {cover}"))

    if plain and body_len < MIN_BODY_WARN:
        issues.append(_issue(WARN, "body-short",
                             f"正文 {body_len} 字，偏短（{MIN_BODY_WARN} 字以上更容易被推荐）"))

    if tags:
        hay = (title + "\n" + body).lower()
        stray = [t for t in tags if t.lower() not in hay]
        if stray:
            issues.append(_issue(WARN, "tag-irrelevant",
                                 f"标签 {stray} 在标题和正文里都没出现过，可能不贴题"))

    if now.hour not in PEAK_HOURS:
        msg = f"现在是 {now.hour} 点，不在常见流量高峰（6-9 / 12-14 / 18-23）"
        hint = peak_hint()
        if hint:
            msg += (f"；你自己数据里表现最好的时段是「{hint[0]}」"
                    f"（{hint[1]} 篇，平均 {hint[2]:.0f} 阅读）")
        issues.append(_issue(WARN, "offpeak", msg))

    # ---- 结构体检（问答体 / 答案胶囊 / 可提取元素 / 事实密度）：全是 warn ----
    issues += structure_issues(title, body, summary)

    issues.sort(key=lambda i: 0 if i["level"] == ERROR else 1)
    return issues


def summarize(issues):
    e = [i for i in issues if i["level"] == ERROR]
    w = [i for i in issues if i["level"] == WARN]
    return e, w


def lint_context(ctx, job=None, published_titles=None):
    """给 csdn_job.py 用：ctx 是 resolve_fields 的结果。"""
    job = job or {}
    kws = []
    for grp in ((job.get("search") or {}).get("keywords") or []):
        kws.append(grp)
    spec = ctx.get("spec") or {}
    for grp in ((spec.get("search") or {}).get("keywords") or []):
        kws.append(grp)
    return lint_article(
        title=ctx.get("title", ""),
        body=ctx.get("body", ""),
        summary=ctx.get("summary", ""),
        tags=ctx.get("tags") or [],
        cover=ctx.get("cover", ""),
        category=ctx.get("category", ""),
        keywords=kws,
        published_titles=published_titles if published_titles is not None
        else load_published_titles(),
        max_tags=job.get("maxTags"),
    )


def format_issues(issues, indent="    "):
    lines = []
    for i in issues:
        mark = "✖" if i["level"] == ERROR else "⚠"
        lines.append(f"{indent}{mark} [{i['code']}] {i['msg']}")
    return lines


def main():
    ap = argparse.ArgumentParser(description="发布前体检（不联网，只看本地文稿）")
    ap.add_argument("md", help="markdown 文稿路径")
    ap.add_argument("--tags", help="逗号分隔，覆盖文稿里的标签")
    ap.add_argument("--summary", help="覆盖文稿里的摘要")
    ap.add_argument("--cover", default=None, help="封面路径（默认不检查封面文件是否存在）")
    ap.add_argument("-k", "--keyword", action="append", default=[], help="搜索关键词，可重复")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    path = Path(args.md)
    if not path.exists():
        raise SystemExit(f"文件不存在: {path}")
    # 用发布链路自己的解析器：H1 兜底标题、front-matter 的 tags/cover/summary 都一致
    art = pc.load_article(path)
    tags = ([t.strip() for t in re.split(r"[,，]", args.tags) if t.strip()]
            if args.tags else art["tags"])
    summary = args.summary if args.summary is not None else art.get("summary", "")
    issues = lint_article(
        title=art["title"],
        body=art["body"],
        summary=summary,
        tags=tags,
        cover=args.cover if args.cover is not None else art.get("cover", ""),
        cover_base=art.get("base"),
        category=art.get("category", ""),
        keywords=args.keyword,
        published_titles=load_published_titles(),
    )
    if args.json:
        print(json.dumps(issues, ensure_ascii=False, indent=2))
    else:
        e, w = summarize(issues)
        print(f"{path.name}: {len(e)} 个 error，{len(w)} 个 warn")
        for line in format_issues(issues):
            print(line)
        if not issues:
            print("    ✅ 没有发现问题")
    return 1 if any(i["level"] == ERROR for i in issues) else 0


if __name__ == "__main__":
    sys.exit(main())
