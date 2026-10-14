#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正文插图：把表格 / 终端输出渲染成 PNG，再上传到 CSDN 换取图床 URL。

为什么需要它：热榜爆款都配 4~45 张图，而我们之前的文章是 0 图 —— 这是结构性劣势。
文本表格在网页里是好看的，但"实测数据的对比表"做成图有三个额外好处：
  1) 被别的平台转载时不会被压成乱码；
  2) 移动端不用横向滚动；
  3) 文章列表/推荐位更容易被当成"图文卡片"。

两个子命令：

    # 1) 渲染（纯本地，不需要浏览器）
    python csdn_image.py render --table exp/numbers.md --out img/t1.png --title "三种检索器的召回率"
    python csdn_image.py render --text exp/console.txt --out img/t2.png --title "验收器真实输出"
    python csdn_image.py render --table exp/numbers.md --out img/t1.png --theme dark

    # 2) 上传到 CSDN（复用已登录浏览器，返回图床 URL）
    python csdn_image.py upload --files img/t1.png img/t2.png --out img/urls.json
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FONT_CJK = r"C:\Windows\Fonts\msyh.ttc"
FONT_CJK_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_MONO = r"C:\Windows\Fonts\consola.ttf"
FONT_MONO_BOLD = r"C:\Windows\Fonts\consolab.ttf"

THEMES = {
    # name: (背景, 表头底色, 表头文字, 正文文字, 强调色, 斑马纹)
    "light": ("#ffffff", "#1f2d3d", "#ffffff", "#1f2937", "#c2410c", "#f6f8fa"),
    "dark": ("#0f1117", "#1f6feb", "#ffffff", "#e6edf3", "#f2cc60", "#161b22"),
}


def _font(path, size, fallback=FONT_CJK):
    for p in (path, fallback):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def parse_md_table(text):
    """从 markdown 里抽出第一个表格，返回 (rows, aligns)。`**x**` 表示要高亮。"""
    rows, aligns = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if rows:                     # 表格结束
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            aligns = ["c" if c.startswith(":") and c.endswith(":") else
                      ("r" if c.endswith(":") else "l") for c in cells]
            continue
        rows.append(cells)
    return rows, (aligns or ["l"] * (len(rows[0]) if rows else 0))


def render_table(rows, out, title=None, footnote=None, theme="light", font_size=26,
                 aligns=None):
    bg, head_bg, head_fg, fg, accent, zebra = THEMES[theme]
    ncol = max(len(r) for r in rows)
    aligns = list(aligns or []) + ["l"] * max(0, ncol - len(aligns or []))
    f = _font(FONT_CJK, font_size)
    fb = _font(FONT_CJK_BOLD, font_size)
    ft = _font(FONT_CJK_BOLD, font_size + 8)
    fn = _font(FONT_CJK, max(font_size - 8, 14))

    probe = Image.new("RGB", (10, 10))
    pd = ImageDraw.Draw(probe)
    pad_x, pad_y = 26, 18
    widths = []
    for c in range(ncol):
        w = 0
        for r in rows:
            cell = r[c] if c < len(r) else ""
            w = max(w, pd.textlength(cell.replace("**", ""), font=fb if "**" in cell else f))
        widths.append(int(w) + pad_x * 2)

    title_h = (font_size + 34) if title else 0
    W = max(sum(widths) + 40, 760)
    row_h = int(font_size * 2.0)
    H = title_h + row_h * len(rows) + (font_size + 26 if footnote else 0) + 30

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    y = 15
    if title:
        d.text((24, y), title, font=ft, fill=fg)
        y += title_h

    # 表头
    x = 20
    d.rectangle([20, y, 20 + sum(widths), y + row_h], fill=head_bg)
    for c in range(ncol):
        cell = (rows[0][c] if c < len(rows[0]) else "").replace("**", "")
        d.text((x + pad_x, y + row_h // 2 - font_size // 2), cell, font=fb, fill=head_fg)
        x += widths[c]
    y += row_h

    for i, r in enumerate(rows[1:], 1):
        if i % 2 == 0:
            d.rectangle([20, y, 20 + sum(widths), y + row_h], fill=zebra)
        x = 20
        for c in range(ncol):
            cell = r[c] if c < len(r) else ""
            hot = "**" in cell
            text = cell.replace("**", "")
            fnt = fb if hot else f
            al = (["l"] * ncol + [])[c] if c >= len(aligns) else aligns[c]
            tw = pd.textlength(text, font=fnt)
            tx = x + pad_x if al == "l" else (x + widths[c] - pad_x - tw if al == "r"
                                              else x + (widths[c] - tw) / 2)
            d.text((tx, y + row_h // 2 - font_size // 2), text, font=fnt,
                   fill=accent if hot else fg)
            x += widths[c]
        y += row_h

    d.rectangle([20, title_h + 15, 20 + sum(widths), y], outline="#d0d7de" if theme == "light" else "#30363d")
    if footnote:
        d.text((24, y + 10), footnote, font=fn, fill="#6b7280")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out, (W, H)


def draw_mixed(d, x, y, text, f_ascii, f_cjk, fill):
    """逐字符画：ASCII 用等宽字体、CJK 用中文字体且占两格 —— 终端输出才能对齐。"""
    cell = f_ascii.getlength("0")
    for ch in text:
        if ord(ch) > 0x2E80:                     # 中文/全角
            d.text((x, y), ch, font=f_cjk, fill=fill)
            x += cell * 2
        else:
            d.text((x, y), ch, font=f_ascii, fill=fill)
            x += f_ascii.getlength(ch)
    return x


def render_console(text, out, title=None, font_size=20):
    """把终端输出渲染成"终端卡片"（深底浅字，等宽对齐）。"""
    lines = text.rstrip("\n").splitlines() or [""]
    f_ascii = _font(FONT_MONO, font_size, FONT_CJK)
    f_cjk = _font(FONT_CJK, font_size)
    ft = _font(FONT_CJK_BOLD, font_size + 6)
    cell = f_ascii.getlength("0")
    pad, bar_h = 20, 34
    title_h = (font_size + 26) if title else 0

    # 行宽：ASCII 一格，CJK 两格
    max_cells = max(sum(2 if ord(c) > 0x2E80 else 1 for c in ln) for ln in lines)
    W = int(max(760, max_cells * cell + pad * 2 + 30))
    line_h = int(font_size * 1.55)
    H = title_h + bar_h + line_h * len(lines) + pad * 2

    img = Image.new("RGB", (W, H), "#0d1117")
    d = ImageDraw.Draw(img)
    y = 0
    if title:
        d.rectangle([0, 0, W, title_h], fill="#161b22")
        d.text((pad, 8), title, font=ft, fill="#e6edf3")
        y = title_h
    d.rectangle([0, y, W, y + bar_h], fill="#161b22")
    for i, col in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse([pad + i * 22, y + 12, pad + i * 22 + 11, y + 23], fill=col)
    y += bar_h
    for ln in lines:
        color = "#7ee787" if ln.strip().startswith(("[PASS]", "+", "✅")) else (
            "#ffa198" if ln.strip().startswith(("[FAIL]", "-", "❌")) else "#c9d1d9")
        draw_mixed(d, pad, y, ln, f_ascii, f_cjk, color)
        y += line_h
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out, (W, H)


def cmd_render(args):
    if args.table:
        text = Path(args.table).read_text(encoding="utf-8-sig")
        rows, aligns = parse_md_table(text)
        if not rows:
            raise SystemExit(f"{args.table} 里没找到 markdown 表格")
        out, size = render_table(rows, args.out, args.title, args.footnote, args.theme)
    elif args.text:
        text = Path(args.text).read_text(encoding="utf-8-sig")
        if args.lines:                       # 只取前 N 行
            text = "\n".join(text.splitlines()[: args.lines])
        out, size = render_console(text, args.out, args.title)
    else:
        raise SystemExit("要么 --table，要么 --text")
    print(f"已生成: {out}  {size[0]}x{size[1]}")
    return 0


# ---------------------------------------------------------------- 上传

UPLOAD_JS_IMAGES = r"""
() => {
  const box = document.querySelector('pre.editor__inner') || document.querySelector('.editor__inner');
  const html = box ? box.innerHTML : '';
  const t = box ? (box.innerText || '') : '';
  return {urls: [...new Set((html + ' ' + t).match(/https:\/\/[^\s"'()]+?\.(?:png|jpg|jpeg|gif|webp)/g) || [])]};
}
"""


def cmd_upload(args):
    """把 PNG 传到 CSDN 图床，拿回可外链的 URL。

    实测（2026-10）：编辑器里那个隐藏的 `input.cfw-file-input` 只负责**上传**，
    不会把 markdown 插进正文（光标上下文不在它手里）。所以这里不依赖"插入"，
    而是抓华为云 OBS 回调的 JSON —— 里面的 data.imageUrl 就是最终图床地址：

        POST https://csdn-img-blog.obs.cn-north-4.myhuaweicloud.com/
        -> {"code":200,"data":{"imageUrl":"https://i-blog.csdnimg.cn/direct/xxx.png", ...}}

    拿到 URL 后直接在 markdown 里写 ![](url) 即可（CSDN 的图床对外可访问，已验证）。
    """
    import time
    import urllib.request

    import publish_csdn as pc
    from playwright.sync_api import sync_playwright

    files = [Path(f).resolve() for f in args.files]
    for f in files:
        if not f.is_file():
            raise SystemExit(f"文件不存在: {f}")

    got = []
    mapping = {}
    with sync_playwright() as pw:
        b = pc.Browser(pw)
        b.launch()
        page = b.page()
        logged, _ = b.is_logged_in()
        if not logged:
            print("未登录，先跑 .\\csdn.bat login")
            b.close()
            return 1

        def on_resp(r):
            if r.request.method == "POST" and "myhuaweicloud.com" in r.url:
                try:
                    j = json.loads(r.text())
                    u = (j.get("data") or {}).get("imageUrl")
                    if u:
                        got.append(u)
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto(pc.CFG["urls"]["editor"], wait_until="domcontentloaded")
        page.wait_for_timeout(6000)
        pc.dismiss_overlays(page)

        for f in files:
            ok_all = False
            for attempt in (1, 2, 3):
                inp = page.locator("input.cfw-file-input").first
                if inp.count() == 0:
                    print("找不到 input.cfw-file-input，编辑器结构可能变了", flush=True)
                    break
                n0 = len(got)
                inp.set_input_files(str(f))
                print(f"  提交上传: {f.name}（{f.stat().st_size // 1024} KB，第 {attempt} 次）", flush=True)
                deadline = time.time() + args.wait
                while time.time() < deadline and len(got) <= n0:
                    page.wait_for_timeout(500)
                if len(got) <= n0:
                    print("    ! 没等到 imageUrl，重试", flush=True)
                    continue
                url = got[n0]                      # 只认这一张新增的
                ok, info = _check_url(url, retries=3, delay=4)
                print(f"    -> {url}  ({info})", flush=True)
                if ok:
                    mapping[str(f)] = url
                    ok_all = True
                    break
                print("    ! 该 URL 还取不到（可能被审核拦下或 CDN 未生效），重试", flush=True)
            if not ok_all:
                print(f"  [FAIL] {f.name} 三次都没能拿到可访问的 URL", flush=True)
        b.close()

    print(f"\n提交 {len(files)} 张，成功 {len(mapping)} 张", flush=True)
    for f in files:
        u = mapping.get(str(f))
        print(f"  [{'OK ' if u else 'BAD'}] {f.name} -> {u or '(无)'}", flush=True)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"映射已写入 {args.out}", flush=True)
    return 0 if len(mapping) == len(files) else 2


def _check_url(url, retries=1, delay=3):
    """验证图床 URL 真能匿名访问（内容类型必须是图片）。CDN 有延迟，所以带重试。"""
    import time
    import urllib.request
    last = ""
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                ctype = r.headers.get("Content-Type", "")
                n = len(r.read())
                if "image" in ctype:
                    return True, f"{r.status} {ctype} {n // 1024}KB"
                last = f"{r.status} {ctype}"
        except Exception as e:
            last = e.__class__.__name__
        if i < retries - 1:
            time.sleep(delay)
    return False, last


def main():
    ap = argparse.ArgumentParser(description="正文插图：渲染 + 上传")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="把表格/终端输出渲染成 PNG")
    r.add_argument("--table", help="含 markdown 表格的文件")
    r.add_argument("--text", help="终端输出文本文件")
    r.add_argument("--out", required=True, help="输出 PNG")
    r.add_argument("--title", default=None, help="图上方的标题")
    r.add_argument("--footnote", default=None, help="图下方的注释")
    r.add_argument("--theme", default="light", choices=sorted(THEMES))
    r.add_argument("--lines", type=int, default=0, help="--text 模式只取前 N 行")

    u = sub.add_parser("upload", help="上传 PNG 到 CSDN 图床，返回 URL")
    u.add_argument("--files", nargs="+", required=True)
    u.add_argument("--out", default="", help="URL 映射写到哪个 json")
    u.add_argument("--wait", type=int, default=10, help="每张图上传后等待秒数")

    args = ap.parse_args()
    return {"render": cmd_render, "upload": cmd_upload}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
