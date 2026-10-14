#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成"有设计感"的文章封面（对比 make_cover.py 的纯渐变 + 一行标题）。

一张封面由这些层叠出来：
  1) 多段对角渐变底（低分辨率逐像素算完再放大，比画 800 条线平滑）
  2) 两个径向光晕 + 四周暗角，做出景深
  3) 右上角细点阵 + 一道斜向高光
  4) 右侧图标面板：按主题画 lock / versions / cache-db 三种图标
  5) 顶部 kicker 胶囊、主标题（自动折行 + 自动缩字号）、强调下划线、副标题、
     底部标签胶囊、右下角署名

全程 2 倍超采样，最后 LANCZOS 降采样，曲线和字边都干净。

用法:
    python make_cover_pro.py            # 生成下面 COVERS 里配置的三张
"""

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FINAL_W, FINAL_H = 1200, 800
SS = 2                                   # 超采样倍数
W, H = FINAL_W * SS, FINAL_H * SS

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"

# 输出目录：默认放在项目内的 output/covers/。
# 原先硬编码成 output/job/job.db-redis/covers —— 那个任务名早已不存在，
# 每次跑都会凭空造出一个空 job 目录，且与当前任何任务都对不上号。
# 要改位置就设环境变量 CSDN_COVER_OUT。
OUT_DIR = Path(os.environ.get(
    "CSDN_COVER_OUT",
    Path(__file__).resolve().parent / "output" / "covers"))


def hx(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def font(path, size):
    try:
        return ImageFont.truetype(path, int(round(size * SS)))
    except Exception:
        return ImageFont.truetype(FONT_REG, int(round(size * SS)))


def multi_gradient(stops, angle_deg=28, sw=120, sh=80):
    """stops: [(pos, "#rrggbb")]，按 pos 升序；对角方向做多段插值。"""
    stops = [(p, hx(c)) for p, c in stops]
    ang = math.radians(angle_deg)
    dx, dy = math.cos(ang), math.sin(ang)
    corners = [(0, 0), (sw - 1, 0), (0, sh - 1), (sw - 1, sh - 1)]
    proj = [(x / (sw - 1)) * dx + (y / (sh - 1)) * dy for x, y in corners]
    lo, hi = min(proj), max(proj)

    small = Image.new("RGB", (sw, sh))
    px = small.load()
    for y in range(sh):
        for x in range(sw):
            t = (((x / (sw - 1)) * dx + (y / (sh - 1)) * dy) - lo) / (hi - lo)
            c = stops[0][1]
            for i in range(len(stops) - 1):
                p0, c0 = stops[i]
                p1, c1 = stops[i + 1]
                if t <= p1:
                    u = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
                    c = lerp(c0, c1, min(max(u, 0.0), 1.0))
                    break
            px[x, y] = c
    return small.resize((W, H), Image.BICUBIC).convert("RGBA")


def radial_mask(diam, falloff=2.4, n=140):
    """中心亮、边缘归零的圆形 alpha 蒙版（算小图再放大，够平滑）。"""
    m = Image.new("L", (n, n))
    px = m.load()
    c = (n - 1) / 2
    for y in range(n):
        for x in range(n):
            r = min(1.0, math.hypot((x - c) / c, (y - c) / c))
            px[x, y] = int((1 - r) ** falloff * 255)
    return m.resize((diam, diam), Image.LANCZOS)


def add_glow(base, cx, cy, radius, color, alpha):
    diam = int(radius * 2 * SS)
    mask = radial_mask(diam).point(lambda v: int(v * alpha / 255))
    g = Image.new("RGBA", (diam, diam), hx(color) + (0,))
    g.putalpha(mask)
    base.alpha_composite(g, (int(cx * SS - diam / 2), int(cy * SS - diam / 2)))


def add_vignette(base, strength=120):
    nw, nh = 200, 134
    m = Image.new("L", (nw, nh))
    px = m.load()
    for y in range(nh):
        for x in range(nw):
            dx = (x - (nw - 1) / 2) / ((nw - 1) / 2)
            dy = (y - (nh - 1) / 2) / ((nh - 1) / 2)
            d = min(1.0, math.hypot(dx, dy) / 1.42)
            px[x, y] = int(max(0.0, (d - 0.30) / 0.70) ** 1.5 * strength)
    mask = m.resize((W, H), Image.LANCZOS)
    dark = Image.new("RGBA", (W, H), (4, 6, 12, 0))
    dark.putalpha(mask)
    base.alpha_composite(dark)


def add_dots_and_streak(base):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")
    step = 34 * SS
    for y in range(int(H * 0.12), int(H * 0.92), step):
        for x in range(int(W * 0.52), int(W * 0.97), step):
            r = 1.6 * SS
            d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 20))
    # 斜向高光（软边）
    d.polygon([(W * 0.60, -50), (W * 0.86, -50), (W * 0.30, H + 50), (W * 0.04, H + 50)],
              fill=(255, 255, 255, 14))
    layer = layer.filter(ImageFilter.GaussianBlur(26 * SS))
    base.alpha_composite(layer)


def icon_panel(d, x, y, size, accent, kind):
    a = hx(accent)
    d.rounded_rectangle([x, y, x + size, y + size], radius=int(size * 0.16),
                        fill=(255, 255, 255, 15), outline=(255, 255, 255, 42),
                        width=int(2 * SS))
    box = (x + size * 0.20, y + size * 0.20, x + size * 0.80, y + size * 0.80)
    lw = int(7 * SS)
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2

    if kind == "lock":
        sw = w * 0.34
        top = y0 + h * 0.04
        d.arc([cx - sw / 2, top, cx + sw / 2, top + sw], start=180, end=360,
              fill=a, width=lw)
        d.line([cx - sw / 2, top + sw / 2, cx - sw / 2, y0 + h * 0.44], fill=a, width=lw)
        d.line([cx + sw / 2, top + sw / 2, cx + sw / 2, y0 + h * 0.44], fill=a, width=lw)
        bw = w * 0.78
        d.rounded_rectangle([cx - bw / 2, y0 + h * 0.40, cx + bw / 2, y1],
                            radius=int(w * 0.10), fill=a)
        hole = y0 + h * 0.58
        d.ellipse([cx - w * 0.07, hole - w * 0.07, cx + w * 0.07, hole + w * 0.07],
                  fill=(12, 16, 30, 235))
        d.rounded_rectangle([cx - w * 0.026, hole, cx + w * 0.026, hole + h * 0.20],
                            radius=int(w * 0.026), fill=(12, 16, 30, 235))

    elif kind == "versions":
        cw, ch = w * 0.58, h * 0.60
        d.rounded_rectangle([x0 + w * 0.02, y0 + h * 0.04, x0 + w * 0.02 + cw,
                             y0 + h * 0.04 + ch], radius=int(w * 0.07),
                            outline=(255, 255, 255, 74), width=int(4 * SS))
        fx, fy = x0 + w * 0.26, y0 + h * 0.22
        d.rounded_rectangle([fx, fy, fx + cw, fy + ch], radius=int(w * 0.07),
                            fill=(255, 255, 255, 26), outline=a, width=int(5 * SS))
        for i in range(3):
            ly = fy + ch * (0.28 + 0.22 * i)
            d.line([fx + cw * 0.14, ly, fx + cw * (0.78 if i else 0.56), ly],
                   fill=(255, 255, 255, 150), width=int(3.4 * SS))
        ay = y1 - h * 0.06
        d.line([x0 + w * 0.10, ay, x1 - w * 0.12, ay], fill=a, width=int(5 * SS))
        d.polygon([(x1 - w * 0.04, ay), (x1 - w * 0.20, ay - h * 0.075),
                   (x1 - w * 0.20, ay + h * 0.075)], fill=a)

    elif kind == "cache-db":
        # 左边三根"缓存"竖条
        bw = w * 0.13
        for i, hh in enumerate((0.30, 0.52, 0.76)):
            bx = x0 + w * 0.02 + i * (bw * 1.5)
            d.rounded_rectangle([bx, y1 - h * hh, bx + bw, y1], radius=int(bw * 0.35),
                                fill=a)
        # 右边数据库圆柱
        cxx0, cxx1 = x0 + w * 0.62, x1
        d.rounded_rectangle([cxx0, y0 + h * 0.14, cxx1, y1 - h * 0.06],
                            radius=int(w * 0.09), outline=a, width=int(5 * SS))
        eh = h * 0.16
        d.arc([cxx0, y0 + h * 0.14 - eh / 2, cxx1, y0 + h * 0.14 + eh / 2],
              start=180, end=360, fill=a, width=int(5 * SS))
        for i in (0.46, 0.68):
            d.arc([cxx0, y0 + h * i - eh / 2, cxx1, y0 + h * i + eh / 2],
                  start=180, end=360, fill=(255, 255, 255, 120), width=int(3.4 * SS))
        # 中间双向箭头
        ax0, ax1 = x0 + w * 0.40, cxx0 - w * 0.04
        ay = y1 - h * 0.22
        d.line([ax0, ay - h * 0.10, ax1, ay - h * 0.10], fill=(255, 255, 255, 170),
               width=int(4 * SS))
        d.polygon([(ax1, ay - h * 0.10), (ax1 - w * 0.06, ay - h * 0.155),
                   (ax1 - w * 0.06, ay - h * 0.045)], fill=(255, 255, 255, 170))
        d.line([ax0, ay + h * 0.10, ax1, ay + h * 0.10], fill=(255, 255, 255, 170),
               width=int(4 * SS))
        d.polygon([(ax0, ay + h * 0.10), (ax0 + w * 0.06, ay + h * 0.045),
                   (ax0 + w * 0.06, ay + h * 0.155)], fill=(255, 255, 255, 170))


def wrap(text, f, max_w, d):
    lines, cur = [], ""
    for ch in text:
        if d.textlength(cur + ch, font=f) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def draw_chip(d, x, y, text, f, fg, bg, border, pad_x=22, pad_y=13, radius=14):
    tw = d.textlength(text, font=f)
    th = f.size
    box = [x, y, x + tw + pad_x * SS, y + th + pad_y * SS]
    d.rounded_rectangle(box, radius=radius * SS, fill=bg, outline=border,
                        width=max(1, int(1.4 * SS)))
    d.text((x + pad_x * SS, y + pad_y * SS - th * 0.06), text, font=f, fill=fg)
    return box[2] - x


def render(cfg):
    base = multi_gradient(cfg["stops"], cfg.get("angle", 28))
    for cx, cy, r, color, alpha in cfg["glows"]:
        add_glow(base, cx, cy, r, color, alpha)
    add_dots_and_streak(base)
    add_vignette(base)

    # 半透明元素必须画在独立图层上再 alpha_composite：
    # 直接往 RGBA 图上画会把 alpha 写进像素，最后 convert("RGB") 一压平就变成纯白/纯黑。
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov, "RGBA")
    accent = hx(cfg["accent"])
    pad = 84

    # ---- kicker 胶囊 ----
    f_kick = font(FONT_REG, 23)
    kw = d.textlength(cfg["kicker"], font=f_kick)
    d.rounded_rectangle([pad * SS, 92 * SS, pad * SS + kw + 44 * SS, 92 * SS + 46 * SS],
                        radius=10 * SS, fill=(255, 255, 255, 18),
                        outline=accent + (150,), width=int(1.6 * SS))
    d.ellipse([pad * SS + 18 * SS, 92 * SS + 19 * SS, pad * SS + 26 * SS, 92 * SS + 27 * SS],
              fill=accent)
    d.text((pad * SS + 36 * SS, 92 * SS + 10 * SS), cfg["kicker"], font=f_kick,
           fill=accent)

    # ---- 图标面板 ----
    panel = 330
    icon_panel(d, FINAL_W * SS - pad * SS - panel * SS, 206 * SS, panel * SS,
               cfg["accent"], cfg["icon"])

    # ---- 主标题（自动缩字号，保证 ≤3 行） ----
    max_w = (FINAL_W - pad - panel - pad - 40) * SS
    size, lines = 60, []
    while size >= 40:
        f_title = font(FONT_BOLD, size)
        lines = wrap(cfg["title"], f_title, max_w, d)
        if len(lines) <= 3:
            break
        size -= 4
    line_h = size * 1.34 * SS
    y = 188 * SS
    for ln in lines:
        d.text((pad * SS, y), ln, font=f_title, fill=(255, 255, 255))
        y += line_h

    # ---- 强调下划线 ----
    y += 18 * SS
    d.rounded_rectangle([pad * SS, y, pad * SS + 132 * SS, y + 7 * SS],
                        radius=4 * SS, fill=accent)
    y += 40 * SS

    # ---- 副标题 ----
    f_sub = font(FONT_REG, 27)
    for ln in wrap(cfg["subtitle"], f_sub, max_w, d)[:2]:
        d.text((pad * SS, y), ln, font=f_sub, fill=(206, 218, 238))
        y += 44 * SS

    # ---- 要点列表（填满中段，也让人一眼知道文章给什么） ----
    if cfg.get("points"):
        y += 30 * SS
        f_pt = font(FONT_REG, 25)
        for pt in cfg["points"][:3]:
            d.ellipse([pad * SS, y + 12 * SS, pad * SS + 9 * SS, y + 21 * SS], fill=accent)
            d.text((pad * SS + 26 * SS, y), pt, font=f_pt, fill=(214, 224, 240))
            y += 48 * SS

    # ---- 底部标签胶囊 ----
    f_chip = font(FONT_REG, 21)
    cx = pad * SS
    cy = 686 * SS
    for tag in cfg["chips"]:
        wch = d.textlength(tag, font=f_chip)
        if cx + wch + 44 * SS > (FINAL_W - pad - 210) * SS:
            break
        cx += draw_chip(d, cx, cy, tag, f_chip, (236, 242, 250, 240),
                        (255, 255, 255, 22), (255, 255, 255, 64)) + 16 * SS

    # ---- 右下角署名 ----
    f_foot = font(FONT_REG, 21)
    fw = d.textlength(cfg["footer"], font=f_foot)
    d.text((FINAL_W * SS - pad * SS - fw, 700 * SS), cfg["footer"], font=f_foot,
           fill=(178, 190, 210))

    out = OUT_DIR / f"{cfg['slug']}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    base.alpha_composite(ov)
    base.convert("RGB").resize((FINAL_W, FINAL_H), Image.LANCZOS).save(out, "PNG")
    print(f"已生成: {out}  ({FINAL_W}x{FINAL_H}, {len(lines)} 行标题, 字号 {size})")
    return out


COVERS = [
    {
        "slug": "innodb-gap-lock",
        "kicker": "MySQL · InnoDB",
        "title": "InnoDB 间隙锁与死锁",
        "subtitle": "两个 UPDATE 互相等待的 3 个原因",
        "points": ["加锁范围为什么比你以为的大", "3 个可复现的死锁现场", "5 条能立刻落地的规避手法"],
        "chips": ["InnoDB", "MySQL", "事务", "锁", "死锁"],
        "icon": "lock",
        "accent": "#7CC2FF",
        "stops": [(0.0, "#060B1A"), (0.45, "#122150"), (0.78, "#1E3A8A"), (1.0, "#2B4FBF")],
        "glows": [(1030, 120, 430, "#2E5FE8", 120), (150, 760, 360, "#7C4DFF", 90)],
        "footer": "<你的昵称>",
    },
    {
        "slug": "mvcc-readview",
        "kicker": "MySQL · MVCC",
        "title": "MVCC 快照读为什么读不到新数据",
        "subtitle": "ReadView 的 4 条可见性规则与版本链",
        "points": ["trx_id 与 m_ids 的比对过程", "快照读与当前读的分界", "6 个可复现的验证实验"],
        "chips": ["MySQL", "InnoDB", "MVCC", "事务隔离"],
        "icon": "versions",
        "accent": "#5EEAD4",
        "stops": [(0.0, "#03130F"), (0.45, "#0A3B33"), (0.78, "#0F766E"), (1.0, "#14A08C")],
        "glows": [(1020, 140, 420, "#10B981", 115), (170, 750, 340, "#22D3EE", 85)],
        "footer": "<你的昵称>",
    },
    {
        "slug": "redis-cache-consistency",
        "kicker": "Redis · 缓存",
        "title": "缓存与数据库一致性",
        "subtitle": "先删缓存还是先更新库的 4 种方案",
        "points": ["4 种写顺序的并发时序推演", "延迟双删到底有没有用", "删除失败与 binlog 兜底"],
        "chips": ["Redis", "缓存", "一致性", "后端"],
        "icon": "cache-db",
        "accent": "#FF9BB0",
        "stops": [(0.0, "#160711"), (0.45, "#5B1030"), (0.80, "#A11D4A"), (1.0, "#D9455F")],
        "glows": [(1010, 150, 420, "#F5C86B", 95), (160, 740, 350, "#FF5C8A", 105)],
        "footer": "<你的昵称>",
    },
]


if __name__ == "__main__":
    for c in COVERS:
        render(c)
