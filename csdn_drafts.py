#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 CSDN 内容管理页的「草稿箱(N)」计数 —— 存草稿模式的验收依据。

用法: python csdn_drafts.py
只打开内容管理页读文本，不发布、不修改。列表页里带「草稿」的短文本会全部打印出来，
便于页面结构变化时重新定位选择器。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

URL = "https://mp.csdn.net/mp_blog/manage/article"

PROBE_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('body *').forEach(el => {
    if (el.children.length) return;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length <= 24 && /草稿/.test(t)) {
      out.push({text: t, tag: el.tagName, cls: String(el.className || '').slice(0, 60)});
    }
  });
  return out;
}
"""


def main():
    with sync_playwright() as p:
        b = pc.Browser(p)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        print(f"登录态: {logged}")
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        rows = page.evaluate(PROBE_JS)
        if not rows:
            print("没找到带「草稿」的文本，页面结构可能变了，用 csdn.bat inspect 导出看看")
        for r in rows:
            print(f"  {r['text']!r:32} <{r['tag']} class={r['cls']!r}>")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
