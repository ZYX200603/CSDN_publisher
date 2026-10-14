#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出 / 清理 CSDN 内容管理里的 草稿箱 / 定时发布 / 审核中 列表。

    python csdn_draft_clean.py                      # 只列草稿（只读，安全）
    python csdn_draft_clean.py --tabs               # 只列出有哪些标签页
    python csdn_draft_clean.py --tab 定时发布        # 列定时发布列表（只读）
    python csdn_draft_clean.py --delete IDX --match 标题子串 [--tab 定时发布]

用于处理"同一篇存草稿跑两次、CSDN 新建了一篇"留下的重复草稿，
以及验证完定时发布之后把测试稿删掉。
删除是真实操作，先跑列表版本核对标题和时间再删。
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

URL = "https://mp.csdn.net/mp_blog/manage/article"

TABS_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('.nav-link')]
    .map(e => clean(e.innerText)).filter(Boolean);
}
"""

OPEN_TAB_JS = r"""
(name) => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const el = [...document.querySelectorAll('.nav-link')]
    .find(e => clean(e.innerText).startsWith(name));
  if (!el) return null;
  el.click();
  return clean(el.innerText);
}
"""

LIST_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('.article-list-item-mp')].map((r, i) => {
    const t = r.querySelector('.list-item-title a');
    const href = t ? (t.getAttribute('href') || '') : '';
    const m = href.match(/articleId=(\d+)/);
    const tm = r.querySelector('.article-list-item-time');
    const acts = [...r.querySelectorAll('a,button,span')]
      .map(e => clean(e.innerText)).filter(x => x && x.length <= 6);
    return {
      idx: i + 1,
      id: m ? m[1] : '',
      title: t ? clean(t.innerText) : '',
      time: tm ? clean(tm.innerText) : '',
      acts: [...new Set(acts)].slice(0, 16)
    };
  });
}
"""

DELETE_JS = r"""
(n) => {
  const rows = [...document.querySelectorAll('.article-list-item-mp')];
  const r = rows[n - 1];
  if (!r) return null;
  const cand = [...r.querySelectorAll('a,button,span')]
    .find(e => /删除/.test((e.innerText || '').trim()) && (e.innerText || '').trim().length <= 6);
  if (!cand) return null;
  cand.click();
  return (r.querySelector('.list-item-title a') || {}).innerText || 'clicked';
}
"""

CONFIRM_JS = r"""
() => {
  // 确认弹窗是 el_mcm-message-box，必须先点它自己的确定按钮。
  // 早先按文本匹配 删除/确定，结果命中了行内的「删除」链接，等于又把弹窗点开一次，
  // 看起来"点了但没删"。
  const box = document.querySelector('.el_mcm-message-box');
  if (!box || !box.getClientRects().length) return null;
  const btn = box.querySelector('.el_mcm-message-box__btns button.btn-msg-confirm')
           || [...box.querySelectorAll('button')]
                .find(b => /^(确定|确认)$/.test((b.innerText || '').trim()));
  if (!btn) return null;
  btn.click();
  return (btn.innerText || '').trim();
}
"""

INSPECT_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  document.querySelectorAll('button,a,span,div,i').forEach(e => {
    const t = clean(e.innerText);
    if (!t || t.length > 12) return;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    const st = getComputedStyle(e);
    if (st.visibility === 'hidden' || st.display === 'none') return;
    out.push({text: t, tag: e.tagName, cls: String(e.className || '').slice(0, 70),
              role: e.getAttribute('role') || ''});
  });
  return out;
}
"""

# 定时发布的文章在列表里会有**两个**时间（提交时间 + 定时时间），
# 普通行的 time 字段只取到其中一个，所以另给一个整行原文的口子。
RAW_JS = r"""
() => {
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('.article-list-item-mp')].map((r, i) => ({
    idx: i + 1,
    title: clean((r.querySelector('.list-item-title a') || {}).innerText),
    times: [...r.querySelectorAll('.article-item-time, .article-list-item-time, [class*=time]')]
             .map(e => clean(e.innerText)).filter(Boolean),
    raw: clean(r.innerText).slice(0, 220),
  }));
}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delete", type=int, nargs="+", default=[],
                    help="删除列表里第 N 条（可多个，按序号从大到小执行）")
    ap.add_argument("--tab", default="草稿箱", help="切到哪个标签页：草稿箱 / 定时发布 / 审核中…")
    ap.add_argument("--tabs", action="store_true", help="只列出页面上有哪些标签页，不切不删")
    ap.add_argument("--raw", action="store_true", help="额外打印整行原文（看定时发布的两个时间）")
    ap.add_argument("--match", default="", help="删除前必须匹配的标题子串（防误删）")
    ap.add_argument("--expect-time", default="", help="删除前必须精确匹配的时间戳（同标题重复草稿时靠它区分）")
    ap.add_argument("--inspect", action="store_true", help="只点击删除并导出确认弹窗里的可点元素，不确认")
    args = ap.parse_args()

    with sync_playwright() as p:
        b = pc.Browser(p)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        print(f"登录态: {logged}")
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        # 标签页是异步渲染的：固定 sleep 偶尔会拿到空列表（实测踩过两次，
        # 结果把「全部」当成「草稿箱」列出来）。这里等到标签真的出现再点。
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('.nav-link').length >= 3", timeout=20000)
        except Exception:
            print("等标签页渲染超时，可能页面结构变了")
        tabs = page.evaluate(TABS_JS)
        print(f"页面标签页: {tabs}")
        if args.tabs:
            b.close()
            return 0
        clicked = page.evaluate(OPEN_TAB_JS, args.tab)
        print(f"切到「{args.tab}」: {clicked!r}")
        if clicked is None:
            print("  没找到这个标签页，下面的列表是当前页面（不是你要的标签页），别据此删东西")
        page.wait_for_timeout(4000)
        try:
            page.wait_for_function(
                "() => document.querySelectorAll('.article-list-item-mp').length > 0", timeout=15000)
        except Exception:
            print("  列表条目没渲染出来")

        rows = page.evaluate(LIST_JS)
        if not rows:
            print("列表为空，或页面结构变了")
        for r in rows:
            print(f"  [{r['idx']}] id={r.get('id') or '-':<10} {r['title'][:46]:48} {r['time']:22} {r['acts']}")
        if args.raw:
            print("--- 整行原文（定时发布的稿子这里能看到两个时间）---")
            for r in page.evaluate(RAW_JS):
                print(f"  [{r['idx']}] title={r['title'][:40]!r}")
                print(f"        times={r['times']}")
                print(f"        raw={r['raw'][:180]!r}")

        if args.delete:
            # 支持一次会话删多份：按序号**从大到小**删（删后面的不会影响前面的序号），
            # 每删一份都重新读列表核对标题与时间，避免列表刷新后误删。
            for idx in sorted(args.delete, reverse=True):
                rows_now = page.evaluate(LIST_JS)
                target = next((r for r in rows_now if r["idx"] == idx), None)
                if not target:
                    print(f"没有第 {idx} 条，跳过")
                    continue
                if args.match and not re.search(re.escape(args.match), target["title"]):
                    print(f"第 {idx} 条标题 {target['title']!r} 不含 {args.match!r}，跳过删除")
                    continue
                if args.expect_time and target["time"] != args.expect_time:
                    print(f"第 {idx} 条时间是 {target['time']!r}，不是期望的 "
                          f"{args.expect_time!r}（列表顺序可能变了），跳过删除")
                    continue
                print(f"准备删除: {target['title'][:52]} / {target['time']}")
                print(f"点击删除: {page.evaluate(DELETE_JS, idx)!r}")
                page.wait_for_timeout(2500)
                if args.inspect:
                    print("--- 弹窗里可见的可点元素 ---")
                    for x in page.evaluate(INSPECT_JS):
                        print(f"  {x['text']!r:16} <{x['tag']} role={x['role']!r} class={x['cls']!r}>")
                    b.close()
                    return 0
                print(f"确认弹窗: {page.evaluate(CONFIRM_JS)!r}")
                page.wait_for_timeout(3000)
            after = page.evaluate(LIST_JS)
            print(f"删除后剩 {len(after)} 条")
            for r2 in after:
                print(f"  [{r2['idx']}] {r2['title'][:52]:54} {r2['time']}")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
