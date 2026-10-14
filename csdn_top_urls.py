#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「今天发布的文章里阅读量最高的 N 篇」链接写进 templates/message.txt。

命令映射：/csdn-publish get_url_message 就是跑这个脚本。

    .venv/Scripts/python.exe csdn_top_urls.py              # 默认：优先复用今天已有的快照
    .venv/Scripts/python.exe csdn_top_urls.py --fresh      # 强制重新联网采集（csdn_stats.py）
    .venv/Scripts/python.exe csdn_top_urls.py --dry-run    # 只预览，不写文件
    .venv/Scripts/python.exe csdn_top_urls.py --top 4      # 取前几篇（默认 4，与模板槽位一致）

行为约定：
  1. 数据源优先取 output/stats/snapshots/ 里最新的快照；快照必须是「今天采集的」
     且「含有今天发布的文章」，否则自动调 csdn_stats.py 重新采集（--fresh 强制重采）。
  2. 筛 publishTime 为今天的文章，按 views 降序取前 N 篇（阅读量从高到低）。
  3. 在 message.txt 里定位「链接槽位」——整行且形如 `1.url` 或 `1.https://...`
     的行，整块替换成新的编号链接；没有槽位时退回替换独占一行的 `url` 字样。
     两者都找不到就报错，绝不在文件里瞎写。
  4. 保持原文件编码（UTF-8，有 BOM 就还你 BOM）和原有换行风格（LF/CRLF）。
  5. 打印写入结果 + 私信正文字符数（上限 500，超了会警告）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAP_DIR = ROOT / "output" / "stats" / "snapshots"
DEFAULT_TEMPLATE = ROOT / "templates" / "message.txt"
FRESH_PAGES = "2"          # 重采时只采前 2 页（今天的文章按发布时间排在最前）
FRESH_TIMEOUT = 300        # 秒；采集约 1 分钟，留足余量
DM_LIMIT = 500             # 私信正文上限（csdn_message.py 同款硬闸门）


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def latest_snapshot() -> Path | None:
    if not SNAP_DIR.is_dir():
        return None
    snaps = sorted(SNAP_DIR.glob("*.json"))
    return snaps[-1] if snaps else None


def load_snapshot(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def snapshot_usable(data: dict, today: str) -> bool:
    """快照是今天采的，且至少含 1 篇今天发布的文章。"""
    if str(data.get("collectedAt", ""))[:10] != today:
        return False
    return any(a.get("publishTime", "").startswith(today) for a in data.get("articles", []))


def top_today(data: dict, today: str, n: int) -> list[dict]:
    arts = [a for a in data.get("articles", []) if a.get("publishTime", "").startswith(today)]
    # 阅读降序；并列时发布早的排前面（观测更久、数值更可信）
    arts.sort(key=lambda a: (-int(a.get("views", 0)), a.get("publishTime", "")))
    return arts[:n]


def collect_fresh() -> Path | None:
    """调 csdn_stats.py 重新采集，返回新的最新快照路径。失败返回 None。"""
    print(f"[fresh] 现有快照不可用，重新采集（csdn_stats.py --pages {FRESH_PAGES}）…")
    try:
        proc = subprocess.run(
            [sys.executable, "csdn_stats.py", "--pages", FRESH_PAGES, "--quiet"],
            cwd=ROOT, timeout=FRESH_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"[fresh] ✗ 采集超时（{FRESH_TIMEOUT}s），已放弃", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"[fresh] ✗ 采集失败，退出码 {proc.returncode}", file=sys.stderr)
        return None
    snap = latest_snapshot()
    print(f"[fresh] ✓ 新快照：{snap.name if snap else '（无）'}")
    return snap


def get_links(n: int, fresh: bool) -> list[dict]:
    today = today_str()
    snap = latest_snapshot()
    data = None
    used = None
    if snap is not None and not fresh:
        try:
            cand = load_snapshot(snap)
        except (json.JSONDecodeError, OSError) as e:
            print(f"[warn] 快照 {snap.name} 读不了（{e}），尝试重采", file=sys.stderr)
            cand = None
        if cand is not None and snapshot_usable(cand, today):
            data, used = cand, snap
    if data is None:
        snap2 = collect_fresh()
        if snap2 is None:
            raise SystemExit("⛔ 重新采集失败（浏览器起不来 / 撞验证 / 超时）。"
                             "按风控规则我不重试绕过，请人工看一眼浏览器后重跑。")
        try:
            cand = load_snapshot(snap2)
        except (json.JSONDecodeError, OSError) as e:
            raise SystemExit(f"⛔ 新快照 {snap2.name} 读不了：{e}")
        if not snapshot_usable(cand, today):
            raise SystemExit(f"⛔ 新快照里没有 {today} 发布的文章，今天可能还没发文章。")
        data, used = cand, snap2
    links = top_today(data, today, n)
    if not links:
        raise SystemExit(f"⛔ 快照 {used.name} 里没有 {today} 发布的文章。")
    print(f"[data] 快照 {used.name}（采集于 {data.get('collectedAt')}），"
          f"今天共 {sum(1 for a in data['articles'] if a.get('publishTime','').startswith(today))} 篇，取前 {len(links)} 篇")
    return links


# 槽位行：`1.url` 或 `1.https://...`（整行，行首行尾锚定）
SLOT_RE = re.compile(r"(?m)^\d+\.(?:url|https?://\S+)$")
# 独占一行的 url 字样
BARE_URL_RE = re.compile(r"(?m)^url$")


def replace_links(text: str, links: list[str]) -> str | None:
    """把链接写进模板。返回新文本；找不到可替换的槽位返回 None（不动文件）。"""
    new_block = "\n".join(f"{i + 1}.{u}" for i, u in enumerate(links))
    m = list(SLOT_RE.finditer(text))
    if m:
        first, last = m[0].start(), m[-1].end()
        # 槽位通常是连续一块；若中间夹了别的内容就退回逐行替换，避免吞掉正文
        between = text[first:last]
        if all(SLOT_RE.fullmatch(line) or line == "" for line in between.split("\n")):
            return text[:first] + new_block + text[last:]
        for mm in reversed(m):
            idx = m.index(mm)
            text = text[:mm.start()] + (f"{idx + 1}.{links[idx]}" if idx < len(links) else "") + text[mm.end():]
        if len(links) > len(m):  # 链接比槽位多：追加在最后一个槽位行之后
            extra = "\n".join(f"{i + 1}.{u}" for i, u in enumerate(links) if i >= len(m))
            pos = m[-1].end()
            text = text[:pos] + ("\n" if not text[:pos].endswith("\n") else "") + extra + text[pos:]
        return text
    bare = BARE_URL_RE.search(text)
    if bare:
        return text[:bare.start()] + new_block + text[bare.end():]
    return None


def read_text(path: Path) -> tuple[str, str, str]:
    """返回 (文本, 编码标记, 换行风格)。"""
    raw = path.read_bytes()
    bom = ""
    if raw.startswith(b"\xef\xbb\xbf"):
        bom, raw = "utf-8-sig", raw[3:]
    text = raw.decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), bom, nl


def write_text(path: Path, text: str, bom: str, nl: str) -> None:
    out = text.replace("\n", nl)
    path.write_bytes(out.encode(bom or "utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="把今天阅读量最高的 N 篇文章链接写进私信模板 message.txt")
    ap.add_argument("--top", type=int, default=4, help="取前几篇（默认 4）")
    ap.add_argument("--fresh", action="store_true", help="强制重新联网采集，不复用快照")
    ap.add_argument("--file", default=str(DEFAULT_TEMPLATE), help="私信模板路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写文件")
    args = ap.parse_args()
    if args.top < 1:
        print("⛔ --top 至少为 1", file=sys.stderr)
        return 2

    tpl = Path(args.file)
    if not tpl.is_file():
        print(f"⛔ 模板不存在：{tpl}", file=sys.stderr)
        return 2

    links = get_links(args.top, args.fresh)
    urls = [a["url"] for a in links]

    print("\n=== 今天阅读量 Top {} ===".format(len(links)))
    for i, a in enumerate(links, 1):
        print(f"{i}. [{a.get('views')} 阅读 | 赞{a.get('likes')} 评{a.get('comments')} 藏{a.get('favorites')}] "
              f"{a.get('publishTime')} | {a.get('title')}")
        print(f"   {a['url']}")

    text, bom, nl = read_text(tpl)
    new_text = replace_links(text, urls)
    if new_text is None:
        print(f"\n⛔ 在 {tpl.name} 里找不到可替换的槽位（`N.url` / `N.http...` 整行，或独占一行的 url）。"
              f"\n   请先在模板里放好编号槽位，例如：\n   1.url\n   2.url\n   3.url\n   4.url", file=sys.stderr)
        return 1
    if new_text == text:
        print(f"\n[=] {tpl.name} 内容无变化（链接已是当前 Top {len(links)}）")

    body = new_text.rstrip(nl)
    blen = len(body)
    print(f"\n=== 写入后的 {tpl.name}（{blen} 字符 / 私信上限 {DM_LIMIT}）===")
    print(body)
    if blen > DM_LIMIT:
        print(f"⚠ 正文 {blen} 字符已超私信上限 {DM_LIMIT}，csdn_message.py 会拒绝发送，请先精简模板。")

    if args.dry_run:
        print("\n[dry-run] 未写文件。")
        return 0
    if new_text != text:
        write_text(tpl, new_text, bom, nl)
        print(f"\n✓ 已写入 {tpl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
