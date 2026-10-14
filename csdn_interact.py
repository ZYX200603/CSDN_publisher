#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键三连（点赞 / 收藏 / 评论）—— 按「近期发布 + 小作者优先」挑文章批量互动。

用法（`count` 由你每次指定，脚本里没有默认篇数）:

    python csdn_interact.py --ops like --count 50
    python csdn_interact.py --ops comment --count 20
    python csdn_interact.py --ops like,comment,save --count 50
    python csdn_interact.py --ops 三连 --count 50           # 三连 = like,save,comment
    python csdn_interact.py --stats                          # 只看库里的存量统计
    python csdn_interact.py --probe-comment <文章URL>         # 勘察评论框真实结构（只开面板，不提交）

为什么候选这么选（都是实测结论，别再走回头路）：
  * CSDN **没有**按发布时间倒序的文章列表——首页 tab 是领域分类、频道页 tab 只有
    推荐/付费专栏/VIP文章、热榜行里没有时间字段、搜索页的「最新」排序实测是坏的
    （返回 2022~2025 的文章，比「综合」还旧）。
  * 唯一能拿到近期文章的是搜索页 **综合排序（s=0）**，而且**结果列表自带发布日期**，
    所以能在列表层直接筛"近 N 天"，不必逐篇进详情页试错。
  * 推荐流 + 热榜实测 48h 命中率 1.7%，45% 的文章根本读不到发布时间 → 不作为主源。
  * 详情页 `.time` 是唯一可靠的发布时间来源，但它有两种形态：
    「于 … 发布」（有=可用）和「已于 … 修改」（**只有修改时间，没有发布时间**）。
    按约定：拿不到发布时间就跳过，不三连。

互动顺序固定 **赞 → 藏 → 评论**：因为评论文案里写了"我已一键三连"，
评论必须排在最后，否则话不实。

去重是「文章 × 操作」两维的：某篇点赞成功、评论被限流拦下，下次跑只补评论，
不会重点赞。这是本工具敢反复跑的前提。

⚠️ 批量互动是平台风控盯着的行为。默认每篇之间随机停 15~30 秒，遇限流立刻收手，
    并且每轮都把重大问题写进 `log/`。不要为了刷量把节奏调快。
"""

import argparse
import json
import random
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = pc.ROOT
OUTPUT_DIR = pc.OUTPUT_DIR
LOG_DIR = ROOT / "log"
DB_PATH = OUTPUT_DIR / "interact" / "interact.db"
PROFILE_DIR = ROOT / ".profile"
LOCKFILE = PROFILE_DIR / "lockfile"

ICFG = (pc.CFG.get("interact") or {})
SEL = ICFG.get("selectors") or {}
DEF = ICFG.get("defaults") or {}
HINTS = ICFG.get("limitHints") or {}
URLS = ICFG.get("urls") or {}

# 执行顺序不能改：评论说了"我已一键三连"，必须最后做
OPS_ALL = ("like", "save", "comment")
OP_CN = {"like": "点赞", "save": "收藏", "comment": "评论"}
OP_ALIAS = {
    "like": "like", "赞": "like", "点赞": "like", "digg": "like",
    "save": "save", "star": "save", "收藏": "save", "favorite": "save",
    "comment": "comment", "评论": "comment", "reply": "comment",
    "all": "all", "三连": "all", "一键三连": "all", "triple": "all",
}
# 视为"这件事已经做过了"，不再重复做
DONE_STATES = ("done", "done_unverified", "skipped_already")
# "已定性、不会再变"的跳过：老文永远是老文、读不到时间的模板也不会变。
# 这些也必须算"不用再看"，否则每轮都会重复打开同一批旧文，白白吃掉时间预算。
SETTLED_STATES = DONE_STATES + (
    "skipped_no_time", "skipped_old", "skipped_author_too_big", "skipped_excluded",
    "skipped_farm", "skipped_no_body", "skipped_no_author", "skipped_manual",
)


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def _now():
    return datetime.now()


# ============================================================ profile 自愈
# 实测故障：探针脚本崩溃/被中断后，chrome 残留进程会占住 .profile，
# 下一次启动报 TargetClosedError，并且 channel=chrome 会直接失败、
# 回退到不存在的自带 chromium，最终报"无法启动浏览器"。
# 表现很像"Chrome 没装"，实际只是脏锁。这里在启动失败时自愈一次。

_CLEAN_PS = r"""
$procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
         Where-Object { $_.CommandLine -like '*csdn-publisher\.profile*' }
$n = ($procs | Measure-Object).Count
$procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Write-Output ("killed=" + $n)
"""


def other_owner_alive():
    """profile 是否被**别的**进程占着？是就返回心跳信息，否则 None。

    为什么要单独判：心跳文件记的是 **Python 进程的 pid**。本项目所有工具都共用
    `.profile`，所以**同时开两个会话跑 CSDN 工具会互抢 profile**——实测表现就是
    "浏览器启动成功、几秒后自己退出"、`TargetClosedError` 反复出现，
    以及 `⛔ profile 正被另一个进程占用（pid=…）`。
    这种情况下**绝不能强清**：一清就把另一个会话正在跑的浏览器杀掉，
    两边的运行都会坏掉。正确反应是报出来、让用户串行跑。
    """
    try:
        hb = pc.read_heartbeat() or {}
    except Exception:
        return None
    pid = hb.get("pid")
    if isinstance(pid, int) and pid != os.getpid() and pc._pid_alive(pid):
        return hb
    return None


def clear_stale_profile():
    """清理本项目 profile 的陈旧锁 + 占用它的**残留** chrome。

    ★ 关键一步是**先清心跳**（`.browser-heartbeat.json`）：
      它记的是**本 Python 进程**的 pid，只要脚本还在跑就永远"活着"。
      于是 `publish_csdn.preflight_profile()` 会看到
      「锁文件存在 + pid 还活着」→ 判定 profile 被别的进程占用 → 直接
      `raise SystemExit("profile 正被占用")` 拒绝启动。
      这正好卡死"同一个进程内重启浏览器"这条路径——实测报错是
      `⛔ profile 正被另一个进程占用（pid=…，启动于 …）`，而那个 pid 就是自己。
      清掉心跳后，preflight 自己就会把陈旧锁当残留删掉。

    ★ 但如果是**真的别的会话**占着（pid 活着且不是我），一律不动手，只报告。
    只动本项目 profile 相关的进程与文件，绝不 taskkill /IM chrome.exe（会连你日常的
    Chrome 一起杀）。返回人类可读的处理说明；被别的会话占用时以 "REFUSE" 开头。
    """
    other = other_owner_alive()
    if other:
        msg = (f"REFUSE profile 正被另一个会话占用（pid={other.get('pid')}，"
               f"启动于 {other.get('started')}）——未做任何清理。"
               f"本项目所有工具共用 .profile，不能同时跑两个；先把那个跑完再回来。")
        log("⛔ " + msg)
        return msg

    notes = []
    try:
        pc.clear_heartbeat()
        notes.append("已清心跳标记")
    except Exception as e:
        notes.append(f"清心跳失败({e.__class__.__name__})")

    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _CLEAN_PS],
            capture_output=True, text=True, timeout=90)
        line = (out.stdout or "").strip().splitlines()
        notes.append(line[-1] if line else f"kill rc={out.returncode}")
    except Exception as e:
        notes.append(f"杀残留 chrome 失败({e.__class__.__name__})")

    # 删掉 chrome 留下的全部锁名（不只是 lockfile）
    removed = []
    for name in getattr(pc, "PROFILE_LOCK_NAMES", ("lockfile",)):
        p = PROFILE_DIR / name
        try:
            if p.exists() and not p.is_dir():
                p.unlink()
                removed.append(name)
        except Exception:
            pass
    notes.append(f"清锁={removed or '无'}")
    detail = " | ".join(notes)
    log(f"profile 清理: {detail}")
    return detail


def launch_browser(pw, issues, clean_first=False):
    """启动浏览器；失败时清理残留再重试一次。返回 Browser 或 None。

    clean_first 只在**本轮第一次启动**时用（清掉上一轮崩溃留下的残留）。
    运行中重启一律 clean_first=False：实测在会话进行中 force-kill chrome 会让
    profile 进入残缺状态，之后每次启动都会在几秒后自己退出——也就是说
    "每次启动前都强杀"本身就是故障源。稳定的做法是：
      干净启动一次 → 会话内一直复用；中途死了先优雅 close 再起，只在起不来时才强清。
    """
    if clean_first:
        log("启动前清理上一轮残留（chrome / lockfile）…")
        clear_stale_profile()
    # 别的会话正占着 profile 就别开：开了两边都会互相搞死
    other = other_owner_alive()
    if other:
        msg = (f"另一个会话正占用 .profile（pid={other.get('pid')}，"
               f"启动于 {other.get('started')}）。本项目所有工具共用同一个 profile，"
               f"**不能并行跑**——请等那边跑完，或先关掉它的浏览器。")
        log("⛔ " + msg)
        issues.append(("profile-busy", msg))
        return None
    b = pc.Browser(pw)
    try:
        b.launch()
        return b
    except (Exception, SystemExit) as e:
        # 必须接住 SystemExit：publish_csdn.Browser.launch() 在 chrome 起不来、
        # 自带 chromium 也不存在时是 raise SystemExit 的，它继承 BaseException，
        # `except Exception` 接不住，自愈逻辑会整个失效。
        msg = f"浏览器启动失败: {e.__class__.__name__}: {str(e)[:200]}"
        log(msg)
        issues.append(("browser-launch", msg))
        try:
            b.close()
        except Exception:
            pass
    log("清理残留后重试启动…")
    clear_stale_profile()
    b = pc.Browser(pw)
    try:
        b.launch()
        issues.append(("browser-launch-recovered",
                       "清理 profile 残留后启动成功（上一次是残留 chrome 占住 .profile）"))
        return b
    except (Exception, SystemExit) as e:
        msg = f"清理后仍无法启动浏览器: {e.__class__.__name__}: {str(e)[:200]}"
        log(msg)
        issues.append(("browser-launch-failed", msg))
        return None


# ============================================================ 存储（SQLite）
SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    article_id   TEXT PRIMARY KEY,
    url          TEXT,
    title        TEXT,
    author       TEXT,
    author_url   TEXT,
    fans         INTEGER,
    orig         INTEGER,
    publish_time TEXT,
    source       TEXT,
    first_seen   TEXT
);
CREATE TABLE IF NOT EXISTS actions (
    article_id TEXT NOT NULL,
    op         TEXT NOT NULL,
    status     TEXT NOT NULL,
    detail     TEXT,
    text       TEXT,
    ts         TEXT,
    run_id     TEXT,
    PRIMARY KEY (article_id, op)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    started    TEXT,
    finished   TEXT,
    ops        TEXT,
    wanted     INTEGER,
    done       INTEGER,
    stopReason TEXT,
    issues     TEXT
);
"""


def db_open():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.executescript(SCHEMA)
    # 轻量迁移：老库没有的列补上，避免"升级脚本把库跑废"
    cols = {r[1] for r in con.execute("PRAGMA table_info(articles)")}
    for name, decl in (("orig", "INTEGER"),):
        if name not in cols:
            con.execute(f"ALTER TABLE articles ADD COLUMN {name} {decl}")
    con.commit()
    return con


def db_state(con):
    """{article_id: {op: status}}"""
    out = {}
    for aid, op, st in con.execute("SELECT article_id, op, status FROM actions"):
        out.setdefault(aid, {})[op] = st
    return out


def db_upsert_article(con, rec):
    con.execute(
        """INSERT INTO articles (article_id, url, title, author, author_url, fans, orig,
                                 publish_time, source, first_seen)
           VALUES (:article_id, :url, :title, :author, :author_url, :fans, :orig,
                   :publish_time, :source, :first_seen)
           ON CONFLICT(article_id) DO UPDATE SET
             title=excluded.title, author=excluded.author, author_url=excluded.author_url,
             fans=excluded.fans, orig=excluded.orig, publish_time=excluded.publish_time""",
        rec)
    con.commit()


def db_record(con, article_id, op, status, detail="", text="", run_id=""):
    con.execute(
        """INSERT INTO actions (article_id, op, status, detail, text, ts, run_id)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(article_id, op) DO UPDATE SET
             status=excluded.status, detail=excluded.detail, text=excluded.text,
             ts=excluded.ts, run_id=excluded.run_id""",
        (article_id, op, status, (detail or "")[:300], (text or "")[:500],
         _now().isoformat(timespec="seconds"), run_id))
    con.commit()


def cmd_stats():
    if not DB_PATH.exists():
        print(f"还没有数据库: {DB_PATH}\n先跑一次互动（--ops ... --count N）")
        return 0
    con = db_open()
    print(f"数据库: {DB_PATH}\n")
    n_art = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"累计见过文章: {n_art}")
    print("\n按操作 × 状态统计:")
    print(f"  {'操作':<10}{'状态':<24}{'篇数':>6}")
    print("  " + "-" * 42)
    for op, st, c in con.execute(
            "SELECT op, status, COUNT(*) FROM actions GROUP BY op, status ORDER BY op, status"):
        print(f"  {op:<10}{st:<24}{c:>6}")
    print("\n最近 8 轮:")
    rows = list(con.execute(
        "SELECT run_id, ops, wanted, done, stopReason, started FROM runs ORDER BY started DESC LIMIT 8"))
    if not rows:
        print("  （还没有完整跑过一轮）")
    for r in rows:
        print(f"  {r[5]}  ops={r[1]:<20} 目标={r[2]} 实做={r[3]}  停止={r[4]}")
    print("\n作者规模分布（已记录粉丝数的）:")
    for lo, hi, lbl in [(0, 1000, "<1k"), (1000, 5000, "1k~5k"),
                        (5000, 10000, "5k~1w"), (10000, 50000, "1w~5w"), (50000, 10**12, ">5w")]:
        c = con.execute("SELECT COUNT(*) FROM articles WHERE fans >= ? AND fans < ?", (lo, hi)).fetchone()[0]
        print(f"  {lbl:<8}{c:>5}")

    print("\n最近记录的文章（最多 25 篇，粉丝少的在前 —— 这些才是会回你三连的人）:")
    print(f"  {'粉丝':>7} {'原创':>6}  {'作者':<20} 标题")
    print("  " + "-" * 88)
    for a, f, o, t in con.execute(
            """SELECT author, COALESCE(fans,-1), COALESCE(orig,-1), title FROM articles
               ORDER BY fans IS NULL, fans ASC LIMIT 25"""):
        print(f"  {f:>7} {o:>6}  {(a or '(未读到)')[:20]:<20} {(t or '')[:40]}")
    con.close()
    return 0


# ============================================================ 候选收集
RESULTS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/article/details/"]')) {
    const t = clean(a.innerText);
    if (t.length < 6) continue;
    const h = a.href.split('?')[0];
    if (seen.has(h)) continue; seen.add(h);
    let el = a, text = '';
    for (let i = 0; i < 7 && el; i++) {
      el = el.parentElement; if (!el) break;
      const x = clean(el.innerText);
      if (x.length > t.length && x.length < 600) { text = x; break; }
    }
    const d = text.match(/(\d{4}-\d{2}-\d{2})/);
    out.push({title: t.slice(0, 120), href: h, date: d ? d[1] : '', text: text.slice(0, 200)});
  }
  return out;
}
"""

NEXT_PAGE_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const cands = [...document.querySelectorAll('a,li,button,div,span')]
    .filter(e => { const t = clean(e.innerText); return t === '下一页' || t === '下页' || t === '>'; });
  if (!cands.length) return false;
  const el = cands[cands.length - 1];
  if (el.disabled || String(el.className).includes('disabled')) return false;
  el.click();
  return true;
}
"""


def article_id_of(url):
    m = re.search(r"/details/(\d+)", url or "")
    return m.group(1) if m else ""


FEED_LINKS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const seen = new Set(); const out = [];
  for (const a of document.querySelectorAll('a[href*="/article/details/"]')) {
    const t = clean(a.innerText);
    if (t.length < 6) continue;
    if (a.closest('.csdnside-copyright-footer') || a.closest('.layout-menus')) continue;
    const h = a.href.split('?')[0];
    if (seen.has(h)) continue; seen.add(h);
    out.push({title: t.slice(0, 120), href: h, date: ''});
  }
  return out;
}
"""


def _track_ok(title):
    """赛道初筛：标题必须命中赛道词，且不能命中排他词。

    为什么需要：实测关键词会撞车 —— `Gin` 抓到 **NCCL 的 GIN**（GPU-Initiated
    Networking）、`后端` 抓到「数字后端」（芯片）和「前后端分离」（前端）。
    这类误命中白开一次详情页才被发现，所以在列表层就先剔掉。
    """
    t = (title or "").lower()
    if not t:
        return False
    hit = ICFG.get("trackKeywords") or []
    bad = ICFG.get("trackExcludeKeywords") or []
    if any(k.lower() in t for k in bad):
        return False
    return any(k.lower() in t for k in hit)


def collect_feeds(page, urls, issues, pool_sources, scrolls=3):
    """从推荐流 / 热榜抓候选。

    为什么要有这个源：实测搜索页「近 7 天」的结果被内容农场淹没
    （26 篇里 12 篇是 1000~6400 篇原创 / 几十粉的 SEO 号），
    而推荐流/热榜里是**真实开发者**（801~7380 粉那种）。
    代价：这两个源的行里**没有发布日期**，只能进详情页才知道新旧，
    所以它们排在有日期的候选之后。
    """
    out = {}
    for url in urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(6000)
            for i in range(scrolls):
                for r in (page.evaluate(FEED_LINKS_JS) or []):
                    aid = article_id_of(r["href"])
                    if aid:
                        out.setdefault(aid, {**r, "article_id": aid,
                                             "keyword": f"feed:{url.rsplit('/', 1)[-1]}"})
                page.mouse.wheel(0, 2600)
                page.wait_for_timeout(2000)
        except Exception as e:
            issues.append(("feed-goto", f"{url} 抓取失败: {e.__class__.__name__}"))
            continue
        pool_sources[url] = len(out)
        log(f"  推荐流 {url.rsplit('/', 1)[-1]:<12} 累计候选 {len(out):>3} 篇")
    return out


def collect_candidates(page, keywords, pages_per_kw, days, want, issues, pool_sources):
    """抓候选：搜索页「综合」排序（有日期）+ 推荐流/热榜（无日期）。

    提前退出的判据是**窗口内的候选数**，不是候选总数：
    实测 34 篇候选里近 7 天只有 4 篇，按"总数够了就停"会在 count 小的时候
    刚抓几十篇就收工，新加的关键词根本轮不到用。
    """
    today = _now().date()
    found = {}

    def in_window():
        n = 0
        for r in found.values():
            d = r.get("date")
            if not d:
                continue
            try:
                if (today - datetime.fromisoformat(d).date()).days <= days:
                    n += 1
            except Exception:
                pass
        return n

    # 窗口内候选的目标量：给 3 倍余量（后面还有农场/粉丝/正文三道关要过）
    target_in_window = max(want * 3, 15)

    for kw in keywords:
        if in_window() >= target_in_window:
            break
        url = (URLS.get("search") or "https://so.csdn.net/so/search?q={kw}&t=blog&s=0").replace(
            "{kw}", kw.replace(" ", "%20"))
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(5000)
        except Exception as e:
            issues.append(("candidate-goto", f"关键词 {kw} 打不开: {e.__class__.__name__}"))
            continue
        got_kw = 0
        for p in range(pages_per_kw):
            try:
                rows = page.evaluate(RESULTS_JS) or []
            except Exception as e:
                issues.append(("candidate-read", f"关键词 {kw} 第 {p+1} 页读取失败: {e.__class__.__name__}"))
                break
            for r in rows:
                aid = article_id_of(r["href"])
                if not aid or aid in found:
                    continue
                found[aid] = {**r, "article_id": aid, "keyword": kw}
                got_kw += 1
            if p == pages_per_kw - 1:
                break
            try:
                if not page.evaluate(NEXT_PAGE_JS):
                    break
                page.wait_for_timeout(5000)
            except Exception:
                break
        pool_sources[kw] = got_kw
        log(f"  关键词 {kw:<12} 新增候选 {got_kw:>3} ｜ 池内 {len(found):>3} 篇，"
            f"其中 {days} 天内 {in_window():>2} 篇（目标 ≥{target_in_window}）")

    # 第二源：推荐流 / 热榜（真实作者多，但行里没日期）
    feeds = ICFG.get("feedUrls") or []
    if feeds:
        log(f"抓推荐流/热榜（{len(feeds)} 个源，行里没有日期，只能进详情页判新旧）…")
        for aid, r in collect_feeds(page, feeds, issues, pool_sources).items():
            found.setdefault(aid, r)

    # 赛道初筛：撞车的关键词会带来 GPU/NCCL/前端/芯片 这类无关结果
    before = len(found)
    found = {k: v for k, v in found.items() if _track_ok(v.get("title"))}
    log(f"赛道初筛: {before} → {len(found)} 篇（标题须命中赛道词，排除 "
        f"{'、'.join((ICFG.get('trackExcludeKeywords') or [])[:6])}… 这类误命中）")

    # 粗筛：列表日期在窗口内 → 高置信；列表没日期 → 放最后，交给详情页判定
    dated, undated = [], []
    for r in found.values():
        if not r.get("date"):
            undated.append(r)
            continue
        try:
            age = (today - datetime.fromisoformat(r["date"]).date()).days
        except Exception:
            undated.append(r)
            continue
        if age <= days:
            r["age_days"] = age
            dated.append(r)
    dated.sort(key=lambda r: r.get("age_days", 999))
    log(f"候选池: 去重 {len(found)} 篇 → 列表日期在 {days} 天内 {len(dated)} 篇，"
        f"列表无日期 {len(undated)} 篇（后排，详情页再判）")
    return dated + undated


# ============================================================ 详情页读取
DETAIL_JS = r"""
(sel) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => !!el && el.getClientRects().length > 0;
  const q = (list) => { for (const s of list) { const e = document.querySelector(s); if (e) return e; } return null; };
  const out = {};
  out.title = clean((document.querySelector('h1.title-article') || document.querySelector('h1') || {}).innerText);
  const tEl = q(sel.publish_time);
  out.timeRaw = tEl ? clean(tEl.innerText) : '';
  out.timeFrom = tEl ? 'selector' : '';
  if (!out.timeRaw) {
    // 兜底：有些模板（笔记/短文/VIP）没有 .time，直接扫页头里形如
    // 「于 2026-09-14 08:00:00 发布」「已于 … 修改」的短文本
    for (const el of document.querySelectorAll('span,div,em,i,p')) {
      if (el.children.length) continue;
      const t = clean(el.innerText);
      if (t && t.length < 60 && /(于|发布于)\s*\d{4}-\d{2}-\d{2}/.test(t)) {
        out.timeRaw = t; out.timeFrom = 'scan:' + String(el.className).slice(0, 30); break;
      }
    }
  }
  const aEl = q(sel.author_name);
  out.author = aEl ? clean(aEl.innerText) : '';
  const aLink = q(sel.author_link);
  out.authorUrl = aLink ? (aLink.href || '') : '';
  // 作者资料：dl 依次是 原创 / 点赞 / 收藏 / 粉丝
  out.fansItems = [...document.querySelectorAll(sel.author_fans_items.join(','))]
                    .map(e => clean(e.innerText)).filter(Boolean);
  const attend = q(sel.attend_btn);
  out.following = attend ? String(attend.className).includes('tool-unbt-attend') : null;
  // 三连当前状态：isactive 的图 visible 就代表"已赞/已藏"
  const likeOn = q(sel.like_on_img), likeOff = q(sel.like_off_img);
  out.liked = vis(likeOn);
  out.likeImgVisible = { on: vis(likeOn), off: vis(likeOff) };
  const saveOn = q(sel.save_on_img), saveOff = q(sel.save_off_img);
  out.saved = vis(saveOn);
  out.saveImgVisible = { on: vis(saveOn), off: vis(saveOff) };
  const lc = q(sel.like_count), sc = q(sel.save_count);
  out.likeCount = lc ? clean(lc.innerText) : '';
  out.saveCount = sc ? (sc.getAttribute('data-num') || clean(sc.innerText)) : '';
  out.hasToolbar = !!q(sel.toolbar);
  return out;
}
"""


def read_detail(page, tries=3, wait_ms=1600):
    """读详情页，并**重试补齐作者名 / 粉丝数**。

    为什么要重试：作者资料块（`.profile-name`、`.profile-intro-rank-information`）
    是异步渲染的，比 `.time` 晚。实测 2026-10-08 的 prepare 阶段只等 3.5 秒时读到
    「作者名空串 + 粉丝 None」，而粉丝数是最关键的一道过滤（大 V 不会回你三连）——
    读不到就等于**过滤器静默失效**，还会把评论写成「大佬您好，博主」。
    所以这里必须以「作者名和粉丝数都拿到」为成功条件重试；实在拿不到才放弃。
    """
    d = {}
    for i in range(max(1, tries)):
        try:
            d = page.evaluate(DETAIL_JS, SEL) or {}
        except Exception:
            d = {}
        fans, _ = parse_author_stats(d.get("fansItems"))
        if d.get("author") and fans is not None:
            return d
        if i < tries - 1:
            page.wait_for_timeout(wait_ms)
    return d


def parse_publish(raw):
    """返回 (datetime|None, kind)。kind: publish / edited / relative / empty / unparsed。"""
    if not raw:
        return None, "empty"
    m = re.search(r"于\s*(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*发布", raw)
    if m:
        return datetime.fromisoformat(f"{m.group(1)} {m.group(2)}"), "publish"
    m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", raw)
    if m:
        return datetime.fromisoformat(f"{m.group(1)} {m.group(2)}"), ("edited" if "修改" in raw else "bare")
    m = re.search(r"(\d+)\s*(分钟|小时|天)前", raw)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"分钟": timedelta(minutes=n), "小时": timedelta(hours=n), "天": timedelta(days=n)}[unit]
        return _now() - delta, "relative"
    return None, "unparsed"


def _num_of(t):
    """从 '2万+ 点赞' / '872 原创' / '1234' 里取数值。"""
    m = re.search(r"([\d.]+)\s*(万)?", t or "")
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    if m.group(2):
        n *= 10000
    return int(n)


def parse_author_stats(items):
    """从 ['872 原创','2万+ 点赞','7万+ 收藏','25万+ 粉丝'] 取 (粉丝数, 原创数)。

    原创数用来识别内容农场：真正的新人写 20 篇攒 36 粉，
    而营销/SEO 号是几百篇原创却只有几十粉。
    """
    fans = orig = None
    for t in items or []:
        if "粉丝" in t:
            fans = _num_of(t)
        elif "原创" in t:
            orig = _num_of(t)
    return fans, orig


def parse_fans(items):
    """兼容旧调用：只要粉丝数。"""
    return parse_author_stats(items)[0]


# ============================================================ 三连操作
def read_notice(page, hints, timeout_ms=2500):
    """轮询抓一闪而过的 toast，命中任一 hint 就返回文案。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            t = page.evaluate(
                """(kws) => {
                    const vis = el => el.getClientRects().length > 0;
                    const nodes = [...document.querySelectorAll(
                        '.el-message, .el-message-box, .el-notification, .el-message-box__wrapper')];
                    for (const b of nodes) {
                        if (!vis(b)) continue;
                        const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (t && kws.some(k => t.includes(k))) return t.slice(0, 200);
                    }
                    return null;
                }""", list(hints))
            if t:
                return t
        except Exception:
            return None
        page.wait_for_timeout(300)
    return None


def read_any_toast(page):
    try:
        return page.evaluate(
            """() => {
                const vis = el => el.getClientRects().length > 0;
                const nodes = [...document.querySelectorAll(
                    '.el-message, .el-notification, .el-message-box')].filter(vis);
                return nodes.map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim())
                            .filter(Boolean).join(' | ').slice(0, 200);
            }""")
    except Exception:
        return ""


def state_after(page, op):
    """读某个操作之后的真实状态：'on' / 'off' / None(读不到)。"""
    try:
        return page.evaluate(
            """(sel) => {
                const vis = el => !!el && el.getClientRects().length > 0;
                const q = (list) => { for (const s of list) { const e = document.querySelector(s); if (e) return e; } return null; };
                if (sel.kind === 'like') return vis(q(sel.on)) ? 'on' : (q(sel.off) ? 'off' : null);
                return vis(q(sel.on)) ? 'on' : (q(sel.off) ? 'off' : null);
            }""",
            {"kind": op, "on": SEL["like_on_img"] if op == "like" else SEL["save_on_img"],
             "off": SEL["like_off_img"] if op == "like" else SEL["save_off_img"]})
    except Exception:
        return None


def click_js(page, selectors):
    """用 JS 点第一个命中的元素（比 locator.click 更耐浮层遮挡）。返回命中的选择器或 None。"""
    try:
        return page.evaluate(
            """(sels) => {
                for (const s of sels) {
                    const el = document.querySelector(s);
                    if (!el) continue;
                    const target = el.querySelector('a.tool-item-href') || el;
                    target.click();
                    return s;
                }
                return null;
            }""", selectors)
    except Exception:
        return None


def native_click(page, selector, timeout=8000):
    """用 Playwright 原生 click（可信事件）。selector 可以是字符串或**选择器列表**（按顺序试）。

    ⚠️ 踩过的坑：`SEL["like_item"]` 是列表，早期版本把它整个当字符串传给 page.locator()，
    直接抛 `SyntaxError: Failed to execute 'querySelectorAll'`（非法选择器）。
    返回值里的第二个元素是**实际生效的选择器**，方便写日志定位。
    """
    sels = [selector] if isinstance(selector, str) else list(selector or [])
    if not sels:
        return False, "没有可用的选择器"
    last = "无候选"
    for s in sels:
        try:
            loc = page.locator(s).first
            if loc.count() == 0:
                last = f"{s} → 元素不存在"
                continue
            loc.click(timeout=timeout)
            return True, s
        except Exception as e:
            last = f"{s} → {e.__class__.__name__}: {str(e)[:70]}"
            continue
    return False, last


def read_like_save_state(page):
    """读点赞/收藏的真实状态。**任何点击之前都必须先读**：

    ⚠️ CSDN 的点赞和收藏都是**开关（toggle）**。实测事故（2026-10-08）：
       do_like() 点之前没读状态，把用户**已经赞过**的文章点成了取消。
       所以这里返回 liked / saved，调用方据此决定"已做就绝不点"。
    """
    try:
        return page.evaluate(
            """() => {
                const vis = el => !!el && el.getClientRects().length > 0;
                const q = s => document.querySelector(s);
                return {
                  liked: vis(q('#is-like-imgactive')),
                  saved: vis(q('#is-collection-imgactive')),
                  likeCount: q('#spanCount') ? q('#spanCount').innerText.trim() : null,
                  saveCount: q('.count.get-collection')
                             ? (q('.count.get-collection').getAttribute('data-num') || '') : null,
                  collectText: q('#is-collection') ? q('#is-collection').innerText.trim() : null,
                };
            }""") or {}
    except Exception:
        return {}


def do_like(page, dry, already=None):
    """点赞。**已赞就绝不点**——点赞是开关，误点会把用户已有的赞取消掉。"""
    if already is None:
        already = read_like_save_state(page).get("liked")
    if already:
        return "skipped_already", "页面显示已是「已赞」→ 不点（点赞是开关，再点会取消）"
    if dry:
        return "done_unverified", "dry-run 不点击"
    hit, why = native_click(page, SEL["like_item"])
    if not hit:
        # 点不动 ≠ 被平台限额。选择器写错/脚本 bug 也会走到这里，
        # 那种情况**不能触发熔断**（否则会把后面所有篇目的赞一起跳过）。
        return "failed", f"点赞按钮点击执行失败（脚本/选择器问题，非平台限额）：{why}"
    page.wait_for_timeout(2200)
    limit = read_notice(page, HINTS.get("like") or [])
    if limit:
        return "blocked", f"限流: {limit}"
    if read_like_save_state(page).get("liked"):
        return "done", "已赞（回读确认）"
    # 点击成功、按钮点得到，但状态不变、也没有任何提示 —— 这才是平台静默拒绝
    # （实测就是"每日点赞数上限"那种）。用独立状态标记，让调用方熔断本轮剩余篇目。
    return ("skipped_rejected",
            "点了点赞按钮但状态没变（页面无提示）——疑似每日点赞上限/静默拒绝。"
            "**不要重复点**（点赞是开关，反复点可能互相抵消）")


def do_save(page, dry, already=None):
    """收藏。**已收藏就绝不点**；点了之后要处理「添加收藏夹」弹窗才算成功。"""
    if already is None:
        already = read_like_save_state(page).get("saved")
    if already:
        return "skipped_already", "页面显示已是「已收藏」→ 不点"
    if dry:
        return "done_unverified", "dry-run 不点击"
    hit, why = native_click(page, SEL["save_item"])
    if not hit:
        return "failed", f"收藏按钮点击执行失败（脚本/选择器问题，非平台限额）：{why}"
    page.wait_for_timeout(2800)
    limit = read_notice(page, HINTS.get("save") or [])
    if limit:
        return "blocked", f"限流: {limit}"
    if read_like_save_state(page).get("saved"):
        return "done", "已收藏（回读确认）"
    # 第二步：弹出「添加收藏夹」→ 点那一行右侧的「收藏」按钮
    folder = confirm_collect_folder(page)
    if folder:
        page.wait_for_timeout(2500)
        if read_like_save_state(page).get("saved"):
            return "done", f"已收藏到「{folder}」（弹窗二次确认）"
        return "skipped_rejected", f"在「添加收藏夹」里点了「{folder}」但状态未变"
    return ("skipped_rejected",
            "点了收藏但没出现「添加收藏夹」弹窗，状态也没变——疑似平台静默拒绝")


def confirm_collect_folder(page, timeout_ms=10000):
    """等「添加收藏夹」弹窗出现，再用**原生点击**点那一行的「收藏」按钮。返回收藏夹名字。

    实测钉死的结构（2026-10-08，连做三个探针才拿到）：
      #csdn-collection.csdn-collection-theme-default        ← 弹窗根（fixed, z=99999）
        └ .csdn-collection-container
            └ ul.csdn-collection-items > li.csdn-collection-item
                ├ .csdn-collection-item_title > span.title-m   「我的收藏夹」
                └ span.collect-btn   「收藏」                    ← 必须点这个才算收藏成功

    两个坑（都踩过，别再犯）：
      ① 容器 class **不是** .el-dialog / .collect-box / [role=dialog] ——
         之前照那套猜，连续三轮都找不到弹窗，还误报成"没出现弹窗"；
      ② 必须用 Playwright **原生** click。用 JS `el.click()` 点了**毫无反应**
         （弹窗不关、状态不变、也没有 toast），这是不可信事件被前端忽略。

    用户口径（2026-10-08）：点「我的收藏夹」那一行、或点它右边的「收藏」都可以；
    账号下只有一个收藏夹，收它就行。
    """
    dialog = (SEL.get("collect_dialog") or ["#csdn-collection"])[0]
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        try:
            if page.locator(dialog).first.count():
                break
        except Exception:
            pass
        page.wait_for_timeout(400)
    else:
        log(f"      等「添加收藏夹」弹窗超时（{timeout_ms}ms 内没等到 {dialog}）")
        return None

    folder = ""
    try:
        loc = page.locator((SEL.get("collect_folder_name") or [dialog]).first).first
        if loc.count():
            folder = (loc.inner_text() or "").strip()
    except Exception:
        folder = ""

    ok, used = native_click(page, SEL.get("collect_confirm") or [dialog])
    if not ok:
        log(f"      「添加收藏夹」弹窗里点确认失败: {used}")
        return None
    log(f"      「添加收藏夹」→ 点「收藏」确认（{used}）")
    return folder or "我的收藏夹"


def find_editor(page):
    for sel in SEL["comment_editor"]:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                return loc, sel
        except Exception:
            continue
    return None, None


def do_comment(page, text, dry):
    # 1) 打开评论面板（用原生 click：实测 JS el.click() 有时不触发 CSDN 的处理函数）
    opened = None
    for sel in SEL["comment_open"]:
        ok, why = native_click(page, sel, timeout=6000)
        if ok:
            opened = sel
            break
    if not opened:
        return "failed", "打不开评论面板 " + str(SEL["comment_open"])
    page.wait_for_timeout(2800)

    # 2) 找输入区（实测未能提前确认是 textarea 还是 contenteditable，所以逐个试）
    ed, ed_sel = find_editor(page)
    if not ed:
        return "failed", ("找不到评论输入框，需要跑 --probe-comment <URL> 勘察真实结构；"
                          "候选: " + str(SEL["comment_editor"]))
    if dry:
        return "done_unverified", f"dry-run 不提交（输入框 {ed_sel}）"

    try:
        tag = ed.evaluate("el => el.tagName.toLowerCase()")
        ce = ed.evaluate("el => el.getAttribute('contenteditable')")
        if tag in ("textarea", "input"):
            ed.click()
            ed.fill(text)
        else:
            ed.click()
            page.keyboard.insert_text(text)
        page.wait_for_timeout(900)
        got = ed.evaluate("el => el.value !== undefined ? el.value : el.innerText") or ""
        if re.sub(r"\s+", "", got) != re.sub(r"\s+", "", text):
            # 回退：直接把内容写进去再派发 input 事件（应对受控组件）
            page.evaluate(
                """([sels, txt]) => {
                    const el = document.querySelector(sels.find(s => document.querySelector(s)));
                    if (!el) return;
                    if (el.value !== undefined) { el.value = txt; }
                    else { el.innerText = txt; }
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }""", [SEL["comment_editor"], text])
            page.wait_for_timeout(700)
    except Exception as e:
        return "failed", f"写入评论失败: {e.__class__.__name__}: {str(e)[:120]}"

    # 3) 提交（用原生 click）
    before = read_any_toast(page)
    submitted = None
    for sel in SEL["comment_submit"]:
        ok, why = native_click(page, sel, timeout=6000)
        if ok:
            submitted = sel
            break
    if not submitted:
        return ("failed",
                "找不到提交按钮，需要跑 --probe-comment 勘察；候选: " + str(SEL["comment_submit"]))
    page.wait_for_timeout(2800)

    limit = read_notice(page, HINTS.get("comment") or [], 3500)
    if limit:
        return "blocked", f"限流/限制: {limit}"
    toast = read_any_toast(page)
    if toast and toast != before and any(k in toast for k in ("成功", "发布", "审核")):
        return "done", f"提交成功（{toast[:60]}）"

    # ★ 回读校验：在评论区里找我们这句话的特征片段，找到才算成功。
    # 为什么必须做：实测（2026-10-08）3 条评论里**有 1 条根本没发出去**
    # （另一台浏览器打开该文章显示「0 条评论」），但当时只看"输入框被清空"就记了
    # done_unverified —— 而它属于"已完成"，等于谎报成功且再也不会重试。
    frag = comment_fragment(text)
    if frag:
        for _ in range(6):
            try:
                area = page.evaluate(VERIFY_JS) or ""
                if frag in area:
                    return "done", f"已回读确认：评论出现在页面上（校验片段「{frag}」）"
            except Exception:
                break
            page.wait_for_timeout(1500)

    try:
        left = ed.evaluate("el => el.value !== undefined ? el.value : el.innerText") or ""
        if not re.sub(r"\s+", "", left):
            return ("done_unverified",
                    f"输入框已清空，但**没在评论区找到这条评论**（校验片段「{frag}」）——"
                    f"可能仍在审核，也可能压根没发出去。"
                    f"跑 `--verify-comments` 可复核；确认没发出去就改成可重试。")
    except Exception:
        pass
    return ("done_unverified",
            f"已点提交（{submitted}）但没抓到成功提示，也没看到输入框清空——"
            f"可能已发出并进入审核，**建议用 `--verify-comments` 复核**；toast={toast[:60]!r}")


DO_OP = {"like": do_like, "save": do_save, "comment": do_comment}


# ============================================================ 日志（log/）
def write_log(run_id, meta, rows, issues, stopped):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _now().strftime("%Y-%m-%d-%H%M")
    path = LOG_DIR / f"{stamp}-interact.md"
    counts = {}
    for r in rows:
        for op, (st, _) in r["ops"].items():
            counts[(op, st)] = counts.get((op, st), 0) + 1

    lines = [f"# 一键三连 · {_now():%Y-%m-%d %H:%M}", ""]
    lines += [
        f"- run_id: `{run_id}`",
        f"- 请求操作: {' → '.join(OP_CN[o] for o in meta['ops'])}",
        f"- 目标篇数: {meta['wanted']}",
        f"- 实际完成: {meta['done']} 篇",
        f"- 筛选条件: 发布时间 {meta['days']} 天内，作者粉丝 < {meta['maxFans'] or '不限'}",
        f"- 候选池: 搜索页综合排序 × {len(meta['keywords'])} 个关键词 × {meta['pagesPerKw']} 页",
        f"- 停止原因: {stopped}",
        f"- 耗时: {meta['elapsed']}",
        "",
        "## 操作 × 状态统计",
        "",
        "| 操作 | 状态 | 篇数 |",
        "|---|---|---|",
    ]
    for (op, st), c in sorted(counts.items()):
        lines.append(f"| {OP_CN.get(op, op)} | {st} | {c} |")

    lines += ["", "## 逐篇明细", "",
              "| # | 文章 | 作者 | 粉丝 | 原创 | 发布时间 | " + " | ".join(OP_CN[o] for o in meta["ops"]) + " |",
              "|---|---|---|---|---|---|" + "---|" * len(meta["ops"])]
    for i, r in enumerate(rows, 1):
        cells = []
        for op in meta["ops"]:
            st, detail = r["ops"].get(op, ("—", ""))
            cells.append(f"{st}<br><sub>{str(detail)[:60]}</sub>" if detail else st)
        title = (r.get("title") or "")[:46]
        url = r.get("url") or ""
        lines.append(f"| {i} | [{title}]({url}) | {r.get('author') or '—'} | "
                     f"{r.get('fans') if r.get('fans') is not None else '—'} | "
                     f"{r.get('orig') if r.get('orig') is not None else '—'} | "
                     f"{r.get('publish') or '—'} | " + " | ".join(cells) + " |")

    lines += ["", "## 重大问题", ""]
    if issues:
        for kind, msg in issues:
            lines.append(f"- `{kind}` {msg}")
    else:
        lines.append("- 无")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")

    # 索引
    idx = LOG_DIR / "README.md"
    if not idx.exists():
        idx.write_text(
            "# 运行日志索引\n\n"
            "> 约定：**每次操作（互动/发布/采集）跑完都在本目录留一份 md**，"
            "平时遇到重大问题也写进这里，方便随时回看。\n"
            "> 命名 `YYYY-MM-DD-HHmm-<动作>.md`；本文件只追加一行摘要。\n"
            "> **动手前先看 `known-issues.md`** —— 那里记着会反复咬人的坑"
            "（尤其\"浏览器 profile 脏锁\"会被误判成 Chrome 没装）。\n\n"
            "| 时间 | 动作 | 结果 | 停止原因 | 日志 |\n|---|---|---|---|---|\n",
            encoding="utf-8")
    with idx.open("a", encoding="utf-8") as f:
        f.write(f"| {_now():%Y-%m-%d %H:%M} | 一键三连({'/'.join(OP_CN[o] for o in meta['ops'])}) "
                f"| 实做 {meta['done']}/{meta['wanted']} | {stopped} | [{path.name}]({path.name}) |\n")
    return path


# ============================================================ 勘察评论框
PROBE_COMMENT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const vis = el => !!el && el.getClientRects().length > 0;
  const box = document.querySelector('#pcCommentBox') || document.querySelector('.comment-box');
  const out = {boxExists: !!box, boxVisible: vis(box)};
  if (box) {
    out.boxHtml = box.outerHTML.replace(/\s+/g, ' ').slice(0, 2200);
    out.editables = [...box.querySelectorAll('textarea, [contenteditable], input[type=text]')].map(el => ({
      tag: el.tagName, id: el.id || '', cls: String(el.className).slice(0, 70),
      ce: el.getAttribute('contenteditable'), ph: el.getAttribute('placeholder') || '',
      visible: vis(el)}));
    out.buttons = [...box.querySelectorAll('button, a, div, span')]
      .filter(el => vis(el) && clean(el.innerText) && clean(el.innerText).length <= 10 &&
                    /发布|提交|发送|评论|取消/.test(clean(el.innerText)))
      .slice(0, 12)
      .map(el => ({tag: el.tagName, id: el.id || '', cls: String(el.className).slice(0, 70),
                   text: clean(el.innerText)}));
  }
  return out;
}
"""


def cmd_probe_comment(pw, url):
    """勘察评论框真实结构。只点开面板，不输入文字、不提交。"""
    issues = []
    b = launch_browser(pw, issues)
    if not b:
        return 1
    try:
        page = b.page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        before = page.evaluate(PROBE_COMMENT_JS)
        print("点击「写评论」前:", json.dumps(before, ensure_ascii=False)[:400], flush=True)
        opened = None
        for sel in SEL["comment_open"]:
            try:
                loc = page.locator(sel).first
                if loc.count():
                    loc.click(timeout=6000)
                    opened = sel
                    break
            except Exception:
                continue
        page.wait_for_timeout(3000)
        after = page.evaluate(PROBE_COMMENT_JS)
        out = {"url": url, "openedWith": opened, "before": before, "after": after,
               "configCandidates": {"editor": SEL["comment_editor"], "submit": SEL["comment_submit"]}}
        print(f"\n点开用: {opened}", flush=True)
        print(f"面板可见: {after.get('boxVisible')}", flush=True)
        print("输入区候选:", flush=True)
        for e in after.get("editables") or []:
            print(f"  <{e['tag']} id={e['id']!r} ce={e['ce']!r} ph={e['ph']!r} visible={e['visible']}> "
                  f"{e['cls'][:60]!r}", flush=True)
        print("按钮候选:", flush=True)
        for bt in after.get("buttons") or []:
            print(f"  <{bt['tag']} id={bt['id']!r}> {bt['text']!r} {bt['cls'][:60]!r}", flush=True)
        print(f"\n面板 HTML: {str(after.get('boxHtml'))[:1200]}", flush=True)
        path = OUTPUT_DIR / "interact" / "probe-comment.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入: {path}", flush=True)
    finally:
        b.close()
    return 0


# ============================================================ 阶段①：抓正文摘要待写评论
EXCERPT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const box = document.querySelector('#content_views')
           || document.querySelector('article')
           || document.querySelector('.blog-content-box');
  const body = box ? (box.innerText || '') : '';
  const heads = box
    ? [...box.querySelectorAll('h2, h3')].map(h => clean(h.innerText)).filter(Boolean).slice(0, 12)
    : [];
  return {
    body: body.replace(/\n{3,}/g, '\n\n').slice(0, 2200),
    bodyLen: body.replace(/\s+/g, '').length,
    headings: heads,
    hasBox: !!box
  };
}
"""


def compose_comment(author, body):
    """拼出最终评论文本：开头固定「大佬您好，{昵称}」，后面接我写的感悟。

    昵称**原样**使用（用户确认：不管多怪都照用，例如 `❀͜͡傀儡师`、`L oiey`）；
    真拿不到作者名时才退回「博主」。感悟为空返回空串 → 调用方据此跳过评论。
    """
    body = (body or "").strip()
    if not body:
        return ""
    name = (author or "").strip() or "博主"
    return f"大佬您好，{name}\n{body}"


def comments_path_for(pending_path):
    """感悟文件路径：与被审清单同目录同名，后缀换成 `.comments.json`。"""
    p = Path(pending_path)
    stem = p.name[:-5] if p.name.endswith(".json") else p.name
    return p.with_name(f"{stem}.comments.json")


def load_pending(pending_path):
    """读清单 + 我的感悟，返回 (meta, items[含 comment_body])。"""
    pp = Path(pending_path)
    if not pp.is_absolute():
        pp = ROOT / pp
    if not pp.exists():
        raise SystemExit(f"清单不存在: {pp}\n先跑 --prepare-comments 生成。")
    meta = json.loads(pp.read_text(encoding="utf-8-sig"))
    cp = comments_path_for(pp)
    comments = {}
    if cp.exists():
        comments = json.loads(cp.read_text(encoding="utf-8-sig"))
    items = []
    for it in meta.get("items") or []:
        raw = (comments.get(it.get("article_id")) or it.get("comment_body") or "").strip()
        # 审阅时可以整篇丢弃：在感悟文件里写 "__DROP__" 表示这篇连赞/藏都不做。
        # 为什么需要：实测候选里混着卖课引流文、企业营销号（"产品手册 + 咨询电话"），
        # 这类连赞都不值得给，但它们在列表层看不出来，只有读到正文才发现。
        dropped = raw == "__DROP__"
        body = "" if dropped else raw
        items.append({**it, "drop": dropped, "comment_body": compose_comment(it.get("author"), body),
                      "comment_raw": body})
    meta["_pending_path"] = str(pp)
    meta["_comments_path"] = str(cp)
    meta["_comments_found"] = len(comments)
    meta["_dropped"] = sum(1 for i in items if i.get("drop"))
    return meta, items


def cmd_prepare_comments(pw, args, ops):
    """阶段①：筛候选 + 抓正文摘要 → 产出待写评论清单。**不发任何东西、不点任何按钮。**

    为什么单独一阶段：脚本不会写文章，评论必须由 agent 读正文后写。
    这跟 csdn_job.py 的 prepare →（agent 写正文）→ publish 是同一个套路。
    """
    t0 = time.time()
    run_id = _now().strftime("%Y%m%d-%H%M%S") + "-prep"
    con = db_open()
    known = db_state(con)
    issues = []
    pool_sources = {}
    out_dir = OUTPUT_DIR / "interact"
    out_dir.mkdir(parents=True, exist_ok=True)
    pending_path = out_dir / f"pending-{run_id}.json"

    def needs_work(aid):
        have = known.get(aid, {})
        todo = []
        for op in ops:
            st = have.get(op)
            if st in SETTLED_STATES:
                continue
            if st in ("failed", "blocked") and not args.retry_failed:
                continue
            todo.append(op)
        return todo

    log(f"阶段① 抓正文摘要: 目标 {args.count} 篇 | 窗口 {args.days or DEF.get('days', 7)} 天 | "
        f"关键词 {len(args._keywords)} 个")
    b = launch_browser(pw, issues, clean_first=True)
    if not b:
        log("⛔ 浏览器起不来，阶段①中止")
        con.close()
        return 2

    items, rejected = [], []
    try:
        page = b.page()
        cands = collect_candidates(page, args._keywords, args._pages_per_kw, args._days,
                                   args.count, issues, pool_sources)
        pool = []
        for c in cands:
            if not needs_work(c["article_id"]):
                continue
            low = (c.get("title") or "").lower()
            if any(k in low for k in args._exclude_titles):
                for op in ops:
                    record_skip(con, c["article_id"], op, "skipped_excluded", "标题命中排除词", run_id)
                continue
            pool.append(c)
        log(f"候选 {len(cands)} 篇 → 待读 {len(pool)} 篇")

        for idx, c in enumerate(pool, 1):
            if len(items) >= args.count:
                break
            if args.max_minutes and (time.time() - t0) / 60 >= args.max_minutes:
                issues.append(("time-budget", f"阶段①达到 {args.max_minutes} 分钟上限"))
                break
            try:
                page.goto(c["href"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3500)
                d = read_detail(page)
                ex = page.evaluate(EXCERPT_JS)
            except Exception as e:
                issues.append(("article-open", f"{c['href'][:70]}: {e.__class__.__name__}"))
                log(f"  [{idx}] 打不开: {e.__class__.__name__}")
                continue

            title = d.get("title") or c.get("title") or ""
            author = d.get("author") or ""
            fans, orig = parse_author_stats(d.get("fansItems"))
            pub, kind = parse_publish(d.get("timeRaw"))

            def reject(reason, detail):
                for op in ops:
                    record_skip(con, c["article_id"], op, reason, detail, run_id)
                rejected.append({"article_id": c["article_id"], "title": title,
                                 "author": author, "reason": f"{reason}: {detail}"})
                log(f"  [{idx}] 排除({reason}) {title[:32]}")

            aname_low = author.lower()
            if aname_low in args._exclude_authors or any(k in aname_low for k in args._exclude_author_kw):
                reject("skipped_excluded", f"作者 {author} 命中排除规则")
                continue
            if (args._farm_orig_min and args._farm_fans_below and orig is not None
                    and fans is not None and orig >= args._farm_orig_min
                    and fans < args._farm_fans_below):
                reject("skipped_farm", f"疑似内容农场：原创 {orig} 篇 / 粉丝 {fans}")
                continue
            if args._max_fans and fans is not None and fans >= args._max_fans:
                reject("skipped_author_too_big", f"作者粉丝 {fans} ≥ {args._max_fans}")
                continue
            if pub is None:
                reject("skipped_no_time", f"读不到发布时间（kind={kind}）")
                continue
            age_h = (_now() - pub).total_seconds() / 3600
            if age_h > args._days * 24:
                reject("skipped_old", f"发布 {age_h/24:.1f} 天前 > {args._days} 天")
                continue

            body = (ex or {}).get("body") or ""
            body_len = int((ex or {}).get("bodyLen") or 0)
            # 作者昵称读不到 → 不评论。两个理由：① 写不出「大佬您好，昵称」，
            # ② 粉丝数也就读不到，农场/规模两道过滤等于失效，没法确认对方是不是
            # 真实小作者。按约定退化成"只做赞和藏"。这类是 VIP/付费/笔记模板。
            if not author:
                log(f"  [{idx}] 读不到作者昵称 → 只做赞/藏 {title[:28]}")
                record_skip(con, c["article_id"], "comment", "skipped_no_author",
                            "读不到作者昵称（页面模板不同），粉丝数也读不到", run_id)
                items.append({
                    "article_id": c["article_id"], "url": c["href"], "title": title,
                    "author": "", "author_url": "", "fans": fans, "orig": orig,
                    "publish_time": pub.isoformat(sep=" "),
                    "body_len": body_len, "headings": (ex or {}).get("headings") or [],
                    "excerpt": "", "comment_allowed": False,
                    "source": f"search:{c.get('keyword','')}",
                })
                continue
            if body_len < args._min_body:
                # 按约定：读不到正文就只做赞和藏，不评论。
                # 但这篇仍值得互动，所以**不**给它定"评论已定性"，留给阶段②只做赞/藏。
                log(f"  [{idx}] 正文太短({body_len}字) → 只做赞/藏 {title[:28]}")
                record_skip(con, c["article_id"], "comment", "skipped_no_body",
                            f"正文仅 {body_len} 字，读不到可写感悟的内容", run_id)
                items.append({
                    "article_id": c["article_id"], "url": c["href"], "title": title,
                    "author": author, "author_url": d.get("authorUrl") or "",
                    "fans": fans, "orig": orig,
                    "publish_time": pub.isoformat(sep=" "),
                    "body_len": body_len, "headings": (ex or {}).get("headings") or [],
                    "excerpt": "", "comment_allowed": False,
                    "source": f"search:{c.get('keyword','')}",
                })
                continue

            items.append({
                "article_id": c["article_id"], "url": c["href"], "title": title,
                "author": author, "author_url": d.get("authorUrl") or "",
                "fans": fans, "orig": orig,
                "publish_time": pub.isoformat(sep=" "),
                "body_len": body_len, "headings": (ex or {}).get("headings") or [],
                "excerpt": body, "comment_allowed": True,
                "source": f"search:{c.get('keyword','')}",
            })
            db_upsert_article(con, {
                "article_id": c["article_id"], "url": c["href"], "title": title,
                "author": author, "author_url": d.get("authorUrl") or "",
                "fans": fans, "orig": orig,
                "publish_time": pub.isoformat(sep=" "),
                "source": f"search:{c.get('keyword','')}",
                "first_seen": _now().isoformat(timespec="seconds")})
            log(f"  [{idx}] ✅ {title[:38]} | {author} | {fans}粉 | 正文 {body_len} 字")
            random_delay(1, 3)
    finally:
        if b:
            b.close()

    ready = [i for i in items if i["comment_allowed"]]
    meta = {"run_id": run_id, "created": _now().isoformat(timespec="seconds"),
            "ops": ops, "count": args.count, "days": args._days,
            "max_fans": args._max_fans, "keywords": args._keywords,
            "elapsed_min": round((time.time() - t0) / 60, 1),
            "rejected": rejected, "issues": issues,
            "stats": {"read": len(items), "ready_to_write": len(ready),
                      "like_save_only": len(items) - len(ready), "rejected": len(rejected)}}
    pending_path.write_text(json.dumps({**meta, "items": items}, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # 给 agent / 人看的清单（正文 + 小标题 + 现成占位）
    md = [f"# 待写评论清单 · {run_id}", "",
          f"- 目标 {args.count} 篇，实际拿到 **{len(items)}** 篇"
          f"（可写评论 {len(ready)}，只能赞/藏 {len(items) - len(ready)}，排除 {len(rejected)}）",
          f"- 窗口 {args._days} 天 | 粉丝上限 {args._max_fans or '不限'} | 耗时 {meta['elapsed_min']} 分钟",
          f"- 清单: `{pending_path.name}`",
          f"- **感悟写到**: `{comments_path_for(pending_path).name}`"
          f"（格式 `{{\"<article_id>\": \"感悟正文\"}}`，脚本会自动补开头「大佬您好，昵称」）",
          ""]
    for i, it in enumerate(items, 1):
        flag = "可写评论" if it["comment_allowed"] else "只赞/藏（正文读不到）"
        md += [f"## {i}. {it['title']}", "",
               f"- article_id: `{it['article_id']}`",
               f"- 作者: {it['author']}（{it['fans']} 粉 / {it['orig']} 原创）",
               f"- 发布: {it['publish_time']} ｜ 正文 {it['body_len']} 字 ｜ **{flag}**",
               f"- 链接: {it['url']}"]
        if it["headings"]:
            md.append(f"- 小标题: {' ｜ '.join(it['headings'][:8])}")
        if it["excerpt"]:
            md += ["", "```text", it["excerpt"][:1400], "```"]
        md.append("")
    md_path = pending_path.with_suffix(".md")
    md_path.write_text("\n".join(md), encoding="utf-8")

    con.close()
    log("=" * 62)
    log(f"阶段① 完成：清单 {len(items)} 篇（可写评论 {len(ready)}）")
    log(f"清单: {pending_path}")
    log(f"人读版: {md_path}")
    log(f"感悟写到: {comments_path_for(pending_path)}")
    log(f"下一步: 我写好评感悟后跑 --preview {pending_path.name} 给你审，再 --send")
    return 0


def record_skip(con, aid, op, status, detail, run_id):
    db_record(con, aid, op, status, detail, run_id=run_id)


def random_delay(lo, hi):
    """阶段①只是读页面，间隔给短一点；真正的发送阶段才用 15~30 秒。"""
    t = random.uniform(lo, hi) if hi > lo else lo
    time.sleep(t)


def cmd_preview(pending_path):
    """阶段②之前给你审：把最终评论（含「大佬您好，昵称」）整条列出来。"""
    meta, items = load_pending(pending_path)
    print(f"清单: {meta['_pending_path']}")
    print(f"感悟文件: {meta['_comments_path']}（命中 {meta['_comments_found']} 条）")
    print(f"操作序列: {' → '.join(OP_CN[o] for o in meta.get('ops') or [])} | "
          f"窗口 {meta.get('days')} 天 | 粉丝上限 {meta.get('max_fans')}\n")
    ready = [i for i in items if i.get("comment_allowed", True) and not i.get("drop")]
    todo = [i for i in ready if i.get("comment_body")]
    print(f"共 {len(items)} 篇：可评论 {len(ready)}，其中**已写好感悟 {len(todo)}**，"
          f"未写 {len(ready) - len(todo)}，只能赞/藏 {len(items) - len(ready) - meta.get('_dropped', 0)}"
          f"，审阅丢弃 {meta.get('_dropped', 0)}")
    print()
    print("=" * 96)
    for i, it in enumerate(items, 1):
        body = it.get("comment_body") or ""
        print(f"\n[{i}] {it['title'][:60]}")
        print(f"    作者={it['author']}（{it.get('fans')}粉/{it.get('orig')}原创） "
              f"发布={it.get('publish_time')} 正文={it.get('body_len')}字")
        if it.get("drop"):
            print("    🗑️ 已标记整篇丢弃（__DROP__）：赞/藏/评论都不做")
        elif not it.get("comment_allowed", True):
            print("    ⚠️ 正文或作者昵称读不到 → 只做赞/藏，不评论")
        elif not body:
            print("    ⚠️ **还没写感悟** → 这一篇不会发评论（会跳过）")
        else:
            # 字数只算**感悟正文**（不含「大佬您好，昵称」那行）——用户的要求是"100字左右"，
            # 把昵称算进去会让怪名字（如 ❀͜͡傀儡师）虚增十几个字，判偏长就失真了。
            parts = body.split("\n", 1)
            n = len(parts[1].strip()) if len(parts) > 1 else len(body)
            print(f"    ── 将发出（感悟 {n} 字）──")
            for ln in body.splitlines():
                print(f"    │ {ln}")
            if n < 60:
                print(f"    ⚠️ 偏短（{n} 字），可能显得敷衍")
            if n > 240:
                print(f"    ⚠️ 偏长（{n} 字）——用户口径是「往 100 靠，多了也没事」，"
                      f"但超过 240 字评论区可能折叠")
    print("\n" + "=" * 96)
    print(f"确认无误后: .\\csdn_interact.bat --send --pending {Path(meta['_pending_path']).name}")
    return 0


VERIFY_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, '');
  const parts = [];
  for (const sel of ['#comment-list-container', '.comment-list-box', '.comment-list-container',
                     '#pcCommentBox', '.comment-box']) {
    const el = document.querySelector(sel);
    if (el) parts.push(clean(el.innerText));
  }
  // ★ 必须带上整页文本兜底：评论区的容器类名会变（实测只查容器会漏判，
  //   把已经发出去的评论误判成"没发出去"，进而重复评论）。
  parts.push(clean(document.body.innerText));
  return parts.join('');
}
"""


def comment_fragment(text):
    """从评论全文里取一段用于回读校验的特征片段（取正文第一句开头）。"""
    body = (text or "").split("\n")[-1]
    return re.sub(r"\s+", "", body)[:14]


def cmd_verify_comments(pw, args):
    """复核评论是否真的发出去了：页面上找得到就升为 done，找不到就降为 failed（可重试）。

    为什么需要：评论的"成功"信号本来就弱（输入框清空 ≠ 发出去了）。
    实测 2026-10-08 有 1 条评论实际没发出去却记成了 done_unverified，
    而它属于"已完成"→ 永远不会重试。这个命令就是为这种情况兜底的。

    复核范围包含 done_unverified **和 failed**：因为复核本身可能误判
    （第一版只查评论区容器，把 2 条已发出的判成了没发），
    必须能反复复核把状态纠正回来——否则会重复评论。
    """
    con = db_open()
    rows = list(con.execute(
        """SELECT a.article_id, ar.url, ar.title, ar.author, a.text, a.status
           FROM actions a LEFT JOIN articles ar ON ar.article_id = a.article_id
           WHERE a.op='comment' AND a.text IS NOT NULL AND a.text != ''
             AND a.status IN ('done_unverified', 'failed')
           ORDER BY a.ts DESC"""))
    if not rows:
        print("没有待复核的评论（没有 comment + done_unverified/failed 且留有原文的记录）")
        con.close()
        return 0
    print(f"待复核评论 {len(rows)} 条\n")
    issues, ok_n, bad_n = [], 0, 0
    b = launch_browser(pw, issues, clean_first=True)
    if not b:
        print("⛔ 浏览器起不来，复核中止")
        con.close()
        return 2
    try:
        page = b.page()
        for i, (aid, url, title, author, text, st0) in enumerate(rows, 1):
            frag = comment_fragment(text)
            print(f"[{i}/{len(rows)}] {author or '?'} | {(title or '')[:34]} | 原状态 {st0}")
            if not url or not frag:
                print("    缺少 url 或评论原文，跳过")
                continue
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight * 0.55)")
                page.wait_for_timeout(2500)
                area = page.evaluate(VERIFY_JS) or ""
            except Exception as e:
                print(f"    打开失败: {e.__class__.__name__}，跳过")
                continue
            if frag and frag in area:
                db_record(con, aid, "comment", "done",
                          f"复核确认：评论已在页面上（片段「{frag}」）", text=text or "")
                print(f"    ✅ 找到「{frag}」→ done")
                ok_n += 1
            else:
                db_record(con, aid, "comment", "failed",
                          f"复核未找到（片段「{frag}」）→ 判定没发出去，可重试",
                          text=text or "")
                print(f"    ❌ 没找到「{frag}」→ failed（可重试）")
                issues.append(("comment-not-posted", f"{author} | {(title or '')[:30]}"))
                bad_n += 1
            page.wait_for_timeout(1200)
    finally:
        if b:
            b.close()
    con.close()
    print(f"\n复核完成：确认已发 {ok_n} 条，判定未发 {bad_n} 条")
    if bad_n:
        print("未发的已改成 failed —— 重跑 --send --pending <同一清单> --retry-failed 即可补发")
    return 0


# ============================================================ 主流程
def revive_browser(pw, b, issues, deaths):
    """浏览器中途死了之后的复活流程（分档升级，实测比"直接重启"可靠得多）。

    为什么不能直接重启：实测浏览器刚死时若立刻起新的，新 chrome 会和还没退干净的
    旧实例抢同一个 .profile，结果"启动成功但几秒后又死"，连续挂 4 次。
    所以这里按次数升级：
      第 1 次 → 优雅关闭 + 等 3 秒让旧进程退干净 + 直接重启
      第 2 次起 → 再额外强清残留（chrome + lockfile）后重启
    重启后立刻做一次存活探测，探测失败就当启动失败处理。
    """
    try:
        b.close()
    except Exception:
        pass
    time.sleep(3)                                  # 给旧进程退干净的时间
    clean = deaths >= 2
    if clean:
        log(f"  第 {deaths} 次退出，升级为强制清理残留后重启…")
    nb = launch_browser(pw, issues, clean_first=clean)
    if not nb:
        return None
    # 存活探测：launch() 成功不代表几秒后还活着
    try:
        page = nb.page()
        page.evaluate("() => 1 + 1")
        time.sleep(2)
        page.evaluate("() => 1 + 1")
        return nb
    except Exception as e:
        issues.append(("relaunch-not-alive",
                       f"重启后存活探测失败: {e.__class__.__name__}"))
        log(f"  重启后存活探测失败: {e.__class__.__name__}")
        try:
            nb.close()
        except Exception:
            pass
        time.sleep(2)
        return None


def parse_ops(s):
    out = []
    for part in re.split(r"[,，、\s]+", (s or "").strip()):
        if not part:
            continue
        key = OP_ALIAS.get(part.lower()) or OP_ALIAS.get(part)
        if not key:
            raise SystemExit(f"无法识别的操作: {part!r}（可用: like/save/comment 或 点赞/收藏/评论/三连）")
        if key == "all":
            return list(OPS_ALL)
        if key not in out:
            out.append(key)
    if not out:
        raise SystemExit("--ops 不能为空")
    # 固定成 赞 → 藏 → 评论，保证评论最后（文案里写了"我已一键三连"）
    return [o for o in OPS_ALL if o in out]


def main():
    ap = argparse.ArgumentParser(description="CSDN 一键三连（点赞/收藏/评论）")
    ap.add_argument("--ops", default="", help="like / save / comment 的组合，逗号分隔；也可写 三连")
    ap.add_argument("--count", type=int, default=None,
                    help="本轮要做多少篇（**必须由你指定，脚本没有默认值**）")
    ap.add_argument("--keywords", default="", help="覆盖 config 里的关键词池，逗号分隔")
    ap.add_argument("--pages-per-kw", type=int, default=None, help="每个关键词翻几页")
    ap.add_argument("--days", type=int, default=None, help="发布时间窗口（天）")
    ap.add_argument("--max-fans", type=int, default=None, help="作者粉丝上限，0=不限")
    ap.add_argument("--delay-min", type=int, default=None, help="每篇之间最少停多少秒")
    ap.add_argument("--delay-max", type=int, default=None, help="每篇之间最多停多少秒")
    ap.add_argument("--max-minutes", type=int, default=None, help="单轮时间上限（分钟），0=不限")
    ap.add_argument("--dry-run", action="store_true", help="只判定与记录，不真正点击")
    ap.add_argument("--retry-failed", action="store_true", help="上次失败/被拦的也重试")
    ap.add_argument("--stats", action="store_true", help="只看库里的存量统计")
    ap.add_argument("--probe-comment", metavar="URL", help="勘察评论框结构（不提交）")
    ap.add_argument("--prepare-comments", action="store_true",
                    help="阶段①：筛候选 + 抓正文摘要，产出待写评论清单（不发任何东西）")
    ap.add_argument("--pending", metavar="JSON",
                    help="阶段③：按阶段①产出的清单发送（感悟读同名 .comments.json）")
    ap.add_argument("--send", action="store_true",
                    help="与 --pending 搭配使用，语义更清楚（单独给 --pending 也会进入发送阶段）")
    ap.add_argument("--preview", metavar="JSON",
                    help="阶段②：把最终评论整条打印出来给你审，不发")
    ap.add_argument("--min-body", type=int, default=None,
                    help="正文少于多少字就只做赞/藏、不评论（默认 400）")
    ap.add_argument("--verify-comments", action="store_true",
                    help="复核 done_unverified 的评论：评论区找不到就降为 failed（可重试）")
    args = ap.parse_args()

    if args.stats:
        return cmd_stats()

    if args.preview:
        return cmd_preview(args.preview)

    if args.verify_comments:
        with sync_playwright() as pw:
            return cmd_verify_comments(pw, args)

    if args.send and not args.pending:
        raise SystemExit("--send 要和 --pending <清单.json> 一起用。\n"
                         "完整流程: --prepare-comments →（写感悟）→ --preview → --send --pending")

    if args.probe_comment:
        with sync_playwright() as pw:
            return cmd_probe_comment(pw, args.probe_comment)

    # 发送阶段的操作与篇数都从清单里读，不需要（也不该）重复指定 --ops / --count
    using_pending = bool(args.pending)
    if not args.ops:
        if using_pending:
            args.ops = "like,save,comment"      # 占位，下面会被清单里的 ops 覆盖
        else:
            raise SystemExit("请指定 --ops（like / save / comment / 三连）")
    ops = parse_ops(args.ops)
    if not using_pending and (not args.count or args.count <= 0):
        raise SystemExit(
            "--count 必须指定（例如 --count 50）。\n"
            "本脚本不预设篇数：每轮做多少篇由你下任务时决定。\n"
            "（发送阶段用 --send --pending <清单.json>，篇数由清单决定，不用给 --count）")

    days = args.days if args.days is not None else int(DEF.get("days", 7))
    max_fans = args.max_fans if args.max_fans is not None else int(DEF.get("maxFans", 10000))
    pages_per_kw = args.pages_per_kw if args.pages_per_kw is not None else int(DEF.get("pagesPerKw", 3))
    dmin = args.delay_min if args.delay_min is not None else int(DEF.get("delayMinSec", 15))
    dmax = args.delay_max if args.delay_max is not None else int(DEF.get("delayMaxSec", 30))
    max_minutes = args.max_minutes if args.max_minutes is not None else int(DEF.get("maxMinutes", 45))
    min_body = args.min_body if args.min_body is not None else int(DEF.get("minBodyChars", 400))
    comment_streak_limit = int(DEF.get("commentLimitStreak", 3))
    keywords = ([k.strip() for k in args.keywords.split(",") if k.strip()]
                if args.keywords else list(ICFG.get("keywords") or ["Go"]))

    exclude_titles = [s.lower() for s in (ICFG.get("excludeTitleKeywords") or [])]
    exclude_authors = {s.lower() for s in (ICFG.get("excludeAuthors") or [])}
    exclude_author_kw = [s.lower() for s in (ICFG.get("excludeAuthorNameKeywords") or [])]
    farm = ICFG.get("farmFilter") or {}
    farm_orig_min = int(farm.get("origMin", 0) or 0)
    farm_fans_below = int(farm.get("fansBelow", 0) or 0)

    # ---------------- 阶段①：抓正文摘要，产出待写评论清单 ----------------
    if args.prepare_comments:
        args._keywords, args._days = keywords, days
        args._pages_per_kw, args._max_fans = pages_per_kw, max_fans
        args._min_body = min_body
        args._exclude_titles, args._exclude_authors = exclude_titles, exclude_authors
        args._exclude_author_kw = exclude_author_kw
        args._farm_orig_min, args._farm_fans_below = farm_orig_min, farm_fans_below
        with sync_playwright() as pw:
            return cmd_prepare_comments(pw, args, ops)

    run_id = _now().strftime("%Y%m%d-%H%M%S") + "-interact"
    t0 = time.time()
    con = db_open()
    known = db_state(con)
    issues = []
    rows = []
    pool_sources = {}

    # ---------------- 阶段③：按清单发送 ----------------
    pending_items = None
    if args.pending:
        pmeta, pending_items = load_pending(args.pending)
        p_ops = parse_ops(",".join(pmeta.get("ops") or []))
        if p_ops:
            ops = p_ops
        args.count = len(pending_items)
        log(f"按清单发送: {pmeta['_pending_path']}")
        log(f"  感悟文件 {pmeta['_comments_path']}（命中 {pmeta['_comments_found']} 条）")
        n_ready = sum(1 for i in pending_items if i.get("comment_body"))
        log(f"  清单 {len(pending_items)} 篇；已写好感悟 {n_ready} 篇，"
            f"未写 {sum(1 for i in pending_items if i.get('comment_allowed', True)) - n_ready} 篇"
            f"（未写的不发评论，其余操作照做）")

    log(f"run {run_id}")
    log(f"操作: {' → '.join(OP_CN[o] for o in ops)} | 目标 {args.count} 篇 | "
        f"窗口 {days} 天 | 粉丝上限 {max_fans or '不限'} | "
        f"节奏 {dmin}~{dmax}s | 时限 {max_minutes or '不限'} 分钟"
        + ("  [dry-run]" if args.dry_run else ""))
    if "comment" in ops and not args.pending:
        log("提示: 评论现在改为「读正文写感悟」——直接跑 --ops comment 不会产生内容。"
            "请先 --prepare-comments 出清单、写好感悟，再 --send --pending。")

    def needs_work(aid):
        """按「文章 × 操作」判断还有哪些操作没做过。

        去重语义（这是本工具敢反复跑的前提）：
          * done / done_unverified / skipped_already  → 做过，不再动
          * skipped_no_time / skipped_old / skipped_author_too_big / skipped_excluded
                                                    → 已定性，不再看
          * skipped_limit  → 本轮因为"评论已被停用"根本没尝试过，下轮必然要重试
          * failed / blocked → 试过但没成，重复点有风险（尤其评论），
                               只有显式 --retry-failed 才重试
        """
        have = known.get(aid, {})
        todo = []
        for op in ops:
            st = have.get(op)
            if st in SETTLED_STATES:
                continue
            if st == "skipped_limit":
                todo.append(op)
                continue
            if st in ("failed", "blocked") and not args.retry_failed:
                continue
            todo.append(op)
        return todo

    done_n = 0
    stopped = "候选池耗尽"
    comment_blocked_streak = 0
    # 每个操作一个"熔断开关"：一旦被限流/点不动，本轮剩余篇目直接跳过它。
    # 用户口径（2026-10-08）：每天点赞数有上限，发现点不了就跳过，不要反复试。
    op_disabled = {}
    browser_deaths = 0

    def record(aid, op, status, detail="", text=""):
        """写操作状态。**dry-run 一律不写**，否则一次演练就把整批文章标成"做过了"，
        下次真跑会被去重库全部跳过——这是最容易踩的坑。"""
        if args.dry_run:
            return
        db_record(con, aid, op, status, detail, text=text, run_id=run_id)

    with sync_playwright() as pw:
        b = launch_browser(pw, issues, clean_first=True)
        if not b:
            reason = ("另一个会话正占用 .profile（本项目工具不能并行跑，请串行）"
                      if other_owner_alive() else "浏览器启动失败")
            write_log(run_id, {"ops": ops, "wanted": args.count, "done": 0, "days": days,
                               "maxFans": max_fans, "keywords": keywords,
                               "pagesPerKw": pages_per_kw,
                               "elapsed": "0 分"}, [], issues, reason)
            return 2
        try:
            page = b.page()
            if pending_items is not None:
                # 阶段③：按阶段① 的清单走，不再重新搜候选
                pool = []
                dropped = 0
                for it in pending_items:
                    if it.get("drop"):
                        for op in ops:
                            record(it["article_id"], op, "skipped_manual",
                                   "审阅时判定不值得互动（营销/引流文）")
                        dropped += 1
                        continue
                    if needs_work(it["article_id"]):
                        pool.append({**it, "href": it["url"]})
                if dropped:
                    log(f"审阅时丢弃 {dropped} 篇（营销/引流文，连赞都不给）")
                log(f"清单里还需处理的: {len(pool)} 篇")
            else:
                log("收集候选（搜索页综合排序）…")
                cands = collect_candidates(page, keywords, pages_per_kw, days, args.count, issues, pool_sources)

                # 先在本地把"已做过/标题被排除"的剔掉，避免白开页面
                pool = []
                skipped_pre = 0
                for c in cands:
                    if not needs_work(c["article_id"]):
                        skipped_pre += 1
                        continue
                    low = (c.get("title") or "").lower()
                    if any(k in low for k in exclude_titles):
                        for op in ops:
                            record(c["article_id"], op, "skipped_excluded", "标题命中排除词")
                        skipped_pre += 1
                        continue
                    pool.append(c)
                log(f"候选 {len(cands)} 篇 → 去掉库里已做过/标题排除 {skipped_pre} 篇，"
                    f"待处理 {len(pool)} 篇")

            for idx, c in enumerate(pool, 1):
                if done_n >= args.count:
                    stopped = f"已完成目标 {args.count} 篇"
                    break
                if max_minutes and (time.time() - t0) / 60 >= max_minutes:
                    stopped = f"达到 {max_minutes} 分钟时间上限（实做 {done_n}/{args.count}）"
                    issues.append(("time-budget", stopped))
                    break

                todo = needs_work(c["article_id"])
                if not todo:
                    continue

                # 打开文章：中途浏览器死了就重启并**重试同一篇**，不浪费这个候选
                opened_ok = False
                for attempt in (1, 2):
                    try:
                        page.goto(c["href"], wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(4000)
                        opened_ok = True
                        break
                    except Exception as e:
                        name = e.__class__.__name__
                        if name == "TargetClosedError" and attempt == 1:
                            browser_deaths += 1
                            issues.append(("browser-died",
                                           f"浏览器中途退出（第 {browser_deaths} 次），重启后重试同一篇"))
                            log(f"  [{idx}] 浏览器退出，重启后重试同一篇…")
                            b = revive_browser(pw, b, issues, browser_deaths)
                            if not b:
                                stopped = "浏览器反复启动失败，中止"
                                break
                            page = b.page()
                            continue
                        issues.append(("article-open", f"打不开 {c['href'][:70]}: {name}"))
                        log(f"  [{idx}] 打不开: {name}")
                        break
                if not b:
                    break
                if not opened_ok:
                    if browser_deaths >= 6:
                        stopped = (f"浏览器连续退出 {browser_deaths} 次，本轮中止"
                                   f"（环境问题，不是被平台拦）")
                        issues.append(("browser-flapping", stopped))
                        log(f"  ⛔ {stopped}")
                        break
                    continue

                d = read_detail(page)
                title = d.get("title") or c.get("title") or ""
                fans, orig = parse_author_stats(d.get("fansItems"))
                pub, kind = parse_publish(d.get("timeRaw"))
                rec = {"article_id": c["article_id"], "url": c["href"], "title": title,
                       "author": d.get("author") or "", "author_url": d.get("authorUrl") or "",
                       "fans": fans, "orig": orig,
                       "publish_time": pub.isoformat(sep=" ") if pub else "",
                       "source": f"search:{c.get('keyword','')}",
                       "first_seen": _now().isoformat(timespec="seconds")}
                db_upsert_article(con, rec)

                def skip_all(reason):
                    for op in todo:
                        record(c["article_id"], op, reason[0], reason[1])

                # 过滤 1：作者被排除（精确名单 + 名字关键词，后者专治"XX齿科/超级简历"这类营销号）
                aname = d.get("author") or ""
                aname_low = aname.lower()
                if aname_low in exclude_authors or any(k in aname_low for k in exclude_author_kw):
                    skip_all(("skipped_excluded", f"作者 {aname} 命中排除规则"))
                    log(f"  [{idx}] 跳过(作者排除: {aname}) {title[:30]}")
                    continue
                # 过滤 2：内容农场 —— 原创数很多但粉丝极少，写文只为 SEO，不会回三连
                if (farm_orig_min and farm_fans_below and orig is not None and fans is not None
                        and orig >= farm_orig_min and fans < farm_fans_below):
                    skip_all(("skipped_farm", f"疑似内容农场：原创 {orig} 篇 / 粉丝 {fans}"))
                    log(f"  [{idx}] 跳过(内容农场 {orig}篇/{fans}粉) {title[:30]}")
                    continue
                # 过滤 2：粉丝数超上限
                if max_fans and fans is not None and fans >= max_fans:
                    skip_all(("skipped_author_too_big", f"作者粉丝 {fans} ≥ {max_fans}"))
                    log(f"  [{idx}] 跳过(作者过大 {fans}粉) {title[:30]}")
                    continue
                # 过滤 3：发布时间（约定：拿不到发布时间就跳过）
                if pub is None:
                    skip_all(("skipped_no_time", f"读不到发布时间（.time={d.get('timeRaw')!r}, kind={kind}）"))
                    log(f"  [{idx}] 跳过(无发布时间 {kind}) {title[:30]}")
                    continue
                age_h = (_now() - pub).total_seconds() / 3600
                if age_h > days * 24:
                    skip_all(("skipped_old", f"发布 {age_h/24:.1f} 天前 > {days} 天"))
                    log(f"  [{idx}] 跳过(旧文 {age_h/24:.1f}天) {title[:30]}")
                    continue

                # 真做
                log(f"  [{idx}] {title[:40]} | {d.get('author')} | {fans}粉 | "
                    f"{pub:%m-%d %H:%M}（{age_h:.1f}h 前）| 待做 {todo}")
                result = {}
                for op in todo:
                    # 熔断：某个操作一旦被限流/点不动，本轮剩余篇目**直接跳过它**
                    # （用户 2026-10-08 明确要求：每天点赞数有上限，发现点不了就跳过，
                    #   不要在平台已经拒绝的时候反复试——反复点还有互相抵消的风险）
                    if op_disabled.get(op):
                        result[op] = ("skipped_op_disabled", f"本轮 {op} 已熔断：{op_disabled[op]}")
                        record(c["article_id"], op, "skipped_op_disabled", result[op][1])
                        log(f"      {OP_CN[op]}: 跳过（本轮已熔断）")
                        continue
                    if op == "comment":
                        ctext = c.get("comment_body") or ""
                        if not ctext:
                            st, detail = ("skipped_no_comment",
                                          "清单里这篇没有感悟 → 本次不评论（补写好再跑即可，"
                                          "该状态不会定性，下次仍会重试）")
                        else:
                            st, detail = do_comment(page, ctext, args.dry_run)
                    elif op == "like":
                        # 关键：把刚读到的真实状态传进去，已赞就绝不点（点赞是开关）
                        st, detail = do_like(page, args.dry_run, already=d.get("liked"))
                    elif op == "save":
                        st, detail = do_save(page, args.dry_run, already=d.get("saved"))
                    else:
                        st, detail = DO_OP[op](page, args.dry_run)
                    result[op] = (st, detail)
                    record(c["article_id"], op, st, detail,
                           text=(c.get("comment_body") or "") if op == "comment" else "")
                    log(f"      {OP_CN[op]}: {st} — {detail[:80]}")
                    if st == "blocked":
                        issues.append((f"{op}-blocked", f"{title[:30]} | {detail}"))
                        if op == "comment":
                            comment_blocked_streak += 1
                            if comment_blocked_streak >= comment_streak_limit:
                                op_disabled["comment"] = f"连续 {comment_blocked_streak} 次被拦"
                                issues.append(("comment-disabled",
                                               f"评论连续 {comment_blocked_streak} 次被限流/拒绝，"
                                               f"本轮剩余篇目跳过评论"))
                                log(f"      → 评论连续 {comment_blocked_streak} 次被拦，"
                                    f"本轮不再尝试评论")
                        else:
                            op_disabled[op] = f"被限流：{detail[:60]}"
                            log(f"      → {OP_CN[op]}被限流，本轮剩余篇目直接跳过{OP_CN[op]}")
                    elif st == "skipped_rejected":
                        # 点得到、但状态不变 = 平台静默拒绝（实测是每日点赞上限）。
                        # 按用户口径"发现点不了就跳过"，本轮不再试这个操作。
                        issues.append((f"{op}-rejected", f"{title[:30]} | {detail}"))
                        if op in ("like", "save"):
                            op_disabled[op] = f"点了没反应（疑似每日限额）：{detail[:60]}"
                            log(f"      → {OP_CN[op]}点了没反应，本轮剩余篇目直接跳过{OP_CN[op]}")
                    elif st in ("done", "done_unverified"):
                        if op == "comment":
                            comment_blocked_streak = 0
                    elif st == "failed":
                        # 注意：failed 现在只表示"点击执行失败"（脚本/选择器问题），
                        # **不触发熔断** —— 否则一个选择器写错就会把整轮的赞/藏全跳过
                        # （2026-10-08 实测就是这么把 3 篇的赞和藏全废掉的）。
                        issues.append((f"{op}-failed", f"{title[:30]} | {detail}"))
                # 只要真的动过手（不是全跳过），就计入完成数
                if any(v[0] in ("done", "done_unverified", "blocked") for v in result.values()):
                    done_n += 1
                known.setdefault(c["article_id"], {}).update({k: v[0] for k, v in result.items()})
                rows.append({**rec, "publish": f"{pub:%Y-%m-%d %H:%M}", "ops": result})

                if done_n >= args.count:
                    stopped = f"已完成目标 {args.count} 篇"
                    break
                # 节奏：每篇之间随机停 15~30 秒（含最后一篇，避免尾随请求）
                nap = random.randint(dmin, dmax) if dmax >= dmin else dmin
                log(f"      …停 {nap}s（防风控）")
                time.sleep(nap)
        finally:
            if b:
                b.close()

    elapsed = f"{(time.time() - t0) / 60:.1f} 分钟"
    meta = {"ops": ops, "wanted": args.count, "done": done_n, "days": days,
            "maxFans": max_fans, "keywords": keywords, "pagesPerKw": pages_per_kw,
            "elapsed": elapsed}
    con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, _now().isoformat(timespec="seconds"), _now().isoformat(timespec="seconds"),
                 ",".join(ops), args.count, done_n, stopped,
                 json.dumps([m for _, m in issues], ensure_ascii=False)[:2000]))
    con.commit()
    con.close()

    lp = write_log(run_id, meta, rows, issues, stopped)
    log("=" * 62)
    log(f"完成 {done_n}/{args.count} 篇，耗时 {elapsed}")
    log(f"停止原因: {stopped}")
    if issues:
        log(f"重大问题 {len(issues)} 条（详见日志）:")
        for kind, msg in issues[:8]:
            log(f"  - [{kind}] {msg[:100]}")
    log(f"日志: {lp}")
    log(f"数据库: {DB_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
