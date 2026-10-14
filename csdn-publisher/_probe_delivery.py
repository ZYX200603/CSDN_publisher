#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核实每一位收件人到底收到没有（只读）。

判据（实测）：
  * 会话里能找到我发的文章链接 → 真送达；
  * 只剩「24小时内最多与5个陌生人发送消息」这类系统提示 → **被平台拦下**，
    界面上当时那一下是乐观渲染，事后会被系统消息替换（所以"输入框清空+出现本条"这个判定会误报成功）。

适配说明（2026-10-09，从 DSH 侧移植到 WB 侧）：
  * 文章标记**从 templates/message.txt 动态提取**——原版写死一个旧文章 ID，
    换过内容后再跑会把所有会话都误判成"看不到我的消息"；
  * 用主 profile（`.profile`，有登录态）——原版指向的 `.tmp/vprofile` 在本副本里不存在，
    照原样跑会以未登录状态打开所有会话；
  * 其余逻辑与原始判定保持一字不差，便于两边对照维护。

用法:
  python _probe_delivery.py                    # 查库中所有 status=sent* 的粉丝
  python _probe_delivery.py --all              # 查全部（含关注的人）
  python _probe_delivery.py --users=a,b,c      # 只查指定的人（复核个别会话用）
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import publish_csdn as pc  # noqa: E402

import csdn_message as cm  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

DB = ROOT / "output" / "message" / "message.db"
OUT = ROOT / "output" / "dm-delivery.json"
MSG_FILE = ROOT / "templates" / "message.txt"

# 拦截文案**共用发送端的同一份**（DM_LIMIT_HINTS），别再手写一份——
# 2026-10-08 就因为这里漏了「对方尚未回复你，你只能发送1条消息」而把"被拦"误判成"不确定"。
NOTICE_KWS = list(cm.DM_LIMIT_HINTS) + ["限制", "屏蔽", "拒收", "频繁", "失败"]

JS = r"""
(args) => {
  const [markers, kws] = args;
  const box = document.querySelector('.chatMsg') || document.querySelector('.rightContent');
  const all = (box ? box.innerText : document.body.innerText) || '';
  const links = [...(box || document).querySelectorAll('a[href]')]
      .map(a => a.getAttribute('href') || '');
  const notices = [...document.querySelectorAll('.pure-text, .system-prompt, .system-msg')]
      .map(e => (e.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(t => t && t.length <= 80 && kws.some(k => t.includes(k)));
  const hasMarker = markers.some(m => all.includes(m) || links.some(h => h.includes(m)));
  return {
    peer: (() => { const h = document.querySelector('.chatHeader .user-name'); return h ? h.innerText.trim() : null; })(),
    hasMarker: hasMarker,
    notices: notices.slice(0, 3),
    msgCount: (box || document).querySelectorAll('.msg-item').length,
  };
}
"""


def load_markers():
    """从私信正文里提取文章 ID 当标记（会话里命中任一即算'我的消息在'）。"""
    text = MSG_FILE.read_text(encoding="utf-8-sig")
    ids = list(dict.fromkeys(re.findall(r"/details/(\d+)", text)))
    if not ids:
        print("⛔ templates/message.txt 里没找到任何 /details/<id> 链接，无法复核（先确认正文）。")
        sys.exit(1)
    return ids


def main():
    only_fans = "--all" not in sys.argv
    # 也可以只查指定的人：--users=a,b,c（用来复核个别"不确定"的会话）
    pick = None
    for a in sys.argv[1:]:
        if a.startswith("--users"):
            pick = [x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()] \
                if "=" in a else None
    con = sqlite3.connect(str(DB))
    if pick:
        users = pick
    elif only_fans:
        rows = con.execute(
            "SELECT DISTINCT user FROM sends WHERE status IN ('sent','sent_unverified') "
            "AND source='follower' ORDER BY id").fetchall()
        users = [r[0] for r in rows]
    else:
        rows = con.execute(
            "SELECT DISTINCT user FROM sends WHERE status IN ('sent','sent_unverified') "
            "ORDER BY id").fetchall()
        users = [r[0] for r in rows]
    con.close()

    markers = load_markers()
    wait_ms = 12000 if pick else 7000      # 单独复核时多等一会儿，避免"慢加载"误判
    print(f"文章标记（来自 templates/message.txt）: {markers}")
    print(f"要核实的收件人：{len(users)} 人（{'指定名单' if pick else ('仅粉丝' if only_fans else '全部')}）")

    result = {}
    ok = blocked = unknown = 0
    with sync_playwright() as p:
        b = pc.Browser(p)
        try:
            b.launch()
        except BaseException as e:
            print("launch failed:", type(e).__name__, str(e)[:150])
            return 1
        page = b.ctx.new_page()
        for i, u in enumerate(users, 1):
            try:
                page.goto(f"https://im.csdn.net/chat/{u}", wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(wait_ms)
                info = page.evaluate(JS, [markers, NOTICE_KWS])
                if info["hasMarker"]:
                    verdict = "送达"
                    ok += 1
                elif info["notices"]:
                    verdict = "被拦:" + info["notices"][0][:24]
                    blocked += 1
                else:
                    verdict = "看不到我的消息（待人工确认）"
                    unknown += 1
                result[u] = {**info, "verdict": verdict}
                print(f"  [{i}/{len(users)}] {u:<22} peer={str(info['peer'])[:14]:<16} {verdict}")
            except BaseException as e:
                result[u] = {"error": f"{type(e).__name__}: {str(e)[:120]}", "verdict": "读取失败"}
                unknown += 1
                print(f"  [{i}/{len(users)}] {u:<22} 读取失败 {type(e).__name__}")
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            b.close()
        except BaseException:
            pass
    print(f"\n汇总：送达 {ok} / 被拦 {blocked} / 不确定 {unknown}（{len(users)} 人）")
    print(f"明细写入 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
