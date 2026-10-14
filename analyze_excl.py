# -*- coding: utf-8 -*-
"""一次性分析：剔除指定高阅读文章后，重算主题词 / 标题特征 / 时段，并做去年龄偏差的主题对比。

只读已有快照，不联网、不发布。用法：
    .\\.venv\\Scripts\\python.exe analyze_excl.py [--window-days 7] [--exclude "正则"]
"""
import argparse
import re
import sys
from datetime import datetime

import csdn_stats as S

DEFAULT_EXCLUDE = r""   # 默认不排除任何文章；用 --exclude "标题正则" 指定

# 领域分类（按顺序取第一个命中的）
DOMAINS = [
    ("算法/刷题", r"(算法|力扣|LeetCode|Hot100|链表|二叉树|动态规划|哈希|指针|单调栈|排序|递归|DFS|深度优先|广度优先|LRU|数组)"),
    ("Redis", r"Redis"),
    ("MySQL/数据库", r"(MySQL|InnoDB|索引|数据库|SQL|事务|行锁|表设计|分库分表)"),
    ("Docker/容器", r"(Docker|容器|镜像|Dockerfile)"),
    ("云原生/微服务", r"(云原生|微服务|Service\s*Mesh|Kubernetes|K8s|分布式|Kafka|消息队列|MQ|网关)"),
    ("Java/Spring", r"(Java|Spring|JVM|并发|线程|Maven|MyBatis)"),
    ("网络/OS/工程基础", r"(网络|HTTP|TCP|IP|操作系统|Linux|Git|Nginx|命令行|Shell)"),
    ("AI/大模型", r"(大模型|LLM|GPT|AI|机器学习|深度学习|Prompt)"),
]


def domain_of(title):
    for name, pat in DOMAINS:
        if re.search(pat, title, re.I):
            return name
    return "未分类"


def prepare(arts, cur, history):
    now = S.parse_time(cur["collectedAt"]) or datetime.now()
    diffs, gap_h = S.diff_snapshots(cur, history[-2] if len(history) >= 2 else None)
    for a in arts:
        pub = S.parse_time(a.get("publishTime"))
        a["_pub"] = pub
        a["_ageH"] = S.age_hours(pub, now)
        a["_ageD"] = (a["_ageH"] / 24) if a["_ageH"] is not None else None
        d = diffs.get(a["id"])
        a["_dViews"] = d["dViews"] if d else None
        # 阅读/天：用年龄做归一，缓解"老文章累计多"的偏差；年龄下限 1 天，避免新文炸表
        a["_vpd"] = (a["views"] / max(a["_ageD"], 1.0)) if a["_ageD"] is not None else None
        a["_domain"] = domain_of(a["title"])
        a["_dViews"] = a["_dViews"] if a["_dViews"] is not None else 0
    return arts, gap_h, diffs


def table(rows, headers, aligns=None):
    return S.table(rows, headers, aligns)


def block(title, lines):
    print("\n### " + title)
    for ln in lines:
        print(ln)


def feature_rows(rows):
    out = []
    for name, pat in S.TITLE_MARKERS:
        hit = [a for a in rows if re.search(pat, a["title"])]
        miss = [a for a in rows if not re.search(pat, a["title"])]
        if not hit:
            continue
        out.append((name, f"{len(hit)}/{len(rows)}",
                    S.fmt_int(sum(a["views"] for a in hit) / len(hit)),
                    S.fmt_int(sum(a["views"] for a in miss) / len(miss)) if miss else "—"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--exclude", default=DEFAULT_EXCLUDE)
    args = ap.parse_args()

    snaps = S.load_snapshots()
    if not snaps:
        print("没有快照，先跑 csdn_stats.bat")
        return 1
    cur = snaps[-1]
    arts, gap_h, diffs = prepare([dict(a) for a in cur["articles"]], cur, snaps)

    excl = re.compile(args.exclude)
    dropped = [a for a in arts if excl.search(a["title"])]
    window_all = [a for a in arts if a["_ageD"] is not None and a["_ageD"] <= args.window_days]
    window = [a for a in window_all if not excl.search(a["title"])]

    print(f"快照：{cur['collectedAt']}  文章 {len(arts)} 篇  距上次 {gap_h:.1f}h" if gap_h
          else f"快照：{cur['collectedAt']}  文章 {len(arts)} 篇")
    print(f"剔除 {len(dropped)} 篇：" + "; ".join(
        f"{a['title'][:28]}（{a['views']} 阅读）" for a in dropped))
    print(f"近 {args.window_days} 天窗口：{len(window_all)} 篇 → 剔除后 {len(window)} 篇")

    # ---------- 0. 窗口内逐篇 ----------
    block(f"0. 近 {args.window_days} 天逐篇（剔除后，按阅读降序）",
          [table([(a["title"][:40], a["_domain"], a["views"], a["likes"], a["favorites"],
                   a["_dViews"], f"{a['_ageD']:.1f}d") for a in
                  sorted(window, key=lambda x: -x["views"])],
                 ["标题", "领域", "阅读", "赞", "藏", "本轮新增", "年龄"],
                 ["---", "---", "---:", "---:", "---:", "---:", "---:"])])

    # ---------- 1. 主题词（脚本原口径）剔除前后 ----------
    for label, rows in (("剔除前", window_all), ("剔除后", window)):
        words = S.theme_words([a["title"] for a in rows])[:8]
        out = []
        for w, _c in words:
            hit = [a for a in rows if re.search(re.escape(w), a["title"], re.I)]
            out.append((w, len(hit), S.fmt_int(sum(a["views"] for a in hit) / len(hit)),
                        S.fmt_int(sum(a["views"] for a in hit))))
        out.sort(key=lambda r: -int(str(r[2]).replace(",", "")))
        block(f"1. 主题词（脚本原口径：标题里的英文词）——{label}",
              [table(out, ["主题词", "篇数", "平均阅读", "合计阅读"],
                     ["---", "---:", "---:", "---:"])])

    # ---------- 2. 领域分类 ----------
    for label, rows in (("剔除前", window_all), ("剔除后", window)):
        g = S.agg(rows, lambda r: r["_domain"])
        block(f"2. 领域分类（人工关键词映射）——{label}",
              [table([(x["key"], x["n"], S.fmt_int(x["avg"]), S.fmt_int(x["median"]),
                       S.fmt_int(x["total"])) for x in g],
                     ["领域", "篇数", "平均阅读", "中位阅读", "合计"],
                     ["---", "---:", "---:", "---:", "---:"])])

    # ---------- 3. 去年龄偏差：阅读/天 ----------
    vp = [a for a in arts if a["_vpd"] is not None and not excl.search(a["title"])]
    g = S.agg(vp, lambda r: r["_domain"])
    rows = []
    for x in g:
        items = [a for a in vp if a["_domain"] == x["key"]]
        rows.append((x["key"], x["n"], S.fmt_int(sum(i["_vpd"] for i in items) / len(items)),
                     S.fmt_int(x["avg"]), S.fmt_int(x["total"])))
    block("3. 全量 151 篇（剔除后）：领域 × 阅读/天（缓解年龄偏差）",
          [table(rows, ["领域", "篇数", "平均阅读/天", "平均累计阅读", "合计累计"],
                 ["---", "---:", "---:", "---:", "---:"]),
           "",
           "> 阅读/天 对刚发布的文章偏高（发布初期是尖峰），对老文章偏低（长尾已平），",
           "> 所以它和「平均累计阅读」一起看才准：两者都高 = 这个领域真的有人持续读。"])

    # ---------- 4. 标题特征 ----------
    block("4. 标题特征（剔除后，近 %d 天）" % args.window_days,
          [table(feature_rows(window),
                 ["标题特征", "命中篇数", "命中平均阅读", "未命中平均阅读"],
                 ["---", "---:", "---:", "---:"]),
           "",
           f"- 标题平均长度：{sum(len(a['title']) for a in window) / len(window):.1f} 字"
           if window else ""])

    # ---------- 5. 时段 ----------
    g = S.agg(window, lambda r: r["_pub"].hour if r["_pub"] else None)
    block("5. 发布小时（剔除后，近 %d 天）" % args.window_days,
          [table([(f"{x['key']:02d}:00", x["n"], S.fmt_int(x["avg"]), S.fmt_int(x["median"]))
                  for x in g], ["发布小时", "篇数", "平均阅读", "中位阅读"],
                 ["---", "---:", "---:", "---:"])])

    # ---------- 6. 本次新增 ----------
    new = [a for a in window if a["_dViews"]]
    block("6. 本轮新增阅读（剔除后，近 %d 天）" % args.window_days,
          [table([(a["title"][:40], a["_dViews"], a["views"]) for a in
                  sorted(new, key=lambda x: -x["_dViews"])],
                 ["标题", "本轮新增", "累计阅读"], ["---", "---:", "---:"])])
    return 0


if __name__ == "__main__":
    sys.exit(main())
