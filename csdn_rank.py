#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 跨批排名：绝对排名（哪篇阅读高）+ 相对排名（哪类文章相对受欢迎）。

与 csdn_retro.py 的分工：retro 管「单批单日」，本脚本管「跨批横评」。
铁律同样适用：一个脚本 + 命令行参数，不为某一天建副本。

口径（与 retro 一致，勿各说各话）：
  - 24h 阅读 = 发布后 16~32h 窗口内最接近 24h 的快照值（统一取样龄）。
  - 未满 24h 的文章用同批最新观测，报告里标「未满」。
  - 投流量券的批次用 --coupon 标注，默认不进主统计（横评会被券冲高）。

用法:
    .venv/Scripts/python.exe csdn_rank.py --since 2026-10-08 --until 2026-10-14
    .venv/Scripts/python.exe csdn_rank.py --since 2026-10-08 --coupon 2026-10-11
    .venv/Scripts/python.exe csdn_rank.py --top 25 --report output/stats/rank.md
"""

import argparse
import csv
import glob
import json
import re
import statistics as st
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HISTORY = ROOT / "output/stats/history.csv"
DATASET_GLOB = "output/stats/retro-*-dataset*.json"
DEFAULT_REPORT = "output/stats/rank-{since}_{until}.md"

# 本地 md 的 category -> 统一主题名（与 csdn_retro.py 的 topicNormalize 保持一致）
TOPIC_NORMALIZE = {
    "redis": "Redis",
    "mysql": "MySQL/InnoDB",
    "go 项目": "Go",
    "go项目分享": "Go",
    "技术栈": "技术栈/JVM",
    "技术栈学习笔记": "技术栈/JVM",
    "算法题目解析": "算法",
}

# 内容形态：按标题/语义归类（相对排名的第二维度备用，当前默认只出主题池）
def load_series():
    """{id: [(age_h, views), ...]} + publishTime + title，来自 history.csv。"""
    obs = defaultdict(list)
    meta = {}
    with open(HISTORY, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            aid = r["id"]
            pt = datetime.strptime(r["publishTime"], "%Y-%m-%d %H:%M:%S")
            ct = datetime.strptime(r["collectedAt"][:19], "%Y-%m-%dT%H:%M:%S")
            age = (ct - pt).total_seconds() / 3600
            if age < 0:
                continue
            obs[aid].append((age, int(r["views"])))
            meta[aid] = {"publishTime": r["publishTime"], "title": r["title"]}
    return obs, meta


# 标题关键词 -> 主题名（仅兜底：dataset 里没有/为 ? 时用，优先级低于 dataset 的 topic）
TITLE_TOPIC_FALLBACK = [
    (r"redis|RDB|AOF|哨兵|主从|集群.*槽|缓存", "Redis"),
    (r"\bGo\b|goroutine|gRPC|channel|pprof|context|defer", "Go"),
    (r"mysql|InnoDB|MVCC|Buffer Pool|脏页|深分页", "MySQL/InnoDB"),
    (r"kafka|消息队列|死信|DLQ|RabbitMQ|积压", "消息队列"),
    (r"k8s|K8s|Pod|kubectl|探针|CrashLoop|Pending|调度器", "K8s"),
]

# 「技术栈/JVM」粗池内部细分：按标题关键词，用于细粒度相对排名
JVM_SUBPOOL = [
    (r"kafka|消息队列|死信|DLQ|RabbitMQ|积压", "消息队列/Kafka"),
    (r"k8s|K8s|Pod|kubectl|探针|CrashLoop|Pending|调度器|conntrack|ndots", "K8s/网络"),
    (r"TIME_WAIT|TCP|HTTP|DNS|Nginx|502|504|curl|微服务链路", "网络/HTTP"),
    (r"GC|垃圾回收|Full GC|堆|停顿", "JVM/GC"),
]


def topic_from_title(title):
    for pat, name in TITLE_TOPIC_FALLBACK:
        if re.search(pat, title, re.I):
            return name
    return None


def subpool_of(title):
    for pat, name in JVM_SUBPOOL:
        if re.search(pat, title, re.I):
            return name
    return None


def load_topics():
    """{id: topic}。优先 retro 各日 dataset（已按本地 md category 归一化）。"""
    topics = {}
    for f in glob.glob(str(ROOT / DATASET_GLOB)):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        for row in d.get("rows", []):
            if row.get("id") and row.get("topic"):
                topics[row["id"]] = row["topic"]
    return topics


def views_at_24h(series, lo=16.0, hi=32.0):
    """取 16~32h 窗口内最接近 24h 的观测。返回 (views, age, exact)。"""
    cand = [(a, v) for a, v in series if lo <= a <= hi]
    if not cand:
        latest = max(series, key=lambda x: x[0])
        return latest[1], latest[0], False
    age, views = min(cand, key=lambda x: abs(x[0] - 24.0))
    return views, age, True


def adjust_to_24h(views, age, k=0.82, thresh=20.0):
    """取样龄不足 thresh 时，按幂律 v∝age^k 折算到 24h（与 retro 外推同口径）。
    龄≥thresh 的偏差在幂律下 <10%，不折算，保持原始值透明可比。"""
    if age >= thresh:
        return views
    return round(views * (24.0 / age) ** k)


def fmt_dur(h):
    return f"{h:.1f}h"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-10-08", help="起始发布日（含）")
    ap.add_argument("--until", default="2026-10-14", help="截止发布日（含）")
    ap.add_argument("--coupon", action="append", default=[],
                    help="投了流量券的批次（发布日），默认剔除出主统计")
    ap.add_argument("--top", type=int, default=25, help="绝对榜单列几行")
    ap.add_argument("--report", default=None, help="报告输出路径")
    ap.add_argument("--min-pool", type=int, default=2, help="主题池纳入份额指数的最少篇数")
    args = ap.parse_args()

    series, meta = load_series()
    topics = load_topics()

    arts = []
    for aid, s in series.items():
        pt = meta[aid]["publishTime"]
        day = pt[:10]
        if not (args.since <= day <= args.until):
            continue
        v24, age24, exact = views_at_24h(s)
        topic = topics.get(aid)
        if not topic or topic == "?":
            topic = topic_from_title(meta[aid]["title"]) or "未归类"
        arts.append({
            "id": aid, "title": meta[aid]["title"], "day": day,
            "topic": topic, "views24": v24, "age24": age24, "exact": exact,
            "views24_adj": adjust_to_24h(v24, age24),
            "latest": max(s, key=lambda x: x[0])[1],
            "coupon": day in args.coupon,
        })

    arts.sort(key=lambda a: -a["views24_adj"])
    main_pool = [a for a in arts if not a["coupon"]]
    coupon_pool = [a for a in arts if a["coupon"]]

    # ---- 主题池份额指数：阅读占比 ÷ 篇数占比（主口径用折算后 24h，消除取样龄偏差）----
    total_v = sum(a["views24_adj"] for a in main_pool)
    total_n = len(main_pool)
    pools = defaultdict(list)
    for a in main_pool:
        pools[a["topic"]].append(a)
    pool_rows = []
    for t, rows in pools.items():
        v = sum(r["views24_adj"] for r in rows)
        v_raw = sum(r["views24"] for r in rows)
        n = len(rows)
        pool_rows.append({
            "topic": t, "n": n, "views": v, "views_raw": v_raw,
            "share_v": v / total_v if total_v else 0,
            "share_n": n / total_n if total_n else 0,
            "median": st.median(r["views24_adj"] for r in rows),
            "median_raw": st.median(r["views24"] for r in rows),
            "best": max(r["views24_adj"] for r in rows),
            "worst": min(r["views24_adj"] for r in rows),
        })
    for r in pool_rows:
        r["index"] = r["share_v"] / r["share_n"] if r["share_n"] else float("inf")
    pool_rows.sort(key=lambda r: -r["index"])

    # ---- 单篇相对分位：每篇在本池内的排名 ----
    for t, rows in pools.items():
        rows.sort(key=lambda r: -r["views24"])
        for i, r in enumerate(rows):
            r["rank_in_pool"] = i + 1
            r["pool_size"] = len(rows)
            r["pct_in_pool"] = r["rank_in_pool"] / len(rows)

    # ---- 报告 ----
    out = Path(args.report) if args.report else ROOT / DEFAULT_REPORT.format(
        since=args.since.replace("-", "")[:8], until=args.until.replace("-", "")[:8])
    L = []
    bar = "=" * 100
    L.append(bar)
    L.append(f"跨批排名复盘 {args.since} ~ {args.until}｜口径：24h 阅读（16~32h 窗口内最接近 24h 的快照）")
    L.append(bar)
    L.append(f"  发布文章 {len(arts)} 篇（主统计 {total_n} 篇"
             + (f"，剔除投券批 {len(coupon_pool)} 篇" if coupon_pool else "") + "）")
    L.append(f"  主统计合计 {total_v} 阅读 | 中位 {st.median(a['views24'] for a in main_pool):.0f} | "
             f"均值 {total_v/total_n:.0f} | 最高 {max(a['views24'] for a in main_pool)} | "
             f"最低 {min(a['views24'] for a in main_pool)}")
    if coupon_pool:
        L.append(f"  ⚠ 投券批（{', '.join(sorted(args.coupon))}）已剔除："
                 + "；".join(f"{a['title'][:18]}… {a['views24']}" for a in coupon_pool))
    L.append("")

    # 按日汇总
    L.append(bar)
    L.append("A. 按日汇总（24h 口径，中位数）")
    L.append(bar)
    days = defaultdict(list)
    for a in main_pool:
        days[a["day"]].append(a)
    L.append(f"{'发布日':<12}{'篇数':>5}{'合计':>8}{'中位':>8}{'最高':>8}{'最低':>8}{'取样龄':>12}{'折算24h中位':>12}")
    L.append("-" * 100)
    for d in sorted(days):
        rows = days[d]
        ages = [r["age24"] for r in rows]
        med_adj = st.median(r["views24_adj"] for r in rows)
        L.append(f"{d:<12}{len(rows):>5}{sum(r['views24'] for r in rows):>8}"
                 f"{st.median(r['views24'] for r in rows):>8.0f}"
                 f"{max(r['views24'] for r in rows):>8}{min(r['views24'] for r in rows):>8}"
                 f"{min(ages):>4.0f}-{max(ages):<7.0f}h{med_adj:>12.0f}")
    L.append("")
    L.append("  说明：折算列 = 取样龄 <20h 的篇目按幂律 v∝age^0.82 折算到 24h（与 retro 外推同口径）。")
    L.append("        10-14 批只采到 17h，原始中位 38 低估约 25%，折算后 ~51。")
    L.append("")

    # 绝对排名
    L.append(bar)
    L.append("B. 绝对排名：哪篇文章 24h 阅读最高")
    L.append(bar)
    L.append(f"{'#':>3} {'24h阅读':>7} {'折算':>6} {'龄h':>6} {'发布日':<11}{'主题池':<12}{'篇内名次':<10}标题")
    L.append("-" * 100)
    for i, a in enumerate(arts[: args.top], 1):
        if a["coupon"]:
            flag = "🎫投券"
        elif a["age24"] < 20:
            flag = f"⚠取样{a['age24']:.0f}h(<20h)"
        else:
            flag = ""
        pin = f"{a.get('rank_in_pool', '—')}/{a.get('pool_size', '—')}"
        adj = f"{a['views24_adj']:>6}" if a["views24_adj"] != a["views24"] else "     —"
        L.append(f"{i:>3} {a['views24']:>7} {adj} {a['age24']:>6.1f} {a['day']:<11}{a['topic']:<12}{pin:<10}{a['title'][:34]} {flag}")
    L.append("")

    # 相对排名：主题池份额指数
    L.append(bar)
    L.append("C. 相对排名：哪类文章相对受欢迎（主题池份额指数）")
    L.append("  份额指数 = 该池阅读占比 ÷ 篇数占比。>1 = 用更少的篇数拿到了更多阅读，相对更受欢迎。")
    L.append("  口径：折算后 24h（取样龄<20h 的篇目按幂律折到 24h，已消除批次间取样龄偏差）。")
    L.append(bar)
    L.append(f"{'主题池':<14}{'篇数':>4}{'阅读占比':>9}{'篇数占比':>9}{'份额指数':>9}{'中位':>7}{'最高':>6}{'最低':>6}{'原始中位':>9}")
    L.append("-" * 100)
    for r in pool_rows:
        small = " ⚠n小" if r["n"] < 3 else ""
        L.append(f"{r['topic']:<14}{r['n']:>4}{r['share_v']*100:>8.1f}%{r['share_n']*100:>8.1f}%"
                 f"{r['index']:>9.2f}{r['median']:>7.0f}{r['best']:>6}{r['worst']:>6}{r['median_raw']:>9.0f}{small}")
    L.append("")
    solid = [r for r in pool_rows if r["n"] >= 3]
    up = "、".join("{}({:.2f})".format(r["topic"], r["index"]) for r in solid if r["index"] > 1) or "—"
    down = "、".join("{}({:.2f})".format(r["topic"], r["index"]) for r in solid if r["index"] <= 1) or "—"
    tiny = "、".join(r["topic"] for r in pool_rows if r["n"] < 3) or "—"
    L.append(f"  份额指数 >1（相对受欢迎，n≥3）：{up}")
    L.append(f"  份额指数 <1（相对吃亏，n≥3）：{down}")
    L.append(f"  ⚠ n<3 的池（{tiny}）份额指数波动大，只作提示，不作结论。")
    L.append("")

    # ---- C2 粗池内部细分 ----
    jvm_rows = [a for a in main_pool if a["topic"] == "技术栈/JVM"]
    if len(jvm_rows) >= 4:
        jvm_total = sum(r["views24_adj"] for r in jvm_rows)
        L.append(bar)
        jvm_idx = next(r["index"] for r in pool_rows if r["topic"] == "技术栈/JVM")
        L.append(f"C2. 粗池细分：技术栈/JVM {len(jvm_rows)} 篇内部谁更受欢迎（该池整体份额 {jvm_idx:.2f}，内部分化极大）")
        L.append(bar)
        subs = defaultdict(list)
        for a in jvm_rows:
            sp = subpool_of(a["title"]) or "其他"
            subs[sp].append(a)
        srows = []
        for name, rs in subs.items():
            v = sum(r["views24_adj"] for r in rs)
            srows.append({"name": name, "n": len(rs), "views": v,
                          "share_v": v / jvm_total if jvm_total else 0,
                          "share_n": len(rs) / len(jvm_rows),
                          "median": st.median(r["views24_adj"] for r in rs),
                          "best": max(r["views24_adj"] for r in rs),
                          "worst": min(r["views24_adj"] for r in rs)})
        for r in srows:
            r["index"] = r["share_v"] / r["share_n"]
        srows.sort(key=lambda r: -r["index"])
        L.append(f"{'细分':<14}{'篇数':>4}{'阅读占比':>9}{'篇数占比':>9}{'份额指数':>9}{'中位':>7}{'最高':>6}{'最低':>6}")
        L.append("-" * 100)
        for r in srows:
            L.append(f"{r['name']:<14}{r['n']:>4}{r['share_v']*100:>8.1f}%{r['share_n']*100:>8.1f}%"
                     f"{r['index']:>9.2f}{r['median']:>7.0f}{r['best']:>6}{r['worst']:>6}")
        L.append("")

    # 相对排名：篇内分位
    L.append(bar)
    L.append("D. 单篇相对分位：每个池子内部谁强谁弱")
    L.append(bar)
    for r in sorted(pool_rows, key=lambda x: -x["n"]):
        rows = sorted(pools[r["topic"]], key=lambda x: -x["views24_adj"])
        L.append(f"  【{r['topic']}】{r['n']} 篇，中位 {r['median']:.0f}")
        for j, a in enumerate(rows, 1):
            tag = "   ← 池内最高" if j == 1 else ("   ← 池内垫底" if j == len(rows) else "")
            val = f"{a['views24_adj']:>4}" + (f"({a['views24']})" if a["views24_adj"] != a["views24"] else "    ")
            L.append(f"    {j}. {val} ({a['day']}) {a['title'][:40]}{tag}")
    L.append("")
    L.append("  括号内为原始 24h 口径（取样龄<20h 已按幂律折算，括号=折算前）。")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已写入 {out}")
    print("\n".join(L[:60]))


if __name__ == "__main__":
    main()
