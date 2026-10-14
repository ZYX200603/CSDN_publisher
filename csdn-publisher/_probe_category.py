#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回读指定草稿/文章在 CSDN 上的**真实栏目归属**（唯一可信的判据）。

为什么要单独做这件事：文章页 HTML 里没有栏目字段，侧栏的 category_* 链接是
账号全部专栏、不可信；`final/*.md` 和 `manifest.json` 里的 category 只是"我想写什么"，
不是"CSDN 上实际是什么"。真正的事实只有一个地方能读到 —— **发布弹窗里的
「分类专栏」勾选状态**（和发布时写入的是同一个控件）。

用法：
    python _probe_category.py 167401136 167401021          # 逐个打开并回读栏目
    python _probe_category.py --list                       # 只列账号里所有真实专栏名

只读：打开编辑器 → 点「发布文章」打开弹窗 → 读勾选状态 → 直接关掉浏览器，
**不会点「确认发布」或「保存为草稿」**（连 dry-run 的副作用都没有）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def probe_one(page, aid):
    page.goto(f"https://editor.csdn.net/md?articleId={aid}", wait_until="domcontentloaded")
    page.wait_for_timeout(7000)
    pc.dismiss_overlays(page)

    title = page.evaluate(
        "() => ((document.querySelector('input.article-bar__title') || {}).value "
        "|| (document.querySelector('.article-bar__title-display') || {}).innerText || '')")

    btn, _ = pc.find_visible(page, pc.CFG["selectors"]["publish_button"], 10000, "发布按钮")
    if not btn:
        print(f"  {aid} ❌ 找不到发布按钮，无法打开弹窗读栏目")
        return None
    btn.click()
    page.wait_for_timeout(3500)

    have = pc.available_categories(page)
    got = pc.read_categories(page)
    print(f"=== {aid} ===")
    print(f"  标题      : {title[:56]!r}")
    print(f"  已选栏目  : {got or '(空 —— 不放入任何栏目)'}")
    print(f"  可选栏目  : {have}")

    # 关掉弹窗，不提交任何东西
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(800)
    except Exception:
        pass
    return got


def main():
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)

    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()

        if args[0] == "--list":
            # 借任意一篇打开弹窗，只为拿「可选栏目」全量清单
            page.goto("https://editor.csdn.net/md/", wait_until="domcontentloaded")
            page.wait_for_timeout(7000)
            pc.dismiss_overlays(page)
            btn, _ = pc.find_visible(page, pc.CFG["selectors"]["publish_button"], 10000, "发布按钮")
            if btn:
                btn.click()
                page.wait_for_timeout(3500)
            print("账号真实专栏名（照抄这个，注意大小写）:")
            for n in pc.available_categories(page):
                print(f"  - {n}")
        else:
            for aid in args:
                probe_one(page, aid)

        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
