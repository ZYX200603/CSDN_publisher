#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""定时发布排期的自检（不联网、不开浏览器、不改任何东西）。

跑法: python test_schedule.py
改完 publish.schedule / scheduleGap 的解析逻辑后跑一遍，确保：
  * 三种写法（auto / 绝对时间 / 相对时间 / 数组）都能算出时间；
  * 越界（不到 4 小时、超过 6 天）会被 problems 拦下；
  * publish.on 与 schedule 的优先级符合文档。
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import csdn_job as cj  # noqa: E402
import publish_csdn as pc  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(f"{'✅' if cond else '❌'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAIL.append(name)


def args(**kw):
    base = {"dry_run": None, "draft": False, "schedule": None}
    base.update(kw)
    return SimpleNamespace(**base)


def main():
    now = datetime(2026, 10, 8, 9, 7, 0)
    print(f"基准时间: {now:%Y-%m-%d %H:%M}\n")

    print("--- parse_gap ---")
    check("parse_gap('6h')", cj.parse_gap("6h") == timedelta(hours=6))
    check("parse_gap('2d')", cj.parse_gap("2d") == timedelta(days=2))
    check("parse_gap('0')", cj.parse_gap("0") == timedelta(0))
    check("parse_gap(None) 默认 1 天", cj.parse_gap(None) == timedelta(days=1))

    print("\n--- auto 排期 ---")
    plan, probs = cj.schedule_plan("auto", 3, {}, now)
    for line in cj.format_plan(plan):
        print("   " + line)
    lo, hi = pc.schedule_window(now)
    check("auto 三篇都没有越界", not probs, str(probs))
    check("auto 每篇间隔 1 天", plan[1][0] - plan[0][0] == timedelta(days=1))
    check("auto 第一篇晚于下限", plan[0][0] >= lo, f"{plan[0][0]:%m-%d %H:%M} >= {lo:%m-%d %H:%M}")
    check("auto 第一篇在下限后 24h 内", plan[0][0] - lo <= timedelta(days=1))
    check("auto 是整点", plan[0][0].minute == 0 and plan[0][0].second == 0)

    print("\n--- 绝对时间 / 相对时间 ---")
    plan2, probs2 = cj.schedule_plan("2026-10-09 19:30", 1, {}, now)
    check("绝对时间解析正确", plan2[0][0] == datetime(2026, 10, 9, 19, 30), str(plan2[0][0]))
    plan3, _ = cj.schedule_plan("+26h", 1, {}, now)
    check("+26h 相对现在（并对齐刻度）",
          plan3[0][0] == pc.align_step(now + timedelta(hours=26)), str(plan3[0][0]))
    plan4, _ = cj.schedule_plan("2026-10-09 19:37", 1, {}, now)
    check("分钟对齐到 15 分钟刻度", plan4[0][0].minute == 30, str(plan4[0][0]))

    print("\n--- 数组（逐篇指定）---")
    plan5, probs5 = cj.schedule_plan(["+5h", "+50h", "auto"], 3, {}, now)
    for line in cj.format_plan(plan5):
        print("   " + line)
    check("数组前两项按指定值", plan5[0][0] == pc.align_step(now + timedelta(hours=5))
          and plan5[1][0] == pc.align_step(now + timedelta(hours=50)))
    check("数组第三项走 auto", not probs5, str(probs5))
    plan6, _ = cj.schedule_plan(["+5h"], 3, {}, now)
    check("数组写少了按 gap 顺延", plan6[2][0] - plan6[1][0] == timedelta(days=1),
          f"{plan6[1][0]:%m-%d %H:%M} -> {plan6[2][0]:%m-%d %H:%M}")

    print("\n--- 越界与撞车 ---")
    _, tooEarly = cj.schedule_plan("+1h", 1, {}, now)
    check("+1h 被拦（不足 4 小时）", bool(tooEarly), str(tooEarly))
    _, tooLate = cj.schedule_plan("+20d", 1, {}, now)
    check("+20d 被拦（超过 6 天）", bool(tooLate), str(tooLate))
    _, sameTime = cj.schedule_plan("+26h", 3, {}, now)
    check("同一个时间发 3 篇会提醒", any("同一时刻" in p for p in sameTime), str(sameTime))
    planGap, probsGap = cj.schedule_plan("auto", 3, {"publish": {"scheduleGap": "6h"}}, now)
    check("scheduleGap=6h 生效",
          (not probsGap) and planGap[1][0] - planGap[0][0] == timedelta(hours=6),
          f"{planGap[0][0]:%m-%d %H:%M} -> {planGap[1][0]:%m-%d %H:%M} {probsGap}")

    print("\n--- publish.on 与 schedule 的优先级 ---")
    dry, draft, mode, sc = cj.resolve_publish_mode(
        {"on": "schedule", "schedule": "2026-10-09 19:30"}, args())
    check("on=schedule + 时间 -> 定时且不 dry 不 draft",
          (dry, draft, sc) == (False, False, "2026-10-09 19:30"), f"{dry},{draft},{sc},{mode}")
    dry, draft, mode, sc = cj.resolve_publish_mode({"on": "schedule"}, args())
    check("on=schedule 不写时间 -> auto", sc == "auto", str(sc))
    dry, draft, mode, sc = cj.resolve_publish_mode({"on": "no", "schedule": "+26h"}, args())
    check("on=no 同时给了 schedule -> 定时优先，draft=False",
          sc == "+26h" and draft is False, f"{draft},{sc}")
    dry, draft, mode, sc = cj.resolve_publish_mode({"on": "no"}, args())
    check("on=no -> 存草稿", (dry, draft, sc) == (False, True, None))
    dry, draft, mode, sc = cj.resolve_publish_mode({"on": "yes"}, args())
    check("on=yes -> 直接发", (dry, draft, sc) == (False, False, None))
    dry, draft, mode, sc = cj.resolve_publish_mode({}, args())
    check("什么都不写 -> dry-run", dry is True and sc is None, f"dry={dry}")
    dry, draft, mode, sc = cj.resolve_publish_mode(
        {"on": "no", "schedule": "2026-10-09 19:30"}, args(dry_run=True))
    check("--dry-run 仍然最高优先级", dry is True and sc == "2026-10-09 19:30")
    dry, draft, mode, sc = cj.resolve_publish_mode({"on": "no"}, args(schedule="+30h"))
    check("命令行 --schedule 覆盖配置", sc == "+30h" and draft is False)

    print("\n" + ("全部通过 ✅" if not FAIL else f"有 {len(FAIL)} 项没过 ❌: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
