#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN 素材研究 —— 按关键词搜集高阅读量文章，供写原创文章时参考。

为什么必须用浏览器：CSDN 对非浏览器客户端一律返回 HTTP 521，
所以 web_fetch 抓不到 CSDN 文章，更看不到阅读量。

阅读量直接从搜索结果卡片的 `.btm-view .num` 读取，不必逐篇打开详情页，
所以筛选很快；只有达标的文章才会被打开抓正文。

本模块既提供 CLI，也对外暴露 research() 供 csdn_job.py 复用。

CLI 用法:
    python csdn_research.py -k "大模型 Agent" -k "Agent 记忆机制" --min-views 1000
    python csdn_research.py -k "云原生" --min-views 1000 --want 6 --out output/research
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

DEFAULT_OUT = pc.OUTPUT_DIR / "research"

# 搜索结果卡片：标题、URL、阅读量、点赞。
# 2026-10-09 改版：卡片换成 result-card-v170，.btm-view .num 没了，
# 阅读量只出现在卡片文本里（"2024-05-09 · 浏览 1,354 · 点赞 30 · 收藏 19"）。
# 所以先试老选择器，取不到再从卡片文本里正则抓，两个口径都保留。
LIST_JS = """
() => {
  const cards = document.querySelectorAll('div.list-item, div.so-items-normal, div.so-item, div.result-card-v170');
  const seen = new Set();
  const out = [];
  cards.forEach(c => {
    const a = c.querySelector('h3 a') || c.querySelector('a[href*="/article/details/"]');
    if (!a) return;
    const title = (a.innerText || '').replace(/\\s+/g, ' ').trim();
    const href = (a.href || '').split('?')[0];
    if (!title || !href.includes('/article/details/')) return;
    if (seen.has(href)) return;
    seen.add(href);
    const num = (sel) => {
      const e = c.querySelector(sel);
      return e ? (e.innerText || '').replace(/[^\\d]/g, '') : '';
    };
    const txt = (c.innerText || '').replace(/\\s+/g, ' ');
    const grab = (label) => {
      const m = txt.match(new RegExp(label + '\\\\s*([\\\\d,\\\\.]+\\\\s*[万kK]?)'));
      return m ? m[1].replace(/,/g, '').trim() : '';
    };
    out.push({title, href,
              viewsRaw: num('.btm-view .num') || grab('浏览'),
              likesRaw: num('.btm-dig .num') || grab('点赞')});
  });
  return out;
}
"""

# 详情页正文 + 头部信息（阅读量限定在文章信息区内，避免抓到侧栏推荐）
DETAIL_JS = """
() => {
  const tx = (root, s) => { const e = root && root.querySelector(s); return e ? (e.innerText || '').trim() : null; };
  const infoBox = document.querySelector('.article-info-box') ||
                  document.querySelector('.article-bar-top') ||
                  document.querySelector('.blog-content-box');
  let readRaw = null;
  if (infoBox) {
    const m = (infoBox.innerText || '').match(/([\\d,\\.]+\\s*万?)\\s*阅读/);
    if (m) readRaw = m[0];
  }
  if (!readRaw && infoBox) readRaw = tx(infoBox, '.read-count');
  const body = document.querySelector('#content_views') ||
               document.querySelector('article.baidu_pl') ||
               document.querySelector('.blog-content-box');
  return {
    readRaw,
    author: tx(document, '.profile-intro-name-boxTop') || tx(document, '.follow-nickName'),
    publishTime: tx(document, '.up-time') || tx(document, '.time'),
    tags: [...document.querySelectorAll('a.tag-link-new, .article-tag-list a, .tag-link')]
            .map(e => (e.innerText || '').trim().replace(/^#+/, ''))
            .filter(Boolean).slice(0, 8),
    bodyText: body ? (body.innerText || '').trim() : null
  };
}
"""


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def parse_views(raw):
    """'1.2万' / '9.7k' / '3120' / '425' → int。"""
    if raw is None or raw == "":
        return 0
    s = str(raw).replace(",", "").strip()
    m = re.match(r"^([\d\.]+)\s*(万|[kK])?", s)
    if not m:
        return 0
    n = float(m.group(1))
    if m.group(2) == "万":
        n *= 10000
    elif m.group(2):
        n *= 1000
    return int(n)


def slugify(title, maxlen=50):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", str(title)).strip("-")
    return (s[:maxlen] or "article").lower()


def click_next_page(page):
    """CSDN 搜索是前端分页，URL 传 p=2 无效，必须点「下一页」。"""
    selectors = [
        "a.el-pager__next:not([disabled])",
        ".el-pager li.number:last-child",
        ".pager a:has-text('下一页')",
        "a:has-text('下一页')",
        "button:has-text('下一页')",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=6000)
                return True
        except Exception:
            continue
    return False


def collect(page, keyword, pages=2, quiet=False):
    """收集候选（含阅读量），只走搜索页，很快。"""
    found, seen = [], set()
    url = f"https://so.csdn.net/so/search?q={keyword.replace(' ', '%20')}&t=blog"
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(7000)
    except Exception as e:
        log(f"  搜索页打开失败: {e.__class__.__name__}")
        return found

    for pno in range(1, pages + 1):
        if pno > 1:
            if not click_next_page(page):
                if not quiet:
                    log(f"  没有更多页，停在第 {pno - 1} 页")
                break
            page.wait_for_timeout(7000)
        items = page.evaluate(LIST_JS) or []
        new = 0
        for it in items:
            if it["href"] in seen:
                continue
            seen.add(it["href"])
            it["views"] = parse_views(it.get("viewsRaw"))
            it["likes"] = parse_views(it.get("likesRaw"))
            it["keyword"] = keyword
            found.append(it)
            new += 1
        if not quiet:
            log(f"  第 {pno} 页: 新增 {new} 条（累计 {len(found)}）")
        if not items or new == 0:
            break
    return found


def fetch_detail(page, url):
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(4500)
    try:
        page.wait_for_selector("#content_views", timeout=12000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    return page.evaluate(DETAIL_JS) or {}


def research(page, keywords, min_views=1000, want=8, pages=2,
             out_dir=None, quiet=False):
    """搜集达标文章并把正文落盘。

    调用方负责提供并关闭浏览器（page 已就绪），这样 csdn_job.py 能复用会话。

    返回: {"articles": [...], "candidates": [...], "outDir": str}
    """
    out = Path(out_dir) if out_dir else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)

    candidates, seen = [], set()
    for kw in keywords:
        if not quiet:
            log(f"搜索: {kw}")
        for it in collect(page, kw, pages, quiet=quiet):
            if it["href"] in seen:
                continue
            seen.add(it["href"])
            candidates.append(it)

    candidates.sort(key=lambda c: -c["views"])
    qualified = [c for c in candidates if c["views"] >= min_views]
    hits = qualified[:want]
    if not quiet:
        log(f"候选 {len(candidates)} 条，其中 {len(qualified)} 篇 ≥{min_views} 阅读，取前 {len(hits)} 篇抓正文")

    articles = []
    for i, it in enumerate(hits, 1):
        try:
            d = fetch_detail(page, it["href"])
        except Exception as e:
            log(f"  [{i}] 打开失败: {it['title'][:30]} ({e.__class__.__name__})")
            continue
        body = d.get("bodyText") or ""
        if len(body) < 800:
            log(f"  [{i}] 正文过短({len(body)}字)，跳过: {it['title'][:30]}")
            continue

        slug = slugify(it["title"])
        f = out / f"{slug}.md"
        f.write_text(
            f"# {it['title']}\n\n"
            f"- 来源: {it['href']}\n"
            f"- 阅读量: {it['views']}\n"
            f"- 点赞: {it['likes']}\n"
            f"- 作者: {d.get('author') or '未知'}\n"
            f"- 发布时间: {d.get('publishTime') or '未知'}\n"
            f"- 标签: {', '.join(d.get('tags') or [])}\n"
            f"- 搜索关键词: {it.get('keyword')}\n\n---\n\n{body}\n",
            encoding="utf-8")
        articles.append({
            "title": it["title"], "url": it["href"], "views": it["views"],
            "likes": it["likes"], "keyword": it.get("keyword"),
            "author": d.get("author"), "publishTime": d.get("publishTime"),
            "tags": d.get("tags") or [], "bodyChars": len(body),
            "file": str(f),
        })
        if not quiet:
            log(f"  [{i}] ✓ {it['views']:>7,} 阅读 {len(body):>6} 字  {it['title'][:32]}")

    payload = {"minViews": min_views, "keywords": keywords,
               "collectedAt": datetime.now().isoformat(timespec="seconds"),
               "candidateCount": len(candidates), "articles": articles}
    (out / "latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# CSDN 素材搜集结果", "",
             f"- 关键词: {', '.join(keywords)}",
             f"- 阅读量门槛: ≥ {min_views}",
             f"- 候选: {len(candidates)} 条；达标并抓到正文: {len(articles)} 篇", "",
             "| # | 阅读量 | 点赞 | 标题 | 正文文件 |",
             "|---|--------|------|------|----------|"]
    for i, r in enumerate(articles, 1):
        lines.append(f"| {i} | {r['views']:,} | {r['likes']} | [{r['title']}]({r['url']}) | `{r['file']}` |")
    (out / "latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"articles": articles, "candidates": candidates, "outDir": str(out)}


def main():
    ap = argparse.ArgumentParser(description="按关键词搜集 CSDN 高阅读量文章")
    ap.add_argument("-k", "--keyword", action="append", required=True, help="关键词，可重复")
    ap.add_argument("--min-views", type=int, default=1000, help="最低阅读量，默认 1000")
    ap.add_argument("--want", type=int, default=8, help="最多抓几篇达标文章的正文")
    ap.add_argument("--pages", type=int, default=2, help="每个关键词最多翻几页")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    args = ap.parse_args()

    with sync_playwright() as p:
        b = pc.Browser(p)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        log(f"登录态: {'有效' if logged else '无效'}")
        res = research(page, args.keyword, args.min_views, args.want, args.pages, args.out)
        b.close()

    log(f"完成：{len(res['articles'])} 篇已保存到 {res['outDir']}")
    if not res["articles"]:
        log("没找到达标文章：降低 --min-views、换更热的关键词，或加大 --want")
    return 0


if __name__ == "__main__":
    sys.exit(main())
