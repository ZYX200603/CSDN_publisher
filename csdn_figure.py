#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更精美的正文插图：矢量示意图（Mermaid）+ 可商用的网络配图（Openverse / Wikimedia）。

为什么不用"网上随便找图"当技术插图：
  * 版权：别人的图表/截图不能转载，CSDN 有版权申诉通道，原创标记还会被牵连；
  * 准确性：别人的图讲的是别人的结论，和你的数字对不上；
  * 水印：大量图带公众号水印。
技术插图的正解是**自己生成矢量图**（流程图/时序图/架构图），它比 PIL 手绘精美一个档次，
而且能精确表达你自己的数据。真正适合"网上找"的只是氛围图/照片/logo。

两个子命令：

    # 1) 矢量示意图（Mermaid 语法；首次会自动把 mermaid.js 缓存到 img/vendor/）
    python csdn_figure.py diagram --code flowchart.md --out img/flow.png --title "5 层验收流水线"
    python csdn_figure.py diagram --code "graph TD; A-->B" --out img/x.png --theme dark

    # 2) 可商用配图：先搜（带许可证信息），再下载
    python csdn_figure.py stock --q "server room" --limit 8
    python csdn_figure.py stock --q "server room" --download 1 --out img/stock

图片下载/渲染完成后，用 csdn_image.py upload 传到 CSDN 图床：
    python csdn_image.py upload --files img/flow.png --out img/urls.json
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VENDOR = ROOT / "img" / "vendor"
MERMAID_CDNS = [
    "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js",
    "https://unpkg.com/mermaid@10.9.1/dist/mermaid.min.js",
    "https://registry.npmmirror.com/mermaid/10.9.1/files/dist/mermaid.min.js",
]
MERMAID_LOCAL = VENDOR / "mermaid.min.js"

DIAGRAM_HTML = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; background:{bg}; }}
  #wrap {{ padding:26px 30px 30px; display:inline-block; }}
  #title {{ font:600 21px/1.5 "Microsoft YaHei",sans-serif; color:{fg}; margin:0 0 18px; }}
  #note {{ font:400 14px/1.6 "Microsoft YaHei",sans-serif; color:{muted}; margin:14px 0 0; }}
  .mermaid {{ text-align:center; }}
</style></head>
<body>
<div id="wrap">
  {title_html}
  <div class="mermaid">{code}</div>
  {note_html}
</div>
<script src="{mermaid_src}"></script>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: "{theme}",
      themeVariables: {{ fontFamily: "Microsoft YaHei, sans-serif", fontSize: "15px" }} }});
</script>
</body></html>
"""


def ensure_mermaid(page):
    """把 mermaid.min.js 抓到本地缓存（只做一次），之后渲染不再依赖网络。"""
    if MERMAID_LOCAL.exists() and MERMAID_LOCAL.stat().st_size > 100_000:
        return MERMAID_LOCAL
    VENDOR.mkdir(parents=True, exist_ok=True)
    for url in MERMAID_CDNS:
        try:
            r = page.request.get(url, timeout=45000)
            if r.ok and len(r.body()) > 100_000:
                MERMAID_LOCAL.write_bytes(r.body())
                print(f"  已缓存 {url.rsplit('/', 2)[-2]}/{url.rsplit('/', 1)[-1]} -> {MERMAID_LOCAL}"
                      f"（{len(r.body()) // 1024} KB）", flush=True)
                return MERMAID_LOCAL
            print(f"  {url} 不可用（{r.status}）", flush=True)
        except Exception as e:
            print(f"  {url} 失败: {e.__class__.__name__}", flush=True)
    return None


def cmd_diagram(args):
    import publish_csdn as pc
    from playwright.sync_api import sync_playwright

    code = args.code
    if Path(code).is_file():
        code = Path(code).read_text(encoding="utf-8-sig").strip()
    if not code:
        raise SystemExit("--code 要给 Mermaid 源码或源码文件")

    theme = args.theme
    bg = "#0f1117" if theme == "dark" else "#ffffff"
    fg = "#e6edf3" if theme == "dark" else "#1f2937"
    muted = "#8b949e" if theme == "dark" else "#6b7280"
    tmp_html = ROOT / "img" / "_diagram.html"
    tmp_html.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        m = ensure_mermaid(page)
        if not m:
            print("拿不到 mermaid.js（CDN 都不通），无法渲染矢量图", flush=True)
            b.close()
            return 1

        tmp_html.write_text(DIAGRAM_HTML.format(
            bg=bg, fg=fg, muted=muted, theme=theme, code=code,
            mermaid_src=f"vendor/{m.name}",          # HTML 在 img/ 下，mermaid 在 img/vendor/
            title_html=f'<div id="title">{args.title}</div>' if args.title else "",
            note_html=f'<div id="note">{args.note}</div>' if args.note else "",
        ), encoding="utf-8")

        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text[:200]}")
                if msg.type in ("error", "warning") else None)
        page.goto(tmp_html.as_uri(), wait_until="load")
        try:
            page.wait_for_selector(".mermaid svg", timeout=30000)
        except Exception:
            print("Mermaid 没渲染出 SVG。诊断信息：", flush=True)
            print(f"  mermaid.js 缓存: {m}（{m.stat().st_size // 1024} KB）", flush=True)
            print(f"  页面报错: {errors[:5] or '（无）'}", flush=True)
            print(f"  window.mermaid 是否存在: "
                  f"{page.evaluate('() => typeof window.mermaid')}", flush=True)
            page.screenshot(path=str(ROOT / "img" / "_diagram-failed.png"), full_page=True)
            b.close()
            return 1
        page.wait_for_timeout(600)
        el = page.locator("#wrap")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        el.screenshot(path=args.out, scale="device")
        size = page.evaluate("() => [document.querySelector('#wrap').offsetWidth,"
                             " document.querySelector('#wrap').offsetHeight]")
        b.close()

    print(f"已生成示意图: {args.out}  {size[0]}x{size[1]}（2x 缩放，矢量为源、位图为输出）")
    return 0


# ---------------------------------------------------------------- 可商用配图

def search_openverse(page, q, limit):
    """Openverse（CC 搜索聚合）：返回图片 URL + 许可信息。"""
    url = (f"https://api.openverse.org/v1/images/?q={q}&page_size={limit}"
           f"&license_type=all-cc,commercial&mature=false")
    r = page.request.get(url, timeout=30000)
    if not r.ok:
        return []
    out = []
    for it in (r.json().get("results") or []):
        out.append({
            "source": "openverse",
            "title": (it.get("title") or "")[:60],
            "creator": it.get("creator") or "unknown",
            "license": (it.get("license") or "").upper() + (
                " " + (it.get("license_version") or "")).strip(),
            "license_url": it.get("license_url") or "",
            "page": it.get("foreign_landing_url") or "",
            "file": it.get("url") or "",
            "w": it.get("width"), "h": it.get("height"),
        })
    return out


def search_commons(page, q, limit):
    """Wikimedia Commons：文件页 + 许可（LicenseShortName）。"""
    api = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search"
           f"&gsrsearch=filetype:bitmap%20{q}&gsrlimit={limit}&prop=imageinfo"
           "&iiprop=url|size|extmetadata&iiurlwidth=1600")
    r = page.request.get(api, timeout=30000)
    if not r.ok:
        return []
    pages = ((r.json().get("query") or {}).get("pages") or {})
    out = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        out.append({
            "source": "commons",
            "title": (p.get("title") or "").replace("File:", "")[:60],
            "creator": (meta.get("Artist", {}).get("value") or "unknown")[:60],
            "license": (meta.get("LicenseShortName", {}).get("value") or "?"),
            "license_url": meta.get("LicenseUrl", {}).get("value") or "",
            "page": ii.get("descriptionurl") or "",
            "file": ii.get("thumburl") or ii.get("url") or "",
            "w": ii.get("thumbwidth") or ii.get("width"),
            "h": ii.get("thumbheight") or ii.get("height"),
        })
    return out


def cmd_stock(args):
    """可商用配图：优先搜索（Openverse / Wikimedia），搜不到就只下已知直链。

    2026-10-08 实测（本机网络）：
      ✗ api.openverse.org / commons.wikimedia.org / upload.wikimedia.org —— 超时或连接重置
      ✓ images.unsplash.com / images.pexels.com / picsum.photos —— 直链 200
    所以在这台机器上"按关键词搜图"不可用，只能：① 你给直链（--url）；
    ② 用可直连的图库直链；③ 换网络/代理后重试搜索。
    技术插图请优先用 `diagram`（矢量示意图），不要用照片。
    """
    import publish_csdn as pc
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        found = []

        if args.url:
            found = [{"source": "direct", "title": Path(u).name or u[:40], "creator": "见来源页",
                      "license": args.license, "license_url": "", "page": u, "file": u,
                      "w": "-", "h": "-"} for u in args.url]
            print(f"使用 {len(found)} 条直链（记得在图注里写明来源与许可）", flush=True)
        else:
            for fn, name in ((search_openverse, "openverse"), (search_commons, "commons")):
                try:
                    got = fn(page, args.q, args.limit)
                    print(f"=== {name}: {len(got)} 张 ===", flush=True)
                    for i, x in enumerate(got, 1):
                        print(f"  [{i}] {x['w']}x{x['h']} {x['license']:<16} "
                              f"{x['title'][:34]:36} by {x['creator'][:18]}", flush=True)
                    found += got
                except Exception as e:
                    print(f"=== {name} 搜索失败: {e.__class__.__name__} ===", flush=True)
            if not found:
                print("\n两个图库都连不上（本机网络实测：图库 API 被拦，图片 CDN 直链可通）。可选：", flush=True)
                print("  1) 用 --url 直接给可商用图片直链（Unsplash / Pexels 的 images.* 域名可直连）；", flush=True)
                print("  2) 技术插图改用 `diagram`（Mermaid 矢量图，无需联网、更精美）；", flush=True)
                print("  3) 换网络/代理后重试搜索。", flush=True)

        if args.download and found:
            outdir = Path(args.out)
            outdir.mkdir(parents=True, exist_ok=True)
            picks = found[: args.download]
            credits = []
            for i, x in enumerate(picks, 1):
                try:
                    r = page.request.get(x["file"], timeout=60000)
                    if not r.ok:
                        print(f"  下载失败 {r.status}: {x['file'][:80]}", flush=True)
                        continue
                    ext = Path(x["file"].split("?")[0]).suffix or ".jpg"
                    f = outdir / f"{args.slug}-{i}{ext}"
                    f.write_bytes(r.body())
                    x["saved"] = str(f)
                    credits.append(x)
                    print(f"  已保存 {f}（{len(r.body()) // 1024} KB, {x['license']}, "
                          f"by {x['creator']}）", flush=True)
                except Exception as e:
                    print(f"  下载异常: {e.__class__.__name__}", flush=True)
            if credits:
                meta = outdir / "credits.json"
                meta.write_text(json.dumps(credits, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"\n署名信息已写入 {meta}（发文时把作者/许可写进图注，CC BY 系列必须署名）")
                print("发文用这张图的话，图注建议：")
                for x in credits:
                    print(f"  图：{x['title']} / {x['creator']} / {x['license']} / {x['page']}")
        b.close()
    return 0


def cmd_html(args):
    """渲染任意本地 HTML 成 PNG（用来在设计横幅/自定义插图时"先看图再上线"）。"""
    import publish_csdn as pc
    from playwright.sync_api import sync_playwright

    src = Path(args.file).resolve()
    if not src.is_file():
        raise SystemExit(f"HTML 文件不存在: {src}")
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        if args.width:
            page.set_viewport_size({"width": args.width, "height": 800})
        page.goto(src.as_uri(), wait_until="load")
        page.wait_for_timeout(500)
        sel = args.selector
        el = page.locator(sel).first
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        el.screenshot(path=args.out)
        box = el.bounding_box()
        b.close()
    print(f"已生成: {args.out}  {int(box['width'])}x{int(box['height'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="更精美的插图：矢量示意图 + 可商用配图")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("diagram", help="Mermaid 矢量示意图 → PNG")
    d.add_argument("--code", required=True, help="Mermaid 源码，或含源码的文件")
    d.add_argument("--out", required=True)
    d.add_argument("--title", default=None)
    d.add_argument("--note", default=None, help="图下方的小字说明")
    d.add_argument("--theme", default="default", choices=["default", "dark", "neutral", "forest"])

    h = sub.add_parser("html", help="把本地 HTML 片段渲染成 PNG（设计稿预览用）")
    h.add_argument("--file", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--selector", default="body", help="只截图这个选择器（默认整个 body）")
    h.add_argument("--width", type=int, default=900, help="视口宽度")

    s = sub.add_parser("stock", help="获取可商用网络配图（搜索需外网；也支持直接给直链）")
    s.add_argument("--q", default="", help="英文关键词搜索（本机网络下通常连不上图库 API）")
    s.add_argument("--url", nargs="*", default=[], help="直接给可商用图片直链（绕过搜索）")
    s.add_argument("--license", default="见来源页（请自行确认可商用）", help="--url 模式下记录的许可")
    s.add_argument("--limit", type=int, default=8)
    s.add_argument("--download", type=int, default=0, help="下载前 N 张")
    s.add_argument("--out", default="img/stock")
    s.add_argument("--slug", default="stock")

    args = ap.parse_args()
    return {"diagram": cmd_diagram, "stock": cmd_stock, "html": cmd_html}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
