#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
声明式 CSDN 发布任务执行器。

你只写一个 job.json 描述"这次要干什么"，换主题只改 JSON，不用碰代码。

字段取值语法（title / content / tags / cover / summary / category 通用）：
    "auto"            自动推导（各字段含义见下）
    "file:<路径>"      从文件读取；相对路径按 job.json 所在目录（默认 jobs/）解析
    其他任意字符串      当作字面值直接用

各字段 auto 的含义：
    title    取正文文件 front-matter 的 title，没有就取正文第一个 # 一级标题
    content  ★ 脚本没有写作能力，auto 时只生成一份骨架，
             必须由 agent（或你）把正文写进去才能发布
    tags     从该文对应的素材文章里挑出现频率最高的标签
    cover    按标题自动生成一张渐变封面（每篇换配色）
    summary  取正文纯文本前 110 字
    category 留空或 auto = 不放入任何栏目

命令：
    python csdn_job.py prepare jobs\\job.all.json    搜集素材 + 建正文骨架 + 生成封面
    python csdn_job.py status  jobs\\job.all.json     查看每篇的完成度（含发布前体检结果）
    python csdn_job.py publish jobs\\job.all.json     发布（默认先 dry-run，见 publish.dryRun）
    python csdn_job.py publish jobs\\job.all.json --only 1    只发第 1 篇
    python csdn_job.py publish jobs\\job.all.json --dry-run   强制只 dry-run
    python csdn_job.py publish jobs\\job.all.json --no-dry-run 强制正式发布
    python csdn_job.py publish jobs\\job.all.json --draft     存草稿而非公开发布
    python csdn_job.py publish jobs\\job.all.json --schedule "2026-10-09 19:30"   定时发布
    python csdn_job.py publish jobs\\job.all.json --force     体检有硬伤也照发（默认拦下）
    python csdn_job.py publish jobs\\job.all.json --no-lint   完全跳过发布前体检
（任务文件统一放在 jobs/ 下；只写文件名不带目录也能找到。
 不带参数跑 `python csdn_job.py` 会列出全部可用任务。）

发布方式由 job.json 的 publish.on 一行决定：
    "yes"       立即公开
    "no"        真实提交但只进草稿箱
    "schedule"  定时发布，时间写在 publish.schedule（省略 = auto 自动排期）
也支持只写 "schedule": "2026-10-09 19:30"（不写 on 也按定时走）。

发布前体检（csdn_lint.py）会自动跑：空标题、骨架正文、正文不足 500 字、
没标签、标签超限、封面文件不存在、和自己的历史文章撞标题 —— 这些会**拦下发布**；
标题没数字、没命中关键词、没封面、不在流量高峰等只提示不拦。
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
import csdn_research as cr  # noqa: E402
import make_cover as mc  # noqa: E402
import csdn_lint as lint  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

AUTO = "auto"
TODO_MARK = "<!-- TODO: 正文待撰写"
THEME_CYCLE = ["blue", "purple", "teal", "orange", "rose", "dark"]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


# ---------------------------------------------------------------- 配置解析

def parse_field(v, default=AUTO):
    """把配置里的一个字段值解析成 (kind, value)。"""
    if v is None:
        return (AUTO, None)
    s = str(v).strip()
    if s == "":
        return ("empty", "")
    if s.lower() == AUTO:
        return (AUTO, None)
    if s.lower().startswith("file:"):
        return ("file", s[5:].strip())
    return ("literal", s)


def resolve_path(p, base: Path):
    q = Path(p)
    return q if q.is_absolute() else (base / q)


def find_in_roots(name, roots=("", "jobs", "templates")):
    """依次到项目根目录、jobs/、templates/ 下找 name，返回第一个存在的路径。

    任务配置统一收在 jobs/ 下，但历史文档里写的 `job.xxx.json` 不带目录，
    这里兜住，免得老命令直接报"文件不存在"。
    """
    q = Path(name)
    if q.is_absolute():
        return q if q.exists() else None
    for r in roots:
        cand = (pc.ROOT / r / q) if r else (pc.ROOT / q)
        if cand.exists():
            return cand
    return None


# 顶层可以当"所有文章的默认值"的字段。单篇里写了同名字段就以单篇为准。
# content 故意不在这个列表里：否则 count:N 会让 N 篇文章共用同一份正文。
TOP_LEVEL_DEFAULTS = ("title", "tags", "cover", "coverTheme", "summary", "category")


def load_job(job_path: Path):
    job = json.loads(job_path.read_text(encoding="utf-8-sig"))
    base = job_path.parent
    search = job.get("search") or {}
    if not search.get("keywords"):
        raise SystemExit("job.json 缺少 search.keywords（搜索关键词必须手填）")
    if search.get("minViews") in (None, ""):
        raise SystemExit("job.json 缺少 search.minViews（最低阅读量必须手填）")
    arts = job.get("articles") or []
    if not arts:
        # 简写：只给 search + count，自动展开成 N 篇全 auto 的文章
        n = int(job.get("count") or 0)
        if n > 0:
            arts = [{"slug": f"article-{i}"} for i in range(1, n + 1)]
        else:
            raise SystemExit(
                "job.json 既没有 articles 也没有 count。\n"
                "  要几篇就写 \"count\": 3，或者手写 articles 数组。")

    # 顶层的 title/tags/cover/coverTheme/summary/category 当作"所有文章的默认值"，
    # 单篇里写了同名字段就以单篇为准。
    # 没有这一步的话，count 简写展开出来的文章只有 slug，顶层写 cover 会被
    # 静默忽略（解析 cover 时只读单篇 spec），表现就是"这个文件的 cover 改不动"。
    defaults = {k: job[k] for k in TOP_LEVEL_DEFAULTS if k in job}
    if defaults:
        arts = [dict(defaults, **a) for a in arts]

    job["articles"] = arts
    return job, base


def effective_search(art, job):
    s = dict(job.get("search") or {})
    s.update(art.get("search") or {})
    return {
        "keywords": s.get("keywords") or [],
        "minViews": int(s.get("minViews") or 1000),
        "wantSources": int(s.get("wantSources") or 6),
        "pages": int(s.get("pages") or 2),
    }


def search_key(s):
    return json.dumps({"k": sorted(s["keywords"]), "m": s["minViews"],
                       "w": s["wantSources"], "p": s["pages"]},
                      ensure_ascii=False, sort_keys=True)


def job_dir(job_path: Path):
    d = pc.OUTPUT_DIR / "job" / job_path.stem
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 工具

def read_first_h1(text):
    m = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


def plain_text(md, limit=400):
    """把 markdown 粗加工成纯文本，用于自动生成摘要。"""
    t = re.sub(r"<[^>]+>", "", md)                 # 去 HTML（含开头横幅）
    t = re.sub(r"```.*?```", "", t, flags=re.S)    # 去代码块
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.MULTILINE)  # 去标题符号
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)     # 去图片
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # 链接留文字
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"[`*_>|]", "", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()[:limit]


def slug_of(spec, idx):
    s = spec.get("slug") or spec.get("id")
    if s:
        return cr.slugify(s, 40)
    return f"article-{idx}"


def derive_tags(sources, max_n, keywords=()):
    """从素材标签 + 搜索关键词里挑标签。

    素材标签噪声很大：CSDN 作者能自定义任意标签，所以会出现
    「mysql怎样开启事务」这种整句式标签。两条过滤规则：
      - 短标签（≤6 字）一般是真的标签，直接留；
      - 长标签必须至少在 2 篇素材里出现，否则视为该文自造的噪声。
    另外关键词要拆出词来当候选（「mysql 事务」→ mysql + 事务），
    否则纯靠素材标签会跑题。
    """
    freq = {}
    for a in sources:
        for t in (a.get("tags") or []):
            t = t.strip()
            if 1 < len(t) <= 12:
                freq[t] = freq.get(t, 0) + 1
    # 短标签（≤6 字）一般是真的标签；长标签只有出现≥2次才可信。
    # 带空格的英文标签（"Service mesh"）额外放行——整句式噪声都是无空格的中文长串。
    freq = {t: c for t, c in freq.items() if len(t) <= 6 or " " in t or c >= 2}
    if not freq:
        return []          # 没素材标签就交回给调用方用 default_tags

    kws = [str(k).strip() for k in keywords if str(k).strip()]
    for t in list(freq):
        for k in kws:
            if k.lower() in t.lower() or t.lower() in k.lower():
                freq[t] += 3
    for k in kws:
        # 多词短语（"Service Mesh" / "mysql 事务"）拆成词当候选；
        # 单个词就直接用。下面的子串归并会把碎片重新合回长标签。
        parts = [p for p in re.split(r"[\s,，/]+", k) if p]
        for p in (parts if len(parts) > 1 else [k]):
            if 1 < len(p) <= 12:
                freq[p] = freq.get(p, 0) + 3

    # 子串归并：按权重降序（同权重长的优先）扫一遍，互为子串的只保留先到的，
    # 权重取较大值。这样 "索引优化" 会并回高权重的 "索引"，
    # 而 "Service"/"Mesh" 会并进 "Service mesh"。
    merged = {}
    for t, w in sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0])):
        lt = t.lower()
        host = next((p for p in merged if lt in p.lower() or p.lower() in lt), None)
        if host:
            merged[host] = max(merged[host], w)
        else:
            merged[t] = w

    ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:max_n]]


def write_front_matter(meta):
    def q(v):
        s = str(v)
        return '"' + s.replace('"', "'") + '"'
    lines = ["---", f"title: {q(meta['title'])}"]
    if meta.get("tags"):
        lines.append("tags: [" + ", ".join(meta["tags"]) + "]")
    if meta.get("cover"):
        lines.append(f"cover: {meta['cover']}")
    if meta.get("summary"):
        lines.append(f"summary: {q(meta['summary'])}")
    if meta.get("category"):
        lines.append(f"category: {meta['category']}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 字段推导

def resolve_fields(spec, idx, base, job, sources, job_d):
    """把一篇的六个字段全部推导出来。返回 dict，含 problems 列表。"""
    ctx = {
        "idx": idx,
        "slug": slug_of(spec, idx),
        "spec": spec,
        "base": base,
        "job": job,
        "sources": sources,
        "jobDir": job_d,
        "problems": [],
        "notes": [],
    }

    # ---- content ----
    ck, cv = parse_field(spec.get("content"))
    if ck == "file":
        ctx["content_file"] = resolve_path(cv, base)
    else:
        # auto / literal 都落到 posts/<slug>.md 上，由人/agent 写正文
        ctx["content_file"] = pc.ROOT / "posts" / f"{ctx['slug']}.md"

    body_raw = ""
    body_meta = {}
    if ctx["content_file"].exists():
        raw = ctx["content_file"].read_text(encoding="utf-8-sig")
        body_meta, body_raw = pc.parse_front_matter(raw)
    else:
        ctx["problems"].append(f"正文文件不存在: {ctx['content_file']}")
    ctx["body"] = body_raw

    scaffolded = TODO_MARK in body_raw
    if scaffolded or not body_raw.strip():
        ctx["problems"].append("正文还是空的/骨架，需要先写正文")

    # ---- title ----
    tk, tv = parse_field(spec.get("title"))
    if tk == "literal":
        ctx["title"] = tv
    elif tk == "file":
        ctx["title"] = resolve_path(tv, base).read_text(
            encoding="utf-8-sig").strip().splitlines()[0]
    else:
        ctx["title"] = (body_meta.get("title") or "").strip() or read_first_h1(body_raw)
        if not ctx["title"]:
            ctx["problems"].append("title 是 auto 但正文里没有 front-matter title 也没有 # 一级标题")

    # ---- tags ----
    raw_tags = spec.get("tags")
    if isinstance(raw_tags, list):
        # 直接写 JSON 数组是最自然的写法，必须支持。
        # 早期只认字符串：["MySQL","测试"] 会先被 str() 转成 Python 的列表表示
        # "['MySQL', '测试']"，再按逗号切，切出 "['MySQL'" 这种垃圾标签。
        ctx["tags"] = [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        gk, gv = parse_field(raw_tags)
        if gk == "literal":
            ctx["tags"] = [t.strip() for t in re.split(r"[,，]", gv) if t.strip()]
        elif gk == "file":
            ctx["tags"] = [t.strip() for t in re.split(
                r"[,，\n]", resolve_path(gv, base).read_text(encoding="utf-8-sig")) if t.strip()]
        else:
            max_n = int(job.get("maxTags") or pc.CFG.get("max_tags") or 5)
            kws = effective_search(spec, job)["keywords"]
            ctx["tags"] = derive_tags(sources, max_n, kws) or list(pc.CFG["default_tags"])
            ctx["notes"].append(f"标签按「素材标签 × 搜索关键词」自动推导: {ctx['tags']}")

    # ---- category ----
    # 单篇没写 category 时，回落到顶层的 category；再没有就是不放入任何栏目。
    # 同样支持数组（最多 3 个栏目），内部统一成逗号分隔的字符串。
    raw_cat = spec["category"] if "category" in spec else job.get("category")
    if isinstance(raw_cat, list):
        ctx["category"] = ",".join(str(c).strip() for c in raw_cat if str(c).strip())
    else:
        cck, ccv = parse_field(raw_cat)
        ctx["category"] = ccv if cck == "literal" else ""

    # 提前校验栏目名：CSDN 上栏目名必须与账号真实专栏名完全一致（含大小写），
    # 写错不会报错，只会「静默留空」——稿子照存、栏目是空的（见 known-issues 第 11 条）。
    # 所以在 prepare 阶段就按 config.json 的白名单对一遍，把问题拦在发布之前。
    _cat_known = [str(x).strip() for x in (pc.CFG.get("category_names") or []) if str(x).strip()]
    if ctx["category"] and _cat_known:
        _bad = [c for c in ctx["category"].split(",") if c.strip() and c.strip() not in _cat_known]
        if _bad:
            ctx["problems"].append(
                f"栏目名不在白名单里: {_bad}；账号真实专栏名只有 {_cat_known}。"
                f"CSDN 上栏目名写错会静默留空（稿子照存、栏目是空的），请照白名单改。")

    # ---- cover ----
    # 语义（与 category 保持一致：留空就是"不要"）：
    #   不写 cover 字段 / "auto"  -> 自动生成一张
    #   "" / null / "none" / "off" -> 不设置封面
    #   "file:..." 或任意路径      -> 用指定图片
    # 早期版本把留空也当 auto，会莫名其妙给文章配一张封面，已修正。
    cover_explicit = "cover" in spec
    cval = spec.get("cover")
    vk, vv = parse_field(cval)
    no_cover = (
        (cover_explicit and (cval is None or vk == "empty"))
        or (vk == "literal" and vv.lower() in ("none", "off", "no", "false", "null"))
    )
    if no_cover:
        ctx["cover"] = ""
        ctx["notes"].append("cover 留空/为 none，不设置封面")
    elif vk == "literal" or vk == "file":
        ctx["cover"] = str(resolve_path(vv, base))
    else:
        theme = spec.get("coverTheme") or THEME_CYCLE[(idx - 1) % len(THEME_CYCLE)]
        out = job_d / "covers" / f"{ctx['slug']}.png"
        ctx["cover"] = str(out)
        ctx["cover_theme"] = theme

    # ---- summary ----
    sk, sv = parse_field(spec.get("summary"))
    if sk == "literal":
        ctx["summary"] = sv
    elif sk == "file":
        ctx["summary"] = resolve_path(sv, base).read_text(
            encoding="utf-8-sig").strip().replace("\n", " ")[:256]
    else:
        ctx["summary"] = plain_text(plain_text(body_raw, 2000), 110).replace("\n", " ")
        if not ctx["summary"]:
            ctx["notes"].append("摘要 auto 但正文为空，先写正文")

    return ctx


def ensure_cover(ctx):
    """cover 是 auto 时按标题生成封面（幂等：已存在就跳过）。"""
    if "cover_theme" not in ctx:
        return
    out = Path(ctx["cover"])
    if out.exists():
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    top, bottom, accent = mc.THEMES[ctx["cover_theme"]]
    from PIL import Image, ImageDraw  # 局部导入，避免无 Pillow 时整个模块挂掉

    img = mc.gradient(top, bottom)
    d = ImageDraw.Draw(img, "RGBA")
    for cx, cy, r, a in [(980, 130, 320, 26), (1090, 640, 260, 20), (150, 720, 220, 16)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (a,))
    d.rounded_rectangle([70, 190, 82, 490], radius=6, fill=accent)

    pad, max_w = 130, mc.W - 130 - 90
    f_title = mc.load_font(mc.FONT_BOLD, 62)
    lines = mc.wrap_cjk(ctx["title"], f_title, max_w, d)[:4]
    line_h = 84
    y = (mc.H - len(lines) * line_h) // 2 - 20
    for ln in lines:
        d.text((pad, y), ln, font=f_title, fill=(255, 255, 255))
        y += line_h
    footer = (ctx["job"].get("coverFooter") or "").strip()
    if footer:
        f = mc.load_font(mc.FONT_REG, 22)
        w = d.textlength(footer, font=f)
        d.text((mc.W - 70 - w, mc.H - 70), footer, font=f, fill=(200, 210, 225))
    img.save(out, "PNG")


# ---------------------------------------------------------------- 命令

def match_cached_group(cached_keys, cfg):
    """在缓存里找一组与当前搜索条件"合格判定"相同的素材。

    只有 keywords + minViews 决定**哪些文章算合格**；wantSources / pages 只决定
    **搜多深**。所以匹配只比前两者——只调深度的改动不该让缓存作废、更不该阻塞发布。
    返回 (命中的缓存键, 是否只是深度参数不同)。
    """
    want_k = sorted(cfg["keywords"])
    for k in cached_keys:
        try:
            c = json.loads(k)
        except Exception:
            continue
        if sorted(c.get("k") or []) == want_k and c.get("m") == cfg["minViews"]:
            loose = c.get("w") != cfg["wantSources"] or c.get("p") != cfg["pages"]
            return k, loose
    return None, False


def load_sources(jd, job, specs):
    """读取素材缓存，检查是否还匹配当前搜索条件。

    用户在 job.json 里改了 keywords / minViews 之后，缓存就是旧的了。
    不检查的话 publish 会静默用空素材、标签悄悄退回默认值，从日志上几乎看不出来。
    """
    sj = jd / "sources.json"
    if not sj.exists():
        return {}, []
    data = json.loads(sj.read_text(encoding="utf-8"))
    groups = data.get("groups", {}) or {}
    cached_keys = list(groups.keys())

    resolved, stale, loose = {}, [], []
    for i, spec in specs:
        cfg = effective_search(spec, job)
        hit, is_loose = match_cached_group(cached_keys, cfg)
        if hit is None:
            stale.append((i, cfg))
            continue
        resolved[search_key(cfg)] = groups[hit]
        if is_loose:
            loose.append((i, cfg, json.loads(hit)))

    for i, cfg in stale:
        log(f"⚠️  素材缓存已过期：当前搜索条件是 {cfg['keywords']}（≥{cfg['minViews']} 阅读，"
            f"对应文章 {i}），但缓存里没有这一组")
    if stale:
        log("⚠️  请重新跑 prepare。否则标签 auto 会退回默认标签，素材也读不到")
    for i, cfg, c in loose:
        log(f"提示：文章 {i} 的 wantSources/pages 与缓存不同"
            f"（缓存 {c.get('w')}/{c.get('p')}，当前 {cfg['wantSources']}/{cfg['pages']}）；"
            f"沿用现有素材。想按新参数重搜就跑一次 prepare")

    return resolved, stale


def cmd_prepare(args):
    job_path = Path(args.job).resolve()
    job, base = load_job(job_path)
    jd = job_dir(job_path)

    # --only：只处理指定序号的文章（分批补素材用），序号按 job 里的原始顺序算
    only = set(args.only) if getattr(args, "only", None) else None
    if only:
        log(f"--only {sorted(only)}：只处理这些篇目，其余沿用已有缓存")

    # 1) 按不同的搜索配置分组跑素材搜集
    groups = {}
    for i, spec in enumerate(job["articles"], 1):
        if only and i not in only:
            continue
        s = effective_search(spec, job)
        groups.setdefault(search_key(s), {"cfg": s, "idxs": []})["idxs"].append(i)
    log(f"共 {len(job['articles'])} 篇文章（本次处理 {sum(len(g['idxs']) for g in groups.values())} 篇），"
        f"{len(groups)} 组不同的搜索条件")

    sources_by_group = {}
    with sync_playwright() as p:
        b = pc.Browser(p)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        if not logged:
            log("警告：登录态无效，素材搜集可能被限制。先跑 csdn.bat login")
        for key, g in groups.items():
            cfg = g["cfg"]
            log(f"--- 搜索组（文章 {g['idxs']}）: {cfg['keywords']} ≥{cfg['minViews']} 阅读")
            res = cr.research(page, cfg["keywords"], cfg["minViews"],
                              cfg["wantSources"], cfg["pages"],
                              out_dir=jd / "sources")
            sources_by_group[key] = res["articles"]
        b.close()

    # 合并旧缓存：--only 分批跑时，未选中组的素材必须留在文件里，不能被覆盖丢掉
    prev = {}
    sp_path = jd / "sources.json"
    if sp_path.exists():
        try:
            prev = json.loads(sp_path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    merged_groups = {**prev.get("groups", {}), **sources_by_group}
    merged_cfg = {**prev.get("configs", {}), **{k: g["cfg"] for k, g in groups.items()}}
    sp_path.write_text(json.dumps(
        {"groups": merged_groups, "configs": merged_cfg},
        ensure_ascii=False, indent=2), encoding="utf-8")
    if only and prev:
        log(f"已合并旧素材缓存：本次 {len(sources_by_group)} 组 + 历史 {len(prev.get('groups', {}))} 组"
            f" → 共 {len(merged_groups)} 组")

    # 2) 逐篇推导字段、建骨架、生成封面
    rows = []
    for i, spec in enumerate(job["articles"], 1):
        cfg = effective_search(spec, job)
        srcs = merged_groups.get(search_key(cfg), sources_by_group.get(search_key(cfg), []))
        ctx = resolve_fields(spec, i, base, job, srcs, jd)

        if only and i not in only:
            rows.append(ctx)  # 未选中的篇目：只进清单，不动它的骨架和封面
            continue

        f = ctx["content_file"]
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(
                f"{TODO_MARK}：写完后删掉这一行，然后跑 "
                f"python csdn_job.py publish jobs/{job_path.name} -->\n\n"
                f"# 在这里写文章的一级标题（title 为 auto 时会取用它）\n\n"
                f"正文写在这里。\n\n"
                f"## 参考资料（写完后请删除本节）\n\n"
                + "\n".join(f"- [{a['title']}]({a['url']})  {a['views']:,} 阅读"
                            for a in srcs[:8])
                + "\n",
                encoding="utf-8-sig")
            ctx["notes"].append(f"已生成正文骨架: {f}")
            ctx["problems"] = [p for p in ctx["problems"] if "不存在" not in p]
        else:
            # resolve_fields 里已经判断过「骨架/空正文」，这里只清掉文件不存在的噪音
            ctx["problems"] = [p for p in ctx["problems"] if "不存在" not in p]

        if ctx["title"]:
            try:
                ensure_cover(ctx)
            except Exception as e:
                log(f"  封面生成失败: {e}")
        rows.append(ctx)

    manifest(job_path, jd, rows, merged_groups, groups)

    need = [r for r in rows if r["problems"]]
    log("=" * 62)
    log(f"prepare 完成。{len(rows) - len(need)}/{len(rows)} 篇已就绪")
    for r in need:
        log(f"  待处理 第{r['idx']}篇 [{r['slug']}]: {'; '.join(r['problems'])}")
    log(f"清单: {jd / 'manifest.md'}")
    log(f"正文骨架在 posts/ 下，写完后跑: python csdn_job.py publish jobs/{job_path.name}")
    log("=" * 62)
    return 0


def manifest(job_path, jd, rows, sources_by_group, groups, plan=None):
    lines = [f"# 任务清单：{job_path.name}", "",
             f"- 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
             f"- 文章数: {len(rows)}", ""]
    if plan:
        lines += ["## 定时发布排期", ""] + [f"- {ln}" for ln in format_plan(plan)] + [""]
    for r in rows:
        when = ""
        if plan and 1 <= r["idx"] <= len(plan):
            when = f"{plan[r['idx'] - 1][0]:%Y-%m-%d %H:%M}"
        lines += [f"## 第 {r['idx']} 篇 — {r['title'] or '(标题待定)'}", "",
                  f"- slug: `{r['slug']}`",
                  f"- 正文文件: `{r['content_file']}`",
                  f"- 标签: {r['tags']}",
                  f"- 封面: `{r['cover'] or '(不设置封面)'}`",
                  f"- 摘要: {r['summary'][:60]!r}",
                  f"- 栏目: {r['category'] or '(不放入任何栏目)'}",
                  f"- 定时发布: {when or '(不定时)'}",
                  f"- 状态: {'❌ ' + '; '.join(r['problems']) if r['problems'] else '✅ 就绪'}"]
        if r["notes"]:
            lines += [f"- 备注: " + "; ".join(r["notes"])]
        lines.append("")
    (jd / "manifest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (jd / "manifest.json").write_text(json.dumps(
        [{"idx": r["idx"], "slug": r["slug"], "title": r["title"],
          "contentFile": str(r["content_file"]), "tags": r["tags"],
          "cover": r["cover"], "summary": r["summary"], "category": r["category"],
          "schedule": (f"{plan[r['idx'] - 1][0]:%Y-%m-%d %H:%M}"
                       if plan and 1 <= r["idx"] <= len(plan) else ""),
          "problems": r["problems"], "notes": r["notes"]} for r in rows],
        ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_status(args):
    job_path = Path(args.job).resolve()
    job, base = load_job(job_path)
    jd = job_dir(job_path)
    specs = list(enumerate(job["articles"], 1))
    sources_by_group, stale = load_sources(jd, job, specs)

    # 配置了定时发布就把排期一并显示出来（status 不联网、不改任何东西）
    pub = job.get("publish") or {}
    sc = pub.get("schedule")
    if isinstance(sc, str) and not sc.strip():
        sc = None
    if sc is None and str(pub.get("on", "")).strip().lower() in ON_SCHEDULE:
        sc = "auto"
    plan, plan_problems = (None, [])
    if sc is not None:
        plan, plan_problems = schedule_plan(sc, len(job["articles"]), job)
        log(f"定时发布计划（publish.schedule = {sc!r}）:")
        for line in format_plan(plan):
            log("  " + line)
        for p in plan_problems:
            log("  ✖ " + p)
        if plan_problems:
            log("  ↑ 这些问题会拦住发布，先改 job.json")

    print(f"{'#':<3}{'slug':<28}{'标题':<34}{'状态'}")
    print("-" * 96)
    pub_titles = lint.load_published_titles()
    rows, detail = [], []
    for i, spec in specs:
        cfg = effective_search(spec, job)
        srcs = sources_by_group.get(search_key(cfg), [])
        ctx = resolve_fields(spec, i, base, job, srcs, jd)
        rows.append(ctx)
        title = (ctx["title"] or "(待定)")[:32]
        if ctx["problems"]:
            status = "❌ " + "; ".join(ctx["problems"])
        else:
            errs, warns = lint.summarize(lint.lint_context(ctx, job, pub_titles))
            if errs:
                status = f"⛔ 体检 {len(errs)} 个硬伤，publish 会被拦下"
                detail.append((ctx, errs, warns))
            elif warns:
                status = f"✅ 就绪（{len(warns)} 条建议）"
                detail.append((ctx, [], warns))
            else:
                status = "✅ 就绪"
        print(f"{i:<3}{ctx['slug']:<28}{title:<34}{status}")
    print()
    for ctx, errs, warns in detail:
        log(f"第{ctx['idx']}篇 [{ctx['slug']}]")
        for line in lint.format_issues(errs + warns, indent="    "):
            log(line)
    # 顺手刷新清单：否则 status 看到的和 manifest.md 里的会不一致（曾因此误判过）
    manifest(job_path, jd, rows, sources_by_group, None, plan)
    log(f"素材组: {len(sources_by_group)} 组；清单: {jd / 'manifest.md'}")
    return 0


ON_YES = {"yes", "y", "true", "1", "on", "是", "发布", "publish"}
ON_NO = {"no", "n", "false", "0", "off", "否", "草稿", "draft"}
ON_SCHEDULE = {"schedule", "scheduled", "later", "cron", "定时", "定时发布", "排期"}


def parse_gap(g, default="1d"):
    """把「篇与篇之间的间隔」解析成 timedelta：1d / 12h / 90m / 0。"""
    s = str(default if g in (None, "") else g).strip().lower()
    if s in ("0", "none", "no", "off", "无", "不间隔"):
        return timedelta(0)
    m = re.fullmatch(r"\+?\s*(\d+(?:\.\d+)?)\s*(days?|d|天|hours?|h|小时|minutes?|min|m|分钟)?", s)
    if not m:
        raise SystemExit(f"看不懂的 scheduleGap: {g!r}（用 1d / 12h / 90m / 0）")
    n, unit = float(m.group(1)), (m.group(2) or "d")
    if unit.startswith("d") or unit == "天":
        return timedelta(days=n)
    if unit.startswith("h") or unit == "小时":
        return timedelta(hours=n)
    return timedelta(minutes=n)


def auto_schedule_first(now=None):
    """自动排期的第一篇时间：优先你自己数据里表现最好的时段，否则晚间 19:00。

    区间下限直接取 CSDN 允许的最早时间（现在 +4h10m），再对齐到整点。
    """
    now = now or datetime.now()
    lo, _ = pc.schedule_window(now)
    hour, why = 19, "默认晚间 19:00（复盘数据还不够，先用常见高峰）"
    try:
        import csdn_lint as lint
        hint = lint.peak_hint()          # ("18-20 晚间", 篇数, 平均阅读) 或 None
        if hint:
            m = re.match(r"(\d{1,2})", str(hint[0]))
            if m:
                hour = int(m.group(1))
                why = (f"你自己数据里阅读最好的时段「{hint[0]}」"
                       f"（{hint[1]} 篇，平均 {hint[2]:.0f} 阅读）")
    except Exception:
        pass
    cand = pc.align_step(lo.replace(hour=hour, minute=0, second=0, microsecond=0))
    if cand < lo:
        cand += timedelta(days=1)
    return cand, why


def plan_schedule_times(schedule, count, gap=None, now=None):
    """把 publish.schedule 变成 count 个时间点，返回 [(datetime, 说明)]。

    schedule 支持三种写法：
        "auto" / true                          自动排期（第一篇按 auto_schedule_first，
                                               后面每篇 +gap，默认 1 天一篇）
        "2026-10-09 19:30" / "+26h"            这一批都用同一个时间
        ["auto", "+50h", "2026-10-12 09:00"]   逐篇指定；写少了就按 gap 继续顺延
    """
    now = now or datetime.now()
    gap = gap if gap is not None else timedelta(days=1)

    def is_auto(v):
        return v is True or str(v).strip().lower() in ("auto", "true", "yes", "自动")

    def one(v, prev):
        if is_auto(v):
            # auto 只决定"第一篇"：已经有前一篇时按 gap 顺延，否则每篇都会算出同一个时间
            if prev is not None:
                return prev + gap, f"顺延 {gap}"
            dt, why = auto_schedule_first(now)
            return dt, f"自动排期：{why}"
        if v in (None, "") and prev is not None:
            return prev + gap, f"顺延 {gap}"
        dt = pc.align_step(pc.parse_schedule_when(v, now))
        return dt, "按配置指定"

    if isinstance(schedule, (list, tuple)):
        items = list(schedule)
    else:
        items = [schedule] * count          # 一个时间 = 整批都用它

    plan, prev = [], None
    for i in range(count):
        v = items[i] if i < len(items) else None
        dt, note = one(v, prev)
        plan.append((dt, note))
        prev = dt
    return plan


def schedule_plan(schedule, count, job, now=None):
    """算出排期并校验窗口，返回 (plan, problems)。problems 非空就别发。"""
    now = now or datetime.now()
    pub = job.get("publish") or {}
    gap = parse_gap(pub.get("scheduleGap") or job.get("scheduleGap"))
    plan = plan_schedule_times(schedule, count, gap, now)
    problems = []
    for i, (dt, _) in enumerate(plan, 1):
        ok, why = pc.check_schedule_window(dt, now)
        if not ok:
            problems.append(f"第{i}篇 {why}")
    if len(plan) > 1 and len({dt for dt, _ in plan}) == 1:
        problems.append(f"{len(plan)} 篇会被排在同一时刻 {plan[0][0]:%Y-%m-%d %H:%M}，"
                        f"建议在 publish.scheduleGap 里错开（如 1d / 6h）")
    return plan, problems


def format_plan(plan):
    return [f"第{i}篇 {dt:%Y-%m-%d %H:%M} 周{'一二三四五六日'[dt.weekday()]}（{note}）"
            for i, (dt, note) in enumerate(plan, 1)]


def fmt_gap(td):
    """把间隔写得好看点：1 天 / 6 小时 / 30 分钟。"""
    secs = int(td.total_seconds())
    if not secs:
        return "不间隔"
    if secs % 86400 == 0:
        return f"{secs // 86400} 天"
    if secs % 3600 == 0:
        return f"{secs // 3600} 小时"
    return f"{secs // 60} 分钟"


def resolve_publish_mode(pub, args):
    """由 job.json 里的 publish 段决定这次是发、存草稿还是定时发。

        "publish": { "on": "yes" }                     → 直接发布到博客
        "publish": { "on": "no" }                      → 真实提交，但只进 CSDN 草稿箱
        "publish": { "on": "schedule",
                     "schedule": "2026-10-09 19:30" }  → 定时发布（到点自动公开）
        "publish": { "on": "schedule", "schedule": "auto" } 或省略 schedule → 自动排期
        "publish": { "schedule": "..." }                → 写了时间就按定时走，不用再改 on
        publish.scheduleGap：schedule=auto 且多篇时，篇与篇的间隔（默认 1d）

    不写 on、也不写 schedule，或写了无法识别的值 → 退回老的 publish.dryRun / publish.draft。

    命令行仍然最高优先级：--dry-run 绝不提交；--draft 强制存草稿；--schedule WHEN 强制定时。
    返回 (dry, draft, mode, schedule)。
    """
    mode = str(pub.get("on", "")).strip().lower()

    schedule = getattr(args, "schedule", None)
    if schedule is None:
        schedule = pub.get("schedule")
    if isinstance(schedule, str) and not schedule.strip():
        schedule = None
    if mode in ON_SCHEDULE and schedule is None:
        schedule = "auto"          # on: schedule 但没写时间 → 自动排期

    explicit = mode in ON_YES or mode in ON_NO or mode in ON_SCHEDULE or schedule is not None

    if args.dry_run is not None:
        dry = args.dry_run
    elif explicit:
        dry = False          # 开关已经明确表态：这次要真实提交
    else:
        dry = bool(pub.get("dryRun", True))

    if schedule is not None:
        # 定时发布既不是"立刻发"也不是"存草稿"，draft 必须为 False
        return dry, False, mode, schedule

    if args.draft:
        draft = True
    elif mode in ON_YES:
        draft = False
    elif mode in ON_NO:
        draft = True
    else:
        draft = bool(pub.get("draft", False))
    return dry, draft, mode, None


def cmd_publish(args):
    job_path = Path(args.job).resolve()
    job, base = load_job(job_path)
    jd = job_dir(job_path)

    pub = job.get("publish") or {}
    dry, draft, mode, schedule = resolve_publish_mode(pub, args)

    # 定时发布：先把整批的时间表算出来并校验窗口，越界就一篇都不发
    plan = None
    if schedule is not None:
        plan, problems = schedule_plan(schedule, len(job["articles"]), job)
        log(f"定时发布计划（共 {len(plan)} 篇，CSDN 允许区间：现在 +4h ~ +6 天）:")
        for line in format_plan(plan):
            log("  " + line)
        if problems:
            log("定时计划有问题，已中止，没有启动浏览器：")
            for p in problems:
                log("  ✖ " + p)
            return 1
        if mode in ON_NO:
            log("注意：publish.on 写的是 no（存草稿），但你给了 schedule，本次按定时发布走")
        if dry:
            log("发布开关: dry-run —— 只把定时弹窗填好，不提交任何内容")
        else:
            log("发布开关: 定时发布 —— 到点由 CSDN 自动公开（提交后仍要过审核）")
    elif dry:
        log("发布开关: dry-run —— 只演练，不提交任何内容")
    elif draft:
        log("发布开关: 存草稿 —— 只进 CSDN 草稿箱，不公开发布")
    else:
        log(f"发布开关: 直接发布 —— 发完即公开{'' if mode else '（未写 publish.on）'}")

    specs = list(enumerate(job["articles"], 1))
    if args.only:
        specs = [s for s in specs if s[0] in args.only]
        if not specs:
            raise SystemExit(f"--only {args.only} 没匹配到任何文章")

    # 素材缓存必须与当前搜索条件匹配，否则标签会静默退化
    sources_by_group, stale = load_sources(jd, job, specs)
    if stale and not args.allow_stale_sources:
        raise SystemExit(
            "搜索条件和素材缓存不一致，已中止。\n"
            "  先跑:  python csdn_job.py prepare " + job_path.name + "\n"
            "  确实要用旧素材：加 --allow-stale-sources")

    # 先把所有要发的都推导一遍，有问题的直接拦下
    ready, blocked = [], []
    for i, spec in specs:
        cfg = effective_search(spec, job)
        ctx = resolve_fields(spec, i, base, job,
                             sources_by_group.get(search_key(cfg), []), jd)
        (blocked if ctx["problems"] else ready).append(ctx)

    if blocked:
        log("以下文章未就绪，已跳过：")
        for r in blocked:
            log(f"  第{r['idx']}篇 [{r['slug']}]: {'; '.join(r['problems'])}")
    if not ready:
        log("没有可发布的文章，先跑 prepare 并写好正文")
        return 1

    # ---- 发布前体检：硬伤拦下，建议只提示 ----
    # 拦的是"发出去就亏"的确定性错误（空标题、骨架正文、撞标题、标签超限…），
    # 不做风格审查。--no-lint 完全跳过，--force 只跳过拦截、仍然打印问题。
    if not getattr(args, "no_lint", False):
        pub_titles = lint.load_published_titles()
        blocked_lint = []
        for ctx in ready:
            issues = lint.lint_context(ctx, job, pub_titles)
            errs, warns = lint.summarize(issues)
            if plan:
                # 定时发布时"现在不在流量高峰"这条没意义 —— 发布时间是排期定的
                warns = [w for w in warns if w["code"] != "offpeak"]
            ctx["_lint_errs"], ctx["_lint_warns"] = errs, warns
            if warns:
                log(f"体检建议（不拦发布）· 第{ctx['idx']}篇 {ctx['title'][:24]}")
                for line in lint.format_issues(warns, indent="    "):
                    log(line)
            if errs:
                blocked_lint.append((ctx, errs))
        if blocked_lint:
            log("=" * 62)
            log("发布前体检发现硬伤：")
            for ctx, errs in blocked_lint:
                log(f"  第{ctx['idx']}篇 [{ctx['slug']}]")
                for line in lint.format_issues(errs, indent="      "):
                    log(line)
            if not getattr(args, "force", False):
                log("已中止，没有提交任何内容。改好之后重跑；"
                    "确认这些不是问题就加 --force 跳过拦截。")
                return 1
            log("⚠ 已指定 --force，跳过以上拦截，继续发布。")
        else:
            log(f"发布前体检通过（{len(ready)} 篇）")

    if len(ready) > 1 and not dry and not draft and not plan:
        log(f"注意：本次要正式发布 {len(ready)} 篇。CSDN 有每日发文额度，"
            f"超了后面的会被拒。建议先 --only 1 试一篇。")

    banner = ""
    pf = (job.get("prependFile") or "").strip()
    if pf:
        # 横幅（start.txt）已集中到 templates/ 下，任务里写 "templates\\start.txt"。
        # 老配置写的是绝对路径，绝对路径优先按原样走。
        bp = Path(pf)
        if not bp.is_absolute():
            cand = (base / bp, pc.ROOT / bp, pc.ROOT / "templates" / bp.name)
            bp = next((c for c in cand if c.exists()), cand[0])
        if bp.exists():
            banner = bp.read_text(encoding="utf-8-sig").strip()
            if job.get("prependTrim", True):
                before_len = len(banner)
                lines = banner.splitlines()
                # 去掉尾部纯分隔线（--- / *** / ___）和空行：
                # 这类行放在文章最开头会渲染成水平线，很突兀
                while lines and (not lines[-1].strip()
                                 or re.fullmatch(r"\s*[-*_]{3,}\s*", lines[-1])):
                    lines.pop()
                banner = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
                if len(banner) != before_len:
                    log(f"  横幅已清理尾部空行/分隔线（{before_len} -> {len(banner)} 字符）；"
                        f"如需原样保留，在 job.json 里设 \"prependTrim\": false")
            log(f"开头横幅: {bp.name}（{len(banner)} 字符）")
        else:
            log(f"警告：prependFile 不存在，将不放横幅: {bp}")

    tmp_dir = jd / "final"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for ctx in ready:
        ensure_cover(ctx)
        meta = {"title": ctx["title"], "tags": ctx["tags"],
                "cover": ctx["cover"], "summary": ctx["summary"],
                "category": ctx["category"]}
        final = tmp_dir / f"{ctx['idx']:02d}-{ctx['slug']}.md"
        body = ctx["body"]
        parts = [write_front_matter(meta)]
        if banner:
            parts.append(banner + "\n")
        parts.append(body)
        final.write_text("\n".join(parts), encoding="utf-8-sig")

        log("=" * 62)
        log(f"第 {ctx['idx']} 篇: {ctx['title']}")
        cover_name = Path(ctx["cover"]).name if ctx["cover"] else "(不设置)"
        log(f"  标签 {ctx['tags']} | 栏目 {ctx['category'] or '(无)'} | "
            f"封面 {cover_name} | 正文 {len(body)} 字符")
        sched_one = None
        if plan:
            sched_one = plan[ctx["idx"] - 1][0]
            log(f"  定时发布: {sched_one:%Y-%m-%d %H:%M}")
        log(f"  成品 md: {final}")

        with sync_playwright() as p:
            # 体检在 cmd_publish 里已经做过（或用户明确 --no-lint 跳过），这里不再重复
            rc = pc.cmd_publish(
                p, final, draft=draft, dry_run=dry, skip_lint=True,
                schedule=sched_one.strftime("%Y-%m-%d %H:%M:%S") if sched_one else None)
        # rc=4 是平台的"文章频繁发布，请稍后再试"（实测连发两篇就会撞上）：
        # 这不是内容问题，等一会儿重试一次是有意义的；额度用尽（rc=3）重试没意义。
        if rc == 4 and not dry:
            wait_s = int(job.get("retryWaitSec") or 120)
            log(f"  被平台频率限制挡下，等 {wait_s} 秒后重试一次…")
            time.sleep(wait_s)
            with sync_playwright() as p:
                rc = pc.cmd_publish(
                    p, final, draft=draft, dry_run=dry, skip_lint=True,
                    schedule=sched_one.strftime("%Y-%m-%d %H:%M:%S") if sched_one else None)
        results.append({"idx": ctx["idx"], "slug": ctx["slug"],
                        "title": ctx["title"], "rc": rc,
                        "schedule": sched_one.strftime("%Y-%m-%d %H:%M") if sched_one else None})
        # 同一份 Chrome 用户目录连着启停容易撞 ProcessSingleton 锁，稍等一下
        time.sleep(2)

    log("=" * 62)
    log("结果汇总：")
    for r in results:
        mark = {0: "✅ 成功", 1: "❌ 失败", 2: "⚠️ 结果未确认",
                3: "⛔ 被 CSDN 拒绝（多半是发文额度，看上面的日志和截图）",
                4: "⏳ 被频率限制挡下（文章频繁发布，等一会儿重跑即可）",
                5: "⛔ 遇到安全验证没解决（内容没提交，重跑即可；建议人守在旁边点一下）",
                6: "❌ 浏览器反复异常关闭（已自动重启过，仍失败；看 output 里的 crash 截图）",
                7: "⛔ 栏目校验没通过（category 与账号真实专栏名不一致，未提交；照日志改完重跑）"}.get(
                    r["rc"], f"rc={r['rc']}")
        when = f" [定时 {r['schedule']}]" if r.get("schedule") else ""
        log(f"  第{r['idx']}篇 {r['title'][:30]} -> {mark}{when}")

    # 真实验收依据：内容管理页的「草稿箱(N)」计数。
    # 实测教训：CSDN 会弹「文章频繁发布，请稍后再试」把存草稿拒掉，而界面上什么都不缺，
    # 光看"草稿已保存"的日志会以为成功了 —— 所以这里再读一次计数兜底。
    if not dry and (draft or plan) and any(r["rc"] == 0 for r in results):
        try:
            with sync_playwright() as p:
                b = pc.Browser(p)
                b.launch()
                page = b.page()
                page.goto("https://mp.csdn.net/mp_blog/manage/article",
                          wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                counts = page.evaluate(
                    """() => [...document.querySelectorAll('.nav-link')]
                             .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
                             .filter(Boolean)""")
                b.close()
            log(f"内容管理页计数（验收依据）: {counts}")
        except Exception as e:
            log(f"草稿箱计数核对失败（忽略）: {e.__class__.__name__}")
    if dry:
        log("本次是 dry-run，没有真正提交任何内容。"
            "要发就改 job.json 里 publish.on：yes=直接发布 / no=只存草稿 / "
            "schedule=定时发布（时间写在 publish.schedule）")
    elif plan:
        log("本次是定时发布（publish.schedule）：文章已提交，到点由 CSDN 自动公开，"
            "提交后仍要过审核；想改时间就在内容管理页点「修改定时发布」")
    elif draft:
        log("本次是存草稿模式（publish.on = no），文章已进 CSDN 草稿箱，未公开发布")
    return 0 if all(r["rc"] == 0 for r in results) else 1


def list_jobs():
    """列出所有 job*.json（jobs/ 下 + 项目根目录），直接告诉你哪个文件是什么主题。"""
    files = sorted(pc.ROOT.glob("job*.json")) + sorted((pc.ROOT / "jobs").glob("job*.json"))
    if not files:
        print("项目里没有 job*.json")
        return 1
    print(f"{'文件':<26}{'主题关键词':<38}{'阅读量门槛':<11}{'篇数'}")
    print("-" * 92)
    for f in files:
        try:
            j = json.loads(f.read_text(encoding="utf-8-sig"))
            s = j.get("search") or {}
            kw = "、".join(s.get("keywords") or []) or "(未填)"
            mv = s.get("minViews", "(未填)")
            n = len(j.get("articles") or []) or j.get("count") or 0
            stale = "  ⚠️缓存过期" if _is_stale_quick(f, j) else ""
            try:
                label = f.relative_to(pc.ROOT).as_posix()
            except ValueError:                       # 相对路径传进来的任务文件
                label = f.name
            print(f"{label:<26}{kw[:36]:<38}{str(mv):<11}{n}{stale}")
        except Exception as e:
            print(f"{f.name:<26}(解析失败: {e})")
    print()
    print("用法:  python csdn_job.py prepare jobs\\<文件名>")
    print("       python csdn_job.py publish jobs\\<文件名> --only 1")
    print("       （只写文件名也行：python csdn_job.py status job.all.json）")
    return 0


def _is_stale_quick(job_path, job):
    """给 list 用的轻量判断，跟 load_sources 用同一套匹配规则。"""
    try:
        sj = pc.OUTPUT_DIR / "job" / job_path.stem / "sources.json"
        if not sj.exists():
            return False
        cached_keys = list((json.loads(sj.read_text(encoding="utf-8")).get("groups") or {}).keys())
        for spec in (job.get("articles") or [{"slug": "x"}]):
            hit, _ = match_cached_group(cached_keys, effective_search(spec, job))
            if hit is None:
                return True
        return False
    except Exception:
        return False


def main():
    # 不带 job 参数（甚至不带任何参数）时列出所有任务文件，省得记文件名
    argv = sys.argv[1:]
    if not argv:
        return list_jobs()
    if argv[0] in ("prepare", "status", "publish") and (
            len(argv) == 1 or (argv[1].startswith("-") and argv[1] not in ("-h", "--help"))):
        return list_jobs()

    ap = argparse.ArgumentParser(description="声明式 CSDN 发布任务执行器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn, helptext in [("prepare", cmd_prepare, "搜集素材+建骨架+生成封面"),
                               ("status", cmd_status, "查看完成度"),
                               ("publish", cmd_publish, "发布")]:
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("job", nargs="?",
                        help="job*.json 文件名或路径（默认在 jobs/ 下）；省略则列出所有可用文件")
        if name in ("prepare", "publish"):
            sp.add_argument("--only", type=int, nargs="+",
                            help="只处理指定序号的文章（prepare 用它分批补素材，未选中的组沿用已有缓存）")
            g = sp.add_mutually_exclusive_group()
            g.add_argument("--dry-run", dest="dry_run", action="store_true", default=None,
                           help="只演练不发布")
            g.add_argument("--no-dry-run", dest="dry_run", action="store_false",
                           help="强制正式发布")
            sp.add_argument("--draft", action="store_true", help="存草稿而非公开")
            sp.add_argument("--schedule", metavar="WHEN",
                           help="定时发布（覆盖配置）：\"2026-10-09 19:30\" / \"+26h\" / auto")
            sp.add_argument("--force", action="store_true",
                            help="体检发现硬伤时也照发（默认拦下）")
            sp.add_argument("--no-lint", action="store_true",
                            help="完全跳过发布前体检")
            sp.add_argument("--allow-stale-sources", action="store_true",
                            help="素材缓存与搜索条件不一致时也继续（标签会退回默认值）")

    args = ap.parse_args()
    if not args.job:
        return list_jobs()
    job_path = find_in_roots(args.job)
    if job_path is None:
        raise SystemExit(f"job 文件不存在: {args.job}\n"
                         f"任务文件放在 jobs/ 下（写成 jobs\\{Path(args.job).name} "
                         f"或只写文件名都可以）；\n"
                         f"跑 python csdn_job.py 可以列出所有可用文件")
    # 用找到的真实路径继续走：任务写在 jobs/ 下，不影响 base（job.json 所在目录）
    args.job = str(job_path)
    return {"prepare": cmd_prepare, "status": cmd_status,
            "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
