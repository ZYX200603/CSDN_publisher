#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探针：读出草稿预览区里横幅的真实 DOM 与计算样式。

要回答的问题：CSDN 到底保留了候选横幅的哪些样式？
（font-size / color / font-weight / border-top / text-align / 链接 href / emoji）
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

JS = r"""
() => {
  const pv = document.querySelector('#content_views')
          || document.querySelector('.markdown_views')
          || document.querySelector('.preview__inner-2')
          || document.querySelector('.cl-preview-section');
  if (!pv) return {found: false, tried: ['#content_views', '.markdown_views', '.preview__inner-2']};
  const cs = el => getComputedStyle(el);
  const nodes = [...pv.children].slice(0, 8).map(el => ({
    tag: el.tagName, cls: String(el.className).slice(0, 30),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 40),
    html: el.outerHTML.replace(/\s+/g, ' ').slice(0, 220),
    styleAttr: el.getAttribute('style'),
    textAlign: cs(el).textAlign, fontSize: cs(el).fontSize,
    borderTop: cs(el).borderTopStyle + ' ' + cs(el).borderTopWidth,
    background: cs(el).backgroundColor, paddingTop: cs(el).paddingTop,
    marginTop: cs(el).marginTop,
  }));
  const anchors = [...pv.querySelectorAll('a')].slice(0, 8).map(a => ({
    text: (a.innerText || '').slice(0, 22),
    href: (a.getAttribute('href') || '').slice(0, 90),
    color: cs(a).color, deco: cs(a).textDecorationLine,
  }));
  const spans = [...pv.querySelectorAll('span')].slice(0, 6).map(s => ({
    text: (s.innerText || '').slice(0, 26),
    styleAttr: s.getAttribute('style'),
    fontSize: cs(s).fontSize, weight: cs(s).fontWeight, color: cs(s).color,
  }));
  return {found: true, previewCls: String(pv.className).slice(0, 60), nodes, anchors, spans,
          toc: !!document.querySelector('.toc, #toc, .markdown-toc, [class*=toc]')};
}
"""


def main():
    aid = sys.argv[1] if len(sys.argv) > 1 else ""
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        page.goto(f"https://editor.csdn.net/md?articleId={aid}", wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        pc.dismiss_overlays(page)
        d = page.evaluate(JS)
        if not d.get("found"):
            print("没找到预览区:", d)
            page.screenshot(path=str(pc.OUTPUT_DIR / "banner-probe.png"))
            b.close()
            return 1
        print(f"预览区 class: {d['previewCls']}   目录节点存在: {d['toc']}")
        print("\n=== 预览区前 8 个块 ===")
        for i, n in enumerate(d["nodes"], 1):
            print(f"[{i}] <{n['tag']}> textAlign={n['textAlign']} fontSize={n['fontSize']}")
            print(f"     border-top={n['borderTop']} background={n['background']} "
                  f"paddingTop={n['paddingTop']} marginTop={n['marginTop']}")
            print(f"     style 属性: {n['styleAttr']}")
            print(f"     文本: {n['text']!r}")
            print(f"     HTML: {n['html'][:180]}")
        print("\n=== 链接（href 有没有活下来）===")
        for a in d["anchors"]:
            print(f"  color={a['color']:<22} deco={a['deco']:<12} {a['text'][:20]:22} {a['href'][:70]}")
        print("\n=== span（字号/加粗/颜色是否保留）===")
        for s in d["spans"]:
            print(f"  fontSize={s['fontSize']:<8} weight={s['weight']:<5} color={s['color']:<22} "
                  f"{s['text'][:22]!r} style={s['styleAttr']}")
        page.screenshot(path=str(pc.OUTPUT_DIR / "banner-probe.png"), full_page=False)
        (pc.OUTPUT_DIR / "banner-probe.json").write_text(json.dumps(d, ensure_ascii=False, indent=2),
                                                        encoding="utf-8")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
