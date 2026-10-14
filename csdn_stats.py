#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN 阅读数据复盘 —— 只读地采集你自己账号的真实数据，存快照、算增量、做聚合。

它回答的是「合规涨阅读」里唯一可靠的那个问题：**到底哪类文章真的有人读**。
所以它只做三件事：

  1. 打开 CSDN 创作中心的「内容管理」页，把每一篇的阅读/点赞/评论/收藏抓下来；
  2. 每次采集存一份快照，跟上一份对比，算出**真实新增阅读**和**当前阅读速度**；
  3. 按发布时间段、标题特征、主题词做聚合，输出一份可执行的复盘报告。

它**不发布、不修改、不刷量**：全程只有页面读取，改不了你账号上的任何东西。

为什么必须用浏览器：CSDN 对非浏览器客户端一律返回 HTTP 521（见 SKILL.md 第 9 条），
所以只能复用你已登录的浏览器会话。

CLI 用法:
    python csdn_stats.py                     # 采集 + 出报表（全量，8 页左右）
    python csdn_stats.py --pages 2           # 只采前 2 页，快速看一眼
    python csdn_stats.py --report-only       # 不联网，用已有快照重新出报表
    python csdn_stats.py --top 20            # 排行榜多列几行
    python csdn_stats.py --window-days 14    # 聚合口径改成最近 14 天发布的文章
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402

STATS_DIR = pc.OUTPUT_DIR / "stats"
SNAP_DIR = STATS_DIR / "snapshots"
MANAGE_URL = "https://mp.csdn.net/mp_blog/manage/article"

# 内容管理页列表行（2026-10 实测）：
#   .article-list-item-mp            一行
#   .list-item-title a               标题（href 是编辑器地址，带 articleId）
#   .article-list-item-time          发布时间
#   .article-list-item-info .item-info-left p   依次是 阅读/点赞/评论/收藏
#   a[href*="/article/details/"]     博客正式地址
#   .type-text                       原创/转载/翻译
LIST_JS = r"""
() => {
  const rows = [...document.querySelectorAll('.article-list-item-mp')];
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return rows.map(r => {
    const t = r.querySelector('.list-item-title a');
    const nums = [...r.querySelectorAll('.article-list-item-info .item-info-left p')]
                   .map(p => (p.innerText || '').replace(/[^\d]/g, ''));
    const blog = r.querySelector('a[href*="blog.csdn.net"][href*="/article/details/"]');
    const kind = r.querySelector('.article-list-type-box .type-text');
    const tm = r.querySelector('.article-list-item-time');
    return {
      title: t ? clean(t.innerText) : '',
      editorUrl: t ? (t.href || '') : '',
      url: blog ? (blog.href || '').split('?')[0] : '',
      publishTime: tm ? clean(tm.innerText) : '',
      views: nums[0] || '0', likes: nums[1] || '0',
      comments: nums[2] || '0', favorites: nums[3] || '0',
      kind: kind ? clean(kind.innerText) : '',
      highQuality: !!r.querySelector('.tag-high-qulity')
    };
  });
}
"""

# 当前页第一行的 articleId，用来判断翻页是否真的换了内容
FIRST_ID_JS = r"""
() => {
  const a = document.querySelector('.article-list-item-mp .list-item-title a');
  return a ? (a.href || '') : '';
}
"""

# 标题特征：都是"读者视角"的特征，用来统计"哪种标题更被点开"
TITLE_MARKERS = [
    ("含数字", r"\d"),
    ("含问号", r"[?？]"),
    ("含冒号/破折号", r"[:：—]"),
    ("疑问式（为什么/如何/怎么）", r"(为什么|为何|如何|怎么|凭什么)"),
    ("教程式（入门/详解/实战/一文）", r"(入门|详解|讲透|讲清|实战|指南|教程|盘点|总结|从零)"),
    ("痛点式（坑/失效/排查/事故）", r"(坑|避坑|失效|排查|事故|踩|翻车|慢|报错)"),
    ("承诺式（看完/彻底/最全）", r"(看完|秒懂|彻底|最全|最强|必备|一次)"),
]


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


# ---------------------------------------------------------------- 采集

def collect_page(page):
    """抓当前页的所有行，转成规范 record。"""
    raw = page.evaluate(LIST_JS) or []
    out = []
    for r in raw:
        aid = ""
        m = re.search(r"articleId=(\d+)", r.get("editorUrl") or "")
        if m:
            aid = m.group(1)
        elif r.get("url"):
            m = re.search(r"/details/(\d+)", r["url"])
            aid = m.group(1) if m else ""
        if not aid or not r.get("title"):
            continue
        out.append({
            "id": aid,
            "title": r["title"],
            "url": r.get("url") or "",
            "publishTime": r.get("publishTime") or "",
            "views": int(r.get("views") or 0),
            "likes": int(r.get("likes") or 0),
            "comments": int(r.get("comments") or 0),
            "favorites": int(r.get("favorites") or 0),
            "kind": r.get("kind") or "",
            "highQuality": bool(r.get("highQuality")),
        })
    return out


def total_pages(page):
    """分页器里最大的页码。拿不到就返回 1。"""
    try:
        nums = page.evaluate(
            "() => [...document.querySelectorAll('ul.el_mcm-pager li.number')]"
            ".map(e => parseInt((e.innerText || '').trim(), 10))"
            ".filter(n => !isNaN(n))")
        return max(nums) if nums else 1
    except Exception:
        return 1


def wait_first_id_change(page, before, timeout_ms=15000):
    """等列表真的换页了（第一行的 articleId 变了）。"""
    deadline = timeout_ms / 1000
    step = 0.4
    waited = 0.0
    while waited < deadline:
        page.wait_for_timeout(int(step * 1000))
        waited += step
        try:
            now = page.evaluate(FIRST_ID_JS)
        except Exception:
            continue
        if now and now != before:
            return True
    return False


def collect_all(page, max_pages=None, quiet=False):
    """翻遍「内容管理」的每一页，返回全部文章记录。"""
    page.goto(MANAGE_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(9000)
    try:
        page.wait_for_selector(".article-list-item-mp", timeout=20000)
    except Exception:
        log("没等到文章列表。可能是未登录，或被重定向到登录页。")
        return []

    total = total_pages(page)
    if max_pages:
        total = min(total, max_pages)
    if not quiet:
        log(f"共 {total} 页，开始采集…")

    seen, articles = set(), []
    for pno in range(1, total + 1):
        rows = collect_page(page)
        new = 0
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            articles.append(r)
            new += 1
        if not quiet:
            log(f"  第 {pno}/{total} 页: {len(rows)} 行，新增 {new}（累计 {len(articles)}）")
        if pno >= total:
            break

        before = page.evaluate(FIRST_ID_JS)
        # 下一页按钮：翻到最后一页会带 disabled
        clicked = page.evaluate(
            "() => { const b = document.querySelector('button.btn-next');"
            " if (!b || b.disabled || b.getAttribute('aria-disabled') === 'true') return false;"
            " b.click(); return true; }")
        if not clicked:
            if not quiet:
                log("  没有下一页了")
            break
        if not wait_first_id_change(page, before):
            if not quiet:
                log("  翻页后内容没变化，停下")
            break
    return articles


# ---------------------------------------------------------------- 快照读写

def parse_time(s):
    """解析两种时间：快照的 ISO 格式（2026-10-07T22:02:20）和页面的空格格式。

    快照是自己写的 ISO，必须走 fromisoformat —— 早期版本只认空格格式，
    结果 gap_h 恒为 None，整个「本次新增」区块被静默清空，24/72h 也永远填不上。
    """
    s = str(s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def load_snapshots():
    """按时间升序返回所有快照。"""
    if not SNAP_DIR.exists():
        return []
    snaps = []
    for f in sorted(SNAP_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d["_file"] = str(f)
            if d.get("collectedAt"):
                snaps.append(d)
        except Exception:
            continue
    snaps.sort(key=lambda d: d["collectedAt"])
    return snaps


def save_snapshot(articles, account=""):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    payload = {
        "collectedAt": now.isoformat(timespec="seconds"),
        "account": account,
        "source": MANAGE_URL,
        "count": len(articles),
        "articles": articles,
    }
    f = SNAP_DIR / f"{now:%Y%m%d-%H%M%S}.json"
    f.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 长期时序：追加成 csv，方便自己画曲线
    csv_path = STATS_DIR / "history.csv"
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["collectedAt", "id", "publishTime", "title",
                        "views", "likes", "comments", "favorites"])
        for a in articles:
            w.writerow([payload["collectedAt"], a["id"], a["publishTime"], a["title"],
                        a["views"], a["likes"], a["comments"], a["favorites"]])
    return f, payload


def diff_snapshots(cur, prev):
    """两两快照对比，返回 {id: {"dViews":.., "dLikes":.., "dFav":..}} 和间隔小时数。"""
    out, hours = {}, None
    if not prev:
        return out, hours
    t1, t0 = parse_time(cur["collectedAt"]), parse_time(prev["collectedAt"])
    if t0 and t1:
        hours = (t1 - t0).total_seconds() / 3600
    old = {a["id"]: a for a in prev.get("articles", [])}
    for a in cur.get("articles", []):
        o = old.get(a["id"])
        if not o:
            continue
        out[a["id"]] = {
            "dViews": a["views"] - o.get("views", 0),
            "dLikes": a["likes"] - o.get("likes", 0),
            "dFavorites": a["favorites"] - o.get("favorites", 0),
            "prevViews": o.get("views", 0),
        }
    return out, hours


def aged_views(history, aid, pub, target_h, tol_h=8.0):
    """从历史快照里取「发布后约 target_h 小时」时的阅读量（做增量对比用）。

    只认落在 [target_h - tol_h, target_h + tol_h] 窗口里的快照，取最接近的那次；
    没有就返回 None —— 宁可空着，也不拿别的时点冒充。
    """
    best = None
    for snap in history:
        st = parse_time(snap.get("collectedAt"))
        if not st:
            continue
        age = (st - pub).total_seconds() / 3600
        if age < 0 or abs(age - target_h) > tol_h:
            continue
        rec = next((a for a in snap.get("articles", []) if a["id"] == aid), None)
        if not rec:
            continue
        d = abs(age - target_h)
        if best is None or d < best[0]:
            best = (d, rec["views"], age)
    return best


# ---------------------------------------------------------------- 聚合

def age_hours(pub, now):
    if not pub:
        return None
    h = (now - pub).total_seconds() / 3600
    return h if h >= 0 else None


def hour_bucket(pub):
    if not pub:
        return "未知"
    h = pub.hour
    if h < 6:
        return "00-05 凌晨"
    if h < 9:
        return "06-08 早高峰"
    if h < 12:
        return "09-11 上午"
    if h < 14:
        return "12-13 午间"
    if h < 18:
        return "14-17 下午"
    if h < 21:
        return "18-20 晚间"
    return "21-23 夜间"


WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def theme_words(titles):
    """标题里的英文技术词（出现 ≥2 次），按出现次数降序。"""
    counter = defaultdict(lambda: defaultdict(int))  # lower -> {原样: 次数}
    for t in titles:
        for w in set(re.findall(r"[A-Za-z][A-Za-z0-9+#.]{1,}", t or "")):
            if re.fullmatch(r"[a-z]{1,2}", w):
                continue          # 丢掉 a/of 这类噪声
            counter[w.lower()][w] += 1
    out = []
    for low, forms in counter.items():
        total = sum(forms.values())
        if total >= 2:
            out.append((max(forms.items(), key=lambda kv: kv[1])[0], total))
    out.sort(key=lambda kv: -kv[1])
    return out


def agg(rows, key_fn, min_n=1):
    """按 key_fn 分组，返回 [{key, n, total, avg, median, avgLikes}]，按 avg desc。"""
    groups = defaultdict(list)
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        groups[k].append(r)
    out = []
    for k, items in groups.items():
        if len(items) < min_n:
            continue
        vs = sorted(i["views"] for i in items)
        n = len(vs)
        med = vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2
        out.append({
            "key": k, "n": n, "total": sum(vs),
            "avg": sum(vs) / n, "median": med,
            "avgLikes": sum(i["likes"] for i in items) / n,
        })
    out.sort(key=lambda g: -g["avg"])
    return out


def table(rows, headers, aligns=None):
    """极简 markdown 表格。"""
    if not rows:
        return "_（无数据）_"
    aligns = aligns or ["---"] * len(headers)
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(aligns) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines)


def fmt_int(n):
    try:
        return f"{int(round(n)):,}"
    except (TypeError, ValueError):
        return str(n)


# ---------------------------------------------------------------- 报表

def build_report(cur, history, top_n=15, window_days=7, notes=None):
    now = parse_time(cur["collectedAt"]) or datetime.now()
    # 用副本加工：下面会往每篇里塞 _pub/_speed 等派生字段（含 datetime），
    # 直接改原对象会让快照没法再 json 序列化。
    arts = [dict(a) for a in cur.get("articles", [])]
    prev = history[-2] if len(history) >= 2 else None
    diffs, gap_h = diff_snapshots(cur, prev)

    for a in arts:
        pub = parse_time(a.get("publishTime"))
        a["_pub"] = pub
        a["_ageH"] = age_hours(pub, now)
        a["_ageD"] = (a["_ageH"] / 24) if a["_ageH"] is not None else None
        a["_speed"] = None
        d = diffs.get(a["id"])
        # 增量只看"有没有上一次的同 id 记录"，不依赖 gap_h：
        # gap_h 缺失时也要给出新增量，只是速度那一列留空。
        a["_dViews"] = d["dViews"] if d else None
        if d and gap_h and gap_h > 0.4:
            a["_speed"] = d["dViews"] / (gap_h / 24)
        a["_v24"] = a["_v72"] = None
        a["_v24age"] = a["_v72age"] = None
        if pub:
            r24 = aged_views(history, a["id"], pub, 24)
            r72 = aged_views(history, a["id"], pub, 72)
            if r24:
                a["_v24"], a["_v24age"] = r24[1], r24[2]
            if r72:
                a["_v72"], a["_v72age"] = r72[1], r72[2]

    total_v = sum(a["views"] for a in arts)
    total_l = sum(a["likes"] for a in arts)
    total_f = sum(a["favorites"] for a in arts)
    total_c = sum(a["comments"] for a in arts)

    L = []
    L.append("# CSDN 阅读数据复盘")
    L.append("")
    L.append(f"- 采集时间：{cur['collectedAt']}"
             + (f"（距上次 {gap_h:.1f} 小时）" if gap_h else "（首次采集，无对比基准）"))
    L.append(f"- 账号：{cur.get('account') or '未知'}   文章数：{len(arts)}")
    L.append(f"- 总阅读：**{fmt_int(total_v)}**   点赞 {fmt_int(total_l)}   "
             f"收藏 {fmt_int(total_f)}   评论 {fmt_int(total_c)}")
    if diffs:
        dv = sum(d["dViews"] for d in diffs.values())
        dl = sum(d["dLikes"] for d in diffs.values())
        df = sum(d["dFavorites"] for d in diffs.values())
        L.append(f"- 本次新增：阅读 **+{fmt_int(dv)}**   点赞 +{fmt_int(dl)}   "
                 f"收藏 +{fmt_int(df)}")
        # 速度是外推值，间隔太短会被放大得离谱（3 分钟 +2 阅读 = 960/天），
        # 所以只有隔了 1 小时以上才敢报。
        if gap_h and gap_h >= 1.0:
            L.append(f"- 新增速度：**{dv / (gap_h / 24):.0f} 阅读/天**"
                     f"（按 {gap_h:.1f} 小时的间隔折算）")
        else:
            L.append(f"- 新增速度：暂不折算——两次采集只隔了 "
                     f"{(gap_h or 0) * 60:.0f} 分钟，外推出来的「每天」会严重失真。"
                     f"隔几小时再跑一次才有意义。")
    L.append("")
    if notes:
        for n in notes:
            L.append(f"> {n}")
        L.append("")

    # ---- 一、新增排行 ----
    L.append("## 一、本次新增阅读排行")
    L.append("")
    if not diffs:
        L.append("_首次采集，只建立了基准。下次再跑就有增量和速度了。_")
    else:
        rows = [(a["title"][:44],
                 f"+{a['_dViews']:,}",
                 f"{a['_speed']:.1f}" if a["_speed"] is not None else "—",
                 fmt_int(a["views"]),
                 f"{a['_ageH']:.0f}h" if a["_ageH"] is not None else "—")
                for a in sorted(arts, key=lambda x: -(x["_dViews"] or 0))
                if (a["_dViews"] or 0) > 0][:top_n]
        L.append(table(rows, ["文章", "新增阅读", "速度/天", "累计阅读", "发布至今"],
                       ["---", "---:", "---:", "---:", "---:"]))
        if not rows:
            L.append("_这段时间所有文章的阅读量都没有变化。_")
    L.append("")

    # ---- 二、累计排行 ----
    L.append("## 二、累计阅读排行")
    L.append("")
    rows = [(a["title"][:44], fmt_int(a["views"]), a["likes"], a["favorites"],
             f"{a['_ageH'] / 24:.1f}d" if a["_ageH"] is not None else "—",
             fmt_int(a["views"] / a["_ageD"]) if a["_ageD"] and a["_ageD"] > 0.2 else "—")
            for a in sorted(arts, key=lambda x: -x["views"])[:top_n]]
    L.append(table(rows, ["文章", "阅读", "赞", "藏", "发布至今", "日均阅读"],
                   ["---", "---:", "---:", "---:", "---:", "---:"]))
    L.append("")

    # ---- 三、发布后 24h / 72h ----
    pub_rows = [(a["title"][:40], a["publishTime"],
                 (f"{fmt_int(a['_v24'])}（{a['_v24age']:.0f}h）"
                  if a["_v24"] is not None else "—"),
                 (f"{fmt_int(a['_v72'])}（{a['_v72age']:.0f}h）"
                  if a["_v72"] is not None else "—"))
                for a in sorted(arts, key=lambda x: -(x["_v24"] or -1))
                if a["_v24"] is not None or a["_v72"] is not None][:top_n]
    L.append("## 三、发布后 24 / 72 小时阅读（口径统一，最适合横向比较）")
    L.append("")
    if pub_rows:
        L.append("> 取「发布后 24 小时（±8 小时）」那次快照的阅读量，括号里是实际间隔，"
                 "不是刚好 24 小时。要做横向比较，就在文章发出后一天左右跑一次。")
        L.append("")
        L.append(table(pub_rows, ["文章", "发布时间", "24h 阅读", "72h 阅读"],
                       ["---", "---", "---:", "---:"]))
    else:
        L.append("_还没有落在「发布后 24 小时 ±8 小时」窗口内的快照。"
                 "以后每次跑这个脚本都会自动补上——想拿到这个数据，"
                 "就在文章发出后 24 小时左右跑一次。_")
    L.append("")

    # ---- 四、聚合口径 ----
    window = [a for a in arts if a["_ageD"] is not None and a["_ageD"] <= window_days]
    fallback = False
    if len(window) < 3:
        window = [a for a in arts if a["views"] > 0]
        fallback = True
    L.append(f"## 四、分组对比（口径：{'全部文章' if fallback else f'最近 {window_days} 天发布'}，"
             f"{len(window)} 篇）")
    L.append("")
    if fallback:
        L.append("> 最近窗口内不足 3 篇，已回退到全部文章。**这时候的分组平均阅读会被"
                 "文章年龄严重污染**（老文章天然累计更多），只当参考。")
        L.append("")

    L.append("### 4.1 按发布时间段")
    L.append("")
    g = agg(window, lambda r: r["publishTime"][11:13] + ":00" if len(r["publishTime"]) >= 13 else "未知")
    L.append(table([(x["key"], x["n"], fmt_int(x["avg"]), fmt_int(x["median"]))
                    for x in g if x["key"] != "未知"],
                   ["发布小时", "篇数", "平均阅读", "中位阅读"],
                   ["---", "---:", "---:", "---:"]))
    L.append("")

    L.append("### 4.2 按发布时间段（粗分）与星期")
    L.append("")
    g = agg(window, lambda r: hour_bucket(r["_pub"]))
    L.append(table([(x["key"], x["n"], fmt_int(x["avg"]), fmt_int(x["median"])) for x in g],
                   ["时段", "篇数", "平均阅读", "中位阅读"],
                   ["---", "---:", "---:", "---:"]))
    L.append("")
    g = agg(window, lambda r: WEEKDAYS[r["_pub"].weekday()] if r["_pub"] else None)
    L.append(table([(x["key"], x["n"], fmt_int(x["avg"]), fmt_int(x["median"])) for x in g],
                   ["星期", "篇数", "平均阅读", "中位阅读"],
                   ["---", "---:", "---:", "---:"]))
    L.append("")

    L.append("### 4.3 按标题特征（同一批文章内部对比，最能说明问题）")
    L.append("")
    rows = []
    for name, pat in TITLE_MARKERS:
        hit = [a for a in window if re.search(pat, a["title"])]
        miss = [a for a in window if not re.search(pat, a["title"])]
        if not hit:
            continue
        rows.append((name, f"{len(hit)}/{len(window)}",
                     fmt_int(sum(a["views"] for a in hit) / len(hit)),
                     fmt_int(sum(a["views"] for a in miss) / len(miss)) if miss else "—"))
    L.append(table(rows, ["标题特征", "命中篇数", "命中的平均阅读", "未命中的平均阅读"],
                   ["---", "---:", "---:", "---:"]))
    L.append("")
    lens = [len(a["title"]) for a in window]
    if lens:
        avg_len = sum(lens) / len(lens)
        L.append(f"- 标题平均长度：**{avg_len:.1f} 字**")
        g = agg([a for a in window if len(a["title"]) >= 15],
                lambda r: "≤30 字" if len(r["title"]) <= 30 else ">30 字")
        if len(g) > 1:
            L.append("- 长度分组：" + "；".join(
                f"{x['key']} {x['n']} 篇，平均 {fmt_int(x['avg'])}" for x in g))
    L.append("")

    # ---- 五、主题词 ----
    L.append("## 五、主题词表现（下一步该深挖什么）")
    L.append("")
    words = theme_words([a["title"] for a in window])
    if not words:
        L.append("_窗口内没有出现两次以上的英文技术词。_")
    else:
        rows = []
        for w, _cnt in words[:12]:
            hit = [a for a in window if re.search(re.escape(w), a["title"], re.I)]
            if not hit:
                continue
            rows.append((w, len(hit),
                         fmt_int(sum(a["views"] for a in hit) / len(hit)),
                         fmt_int(sum(a["views"] for a in hit))))
        rows.sort(key=lambda r: -int(str(r[2]).replace(",", "")))
        L.append(table(rows, ["主题词", "篇数", "平均阅读", "合计阅读"],
                       ["---", "---:", "---:", "---:"]))
        L.append("")
        if rows:
            L.append(f"- **最值得继续写**：`{rows[0][0]}`（{rows[0][1]} 篇，"
                     f"平均 {rows[0][2]} 阅读）")
            L.append(f"- **读者最不买账**：`{rows[-1][0]}`（{rows[-1][1]} 篇，"
                     f"平均 {rows[-1][2]} 阅读）")
    L.append("")

    L.append("## 六、零阅读/低阅读文章（最该回头看标题的）")
    L.append("")
    low = sorted([a for a in arts if a["views"] < 50], key=lambda x: x["views"])[:10]
    if low:
        L.append(table([(a["title"][:44], a["views"], a["publishTime"], a["kind"]) for a in low],
                       ["文章", "阅读", "发布时间", "类型"],
                       ["---", "---:", "---", "---"]))
    else:
        L.append("_没有阅读量低于 50 的文章。_")
    L.append("")
    L.append("---")
    L.append("")
    L.append("_数据来自 CSDN 创作中心「内容管理」页，只读采集。"
             "下次采集会与本次快照对比，给出真实新增阅读。_")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 命令

def cmd_collect(args):
    account = ""
    if not args.report_only:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = pc.Browser(p)
            b.launch()
            page = b.page()
            logged, hits = b.is_logged_in()
            log(f"登录态: {'有效' if logged else '无效'} {hits}")
            if not logged:
                b.close()
                raise SystemExit("没登录。先跑:  .\\csdn.bat login")
            arts = collect_all(page, args.pages)
            try:
                account = page.evaluate(
                    "() => { const m = document.body.innerHTML.match("
                    "/([0-9A-Za-z_]+)\\/article\\/details\\//); return m ? m[1] : ''; }")
            except Exception:
                pass
            if not args.keep_open:
                b.close()
        if not arts:
            return 1
        log(f"采集到 {len(arts)} 篇，正在写快照…")
        f, cur = save_snapshot(arts, account)
        log(f"快照: {f}")
    else:
        snaps = load_snapshots()
        if not snaps:
            raise SystemExit("还没有任何快照，先跑一次不带 --report-only 的采集。")
        cur = snaps[-1]
        account = cur.get("account", "")

    history = load_snapshots()
    report = build_report(cur, history, top_n=args.top, window_days=args.window_days)

    STATS_DIR.mkdir(parents=True, exist_ok=True)
    md = STATS_DIR / "latest.md"
    md.write_text(report, encoding="utf-8")
    (STATS_DIR / "latest.json").write_text(
        json.dumps({"snapshot": cur, "snapshotCount": len(history)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"报表: {md}")
    if not args.quiet:
        print()
        print(report)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="CSDN 阅读数据复盘：采集真实阅读数据、存快照、算增量、出报表（只读，不发布）")
    ap.add_argument("--pages", type=int, default=None, help="最多采集几页（默认全部）")
    ap.add_argument("--report-only", action="store_true", help="不联网，用已有快照重新出报表")
    ap.add_argument("--top", type=int, default=15, help="排行榜显示几行，默认 15")
    ap.add_argument("--window-days", type=int, default=7, help="分组对比只统计最近几天的文章，默认 7")
    ap.add_argument("--keep-open", action="store_true", help="采集完不关浏览器（调试用）")
    ap.add_argument("--quiet", action="store_true", help="不把报表打到终端")
    args = ap.parse_args()
    return cmd_collect(args)


if __name__ == "__main__":
    sys.exit(main())
