#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验草稿里正文插图是否真的存活（打开指定 articleId 的草稿，数图床 URL）。

为什么必须单独验一次：markdown 导入有可能把外链图片过滤掉、或改成相对路径。
"我写进 md 了"不等于"CSDN 存下来了"。

    python _check_draft_images.py 1672xxxxx [更多 id]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CHECK_JS = r"""
() => {
  const box = document.querySelector('pre.editor__inner') || document.querySelector('.editor__inner');
  const html = box ? box.innerHTML : '';
  const text = box ? (box.innerText || '') : '';
  const imgs = [...document.querySelectorAll('.cl-preview-section img, .preview__inner-2 img')]
      .map(i => i.getAttribute('src') || '');
  return {
    title: (document.querySelector('input.article-bar__title') || {}).value
            || (document.querySelector('.article-bar__title-display') || {}).innerText || '',
    mdUrls: [...new Set((html + ' ' + text).match(/https:\/\/i-blog\.csdnimg\.cn\/[^\s"'()]+/g) || [])],
    rendered: imgs.filter(Boolean),
    textLen: text.length,
  };
}
"""


def main():
    ids = sys.argv[1:]
    if not ids:
        raise SystemExit("用法: python _check_draft_images.py <articleId> [articleId...]")
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        for aid in ids:
            page.goto(f"https://editor.csdn.net/md?articleId={aid}", wait_until="domcontentloaded")
            page.wait_for_timeout(8000)
            pc.dismiss_overlays(page)
            d = page.evaluate(CHECK_JS)
            print(f"=== 草稿 {aid} ===")
            print(f"  标题: {d['title'][:60]!r}")
            print(f"  正文长度: {d['textLen']}")
            print(f"  markdown 里的图床 URL: {len(d['mdUrls'])} 个")
            for u in d["mdUrls"]:
                print(f"     {u}")
            print(f"  预览区已渲染的 <img>: {len(d['rendered'])} 个")
            page.screenshot(path=str(pc.OUTPUT_DIR / f"draft-{aid}-images.png"))
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
