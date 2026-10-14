#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成文章封面图（渐变底 + 标题排版）。

三篇不同主题的文章用不同 --theme，封面观感就不会重复。

用法:
    python make_cover.py -t "一文搞懂 Agent 记忆机制" -s "从短期记忆到向量检索" -o posts/c1.png
    python make_cover.py -t "标题" --theme purple -o posts/c2.png
    python make_cover.py --list-themes

产出: 1200x800 PNG（CSDN 封面的常见比例，上传后可在裁剪窗里再调）
"""

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 800

THEMES = {
    "blue":   ((14, 36, 82), (32, 92, 186), (96, 176, 255)),
    "purple": ((38, 18, 76), (96, 44, 168), (186, 132, 255)),
    "teal":   ((10, 46, 52), (16, 104, 108), (86, 214, 196)),
    "orange": ((74, 26, 14), (186, 76, 26), (255, 168, 96)),
    "dark":   ((16, 18, 26), (44, 50, 68), (140, 160, 200)),
    "rose":   ((70, 16, 44), (170, 40, 96), (255, 150, 180)),
}

FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"


def load_font(path, size):
    for p in (path, FONT_REG):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def wrap_cjk(text, font, max_w, draw):
    """按像素宽度折行，中英文混排都能处理。"""
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def gradient(top, bottom):
    img = Image.new("RGB", (W, H), top)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        d.line([(0, y), (W, y)], fill=(
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        ))
    return img


def main():
    ap = argparse.ArgumentParser(description="生成文章封面图")
    ap.add_argument("-t", "--title", help="封面主标题")
    ap.add_argument("-s", "--subtitle", default="", help="副标题")
    ap.add_argument("-o", "--out", help="输出 png 路径")
    ap.add_argument("--theme", default="blue", choices=sorted(THEMES), help="配色主题")
    ap.add_argument("--footer", default="", help="右下角小字，比如你的博客名")
    ap.add_argument("--list-themes", action="store_true", help="列出所有主题")
    args = ap.parse_args()

    if args.list_themes:
        print("可用主题:", ", ".join(sorted(THEMES)))
        return 0
    if not args.title or not args.out:
        ap.error("需要 -t/--title 和 -o/--out")

    top, bottom, accent = THEMES[args.theme]
    img = gradient(top, bottom)
    d = ImageDraw.Draw(img, "RGBA")

    # 装饰性光斑
    for cx, cy, r, a in [(980, 130, 320, 26), (1090, 640, 260, 20), (150, 720, 220, 16)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (a,))

    # 左侧强调竖条
    d.rounded_rectangle([70, 190, 82, 190 + 300], radius=6, fill=accent)

    pad_x = 130
    max_w = W - pad_x - 90

    f_title = load_font(FONT_BOLD, 62)
    f_sub = load_font(FONT_REG, 30)
    f_foot = load_font(FONT_REG, 22)

    lines = wrap_cjk(args.title, f_title, max_w, d)
    if len(lines) > 4:
        lines = lines[:4]

    line_h = 84
    total_h = len(lines) * line_h
    sub_h = 60 if args.subtitle else 0
    y = (H - total_h - sub_h) // 2 - 20

    for ln in lines:
        d.text((pad_x, y), ln, font=f_title, fill=(255, 255, 255))
        y += line_h

    if args.subtitle:
        y += 10
        for ln in wrap_cjk(args.subtitle, f_sub, max_w, d)[:2]:
            d.text((pad_x, y), ln, font=f_sub, fill=(226, 232, 240))
            y += 44

    if args.footer:
        fw = d.textlength(args.footer, font=f_foot)
        d.text((W - 70 - fw, H - 70), args.footer, font=f_foot, fill=(200, 210, 225))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    print(f"已生成封面: {out}  ({W}x{H}, 主题={args.theme}, 标题行数={len(lines)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
