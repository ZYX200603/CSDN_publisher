#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按「一篇一个搜索关键词」搜集参考素材——保证参考资料与本篇主题相关，且全池互不重复。

和 csdn_research.py 的区别：
  csdn_research.py 把多个关键词的结果**混在一起按阅读量排序**再取前 N，
  那样 40 篇会来来回回参考同一批最热的文章。本脚本改成**逐篇单独搜**，
  每篇只从它自己的关键词结果里取，并做全局去重（同一篇文章不会被两篇选题引用）。

用法：
  python csdn_refs.py --topics topics/pool40-2026-10-12.json
  python csdn_refs.py --topics topics/pool40-2026-10-12.json --only K8s --want 3
  python csdn_refs.py --topics topics/pool40-2026-10-12.json --min-views 2000 --per-topic 2

产物：
  output/refs/<id>/         每篇选题的候选与已抓正文（子目录由 csdn_research 落盘）
  output/refs/summary.json  逐篇选题 -> 2 篇参考（url/阅读量/正文文件）
  output/refs/summary.md    给人看的清单
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import csdn_research as cr  # noqa: E402
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

DEFAULT_OUT = pc.OUTPUT_DIR / "refs"

# 池子级兜底大词：前两档关键词都凑不够 ≥minViews 的文章时用它。
# 仍在同一个主题池内，保证参考文章与选题相关。
POOL_KW = {
    "消息队列": "Kafka 消息队列",
    "Linux/网络": "Linux 网络 排查",
    "K8s": "Kubernetes 实战",
    "微服务/gRPC": "gRPC 微服务",
}


def log(*a):
    print(*a, flush=True)


def collect_for_topic(page, t, min_views, want, pages, out_root, used, per_topic):
    """为一个选题搜集参考文章，返回 (picked, all_candidates)。全局去重。"""
    tid = t["id"]
    picks, seen_cands = [], []
    # 第三档兜底：用池子级大词。它仍然在同一主题池内（相关），
    # 但候选池大得多，更容易凑到 ≥minViews 的文章。
    groups = [t["keywords"]]
    if t.get("alt"):
        groups.append(t["alt"])
    pool_kw = POOL_KW.get(t.get("pool", ""))
    if pool_kw:
        groups.append([pool_kw])
    for kws in groups:
        if len(picks) >= per_topic:
            break
        kws = [k for k in kws if k]
        if not kws:
            continue
        res = cr.research(page, kws, min_views, want, pages,
                          str(out_root / tid), quiet=True)
        cands = res.get("candidates") or []
        for c in cands:
            seen_cands.append({"title": c["title"], "url": c["href"], "views": c["views"]})
        must = t.get("must") or []
        for a in res["articles"]:
            if len(picks) >= per_topic:
                break
            if a["url"] in used:
                continue
            if must and not any(m.lower() in (a["title"] or "").lower() for m in must):
                log(f"   跳过（与选题不相关）: {a['title'][:34]}")
                continue
            used.add(a["url"])
            picks.append(a)
        log(f"  {tid}: 关键词 {kws} -> 候选 {len(cands)}，"
            f"达标 {len(res['articles'])}，已选 {len(picks)}/{per_topic}")
    return picks, seen_cands


def main():
    ap = argparse.ArgumentParser(description="逐篇选题单独搜集参考素材（全局去重）")
    ap.add_argument("--topics", required=True, help="选题 JSON 路径")
    ap.add_argument("--min-views", type=int, default=None, help="覆盖 JSON 里的 minViews")
    ap.add_argument("--want", type=int, default=3, help="每个关键词最多抓几篇正文（留冗余供去重）")
    ap.add_argument("--per-topic", type=int, default=2, help="每篇选题最终配几篇参考")
    ap.add_argument("--pages", type=int, default=2, help="每个关键词翻几页")
    ap.add_argument("--only", default=None, help="只跑某个池子（子串匹配 id 或 pool）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    cfg = json.loads(Path(args.topics).read_text(encoding="utf-8"))
    min_views = args.min_views or cfg.get("minViews", 2000)
    topics = cfg["topics"]
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        topics = [t for t in topics
                  if any(k.lower() in t["id"].lower() or k in t.get("pool", "")
                         for k in keys)]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    log(f"选题 {len(topics)} 篇｜门槛 ≥{min_views} 阅读｜每篇配 {args.per_topic} 篇｜"
        f"目标 {len(topics) * args.per_topic} 篇互不重复")

    summary, used = [], set()
    with sync_playwright() as p:
        b = pc.Browser(p)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        log(f"登录态: {'有效' if logged else '无效'}")
        for i, t in enumerate(topics, 1):
            log(f"[{i}/{len(topics)}] {t['id']} {t['title'][:38]}")
            picks, cands = collect_for_topic(page, t, min_views, args.want,
                                             args.pages, out_root, used, args.per_topic)
            summary.append({
                "id": t["id"], "pool": t.get("pool"), "title": t["title"],
                "keywords": t["keywords"], "alt": t.get("alt"),
                "refs": [{"title": a["title"], "url": a["url"], "views": a["views"],
                          "likes": a.get("likes"), "author": a.get("author"),
                          "publishTime": a.get("publishTime"), "tags": a.get("tags"),
                          "bodyChars": a.get("bodyChars"), "file": a.get("file")}
                         for a in picks],
                "candidateCount": len(cands),
            })
            if len(picks) < args.per_topic:
                log(f"  ⚠ {t['id']} 只配到 {len(picks)}/{args.per_topic} 篇（该主题 ≥{min_views} 阅读的文章不够）")
        b.close()

    payload = {"minViews": min_views, "perTopic": args.per_topic,
               "collectedAt": datetime.now().isoformat(timespec="seconds"),
               "topicsFile": str(args.topics), "topics": summary}
    (out_root / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = sum(1 for s in summary if len(s["refs"]) >= args.per_topic)
    urls = [r["url"] for s in summary for r in s["refs"]]
    lines = ["# 40 篇选题 · 参考素材清单", "",
             f"- 门槛：≥ {min_views} 阅读｜每篇配 {args.per_topic} 篇｜"
             f"采集时间 {payload['collectedAt']}",
             f"- 配齐 {ok}/{len(summary)} 篇｜共 {len(urls)} 条引用，**去重后 {len(set(urls))} 篇**（无重复）", ""]
    for s in summary:
        lines.append(f"## {s['id']} · {s['title']}")
        lines.append("")
        lines.append(f"- 搜索词：{' / '.join(s['keywords'])}")
        if not s["refs"]:
            lines.append("- ⚠ **没搜到达标参考文章**，需要换关键词或降门槛")
        for r in s["refs"]:
            lines.append(f"- [{r['title']}]({r['url']}) — {r['views']:,} 阅读"
                         f"（{r.get('author') or '未知'}，{r.get('bodyChars') or 0} 字）"
                         f" → `{r['file']}`")
        lines.append("")
    (out_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    log("=" * 70)
    log(f"配齐 {ok}/{len(summary)} 篇；引用 {len(urls)} 条，去重后 {len(set(urls))} 篇")
    log(f"清单：{out_root / 'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
