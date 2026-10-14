#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读：抓指定专栏页（category_<id>）里实际有哪些文章。

为什么必须看这个页：前面我用「发布弹窗里的勾选状态」判断栏目，但那只能证明
"弹窗里勾上了"，证明不了"CSDN 存下来了"。专栏页是唯一由 CSDN 自己渲染、
不会骗人的真相来源 —— 文章进了栏目才会出现在这里。

用法：
    python _probe_column.py <专栏ID>                # 抓指定专栏，打印全部文章标题
    python _probe_column.py <专栏ID> <账号ID>       # 账号 ID 也可用环境变量 CSDN_ACCOUNT
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ACCOUNT = os.environ.get("CSDN_ACCOUNT", "")   # 你的账号 ID（URL 里那一段）
DEFAULT_COL = ""      # 填你自己账号的专栏 ID（专栏页 URL 里 category_<id> 那一段）

# 从专栏页里抽文章条目：标题 + 链接
LIST_JS = r"""
() => {
  const out = [];
  // 专栏页文章列表：常见结构是 .column_article_list li / .blog-list-box
  const sels = ['.column_article_list li', '.blog-list-box', '.article-item-box',
                '.column-list-item', 'li.blog-column-list-item'];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      const a = el.querySelector('a[href*="/article/details/"]');
      if (!a) continue;
      const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
      out.push({ title: t.slice(0, 80), href: a.href });
    }
    if (out.length) break;
  }
  return out;
}
"""


def grab(page, url, wait_ms=6000):
    """打开 url 并抓列表；页面被关时抛 TargetClosedError 由上层重试。"""
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(wait_ms)
    try:
        pc.dismiss_overlays(page)
    except Exception:
        pass
    return page.evaluate(LIST_JS) or page.evaluate(
        """() => [...document.querySelectorAll('a[href*="/article/details/"]')]
            .map(a => ({title: (a.innerText||'').replace(/\\s+/g,' ').trim().slice(0,80),
                        href: a.href}))
            .filter(x => x.title)""")


def main():
    col = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_COL
    acct = sys.argv[2] if len(sys.argv) > 2 else ACCOUNT
    if not col or not acct:
        print("用法: python _probe_column.py <专栏ID> [账号ID]")
        print("  专栏ID = 专栏页 URL 里 category_<id> 那一段；账号ID 也可设环境变量 CSDN_ACCOUNT")
        return 2
    url = f"https://blog.csdn.net/{acct}/category_{col}.html"
    print(f"抓取: {url}\n")

    items, head = None, ""
    for attempt in range(1, 5):
        with sync_playwright() as pw:
            b = pc.Browser(pw)
            try:
                b.launch()
                page = b.page()
                items = grab(page, url)
                head = page.evaluate("() => (document.body.innerText || '').slice(0, 600)")
            except Exception as e:
                print(f"  第 {attempt} 次尝试失败: {e.__class__.__name__}，重试…")
                items = None
            finally:
                try:
                    b.close()
                except Exception:
                    pass
        if items:
            break
        import time as _t
        _t.sleep(8)

    if items is None:
        print("❌ 4 次都没抓到，页面反复被关（多半是 profile 争抢）")
        return 1

    # 去重（按 articleId）
    seen, uniq = set(), []
    for it in items:
        m = re.search(r"details/(\d+)", it["href"] or "")
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        uniq.append((m.group(1), it["title"]))

    print(f"页面头部片段: {head.splitlines()[:6]}\n")
    print(f"抓到 {len(uniq)} 篇（去重后）:\n")
    for aid, t in uniq:
        print(f"  {aid}  {t}")

    print(f"\n合计 {len(uniq)} 篇在专栏 {col} 里。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
