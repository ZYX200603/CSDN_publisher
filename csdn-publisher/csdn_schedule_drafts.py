#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「草稿箱」里现成的稿子改成定时发布（不动正文 / 标签 / 摘要 / 栏目）。

    python csdn_schedule_drafts.py --at "2026-10-08 16:30" --dry-run --only 1
    python csdn_schedule_drafts.py --at "2026-10-08 16:30"

做法：从内容管理页读出草稿箱的 articleId 列表 → 逐个用
`editor.csdn.net/md?articleId=<id>` 打开（编辑器会自动恢复该草稿的正文/标题/标签/摘要）
→ 点「发布文章」→ 点子弹窗「定时发布」→ 填日期+时间 → 回读校验 → 点红色「定时发布」。

安全设计：
  * 打开后先核对标题：和列表里读到的不一致就**跳过这一篇**（宁可不改，也不发错稿）；
  * `--dry-run` 只填不提交，用来先验证链路；
  * CSDN 的额度拦截会被识别出来，标成 rc=3 并继续下一篇；
  * 整个过程不写正文，草稿里是什么就发什么。
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

MANAGE = "https://mp.csdn.net/mp_blog/manage/article"
EDITOR = "https://editor.csdn.net/md?articleId={aid}"

LIST_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('.article-list-item-mp')].map((r, i) => {
    const a = r.querySelector('.list-item-title a');
    const href = a ? (a.getAttribute('href') || '') : '';
    const m = href.match(/articleId=(\d+)/);
    const t = r.querySelector('.article-item-time, .article-list-item-time');
    return {
      idx: i + 1,
      id: m ? m[1] : '',
      title: a ? clean(a.innerText) : '',
      time: t ? clean(t.innerText) : '',
    };
  });
}
"""

OPEN_TAB_JS = r"""
(name) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const el = [...document.querySelectorAll('.nav-link')]
    .find(e => clean(e.innerText).startsWith(name));
  if (!el) return null;
  el.click();
  return clean(el.innerText);
}
"""

QUOTA_JS = r"""
() => {
  const el = document.querySelector('.publish-quota-tip');
  return el ? (el.innerText || '').replace(/\s+/g, ' ').trim() : null;
}
"""


def read_editor_title(page):
    """读编辑器里当前的标题（标题框可能还是 display:none，所以两个地方都看）。"""
    try:
        return page.evaluate(
            """() => {
                const inp = document.querySelector('input.article-bar__title');
                const disp = document.querySelector('.article-bar__title-display');
                const body = document.querySelector('pre.editor__inner');
                return {
                    input: inp ? (inp.value || '').trim() : null,
                    display: disp ? (disp.innerText || '').trim() : null,
                    bodyLen: body ? (body.innerText || '').replace(/\\s+/g, '').length : 0,
                };
            }"""
        )
    except Exception:
        return {}


def norm(s):
    return re.sub(r"[\s\W_]+", "", str(s or "")).lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", required=True,
                    help='定时时间："2026-10-08 16:30" / "+26h" / auto')
    ap.add_argument("--dry-run", action="store_true", help="只填不提交（先验证链路）")
    ap.add_argument("--only", type=int, nargs="+", help="只处理草稿箱列表里的第 N 篇")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几篇")
    args = ap.parse_args()

    target = pc.align_step(pc.parse_schedule_when(args.at))
    ok, why = pc.check_schedule_window(target)
    print(f"目标时间: {target:%Y-%m-%d %H:%M}  {why}", flush=True)
    if not ok:
        print("时间不在 CSDN 允许的区间内，什么都没做", flush=True)
        return 1

    results = []
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        print(f"登录态: {logged}", flush=True)
        if not logged:
            b.close()
            return 1

        # 1) 读草稿箱列表（只读一次，后面的提交会让列表变化）
        page.goto(MANAGE, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        pc.dismiss_overlays(page)
        print(f"切草稿箱: {page.evaluate(OPEN_TAB_JS, '草稿箱')!r}", flush=True)
        page.wait_for_timeout(3500)
        drafts = [d for d in page.evaluate(LIST_JS) if d["id"]]
        print(f"草稿箱里 {len(drafts)} 篇:", flush=True)
        for d in drafts:
            print(f"  [{d['idx']}] id={d['id']} {d['title'][:46]:48} {d['time']}", flush=True)

        todo = drafts
        if args.only:
            todo = [d for d in drafts if d["idx"] in args.only]
        if args.limit:
            todo = todo[: args.limit]
        if not todo:
            print("没有要处理的草稿", flush=True)
            b.close()
            return 1

        print(f"\n开始处理 {len(todo)} 篇，目标时间 {target:%Y-%m-%d %H:%M}"
              f"{'（dry-run，不提交）' if args.dry_run else ''}", flush=True)

        # 2) 逐篇：打开草稿 → 定时发布
        for d in todo:
            print("=" * 70, flush=True)
            print(f"第{d['idx']}篇 id={d['id']} 列表标题: {d['title']}", flush=True)
            page.goto(EDITOR.format(aid=d["id"]), wait_until="domcontentloaded")
            page.wait_for_timeout(6500)
            pc.dismiss_overlays(page)

            info = read_editor_title(page)
            cur = info.get("input") or info.get("display") or ""
            if norm(d["title"]) and norm(d["title"]) not in norm(cur) and norm(cur) not in norm(d["title"]):
                print(f"  ⚠ 打开后的标题对不上（编辑器里是 {cur[:40]!r}），跳过这一篇，不发", flush=True)
                b.shot(page, f"draft{d['id']}-title-mismatch")
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 9})
                continue
            print(f"  标题核对通过: {cur[:44]!r} 正文字符数≈{info.get('bodyLen')}", flush=True)
            print(f"  额度提示: {page.evaluate(QUOTA_JS)!r}", flush=True)

            btn, _ = pc.find_visible(page, pc.CFG["selectors"]["publish_button"], 10000, "发布按钮")
            if not btn:
                print("  找不到「发布文章」按钮，跳过", flush=True)
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 1})
                continue
            btn.click()
            page.wait_for_timeout(3500)
            pc.dump_publish_dialog(page)

            dlg, fail = pc.open_schedule_dialog(page)
            if not dlg:
                print(f"  打不开定时发布子弹窗: {fail}", flush=True)
                b.shot(page, f"draft{d['id']}-schedule-open-failed")
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 1})
                continue
            if not pc.fill_schedule_dialog(page, target):
                print("  时间没填成功，跳过", flush=True)
                b.shot(page, f"draft{d['id']}-schedule-fill-failed")
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 1})
                continue
            good, detail = pc.verify_scheduled_time(page, target)
            print(f"  定时时间回读: {detail} -> {'一致' if good else '不一致'}", flush=True)
            b.shot(page, f"draft{d['id']}-filled")
            if not good:
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 1})
                continue

            if args.dry_run:
                print("  dry-run：已填好，停在这里不提交", flush=True)
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 0})
                continue

            pc.confirm_schedule(page)
            page.wait_for_timeout(3000)
            b.shot(page, f"draft{d['id']}-submitted")
            ok2, msg = pc.wait_schedule_success(page)
            b.shot(page, f"draft{d['id']}-result")
            if ok2:
                print(f"  ✅ 已排期到 {target:%Y-%m-%d %H:%M}（{msg}）", flush=True)
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 0})
            elif msg.startswith("平台拦下"):
                print(f"  ⛔ CSDN 拒绝：{msg}", flush=True)
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 3})
            else:
                print(f"  ⚠ 结果未确认：{msg}", flush=True)
                results.append({"idx": d["idx"], "id": d["id"], "title": d["title"], "rc": 2})
            time.sleep(2)

        b.close()

    print("=" * 70, flush=True)
    print("结果汇总：", flush=True)
    mark = {0: "✅ 成功", 1: "❌ 失败", 2: "⚠️ 未确认", 3: "⛔ 被拒（额度/审核）",
            9: "⏭ 标题不符已跳过"}
    for r in results:
        print(f"  第{r['idx']}篇 id={r['id']} {r['title'][:34]:36} -> {mark.get(r['rc'], r['rc'])}",
              flush=True)
    bad = [r for r in results if r["rc"] not in (0,)]
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
