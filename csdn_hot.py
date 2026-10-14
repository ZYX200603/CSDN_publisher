#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 热榜取数 + 爆款风格指纹。

为什么要单独一个工具：热榜页是 JS 渲染的（curl 只能拿到 SPA 空壳，
周榜/月榜直接 521），所以必须复用已登录的浏览器。

    python csdn_hot.py                                  # 全站综合热榜（默认）
    python csdn_hot.py --tab 领域内容榜 --limit 30
    python csdn_hot.py --fingerprint URL [URL ...]      # 拆某篇爆款的风格指纹
    python csdn_hot.py --out output/hot/hot.json

风格指纹 = 我们写文章时要对齐的那几项：字数、二级标题数、问句标题占比、
代码块 / 表格 / 图片数、每千字数字个数、开头 200 字。
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

RANK_URL = "https://blog.csdn.net/rank/list"

ROWS_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const titled = [...document.querySelectorAll('a[href*="/article/details/"]')]
    .filter(a => clean(a.innerText).length > 4 && !/榜单规则/.test(clean(a.innerText)));
  const out = [];
  const seen = new Set();
  for (const a of titled) {
    const href = a.href;
    if (seen.has(href)) continue;
    seen.add(href);
    // 整行文本长这样：1 文章标题 773 浏览 21 评论 23 收藏 37137 热度 作者名 关注
    let el = a, item = '';
    for (let i = 0; i < 6 && el; i++) {
      el = el.parentElement;
      if (!el) break;
      const t = clean(el.innerText);
      if (/热度/.test(t) && t.length < 420) item = t;
    }
    const m = item.match(/^(\d{1,3})\s+(.*?)\s*([\d.,]+)\s*浏览\s*(\d+)\s*评论\s*([\d.,]+)\s*收藏\s*([\d.,]+)\s*热度\s*(.*?)\s*关注/);
    out.push({
      href, title: clean(a.innerText), item,
      rank: m ? parseInt(m[1], 10) : null,
      views: m ? m[3] : null, comments: m ? parseInt(m[4], 10) : null,
      favorites: m ? m[5] : null, heat: m ? m[6] : null, author: m ? m[7] : null,
    });
  }
  return out;
}
"""

TAB_JS = r"""
(name) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const el = [...document.querySelectorAll('a,li,div,span')]
    .filter(e => clean(e.innerText).startsWith(name) && e.children.length <= 2)
    .pop();
  if (!el) return null;
  el.click();
  return clean(el.innerText).slice(0, 40);
}
"""

FINGERPRINT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const box = document.querySelector('#content_views') || document.querySelector('article');
  if (!box) return null;
  const text = box.innerText || '';
  const hs = [...box.querySelectorAll('h1,h2,h3')].map(h => clean(h.innerText));
  const imgs = [...box.querySelectorAll('img')].filter(i => (i.src || '').startsWith('http'));
  return {
    title: clean((document.querySelector('h1.title-article') || document.querySelector('h1') || {}).innerText),
    text,
    headings: hs,
    h2: [...box.querySelectorAll('h2')].map(h => clean(h.innerText)),
    codeBlocks: box.querySelectorAll('pre').length,
    tables: box.querySelectorAll('table').length,
    images: imgs.length,
    links: box.querySelectorAll('a').length,
    publishTime: clean((document.querySelector('.time') || {}).innerText || ''),
  };
}
"""

QUESTION_HINTS = ("为什么", "怎么", "如何", "是什么", "什么是", "能不能", "要不要",
                  "哪些", "多少", "到底", "？", "?")


def fingerprint(data):
    """把一篇爆款的正文变成可比对的指标。"""
    text = data.get("text") or ""
    compact = re.sub(r"\s+", "", text)
    n = max(len(compact), 1)
    h2 = data.get("h2") or []
    q = sum(1 for h in h2 if h.rstrip().endswith(("？", "?")) or any(k in h for k in QUESTION_HINTS))
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return {
        "title": data.get("title") or "",
        "字数(非空白)": len(compact),
        "二级标题": len(h2),
        "问句标题占比": f"{(q / len(h2) * 100):.0f}%" if h2 else "—",
        "代码块": data.get("codeBlocks"),
        "表格": data.get("tables"),
        "图片": data.get("images"),
        "段落数": len(paras),
        "平均段落长度": round(len(compact) / max(len(paras), 1)),
        "每千字数字": round(len(re.findall(r"\d", compact)) * 1000 / n, 1),
        "内链": data.get("links"),
        "开头 200 字": compact[:200],
    }


def cmd_trend(limit=20):
    """选题雷达：把所有历史榜单快照横向比一遍，找出"反复在榜"的题材。

    为什么要它：单次热榜只能告诉你"今天什么火"，容易被一天的噪声带偏。
    连续 2~3 天都在榜的题材才是真趋势。纯离线，不联网、不开浏览器。
    """
    files = sorted((pc.OUTPUT_DIR / "hot").glob("*.json"))
    snaps = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = d.get("rows") or []
        if rows:
            snaps.append((d.get("collectedAt", f.stem), rows))
    if not snaps:
        print(f"还没有榜单快照。先跑一次: python csdn_hot.py --limit 30")
        return 1

    print(f"共 {len(snaps)} 份榜单快照（{snaps[0][0]} ~ {snaps[-1][0]}）\n")
    seen, kw = {}, {}
    for ts, rows in snaps:
        for r in rows:
            t = (r.get("title") or "").strip()
            if not t:
                continue
            seen.setdefault(t, []).append((ts, r.get("heat"), r.get("rank")))
            for k in ("AI", "Agent", "智能体", "大模型", "RAG", "MCP", "DeepSeek", "Docker",
                      "Redis", "MySQL", "K8s", "Kubernetes", "Rust", "C++", "Linux", "算法"):
                if k.lower() in t.lower():
                    kw[k] = kw.get(k, 0) + 1

    multi = {t: v for t, v in seen.items() if len(v) >= 2}
    print(f"上榜文章 {len(seen)} 篇，其中出现 ≥2 次的 {len(multi)} 篇：")
    for t, v in sorted(multi.items(), key=lambda kv: -len(kv[1]))[:limit]:
        heats = "、".join(str(x[1]) for x in v)
        print(f"  ×{len(v)}  热度 {heats:>18}  {t[:52]}")

    print(f"\n题材词出现次数（跨 {len(snaps)} 份快照累计）：")
    for k, c in sorted(kw.items(), key=lambda kv: -kv[1])[:15]:
        bar = "█" * min(c, 40)
        print(f"  {k:<10} {c:>3}  {bar}")

    if len(snaps) == 1:
        print("\n提示：只有 1 份快照时看不到趋势。以后每轮任务前跑一次 csdn_hot.py，"
              "积累几天再回来看这张表。")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tab", default="", help="切到哪个榜：领域内容榜 / 热门专栏榜 / 作者周榜 …（默认综合热榜）")
    ap.add_argument("--limit", type=int, default=50, help="最多输出几条，默认 50")
    ap.add_argument("--fingerprint", nargs="*", default=[], help="拆这些文章 URL 的风格指纹")
    ap.add_argument("--out", default="", help="JSON 输出路径（默认 output/hot/<时间>.json）")
    ap.add_argument("--trend", action="store_true",
                    help="选题雷达：横向比对所有历史快照，找反复在榜的题材（离线，不开浏览器）")
    args = ap.parse_args()

    if args.trend:
        return cmd_trend(args.limit)

    out_dir = pc.OUTPUT_DIR / "hot"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else out_dir / f"{datetime.now():%Y%m%d-%H%M%S}.json"

    result = {"collectedAt": datetime.now().isoformat(timespec="seconds"), "tab": args.tab or "综合热榜"}

    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        print(f"登录态: {logged}", flush=True)

        if args.fingerprint:
            result["fingerprints"] = []
            for url in args.fingerprint:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                data = page.evaluate(FINGERPRINT_JS)
                if not data:
                    print(f"  取不到正文: {url}", flush=True)
                    continue
                fp = fingerprint(data)
                fp["url"] = url
                result["fingerprints"].append(fp)
                print("=" * 66, flush=True)
                for k, v in fp.items():
                    if k == "开头 200 字":
                        print(f"  {k}: {str(v)[:120]}…", flush=True)
                    else:
                        print(f"  {k}: {v}", flush=True)
        else:
            page.goto(RANK_URL, wait_until="domcontentloaded")
            # 榜单是异步渲染的：固定 sleep 会抓到空列表（实测 6 秒时只有页脚链接，
            # 7 秒才有 27 条）。所以要等到条目真的出现。
            try:
                page.wait_for_function(
                    """() => [...document.querySelectorAll('a[href*="/article/details/"]')]
                             .filter(a => (a.innerText || '').trim().length > 4).length >= 10""",
                    timeout=30000)
                print("榜单已渲染", flush=True)
            except Exception:
                print("等榜单超时，按当前 DOM 抓（可能不完整）", flush=True)
            page.wait_for_timeout(1500)
            if args.tab:
                print(f"切榜: {page.evaluate(TAB_JS, args.tab)!r}", flush=True)
                page.wait_for_timeout(5000)
            rows = page.evaluate(ROWS_JS)
            clean_rows = [r for r in rows if r.get("rank") or r.get("title")]
            clean_rows.sort(key=lambda x: (x["rank"] is None, x["rank"] or 999))
            result["rows"] = clean_rows[: args.limit]
            ai_pat = re.compile(r"AI|Agent|大模型|RAG|MCP|LLM|GPT|DeepSeek|智能体|提示词|模型", re.I)
            n_ai = sum(1 for r in result["rows"] if ai_pat.search(r["title"] or ""))
            print(f"榜单条目: {len(clean_rows)}（输出前 {len(result['rows'])} 条；"
                  f"其中 AI/大模型相关 {n_ai} 条）", flush=True)
            for r in result["rows"]:
                print(f"  #{str(r['rank']):>3} 热度={str(r['heat']):>8} 浏览={str(r['views']):>6} "
                      f"收藏={str(r['favorites']):>5}  {r['title'][:46]}", flush=True)

        b.close()

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
