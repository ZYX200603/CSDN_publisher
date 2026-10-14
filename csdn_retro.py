#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 单批/单日复盘：线上核验 + 建数据集 + 分析 + 两次快照对比。

设计原则（2026-10-10 重构）：**一个脚本 + 一份配置文件**。
所有易变量（复盘哪天、本地正文在哪、同龄基准、外推口径、输出路径）都在配置里，
每天跑复盘只需要换配置文件或改 `--day`，不再为每天新建脚本副本。

用法:
    .venv/Scripts/python.exe csdn_retro.py probe                     # 线上核验（需要浏览器）
    .venv/Scripts/python.exe csdn_retro.py build                     # 建数据集（离线）
    .venv/Scripts/python.exe csdn_retro.py analyze                   # 分析（离线）
    .venv/Scripts/python.exe csdn_retro.py compare <旧.json> <新.json>  # 两次快照对比（离线）

    --config retro/config.json   指定配置（默认同上）
    --day 2026-10-10             覆盖配置里的 day

离线子命令（build/analyze/compare）不依赖 playwright；只有 probe 需要。
"""

import argparse
import json
import math
import os
import re
import statistics as st
import sys
import time
import random
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "retro" / "config.json"

# 内置默认值：配置文件里没写的键都用这里的（配置文件只需写差异）
DEFAULTS = {
    "day": None,                      # null → 取最新快照所在日
    "paths": {
        "statsDir": "output/stats",
        "snapshots": ["output/stats/snapshots"],
        "trustFileTpl": "output/article-truth-{day}.json",
        "datasetOutTpl": "output/stats/retro-{day}-dataset.json",
        "historyDataset": "output/stats/retro-3d-dataset.json",
        "localSearchGlobs": ["output/job/*/final/*.md", "posts/*.md"],
    },
    "window": {
        "mode": "single-day",         # single-day | since
        "since": None,                # mode=since 时用，如 "2026-10-07"
        "excludeTitleContains": [],
    },
    "localFiles": {},                 # 标题关键词 -> 本地 md 路径（显式指定优先）
    "topic": {},                      # 标题关键词 -> 主题名（优先级最高，用于覆盖/兜底）
    "topicNormalize": {               # 本地 md 的 category 值 -> 统一主题名（小写键匹配）
        "redis": "Redis",
        "mysql": "MySQL/InnoDB",
        "go 项目": "Go",
        "go项目分享": "Go",
        "技术栈": "技术栈/JVM",
        "技术栈学习笔记": "技术栈/JVM",
        "算法题目解析": "算法",
    },
    "baselines": [],                  # [{"batch": "2026-10-09", "snap": "2026-10-09T11:06:20", "label": "..."}]
    "sameAge": {                      # 同龄参照：在 T 时刻，历史批 har 在相近「发布后小时数」的阅读
        "refBatches": ["2026-10-09"],
        "minAgeRatio": 0.6,           # 观测龄 >= 目标龄*该比例 才认作有效同龄观测
        "toleranceH": 0.3,
    },
    "extrapolation": {
        "targetH": 24,
        "fitWindowH": [6, 30],        # 拟合幂律指数 k 用的观测龄区间
        "minViews": 5,
        "fallbackK": 0.82,
        "methods": ["power", "linear"],
    },
    "probe": {"sleepSec": [3.0, 6.0], "pageWaitMs": 6500, "timeoutMs": 45000},
    "report": {"topN": 4, "bottomN": 4},
}

TITLE_FEATURE_PATTERNS = {
    "含阿拉伯数字": lambda t: bool(re.search(r"\d", t)),
    "含问号": lambda t: bool(re.search(r"[?？]", t)),
    "含冒号": lambda t: bool(re.search(r"[：:]", t)),
    "故障/痛点词": lambda t: bool(re.search(r"卡住|拖垮|泄漏|等待|阻塞|排查|告警|飙|失效|死锁", t)),
    "系列编号(（一）…)": lambda t: bool(re.search(r"（[一二三四五六七八九十]）", t)),
    "教程承诺式(入门/讲透/详解)": lambda t: bool(re.search(r"入门|讲透|一次讲|详解|实战", t)),
    "数字清单式(N个/N类/N种)": lambda t: bool(re.search(r"\d+\s*(个|类|种|步|条|项)", t)),
}


# ---------------------------------------------------------------- 配置 / IO

def load_config(path: Path) -> dict:
    if not path.exists():
        print(f"!! 配置文件不存在：{path}（用内置默认值）")
        return json.loads(json.dumps(DEFAULTS))
    cfg = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULTS))
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    return merged


def abs_path(p: str) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


def load_snapshots(cfg):
    snaps = []
    for d in cfg["paths"]["snapshots"]:
        d = abs_path(d)
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                s = data.get("snapshot", data)
                snaps.append((datetime.fromisoformat(s["collectedAt"]), s))
            except Exception:
                continue
    seen, out = set(), []
    for t, s in sorted(snaps, key=lambda x: x[0]):
        if t in seen:
            continue
        seen.add(t)
        out.append((t, s))
    return out


# ---------------------------------------------------------------- 本地正文

_md_cache = None


def scan_local_md(cfg):
    """扫描候选本地 md，建立「front matter 标题 -> 路径」索引（只在首次调用时扫）。"""
    global _md_cache
    if _md_cache is not None:
        return _md_cache
    _md_cache = {}
    for g in cfg["paths"]["localSearchGlobs"]:
        for f in ROOT.glob(g):
            try:
                head = f.read_text(encoding="utf-8-sig", errors="ignore")[:2000]
                mt = re.search(r'^title:\s*"?([^"\n]+)"?', head, re.M)
                if mt:
                    _md_cache[mt.group(1).strip()] = f
            except Exception:
                continue
    return _md_cache


def resolve_local(title, cfg):
    """先查显式配置（关键词匹配），再按 front matter 标题精确匹配自动定位。"""
    for k, p in cfg["localFiles"].items():
        if k in title:
            pth = abs_path(p)
            if pth.exists():
                return pth
    return scan_local_md(cfg).get(title)


def plain(md: str) -> str:
    t = re.sub(r"<[^>]+>", " ", md)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"`{1,3}", "", t)
    t = re.sub(r"^\s*[#>*\-\+]+\s*", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def md_features(path):
    raw = Path(path).read_text(encoding="utf-8-sig")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    fm, body = (m.group(1), raw[m.end():]) if m else ("", raw)
    fm_title = fm_summary = fm_cover = fm_category = None
    for line in fm.splitlines():
        if line.startswith("title:"):
            fm_title = line.split(":", 1)[1].strip().strip('"\'')
        elif line.startswith("summary:"):
            fm_summary = line.split(":", 1)[1].strip().strip('"\'')
        elif line.startswith("cover:"):
            fm_cover = line.split(":", 1)[1].strip().strip('"\'')
        elif line.startswith("category:"):
            fm_category = line.split(":", 1)[1].strip().strip('"\'')
    lines = body.splitlines()
    in_fence = in_div = in_bq = False
    h1 = h2 = h3 = fences = tb = imgs = bl = 0
    last_tbl = False
    opening = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            fences += 1 if in_fence else 0
            continue
        if in_fence:
            continue
        if "<div" in s:
            in_div = True
        if in_div:
            bl += 1
            if "</div>" in s:
                in_div = False
            continue
        if s.startswith("> <font"):
            in_bq = True
            bl += 1
            continue
        if in_bq:
            if s.startswith(">") or s == "":
                bl += 1
                if s == "":
                    in_bq = False
                continue
            in_bq = False
        if s in ("@[toc]", "", "---"):
            continue
        mh = re.match(r"^(#{1,3})\s+(.*)$", s)
        if mh:
            lv = len(mh.group(1))
            h1, h2, h3 = (h1 + (lv == 1), h2 + (lv == 2), h3 + (lv == 3))
            continue
        if s.startswith("|"):
            tb += 0 if last_tbl else 1
            last_tbl = True
        else:
            last_tbl = False
        imgs += len(re.findall(r"!\[|<img", s))
        if opening is None and not s.startswith(">"):
            opening = s
    text = plain(body)
    head3k = body[:3500]
    banner = "new" if "> <font" in head3k else ("old-div" if "<div align" in head3k else "none")
    return {
        "title_local": fm_title, "summary_local": fm_summary, "category_local": fm_category,
        "cover_submitted": bool(fm_cover), "banner_style": banner,
        "chars_plain": len(text), "h1_in_body": h1, "h2": h2, "h3": h3,
        "code_blocks": fences, "table_blocks": tb, "images": imgs, "banner_lines": bl,
        "opening": (opening or "")[:400],
    }


# ---------------------------------------------------------------- 子命令：probe

PROBE_JS = r"""
() => {
  const pickText = (sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el) { const v = (el.innerText || el.textContent || '').trim(); if (v) return v; }
    }
    return null;
  };
  const meta = (n) => {
    const el = document.querySelector(`meta[name="${n}"]`) || document.querySelector(`meta[property="${n}"]`);
    return el ? (el.getAttribute('content') || '') : null;
  };
  const cv = document.querySelector('#content_views');
  const txt = cv ? (cv.innerText || '') : '';
  const clean = txt.replace(/\s+/g, ' ').trim();
  const counts = cv ? {
    h2: cv.querySelectorAll('h2').length, h3: cv.querySelectorAll('h3').length,
    pre: cv.querySelectorAll('pre').length, table: cv.querySelectorAll('table').length,
    img: cv.querySelectorAll('img').length,
  } : null;
  return {
    url: location.href,
    title: pickText(['h1.title-article', '.article-title-box h1', 'h1#articleContentId', '#articleContentId', 'h1']),
    metaDescription: meta('description'), metaKeywords: meta('keywords'),
    timeText: pickText(['.time', '.up-time']), textLen: clean.length,
    head900: clean.slice(0, 900), counts,
  };
}
"""

AUTHOR = ""   # 账号 ID 不写死：按 config.author → 环境变量 CSDN_RETRO_AUTHOR → 快照 account → 文章 URL 推导


def resolve_author(cfg, snap):
    """账号 ID 解析优先级：config.author > 环境变量 CSDN_RETRO_AUTHOR > 快照 account > 文章 URL 推导。
    解析不出就返回空串，由调用方中止并提示（绝不猜、绝不写死）。"""
    author = (cfg.get("author") or os.environ.get("CSDN_RETRO_AUTHOR")
              or snap.get("account") or "").strip()
    if author:
        return author
    for a in (snap.get("articles") or []):
        m = re.search(r"blog" + chr(92) + ".csdn" + chr(92) + ".net/([^/]+)/article/details/", a.get("url") or "")
        if m:
            return m.group(1)
    return ""


def cmd_probe(cfg, day, args):
    sys.path.insert(0, str(ROOT))
    import publish_csdn as pc                       # noqa: E402
    from playwright.sync_api import sync_playwright  # noqa: E402

    snap = json.loads((abs_path(cfg["paths"]["statsDir"]) / "latest.json").read_text(encoding="utf-8"))["snapshot"]
    author = resolve_author(cfg, snap)
    if not author:
        print("⛔ 解析不出账号 ID。请在 retro/config.json 里写 \"author\": \"<你的账号ID>\"，"
              "或设环境变量 CSDN_RETRO_AUTHOR，或先跑一次 csdn_stats.py 让快照带上 account。")
        return 2
    targets = pick_targets(snap, cfg, day)
    print(f"线上核验 {day}：{len(targets)} 篇（快照 {snap['collectedAt']}）")

    results, fails = {}, []
    lo, hi = cfg["probe"]["sleepSec"]
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        for i, a in enumerate(targets, 1):
            aid = a["id"]
            try:
                page.goto(f"https://blog.csdn.net/{author}/article/details/{aid}",
                          wait_until="domcontentloaded", timeout=cfg["probe"]["timeoutMs"])
                page.wait_for_timeout(cfg["probe"]["pageWaitMs"])
                pc.dismiss_overlays(page)
                d = page.evaluate(PROBE_JS)
                d["articleId"] = aid
                d["snapshotTitle"] = a["title"]
                d["snapshotViews"] = a["views"]
                d["publishTime"] = a["publishTime"]
                d["summary"] = re.sub(r"^文章浏览阅读\d+次(，点赞\d+次)?(，收藏\d+次)?。", "",
                                      d.get("metaDescription") or "").strip()
                t = (d.get("title") or "").strip()
                s = (d.get("summary") or "").strip()
                d["summaryLooksAuto"] = bool(t and s and (s.startswith(t) or s.startswith(t[:16])))
                results[aid] = d
                print(f"[{i:2}/{len(targets)}] {'OK' if d.get('title') == a['title'] else 'TITLE-DIFF':10} "
                      f"{a['title'][:38]} | 摘要{len(d.get('summary') or '')}字 | {d.get('counts')}")
            except Exception as e:
                fails.append({"id": aid, "title": a["title"], "error": str(e)[:200]})
                print(f"[{i:2}/{len(targets)}] FAIL      {a['title'][:38]} | {str(e)[:110]}")
            time.sleep(random.uniform(lo, hi))
        b.close()

    out = abs_path(cfg["paths"]["trustFileTpl"].format(day=day))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"day": day, "results": results, "fails": fails},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n成功 {len(results)} / 失败 {len(fails)}    已写入 {out}")
    return 0 if not fails else 1


def pick_targets(snap, cfg, day):
    arts = snap["articles"]
    mode = cfg["window"]["mode"]
    if mode == "since" and cfg["window"]["since"]:
        cut = datetime.fromisoformat(cfg["window"]["since"])
        sel = [a for a in arts if datetime.fromisoformat(a["publishTime"]) >= cut]
    else:
        sel = [a for a in arts if a["publishTime"][:10] == day]
    excl = cfg["window"]["excludeTitleContains"]
    sel = [a for a in sel if not any(e in a["title"] for e in excl)]
    sel.sort(key=lambda x: x["publishTime"])
    return sel


# ---------------------------------------------------------------- 子命令：build

def cmd_build(cfg, day, args):
    snaps = load_snapshots(cfg)
    if not snaps:
        print("!! 没有可用快照"); return 1
    latest_t, latest = snaps[-1]
    print(f"快照 {len(snaps)} 个，最新 {latest_t}  count={latest['count']}")

    truth_p = abs_path(cfg["paths"]["trustFileTpl"].format(day=day))
    truth = {}
    if truth_p.exists():
        truth = json.loads(truth_p.read_text(encoding="utf-8")).get("results", {})
        print(f"线上核验：{len(truth)} 篇")
    else:
        print("!! 无线上核验结果（先跑 probe），live 字段将缺失")

    arts = pick_targets(latest, cfg, day)
    print(f"窗口内文章：{len(arts)} 篇")

    # 同龄基准
    baselines = {}
    for b in cfg["baselines"]:
        at = datetime.fromisoformat(b["snap"])
        vals = {}
        for a in latest["articles"]:
            if a["publishTime"][:10] != b["batch"]:
                continue
            v = views_at(a["id"], snaps, at)
            if v is None:
                continue
            vals[a["id"]] = {"title": a["title"], "views": v,
                             "age_h": round((at - datetime.fromisoformat(a["publishTime"])).total_seconds() / 3600.0, 2)}
        if vals:
            baselines[b["label"]] = vals
            print(f"  基准 {b['label']}: {len(vals)} 篇")

    rows = []
    for a in arts:
        aid = a["id"]
        pt = datetime.fromisoformat(a["publishTime"])
        age_h = (latest_t - pt).total_seconds() / 3600.0
        series = []
        for t, s in snaps:
            for x in s["articles"]:
                if x["id"] == aid:
                    series.append((t.isoformat(), x["views"]))
                    break
        local = resolve_local(a["title"], cfg)
        feat = md_features(local) if local else {}
        tr = truth.get(aid, {})
        eng = (a["likes"] + a["favorites"] + a["comments"]) / a["views"] if a["views"] else None
        topic = next((v for k, v in cfg["topic"].items() if k in a["title"]), None)
        if topic is None:
            raw_cat = feat.get("category_local")
            topic = cfg["topicNormalize"].get((raw_cat or "").strip().lower(), raw_cat) if raw_cat else None
        if not topic:
            topic = "?"
        rows.append({
            "id": aid, "title": a["title"], "publishTime": a["publishTime"],
            "topic": topic,
            "views": a["views"], "likes": a["likes"], "favorites": a["favorites"], "comments": a["comments"],
            "age_h": round(age_h, 2),
            "rate_h": round(a["views"] / age_h, 1) if age_h else None,
            "engage": round(eng, 3) if eng is not None else None,
            "series": series,
            "title_live": tr.get("title"), "summary_live": tr.get("summary"),
            "summary_looks_auto": tr.get("summaryLooksAuto"),
            "textLen_live": tr.get("textLen"), "counts_live": tr.get("counts"),
            "local_path": str(local) if local else None,
            **feat,
        })

    out = abs_path(cfg["paths"]["datasetOutTpl"].format(day=day))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"builtAt": datetime.now().isoformat(), "day": day,
                               "snapshotAt": latest_t.isoformat(), "baselines": baselines,
                               "config": str(args.config), "rows": rows},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}\n")
    print(f"{'时间':6}{'主题':12}{'标题':42}{'阅读':>6}{'龄h':>6}{'速/h':>7}{'赞':>4}{'藏':>4}{'评':>4}{'互动率':>7}")
    print("-" * 118)
    for r in rows:
        e = f"{r['engage']:.1%}" if r["engage"] is not None else "—"
        print(f"{r['publishTime'][11:16]:6}{r['topic']:12}{r['title'][:40]:42}"
              f"{r['views']:>6}{r['age_h']:>6}{str(r['rate_h']):>7}"
              f"{r['likes']:>4}{r['favorites']:>4}{r['comments']:>4}{e:>7}")
    return 0


def views_at(aid, snaps, at):
    v = None
    for t, s in snaps:
        if t > at:
            continue
        for x in s["articles"]:
            if x["id"] == aid:
                v = x["views"]
                break
    return v


# ---------------------------------------------------------------- 分析公共

def same_age_values(hist_rows, batch_day, age, cfg):
    """某批文章在「发布后 age 小时」的阅读值（跳过负龄与远早观测）。

    返回 (vals, ages_used)：ages_used 是每篇文章实际被选中的观测龄。
    调用方必须核对实际观测龄与目标龄的差——历史批在目标龄附近没有快照时，
    这里只能退而取"最近的可得观测"，但那不是同龄横评（统一取样龄铁律）。
    """
    ratio = cfg["sameAge"]["minAgeRatio"]
    vals, ages_used = [], []
    for r in hist_rows:
        if r["publishTime"][:10] != batch_day or not r.get("series"):
            continue
        pt = datetime.fromisoformat(r["publishTime"])
        best = None
        for (t, v) in r["series"]:
            a = (datetime.fromisoformat(t) - pt).total_seconds() / 3600.0
            if a <= 0:                    # 快照早于发布 → 文章还没发出，不能当观测
                continue
            if a < age * ratio:           # 观测太早（刚发布），用了会拉低中位
                continue
            if best is None or abs(a - age) < best[0]:
                best = (abs(a - age), v, a)
        if best:
            vals.append(best[1])
            ages_used.append(best[2])
    return vals, ages_used


def fit_power_k(hist_rows, cfg):
    ex = cfg["extrapolation"]
    lo, hi = ex["fitWindowH"]
    ks = []
    for r in hist_rows:
        if not r.get("series"):
            continue
        pt = datetime.fromisoformat(r["publishTime"])
        pts = []
        for (t, v) in r["series"]:
            a = (datetime.fromisoformat(t) - pt).total_seconds() / 3600.0
            if a > 0 and v > 0:
                pts.append((a, v))
        pts.sort()
        w = [(a, v) for (a, v) in pts if lo <= a <= hi]
        if len(w) < 2:
            continue
        a1, v1 = w[0]
        a2, v2 = w[-1]
        if a2 / a1 > 1.2 and v1 >= ex["minViews"]:
            ks.append(math.log(v2 / v1) / math.log(a2 / a1))
    if not ks:
        return ex["fallbackK"], 0, None, None
    return st.median(ks), len(ks), sorted(ks)[len(ks) // 4], sorted(ks)[len(ks) * 3 // 4]


# ---------------------------------------------------------------- 子命令：analyze

def cmd_analyze(cfg, day, args):
    ds_p = abs_path(cfg["paths"]["datasetOutTpl"].format(day=day))
    if not ds_p.exists():
        print(f"!! 数据集不存在：{ds_p}（先跑 build）"); return 1
    D = json.loads(ds_p.read_text(encoding="utf-8"))
    rows = D["rows"]
    hist_p = abs_path(cfg["paths"]["historyDataset"])
    hist = json.loads(hist_p.read_text(encoding="utf-8"))["rows"] if hist_p.exists() else []

    print("=" * 120)
    print(f"A. 概览 {day}（快照 {D['snapshotAt'][11:16]}，龄 {min(r['age_h'] for r in rows)}~{max(r['age_h'] for r in rows)}h）")
    vs = [r["views"] for r in rows]
    print(f"  篇数 {len(rows)} | 合计 {sum(vs)} | 中位 {st.median(vs)} | 均值 {st.mean(vs):.1f} | "
          f"最低 {min(vs)} | 最高 {max(vs)} | 速度中位 {st.median([r['rate_h'] for r in rows]):.1f}/h")

    print("\n" + "=" * 120)
    print("B. 同龄对比")
    print(f"{'组':28}{'n':>3}{'中位':>8}{'均值':>8}{'最低':>6}{'最高':>6}{'速度中位':>9}")
    print("-" * 120)
    age_h = st.median([r["age_h"] for r in rows])
    tv = [r["views"] for r in rows]
    print(f"{'今天 ' + day:28}{len(rows):>3}{st.median(tv):>8.1f}{st.mean(tv):>8.1f}{min(tv):>6}{max(tv):>6}"
          f"{st.median([r['rate_h'] for r in rows]):>9.1f}")
    for lbl, vals in (D.get("baselines") or {}).items():
        v = [x["views"] for x in vals.values()]
        r_ = [x["views"] / x["age_h"] for x in vals.values()]
        print(f"{lbl:28}{len(v):>3}{st.median(v):>8.1f}{st.mean(v):>8.1f}{min(v):>6}{max(v):>6}{st.median(r_):>9.1f}")
    for b in cfg["sameAge"]["refBatches"]:
        v, ages_used = same_age_values(hist, b, age_h, cfg)
        if v:
            tol = cfg["sameAge"].get("toleranceH", 0.3)
            med_age = st.median(ages_used)
            note = ""
            if abs(med_age - age_h) > tol:
                note = (f"  ⚠ 该批在 {age_h:.1f}h 附近无快照，实际取 {med_age:.1f}h 的观测"
                        f"（差 {med_age - age_h:+.1f}h）——不是同龄横评，只作背景参考")
            print(f"{b + ' 批 @同龄 ' + format(age_h, '.1f') + 'h':28}{len(v):>3}{st.median(v):>8.1f}"
                  f"{st.mean(v):>8.1f}{min(v):>6}{max(v):>6}{'':>9}{note}")

    print("\n" + "=" * 120)
    print("C. 24h 外推")
    ex = cfg["extrapolation"]
    target = ex["targetH"]
    k, n_k, k_lo, k_hi = fit_power_k(hist, cfg)
    print(f"  幂律：历史 {n_k} 篇拟合 k={k:.2f}（P25 {k_lo:.2f} / P75 {k_hi:.2f}）")
    est_power, est_lin = [], []
    for r in rows:
        age = max(r["age_h"], 0.5)
        est_power.append(r["views"] * (target / age) ** k)
        prev = r["series"][-2][1] if len(r["series"]) >= 2 else None
        rate = (r["views"] - prev) / 3.0 if prev is not None else r["rate_h"]
        est_lin.append(r["views"] + max(rate, 0) * max(target - age, 0))
    print(f"  幂律预估 24h 中位 {st.median(est_power):.0f} ｜ 线性外推中位 {st.median(est_lin):.0f}")
    if hist:
        hv = [x.get("h24_views") for x in hist if x.get("h24_views") is not None]
        if hv:
            print(f"  参照：历史 {len(hv)} 篇实测 24h 中位 {st.median(hv):.0f}")
    print(f"\n{'标题':44}{'当前':>6}{'龄h':>7}{'幂律24h':>9}{'线性24h':>9}")
    print("-" * 120)
    for r, ep, el in sorted(zip(rows, est_power, est_lin), key=lambda x: -x[0]["views"]):
        print(f"{r['title'][:42]:44}{r['views']:>6}{r['age_h']:>7.2f}{ep:>9.0f}{el:>9.0f}")

    def group(keyf, label):
        g = {}
        for r in rows:
            key = keyf(r)
            g.setdefault(key, []).append(r)
        print(f"\n### {label}")
        print(f"{'组':24}{'n':>3}{'阅读中位':>9}{'速度中位':>9}")
        out = []
        for key, rs in g.items():
            out.append((st.median([x["views"] for x in rs]), key, len(rs),
                        st.median([x["rate_h"] for x in rs])))
        for med, key, n, rm in sorted(out, reverse=True):
            print(f"{str(key):24}{n:>3}{med:>9.1f}{rm:>9.1f}")

    print("\n" + "=" * 120)
    print("D. 分组（样本小，只作提示）")
    group(lambda r: r["topic"], "D1 主题")
    group(lambda r: "自动摘要" if r.get("summary_looks_auto") else "手写摘要", "D2 摘要类型")
    group(lambda r: "长文(≥6.5k字)" if (r.get("textLen_live") or 0) >= 6500 else "短文(<6.5k字)", "D3 线上字数")
    group(lambda r: "代码块≥6" if (r.get("counts_live") or {}).get("pre", 0) >= 6 else "代码块<6", "D4 线上代码块")
    group(lambda r: "表格≥3" if (r.get("counts_live") or {}).get("table", 0) >= 3 else "表格<3", "D5 线上表格")

    print("\n" + "=" * 120)
    print("E. 标题特征")
    print(f"{'特征':28}{'命中n':>5}{'命中中位':>9}{'未命中n':>6}{'未命中中位':>10}")
    for name, fn in TITLE_FEATURE_PATTERNS.items():
        hit = [r["views"] for r in rows if fn(r["title"])]
        mis = [r["views"] for r in rows if not fn(r["title"])]
        print(f"{name:28}{len(hit):>5}{(f'{st.median(hit):.0f}' if hit else '—'):>9}"
              f"{len(mis):>6}{(f'{st.median(mis):.0f}' if mis else '—'):>10}")

    srt = sorted(rows, key=lambda r: -r["views"])
    n_top, n_bot = cfg["report"]["topN"], cfg["report"]["bottomN"]
    print("\n" + "=" * 120)
    print("F. 头部 / 尾部")
    for tag, grp in (("TOP", srt[:n_top]), ("BOTTOM", srt[-n_bot:])):
        print(f"  {tag}:")
        for r in grp:
            print(f"    {r['views']:>4} | {r['rate_h']:>5}/h | 龄{r['age_h']}h | {r['topic']:10} | {r['title'][:48]}")
    return 0


# ---------------------------------------------------------------- 子命令：compare

def cmd_compare(cfg, day, args):
    def load(p):
        pth = Path(p)
        if not pth.is_absolute():
            pth = ROOT / p
        return json.loads(pth.read_text(encoding="utf-8"))
    old, new = load(args.old), load(args.new)
    orows = {r["id"]: r for r in old["rows"]}
    nrows = {r["id"]: r for r in new["rows"]}
    ot = datetime.fromisoformat(old["snapshotAt"])
    nt = datetime.fromisoformat(new["snapshotAt"])
    gap = (nt - ot).total_seconds() / 3600.0
    print("=" * 126)
    print(f"快照对比 {ot} → {nt}   间隔 {gap:.2f}h")

    orank = {k: i + 1 for i, k in enumerate(sorted(orows, key=lambda k: -orows[k]["views"]))}
    nrank = {k: i + 1 for i, k in enumerate(sorted(nrows, key=lambda k: -nrows[k]["views"]))}
    print(f"\n{'标题':44}{'旧':>5}{'新':>6}{'增量':>6}{'区间/h':>8}{'累计/h':>8}{'排名':>8}")
    print("-" * 126)
    for k, r in sorted(nrows.items(), key=lambda x: -(x[1]["views"] - orows[x[0]]["views"])):
        o = orows[k]
        dv = r["views"] - o["views"]
        rk = f"{orank[k]}→{nrank[k]}" if orank[k] != nrank[k] else f"{nrank[k]}"
        print(f"{r['title'][:42]:44}{o['views']:>5}{r['views']:>6}{dv:>6}{dv / gap:>8.1f}"
              f"{r['rate_h']:>8.1f}{rk:>8}")

    print("\n" + "=" * 126)
    print("汇总")
    for tag, rs in (("旧", orows), ("新", nrows)):
        v = [r["views"] for r in rs.values()]
        print(f"  {tag:4} n={len(v):<3} 合计 {sum(v):<6} 中位 {st.median(v):<7.1f} 均值 {st.mean(v):<7.1f} "
              f"最低 {min(v):<5} 最高 {max(v):<5} 龄 {st.median([r['age_h'] for r in rs.values()]):.2f}h")
    sv = sorted((r["views"] for r in nrows.values()), reverse=True)
    print(f"  头部3篇占比 {sum(sv[:3]) / sum(sv):.1%}")

    hist_p = abs_path(cfg["paths"]["historyDataset"])
    if hist_p.exists():
        hist = json.loads(hist_p.read_text(encoding="utf-8"))["rows"]
        print("\n同龄参照（历史批在相同「发布后小时数」的阅读中位）")
        for age in (st.median([r["age_h"] for r in orows.values()]),
                    st.median([r["age_h"] for r in nrows.values()])):
            tol = cfg["sameAge"].get("toleranceH", 0.3)
            for b in cfg["sameAge"]["refBatches"]:
                v, ages_used = same_age_values(hist, b, age, cfg)
                if v:
                    med_age = st.median(ages_used)
                    note = ""
                    if abs(med_age - age) > tol:
                        note = (f"  ⚠ 该批在 {age:.1f}h 附近无快照，实际取 {med_age:.1f}h 的观测"
                                f"（差 {med_age - age:+.1f}h）——不是同龄横评，只作背景参考")
                    print(f"  龄 {age:5.2f}h  {b}（n={len(v)}）中位 {st.median(v):.0f} / 均值 {st.mean(v):.0f}{note}")
        cmd = cfg["extrapolation"]
        target = cmd["targetH"]
        k, n_k, *_ = fit_power_k(hist, cfg)
        est = [r["views"] * (target / max(r["age_h"], 0.5)) ** k for r in nrows.values()]
        print(f"\n24h 外推（幂律 k={k:.2f}，历史 {n_k} 篇）中位 {st.median(est):.0f}")
    return 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="CSDN 复盘：一个脚本 + 一份配置")
    ap.add_argument("cmd", choices=["probe", "build", "analyze", "compare"])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--day", default=None, help="覆盖配置里的 day")
    ap.add_argument("rest", nargs="*", help="compare 时用：旧数据集 新数据集")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    if args.day:
        cfg["day"] = args.day

    if args.cmd == "compare":
        if len(args.rest) < 2:
            print("用法: csdn_retro.py compare <旧数据集.json> <新数据集.json>"); return 2
        args.old, args.new = args.rest[0], args.rest[1]
        return cmd_compare(cfg, cfg["day"], args)

    day = cfg["day"]
    if not day:
        snaps = load_snapshots(cfg)
        if not snaps:
            print("!! 没有快照，无法推断 day，请传 --day"); return 1
        day = snaps[-1][0].strftime("%Y-%m-%d")
        print(f"day 未配置 → 取最新快照日 {day}")
    return {"probe": cmd_probe, "build": cmd_build, "analyze": cmd_analyze}[args.cmd](cfg, day, args)


if __name__ == "__main__":
    sys.exit(main())
