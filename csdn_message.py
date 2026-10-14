#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSDN 私信群发 —— 按 templates/message.txt 给「我关注的人 / 我的粉丝」发私信。

设计原则（照抄一键三连 csdn_interact.py 的那套）：
  * 选择器/接口地址全部来自 config.json 的 message 段，改版只改配置不改代码；
  * 名单来源与发送是两件事：先抓名单、再逐条发，中间可停下来给人看；
  * **不写 --yes / --wait 就什么都不发**（这是硬闸门，不是提示）；
  * 只发 templates/message.txt 里的原文，不做任何个性化替换。

★ 名单能取全了（2026-10-09 改造，见 collect_from_profile）：
  以前名单走「个人主页 → 粉丝」这个**列表页 DOM**，而该视图**写死 20 条**
  （没有分页器、没有「加载更多」、滚到底也不涨），所以 --limit all 也只能发 20 人。
  实测站点自己拉列表走的是接口 `mp-action.csdn.net/.../getFansOffsetList?pageSize=20`
  ——把 pageSize 调大就能一次取全（41 粉丝 / 208 关注，实测全部到手）。
  现在**默认走接口**，接口不可用才退回 DOM 抓取（那时最多 20 条，会明确告警，不静默少发）。

用法：
  # ① 只看名单（不发任何私信），并存下这份待确认名单
  .\\csdn_message.bat --to follower --limit 20 --wait

  # ② 名单确认后真发（会优先用上一步存下的那份名单，保证发的是你看过的那些人）
  .\\csdn_message.bat --to follower --limit 20 --yes

  # 关注的人 + 粉丝都发（all = 全部，不再受 20 条上限）
  .\\csdn_message.bat --to following,follower --limit all --wait

  # --limit all 时跳过"以前已经成功发过"的人，避免重复私信
  .\\csdn_message.bat --to follower --limit all --yes --skip-sent

  # 只演练选择器：填进输入框、截图，但绝不点发送
  .\\csdn_message.bat --to follower --limit 1 --dry-run

  # 看历史发送记录
  .\\csdn_message.bat --stats
"""

import argparse
import hashlib
from collections import Counter
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish_csdn as pc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

ROOT = pc.ROOT
OUTPUT_DIR = pc.OUTPUT_DIR
MSG_DIR = OUTPUT_DIR / "message"
DB_PATH = MSG_DIR / "message.db"
PENDING_PATH = MSG_DIR / "pending.json"
REPORT_PATH = MSG_DIR / "latest.md"

MCFG = (pc.CFG.get("message") or {})
SEL = MCFG.get("selectors") or {}
DEF = MCFG.get("defaults") or {}
URLS = MCFG.get("urls") or {}

DEFAULT_FILE = MCFG.get("file") or "templates/message.txt"
MAX_CHARS = int(MCFG.get("maxChars", 500))

# 名单来源：中文/英文写法都认
TO_ALIAS = {
    "following": "following", "follow": "following", "关注": "following",
    "关注的人": "following", "我关注的": "following",
    "follower": "follower", "followers": "follower", "fans": "follower",
    "粉丝": "follower", "我的粉丝": "follower",
    "both": "both", "all": "both", "全部": "both", "两个都发": "both", "两者": "both",
}
TO_CN = {"following": "关注的人", "follower": "粉丝", "both": "关注的人+粉丝"}

# 私信被拦的提示语（平台拦截 / 风控 / 对方设置）
BLOCK_HINTS = list(MCFG.get("blockHints") or [
    "发送失败", "发送频繁", "操作过于频繁", "请求过于频繁", "稍后再试", "频繁",
    "已被拉黑", "加入黑名单", "拒收", "无法发送", "不能发送", "对方设置",
    "内容包含敏感", "敏感词", "账号异常", "验证", "禁言", "限制",
    "请先登录", "登录后",
])
FREQ_HINTS = ["频繁", "稍后再试", "请求过于频繁", "操作过于频繁"]
# 值得「隔一会儿重试一次」的失败：原因不明、多半是瞬时（页面没就绪 / 输入框没出来）。
# 平台明确拒绝的（限额 failed_dm_limit、风控 failed_freq/failed_block）**不在内**——
# 那些重试只会白撞风控，留着下次用 --skip-sent 补发才对。
RETRYABLE_STATUS = {"failed_unknown", "failed_no_input", "failed_fill"}
# 私信限额提示（实测 2026-10-08，**有两种，别只认一种**）：
#   ① 「24小时内最多与5个陌生人发送消息」——对方跟你没有历史会话时的账号级限额；
#   ② 「对方尚未回复你，你只能发送1条消息」——对方没回过你时，最多只能发 1 条。
# 两种都意味着**这条没送到**，而且界面都是"先乐观渲染、再替换成系统提示"。
DM_LIMIT_HINTS = list(
    MCFG.get("limitHints") or MCFG.get("strangerHints")
    or ["陌生人", "最多与", "对方尚未回复", "只能发送1条", "只能发送一条"])

# ---------------------------------------------------------------- 名单接口
# ★ 2026-10-09 实测：列表页 DOM **写死 20 条**，而站点自己拉列表用的是接口：
#     GET {base}/getFansOffsetList?pageSize=20&username=<me>     ← 我的粉丝
#     GET {base}/getFollowOffsetList?pageSize=20&username=<me>   ← 我关注的人
#   pageSize 是**唯一**有效旋钮：实测 1→1、5→5、20→20、41→41、1000→41（超出总数自动截断）；
#   没有 offset / page / cursor 之类的分页参数（试了 13 个名字全部无效，
#   传 fanId 反而会被当成过滤条件返回 0 条）——所以"翻页"就等于把 pageSize 调大。
#   响应结构：{code, msg, total(恒 0，没用), data:{total(恒 0/null，没用), list:[{username, nickname, ...}]}}
#   ⚠ 单次拿多少只能靠 pageSize，拿不到服务端总数 → 总数用主页头部的「N 粉丝」对账。
#   ⚠ 短时间内高频打这个接口会被网关在 TLS 层掐断
#     （ssl3_read_bytes:tlsv1 alert internal error，之后连正常请求也会连着失败），
#     所以一次名单只发 1~2 个请求 + 退避重试，绝不循环狂刷。
FANS_API = MCFG.get("fansApi") or {}
API_BASE = str(FANS_API.get("base")
               or "https://mp-action.csdn.net/interact/wrapper/pc/fans/v1/api").rstrip("/")
API_PATH = {
    "follower": str(FANS_API.get("fans") or "getFansOffsetList"),
    "following": str(FANS_API.get("following") or "getFollowOffsetList"),
}
API_MIN_SIZE = int(FANS_API.get("minPageSize", 200))
API_MAX_SIZE = int(FANS_API.get("maxPageSize", 2000))
API_HEADROOM = int(FANS_API.get("headroom", 50))
API_RETRIES = int(FANS_API.get("retries", 3))
API_RETRY_WAIT_MS = int(FANS_API.get("retryWaitMs", 2500))
API_TIMEOUT_MS = int(FANS_API.get("timeoutMs", 20000))


def log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def _now():
    return datetime.now()


# ============================================================ 浏览器启动
# publish_csdn.Browser.launch() 已经内置了「清陈旧 profile 锁 + 重试」，
# 这里只需要在彻底起不来时再自愈一次（复用一键三连那套 clear_stale_profile）。

def launch_browser(pw, issues, clean_first=False):
    if clean_first:
        _clear_stale_profile()
    b = pc.Browser(pw)
    try:
        b.launch()
        return b
    except (Exception, SystemExit) as e:
        # 必须接住 SystemExit：Browser.launch() 起不来时是 raise SystemExit
        log(f"浏览器启动失败: {e.__class__.__name__}: {str(e)[:200]}")
        issues.append(("browser-launch", str(e)[:200]))
        try:
            b.close()
        except Exception:
            pass
    log("清理残留后重试启动…")
    _clear_stale_profile()
    b = pc.Browser(pw)
    try:
        b.launch()
        issues.append(("browser-launch-recovered", "清理 profile 残留后启动成功"))
        return b
    except (Exception, SystemExit) as e:
        issues.append(("browser-launch-failed", str(e)[:200]))
        log(f"清理后仍无法启动浏览器: {e.__class__.__name__}")
        return None


def _clear_stale_profile():
    try:
        from csdn_interact import clear_stale_profile  # noqa: PLC0415
    except BaseException as e:      # 复用的模块可能正好被改到一半，别静默降级
        log(f"（拿不到 clear_stale_profile: {e.__class__.__name__}: {str(e)[:120]}，跳过自愈）")
        return
    try:
        clear_stale_profile()
    except BaseException as e:
        log(f"（clear_stale_profile 执行失败: {e.__class__.__name__}: {str(e)[:120]}）")


# ============================================================ 名单采集
# 两条路：
#   ① 接口（默认）：在主页里用 fetch() 打 getFans/getFollowOffsetList，pageSize 调大取全；
#   ② DOM（兜底）：接口挂了才用。粉丝 / 关注的人都在个人主页的「关注/订阅/互动」里，
#      二级页签是 <li>，label 常常宽高为 0 → 不能用 locator.click()，必须 JS 直接 click()。
#      ⚠ 这个视图最多 20 条，所以兜底路径天然发不全，会告警。（见 config.json message.fansApi）

CLICK_TEXT_JS = r"""
(want) => {
  const vis = (el) => el.getClientRects().length > 0;
  const all = [...document.querySelectorAll('li, a, span, p, div')]
      .filter(el => (el.innerText || '').replace(/\s+/g, ' ').trim() === want);
  // 优先点二级页签（li.sub-second-list-li），它才是真正切列表的那个
  const prefer = all.find(el => /sub-second-list/.test(String(el.className || ''))) || all[0];
  for (const el of [prefer, ...all]) {
    if (!el || !vis(el)) continue;
    const target = el.closest('li') || el;
    target.click();
    return {ok: true, cls: String(target.className || '').slice(0, 60)};
  }
  return {ok: false, seen: all.length};
}
"""

# 抓用户行：只认「纯个人主页链接」blog.csdn.net/<user>（带 /article/ 的一律不算）
SCRAPE_USERS_JS = r"""
(me) => {
  const vis = (el) => el.getClientRects().length > 0;
  const out = [];
  const seen = {};
  document.querySelectorAll('a[href*="blog.csdn.net/"]').forEach(a => {
    if (!vis(a)) return;
    const href = a.getAttribute('href') || '';
    const m = href.match(/^https?:\/\/blog\.csdn\.net\/([A-Za-z0-9_.\-]+)\/?(?:[?#].*)?$/);
    if (!m) return;
    const user = m[1];
    if (!user || user === me) return;            // 别把自己发进去
    if (seen[user]) return;
    seen[user] = 1;
    const box = a.closest('li, [class*=item], [class*=card], [class*=fans], [class*=follow], div');
    const name = (a.innerText || '').replace(/\s+/g, ' ').trim();
    out.push({
      user: user,
      name: name || user,
      boxCls: box ? String(box.className || '').split(/\s+/).slice(0, 4).join('.') : '',
      boxText: box ? (box.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 80) : '',
    });
  });
  // 翻页器状态：有下一页按钮才算还有更多
  const next = [...document.querySelectorAll(
      '.el-pagination button.btn-next, button.btn-next, a.next, li.next, [class*=next]')]
      .filter(vis);
  const nextDisabled = next.length
      ? next.some(el => el.disabled || /is-disabled|disabled/.test(String(el.className || '')))
      : null;
  return {rows: out, hasNext: next.length > 0 && !nextDisabled, nextCount: next.length};
}
"""

CLICK_NEXT_JS = r"""
() => {
  const vis = (el) => el.getClientRects().length > 0;
  const cands = [...document.querySelectorAll(
      '.el-pagination button.btn-next, button.btn-next, a.next, li.next, [class*=next]')]
      .filter(el => vis(el) && !el.disabled
                    && !/is-disabled|disabled/.test(String(el.className || '')));
  if (!cands.length) return false;
  cands[0].click();
  return true;
}
"""

# 在页面里直接打名单接口（credentials: include 带上登录 cookie；
# 页面本身就是 blog.csdn.net，接口在 mp-action.csdn.net，站点自己就是这么跨域请求的）。
FETCH_LIST_JS = r"""
async (url) => {
  try {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), __TIMEOUT__);
    const r = await fetch(url, {
      credentials: 'include',
      headers: {'Accept': 'application/json'},
      signal: ctl.signal,
    });
    clearTimeout(timer);
    const t = await r.text();
    let j = null;
    try { j = JSON.parse(t); } catch (e) {
      return {status: r.status, error: 'NOT_JSON', head: t.slice(0, 160)};
    }
    const data = (j && j.data) || {};
    const list = Array.isArray(data.list) ? data.list : [];
    return {
      status: r.status,
      code: (j && j.code) || null,
      msg: (j && (j.msg || j.message)) || null,
      n: list.length,
      rows: list.map(x => ({
        user: String(x.username || '').trim(),
        name: String(x.nickname || x.username || '').trim(),
      })).filter(x => x.user),
    };
  } catch (e) {
    return {error: String(e && e.name || e).slice(0, 120)};
  }
}
"""


def load_message_text(path: Path):
    """读私信正文。utf-8-sig：文件可能带 BOM。返回 (text, err)。"""
    if not path.exists():
        return None, (f"找不到私信正文文件: {path}\n"
                      f"  请把要发的内容写进这个文件（纯文本，原样发送，不做任何替换）。")
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except Exception as e:
        return None, f"读取 {path} 失败: {e.__class__.__name__}: {e}"
    text = raw.strip()
    if not text:
        return None, (f"{path} 是空的。\n"
                      f"  空内容不会发送——请先把要发的文字写进去再跑。")
    n = js_len(text)
    if n > MAX_CHARS:
        return None, (f"私信正文太长了：{n} 字，CSDN 私信单条上限 {MAX_CHARS} 字"
                      f"（输入框右下角有 x/{MAX_CHARS} 计数）。\n"
                      f"  请把它改短，或自己拆成两条分别发。")
    return text, None


def js_len(s: str) -> int:
    """按 JS 的 .length 口径算长度（emoji 算 2），和页面上的 x/500 计数对齐。"""
    return len(s.encode("utf-16-le")) // 2


def my_identity(b, page=None):
    """我是谁 —— 返回 (账号ID, 昵称, 来源)。

    决定去谁的主页读粉丝列表，弄错就会抓到**别人**的粉丝（实测踩过）。

    实测（2026-10-08）：cookie `UserName` / `UN` 里是**账号 ID**（例 `2501_9xxxxxxx`）；
    `UserNick` 是**昵称**（例「示例昵称」），两个都要，因为主页头部显示昵称、URL 用 ID。
    `UserInfo` 只是一串 hash，不能当用户名用；曾经因为只解它而拿到 None，页面 URL 就退化成
    `blog.csdn.net/?type=sub&subtype=fans`，把首页推荐作者当成"我的粉丝"——
    **这类静默抓错比报错危险得多**，所以拿不到 ID 就中止，绝不猜。
    """
    from urllib.parse import unquote
    try:
        cks = b.ctx.cookies()
    except Exception:
        cks = []
    vals = {}
    for c in cks:
        n = c.get("name")
        if n in ("UserName", "UN", "UserNick"):
            vals.setdefault(n, []).append(c.get("value") or "")

    def clean(v):
        return unquote(v).strip().rstrip("=").strip()

    uid = ""
    for name in ("UserName", "UN"):
        for v in vals.get(name, []):
            u = clean(v)
            if u and " " not in u:
                uid = u
                break
        if uid:
            break
    nick = ""
    for v in vals.get("UserNick", []):
        n = clean(v)
        if n:
            nick = n
            break

    if uid:
        return uid, nick, "cookie UserName/UN"
    # page 兜底：页面上"自己的博客"链接（a.user-info / 个人主页）
    if page is not None:
        try:
            u = page.evaluate(r"""
            () => {
              const cands = [...document.querySelectorAll(
                  'a.user-info, a[href*="blog.csdn.net"]')];
              for (const a of cands) {
                const t = (a.innerText || '').trim();
                const m = (a.getAttribute('href') || '')
                    .match(/^https?:\/\/blog\.csdn\.net\/([A-Za-z0-9_.\-]+)\/?(?:[?#].*)?$/);
                if (!m) continue;
                if (/user-info|我的主页|个人主页/.test(String(a.className || '') + t)) return m[1];
              }
              return null;
            }
            """)
            if u:
                return u, nick, "页面链接"
        except Exception:
            pass
    return None, nick, None


# 页面自检：确认这是"我自己的主页"、页签是否真的切过去了、以及是不是 404
PAGE_STATE_JS = r"""
(ids) => {
  const vis = (el) => el.getClientRects().length > 0;
  const flat = (el) => (el.innerText || '').replace(/\s+/g, ' ').trim();
  const body = (document.body.innerText || '').replace(/\s+/g, ' ');
  const out = {url: location.href, notFound: /页面不存在|你访问的页面不存在/.test(body)};
  const head = document.querySelector('.user-profile-head-info')
            || document.querySelector('.user-profile-head');
  out.head = head ? flat(head).slice(0, 200) : null;
  // 头部显示的是**昵称**，不是账号 ID（实测：URL 用账号 ID，头部写昵称），
  // 所以 ID 和昵称有一个出现就算"这是我自己的主页"。
  out.matched = (ids || []).filter(x => x && out.head && out.head.includes(x));
  out.hasProfileStats = /总访问量/.test(out.head || '') && /原创|粉丝/.test(out.head || '');
  out.onMyPage = out.matched.length > 0 || out.hasProfileStats;
  // 二级页签里哪个是选中的（active）；CSDN 用 li.sub-second-list-li 这一层
  out.activeSub = [...document.querySelectorAll('li, a, span, p')]
      .filter(el => /active|selected|current|is-active/.test(String(el.className || '')))
      .filter(el => el.getClientRects().length > 0)
      .map(flat).filter(t => t && t.length <= 10).slice(0, 12);
  out.headStats = [...document.querySelectorAll('.user-profile-statistics-name, li')]
      .map(flat).filter(t => t && t.length <= 24 && /粉丝|关注|原创|排名|总访问量/.test(t))
      .slice(0, 10);
  return out;
}
"""


def parse_head_stat(head_stats, kind):
    """从主页头部统计里读「41 粉丝」/「208 关注」这个总数。

    为什么还要它：名单接口只按 pageSize 返回，**不给服务端总数**（total 字段恒 0），
    所以「主页显示多少人」是唯一的对账基准 —— 抓到的比它少就是没抓全，必须告警。
    以前这条还是"列表页硬上限 20 条"的遮羞布（抓到 20 人≠只有 20 个粉丝）；
    现在接口能取全，它变成纯校验：对不上就说明真没取全。
    """
    want = "粉丝" if kind == "follower" else "关注"
    for t in head_stats or []:
        m = re.match(rf"^([\d,]+)\s*{want}$", str(t).strip())
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def fetch_list_api(page, me, kind, want_total, issues):
    """用站点自己的接口把「粉丝 / 关注的人」一次取全。返回 rows；不可用返回 None。

    pageSize 是唯一旋钮（没有 offset/page 参数）：先按主页总数+余量要；
    若取回来的条数**正好等于**请求条数（疑似被截断），就翻倍再要一次，直到 maxPageSize。
    接口会退避重试——短时间内高频打它会被网关在 TLS 层掐断（见文件头注释）。
    """
    if not me:
        return None
    js = FETCH_LIST_JS.replace("__TIMEOUT__", str(API_TIMEOUT_MS))

    def call(n):
        """打一次接口。返回 (rows 或 None, 错误说明)。"""
        url = f"{API_BASE}/{API_PATH[kind]}?pageSize={int(n)}&username={me}"
        err = ""
        for attempt in range(API_RETRIES + 1):
            try:
                res = page.evaluate(js, url) or {}
            except BaseException as e:
                res = {"error": f"{e.__class__.__name__}: {str(e)[:100]}"}
            rows = res.get("rows")
            if isinstance(rows, list) and int(res.get("code") or 0) == 200:
                return rows, ""
            err = str(res.get("error") or res.get("msg") or res.get("head")
                      or f"code={res.get('code')} status={res.get('status')}")[:140]
            if attempt < API_RETRIES:
                wait = API_RETRY_WAIT_MS * (attempt + 1)
                log(f"  名单接口第 {attempt + 1} 次失败（{err[:70]}），"
                    f"{wait / 1000:.1f}s 后重试…")
                page.wait_for_timeout(int(wait))
        return None, err

    size = API_MIN_SIZE
    if want_total:
        size = max(size, int(want_total) + API_HEADROOM)
    size = max(API_MIN_SIZE, min(API_MAX_SIZE, size))

    rows, err = call(size)
    if rows is None:
        issues.append((f"list-api-{kind}", f"名单接口不可用：{err}"))
        log(f"  ⚠ 名单接口不可用（{err[:90]}）")
        return None

    # 「取回条数 == 请求条数」→ 很可能被 pageSize 截断，加倍再要一次
    guard = 0
    while len(rows) >= size and size < API_MAX_SIZE and guard < 4:
        bigger = min(API_MAX_SIZE, max(size * 2, size + API_HEADROOM))
        log(f"  接口回 {len(rows)} 条 = 请求的 {size} 条，疑似被截断，扩到 {bigger} 再要一次")
        rows2, err2 = call(bigger)
        if rows2 is None:
            issues.append((f"list-api-grow-{kind}",
                           f"扩容重试（pageSize={bigger}）失败：{err2}"))
            break
        grew = len(rows2) > len(rows)
        rows, size = rows2, bigger
        guard += 1
        if not grew:
            break
    if len(rows) >= API_MAX_SIZE:
        issues.append((f"list-api-max-{kind}",
                       f"{TO_CN[kind]}接口已达上限 pageSize={API_MAX_SIZE}（回 {len(rows)} 条），"
                       f"若主页数更大说明还有没取到的"))

    log(f"  名单接口拿到 {len(rows)} 人"
        + (f"（主页显示 {want_total} 人）" if want_total is not None else "（主页总数没读到）"))
    if want_total is not None and len(rows) < want_total:
        issues.append((f"list-cap-{kind}",
                       f"{TO_CN[kind]}主页显示共 {want_total} 人，接口只返回 {len(rows)} 人"
                       f"（pageSize 已到 {API_MAX_SIZE} 上限）"))
    return rows


def collect_from_profile(page, me, kind, issues, max_pages=10, want=None, nick=None):
    """在**我自己的**个人主页拿「粉丝」或「关注的人」名单。返回 (rows, total)。

    顺序：开页面 + 自检 → 读主页总数（对账基准）→ 打名单接口（全量）→ 接口挂了才退回 DOM。

    自检那两条静默抓错的教训仍然成立、一步都不能省：
      · 用户名拿错时 URL 会退化成 `blog.csdn.net/?type=...`，那一页照样有一堆
        `blog.csdn.net/<user>` 链接（推荐作者），会被当成"我的粉丝"照单全收；
      · 所以先确认"这就是我自己的主页"，对不上就中止，绝不猜。
    """
    url = (URLS.get("fans_page") if kind == "follower" else URLS.get("follow_page")) \
        or f"https://blog.csdn.net/{{me}}?type=sub&subtype=" \
           + ("fans" if kind == "follower" else "follow")
    url = url.replace("{me}", me or "")
    tabs = (SEL.get("fans_tab") if kind == "follower" else SEL.get("following_tab")) \
        or (["粉丝"] if kind == "follower" else ["关注的人"])
    tabs = tabs if isinstance(tabs, list) else [tabs]
    want_tab = tabs[0]

    log(f"打开{TO_CN[kind]}列表页: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(int(DEF.get("listWaitMs", 9000)))
    pc.guard(page, None, f"读{TO_CN[kind]}列表", announce_only=True)

    state = page.evaluate(PAGE_STATE_JS, [x for x in (me, nick) if x])
    log(f"  页面自检: 是我的主页={state.get('onMyPage')}（命中 {state.get('matched')}）"
        f" 头部统计={state.get('headStats')}")
    if state.get("notFound"):
        issues.append((f"list-{kind}", f"主页打不开（页面 404）：{state.get('url')}"))
        log(f"  ⛔ 页面 404，判定为抓不到（用户名 {me!r} 可能不对）")
        return [], None
    if not state.get("onMyPage"):
        issues.append((f"list-{kind}",
                       f"头部既没有 {me!r} 也没有主页统计信息，可能不是我的主页，已放弃抓取"))
        log(f"  ⛔ 头部信息对不上 {me!r}，不敢当作「我的{TO_CN[kind]}」，中止本次名单抓取")
        return [], None

    total = parse_head_stat(state.get("headStats"), kind)
    if total is not None:
        log(f"  主页显示共 {total} 人（本次对账基准）")

    # ---------------- ① 接口取全量（默认路径）----------------
    api_rows = fetch_list_api(page, me, kind, total, issues)
    if api_rows is not None:
        rows, seen = [], set()
        for r in api_rows:
            u = (r.get("user") or "").strip()
            if not u or u == me or u in seen:
                continue
            seen.add(u)
            rows.append({"user": u, "name": (r.get("name") or u).strip(), "source": kind})
        issues.append((f"list-{kind}",
                       f"{TO_CN[kind]}列表共抓到 {len(rows)} 人（接口全量，接口去重后）"))
        log(f"  ✓ {TO_CN[kind]}名单：{len(rows)} 人（接口路径）")
        return rows, total

    # ---------------- ② 兜底：DOM 抓取（这个视图最多 20 条）----------------
    log(f"  ⚠ 退回页面抓取：该视图最多 20 条，发不全会告警（不静默少发）")

    def snap():
        return page.evaluate(SCRAPE_USERS_JS, me or "")

    # 页签已经在目标上就不用点；否则点一下，并要求"点完确实换了内容"
    before = {r["user"] for r in snap()["rows"]}
    if want_tab not in (state.get("activeSub") or []):
        for label in tabs:
            res = page.evaluate(CLICK_TEXT_JS, label)
            log(f"  点二级页签 {label!r} -> {res}")
            if not res.get("ok"):
                continue
            page.wait_for_timeout(int(DEF.get("tabWaitMs", 6000)))
            after = {r["user"] for r in snap()["rows"]}
            if after - before or not before:
                break
            # 页签点不动时，有些版本要先展开「关注/订阅/互动」
            if page.evaluate(CLICK_TEXT_JS, "关注/订阅/互动").get("ok"):
                log("  先展开「关注/订阅/互动」再点一次")
                page.wait_for_timeout(2500)
                page.evaluate(CLICK_TEXT_JS, label)
                page.wait_for_timeout(int(DEF.get("tabWaitMs", 6000)))
                if {r["user"] for r in snap()["rows"]} - before:
                    break
    else:
        log(f"  页面已停在「{want_tab}」页签上，直接抓")

    rows = []
    seen = set()

    def absorb(got):
        new = 0
        for r in got["rows"]:
            u = (r.get("user") or "").strip()
            if not u or u == me or u in seen:
                continue
            seen.add(u)
            r["user"] = u
            r["source"] = kind
            rows.append(r)
            new += 1
        return new

    absorb(snap())
    log(f"  第 1 页拿到 {len(rows)} 人")

    page_no = 1
    while page_no < max_pages:
        grew = 0
        # ① 先滚到底（懒加载）
        for _ in range(int(DEF.get("scrollRounds", 3))):
            before_n = len(rows)
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
            absorb(snap())
            if len(rows) > before_n:
                grew += len(rows) - before_n
        # ② 再试翻页按钮
        if page.evaluate(CLICK_NEXT_JS):
            page_no += 1
            page.wait_for_timeout(int(DEF.get("pageWaitMs", 4000)))
            before_n = len(rows)
            absorb(snap())
            new = len(rows) - before_n
            log(f"  第 {page_no} 页新增 {new} 人（累计 {len(rows)}）")
            if new == 0 and grew == 0:
                break
        else:
            break
        if want and len(rows) >= want:
            break
    issues.append((f"list-{kind}", f"{TO_CN[kind]}列表共抓到 {len(rows)} 人（DOM 兜底路径）"))
    return rows, total


def collect_recipients(page, b, targets, issues, max_pages=10, want=None):
    """抓齐所有目标名单。返回 (合并去重后的名单, 我的ID, 各来源主页总数, 各来源实际抓到数)。

    去重分两层，都在这里做掉：
      ① 单个来源内部：一个人可能在接口返回里重复（罕见，但按用户名兜住）；
      ② 来源之间：「关注的人」和「粉丝」大面积重叠（实测 208 关注 + 41 粉丝 = 210 人，
         重叠 39 个）——**同一个人只发一次**，否则他会收到两条一样的私信。
    """
    me, nick, how = my_identity(b, page)
    if not me:
        issues.append(("no-username", "解析不出自己的账号 ID，已放弃抓名单（不猜）"))
        log("⛔ 解析不出自己的用户名 —— 不猜、不抓，避免把别人的粉丝当成你的")
        return None, None, {}, {}
    log(f"我的账号 ID: {me}（昵称 {nick or '—'}，来源: {how}）")
    out = []
    seen = set()
    totals = {}
    per_kind = {}
    for kind in targets:
        rows, total = collect_from_profile(page, me, kind, issues, max_pages=max_pages,
                                          want=want, nick=nick)
        totals[kind] = total
        per_kind[kind] = len(rows)      # ★ 按"这一来源抓到的原始条数"记，供对账
        if total is not None and len(rows) < total:
            note = (f"{TO_CN[kind]}主页显示共 {total} 人，只抓到 {len(rows)} 人"
                    f"（接口已按上限扩过 pageSize；抓不全就别发，避免漏人）")
            issues.append((f"list-cap-{kind}", note))
            log(f"  ⚠ {note}")
        for r in rows:
            if r["user"] in seen:
                continue          # 跨来源去重：同一个人只发一次
            seen.add(r["user"])
            out.append(r)
    return out, me, totals, per_kind


# ============================================================ 发送
CHAT_STATE_JS = r"""
(keywords) => {
  const vis = (el) => el.getClientRects().length > 0;
  const ta = document.querySelector('textarea#messageText')
          || document.querySelector('.send-msg-box textarea')
          || document.querySelector('textarea');
  const value = ta ? String(ta.value || '') : null;
  const box = document.querySelector('.chatMsg') || document.querySelector('.rightContent');
  const items = box ? [...box.querySelectorAll('.msg-item')] : [];
  const texts = items.map(e => (e.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
  // 比对前把空白全部去掉：消息里可能带换行/空格，而标记是压掉空白取的
  // ★ 命中窗口放宽到最后 12 条：我的气泡后可能还跟着对方/系统的气泡，只盯最后 3~4 条会漏。
  const tail = texts.slice(-12).map(t => t.replace(/\s+/g, ''));
  const hit = keywords.some(k => k && tail.some(t => t.includes(k)));
  const hitAny = keywords.some(k => k && texts.some(t => t.includes(k)));
  // 弹窗 / toast 文案
  const notices = [...document.querySelectorAll(
      '.el-message, .el-notification, .el-message-box, .el-message-box__wrapper')]
      .filter(vis).map(el => (el.innerText || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
  // 会话内的系统提示（如「24小时内最多与5个陌生人发送消息」）——它不是 toast，
  // 而是消息流里的一个系统气泡，必须单独抓，否则会漏判成"发送成功"。
  // ★ 2026-10-10 修正：这里**不再做关键词过滤**，原样返回全部文本；
  //   哪些是"本次新出现的"由 Python 侧跟发送前的基线做差集来判断。
  //   原因：会话里残留着**上一批群发留下的历史系统提示**，以前直接拿它当"本次被拦"，
  //   结果把 7 个明明收到消息的人全判成 failed_dm_limit（用户当场核对发现全送到了）。
  const sysAll = [...document.querySelectorAll(
      '.pure-text, .system-prompt, .system-msg, .msg-system')]
      .filter(vis).map(el => (el.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(t => t && t.length <= 200);
  const header = document.querySelector('.chatHeader .user-name');
  return {value: value, count: texts.length, last: texts.slice(-3), hit: hit, hitAny: hitAny,
          notices: notices.slice(0, 3), sysAll: sysAll,
          peer: header ? header.innerText.trim() : null,
          url: location.href};
}
"""


def snippet_of(text, n=14):
    """取一段用于"这条消息到底发出去没有"的比对片段（去掉空白，避开链接化差异）。"""
    flat = "".join(text.split())
    return flat[:n] if flat else ""


def markers_of(text):
    """本次私信的"身份证"——用来判断「这条到底在不在会话里」。

    优先取正文里的文章 ID（`/details/<id>`），取不到才退回开头 14 字。

    ★ 为什么换成文章 ID（2026-10-10）：群发正文是**同一份模板**，靠开头几个字没法区分
    "这次发的"和"上次发的"；而文章 ID 天然标识批次。更重要的是——这和复核脚本
    `_probe_delivery.py` 的口径完全一致：**它能验证谁真收到了，发送判定就必须用同一把尺子**。
    """
    ids = list(dict.fromkeys(re.findall(r"/details/(\d+)", text)))
    if ids:
        return ids
    return [snippet_of(text)]


def new_sys_notices(before, st):
    """本次**新出现**的系统提示 = 发送后的气泡 - 发送前的气泡（按条数做差，不是按文案去重）。

    为什么必须用差集：会话里留着上一批群发的历史系统提示（「24小时内最多与5个陌生人」这种），
    直接扫全会话会把"收到消息的人"判成被拦（2026-10-10 实测 7/7 误判）。
    """
    return list((Counter(st.get("sysAll") or [])
                 - Counter(before.get("sysAll") or [])).elements())


def judge_sent(before, st, markers, settled):
    """判定一次轮询的结果（纯函数，方便离线测试）。

    返回 (ok, status, detail)；返回 None = 这次还没结论，继续等下一轮。

    主判据：**本次标记在不在会话里**（和 `_probe_delivery.py` 复核口径一致）。
    只有当标记没命中时，才去看"本次新出现"的系统提示。
    """
    before_count = int(before.get("count") or 0)
    before_hit = bool(before.get("hit"))
    grew = int(st.get("count") or 0) > before_count
    # ① 标记命中，且能证明它是这次新加进来的（条数变多 / 发送前压根没有这个标记）
    if st.get("hit") and (grew or not before_hit):
        if not settled:
            return None       # 还没沉淀够时间，乐观渲染可能骗人，再等等
        return True, "sent", (f"已发送并在会话里复核到本条"
                              f"（会话 {before_count}→{st.get('count')} 条，"
                              f"命中标记 {markers[:2]}，输入框={st.get('value')!r}）")
    # ② 标记没命中 —— 才看本次新出现的提示
    joined = " | ".join((st.get("notices") or []) + new_sys_notices(before, st))
    if joined:
        if any(k in joined for k in DM_LIMIT_HINTS):
            return False, "failed_dm_limit", (
                f"平台限额拦下，这条**没送到**（会话里也查不到本条）：{joined[:160]}")
        if any(k in joined for k in BLOCK_HINTS):
            kind = "freq" if any(k in joined for k in FREQ_HINTS) else "block"
            return False, f"failed_{kind}", f"平台提示: {joined[:160]}"
    return None


def judge_final(before, last, markers, bsel="?"):
    """等到截止还没结论时的兜底判定（纯函数）。"""
    before_count = int(before.get("count") or 0)
    before_hit = bool(before.get("hit"))
    new_sys = new_sys_notices(before, last)
    if new_sys:
        return False, "failed_block", f"发送后出现新的系统提示: {' | '.join(new_sys)[:160]}"
    if last.get("hit") and before_hit and int(last.get("count") or 0) <= before_count:
        return True, "sent_unverified", ("会话里能查到本条内容，但**条数没变化**，"
                                        "无法确认是这次发的新消息（可能本来就有同样的内容），"
                                        "请人工瞄一眼这条会话")
    if last.get("hit"):
        return True, "sent", (f"已发送并在会话里复核到本条"
                              f"（会话共 {last.get('count')} 条，输入框={last.get('value')!r}）")
    if last.get("value") == "":
        return True, "sent_unverified", ("输入框已清空，但**没在会话里比对到本条**"
                                        "（可能是乐观渲染后没落地），请人工瞄一眼这条会话")
    return False, "failed_unknown", (f"点了发送但输入框里还有内容，疑似没发出去"
                                     f"（按钮={bsel}，提示={last.get('notices')}）")


def send_one(page, b, user, text, markers, dry=False):
    """给一个人发私信。返回 (ok, status, detail)。

    ★ 判定口径（2026-10-10 重写 —— 以前那套把 7 个收到消息的人全判成"被拦"，实锤误判）：
      旧口径 = 「会话里有没有系统拦截文案」→ 错。那些文案是**上一批群发残留在会话里的历史提示**，
               本次明明发出去了也会被判 failed_dm_limit。
      新口径 = 「**本次正文标记在不在会话里**」，和复核脚本 `_probe_delivery.py` 用同一把尺子：
               ① 发之前先留基线（会话条数 / 是否已有该标记 / 已有哪些系统提示）；
               ② 沉淀后标记仍命中、且（条数变多了 或 发之前压根没有这个标记）→ 送达；
               ③ 只有在"标记没命中"时，才去看**本次新出现**的系统提示（跟基线做差集）。
    """
    url = (URLS.get("chat") or "https://im.csdn.net/chat/{user}").replace("{user}", user)
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(int(DEF.get("chatWaitMs", 7000)))
    pc.guard(page, b, f"私信 {user}")

    loc, sel = pc.find_visible(page, SEL.get("chat_input") or [
        "textarea#messageText", ".send-msg-box textarea", "textarea.el-textarea__inner", "textarea",
    ], 15000, "私信输入框")
    if not loc:
        return False, "failed_no_input", "找不到私信输入框（页面结构可能变了，跑 inspect 校准）"

    # 发送前的基线快照：会话条数、是否已存在本次标记、已经有哪些系统提示。
    # 没有它就没法区分"本次新出现的拦截提示"和"上一批残留在会话里的历史提示"。
    try:
        before = page.evaluate(CHAT_STATE_JS, markers)
    except Exception as e:
        before = {}
        log(f"  （取发送前基线失败，降级为无基线判定: {e.__class__.__name__}）")
    log(f"  发送前基线：会话 {before.get('count')} 条，已含本条标记={bool(before.get('hit'))}，"
        f"历史系统提示 {len(before.get('sysAll') or [])} 条")

    # 清空历史残留，再填正文
    try:
        loc.click()
        loc.fill("")
        page.wait_for_timeout(300)
        loc.fill(text)
    except Exception as e:
        return False, "failed_fill", f"写入输入框失败: {e.__class__.__name__}: {str(e)[:120]}"
    page.wait_for_timeout(600)

    if dry:
        return True, "dry_run", f"已填入输入框但未点发送（选择器命中 {sel}）"

    btn, bsel = pc.find_visible(page, SEL.get("send_button") or [
        ".send button", "div.send button", "button:has-text('发送')",
    ], 8000, "发送按钮")
    if btn:
        btn.click()
    else:
        loc.press("Enter")      # 页面提示「按下Enter发送内容」
        bsel = "Enter"

    # 等结果。
    # ⚠ 不能"看到气泡就判成功"：实测 CSDN 会先**乐观渲染**出气泡（当场能看到本条原文），
    # 几秒后被风控拦下时，气泡会被**替换成一条系统提示**（2026-10-08 踩过）。
    # 所以仍然要等一个"沉淀时间"再看最终状态。
    # ★ 但"看到系统提示就判失败"同样是错的（2026-10-10 踩过）：那些提示多半是**上一批群发
    #   残留在会话里的**，本次明明发出去了也会被判 failed_dm_limit。
    #   现在主判据是"本次标记在不在会话里"，系统提示只看**本次新增的**（跟基线做差集）。
    settle = float(DEF.get("sendSettleSec", 3500)) / 1000
    started = time.time()
    settled_at = started + settle
    deadline = started + max(float(DEF.get("sendVerifySec", 12)), settle)
    last = {}
    while time.time() < deadline:
        page.wait_for_timeout(500)
        st = page.evaluate(CHAT_STATE_JS, markers)
        last = st
        got = judge_sent(before, st, markers, settled=time.time() >= settled_at)
        if got:
            return got
    return judge_final(before, last, markers, bsel)


# ============================================================ 存储
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id      TEXT PRIMARY KEY,
  started     TEXT,
  mode        TEXT,
  target      TEXT,
  limit_n     TEXT,
  msg_file    TEXT,
  msg_hash    TEXT,
  msg_len     INTEGER,
  planned     INTEGER,
  sent        INTEGER,
  failed      INTEGER,
  note        TEXT
);
CREATE TABLE IF NOT EXISTS sends (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    TEXT,
  ts        TEXT,
  user      TEXT,
  name      TEXT,
  source    TEXT,
  status    TEXT,
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sends_user ON sends(user);
CREATE INDEX IF NOT EXISTS idx_sends_run ON sends(run_id);
"""


def db_open():
    MSG_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.executescript(SCHEMA)
    con.commit()
    return con


def db_record(con, run_id, rec):
    con.execute(
        "INSERT INTO sends (run_id, ts, user, name, source, status, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        (run_id, _now().strftime("%Y-%m-%d %H:%M:%S"), rec["user"], rec.get("name", ""),
         rec.get("source", ""), rec["status"], rec.get("detail", "")[:400]))
    con.commit()


def db_run(con, **kw):
    cols = ("run_id", "started", "mode", "target", "limit_n", "msg_file", "msg_hash",
            "msg_len", "planned", "sent", "failed", "note")
    vals = [kw.get(c) for c in cols]
    con.execute(f"INSERT OR REPLACE INTO runs ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", vals)
    con.commit()


def db_sent_users(con):
    """以前**成功发过**私信的人 → 最后一次成功时间。给 --skip-sent 做跨运行去重。

    只认 status 以 sent 开头的（sent / sent_unverified / sent_on_retry）：
    被限额拦下、找不到输入框那些不算"发过"，下次还该重试。
    """
    try:
        return {r[0]: r[1] for r in con.execute(
            "SELECT user, MAX(ts) FROM sends WHERE status LIKE 'sent%' GROUP BY user")}
    except Exception:
        return {}


def write_failed_manifest(run_id, rows):
    """把这次「没送到」的人单独存一份（output/message/failed.json）。

    怎么用：下次跑 `--limit all --yes --skip-sent` 就只补这批——
    --skip-sent 会把"已经成功发过"的人剔掉，剩下的正好是这次没送到的。
    """
    unsent = [{"user": r["user"], "name": r.get("name", ""), "source": r.get("source", ""),
               "status": r.get("status", ""), "detail": str(r.get("detail", ""))[:200]}
              for r in rows
              if not str(r.get("status", "")).startswith("sent")
              and r.get("status") not in ("dry_run", "pending")]
    if not unsent:
        return None
    p = MSG_DIR / "failed.json"
    try:
        p.write_text(json.dumps({"created": _now().isoformat(), "run_id": run_id,
                                 "users": unsent}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        log(f"未送达清单: {p}（{len(unsent)} 人，可用来补发）")
        return p
    except Exception as e:
        log(f"（未送达清单写失败: {e.__class__.__name__}）")
        return None


def cmd_stats():
    if not DB_PATH.exists():
        print(f"还没有发送记录（{DB_PATH} 不存在）。先跑一次 --wait 或 --yes。")
        return 0
    con = db_open()
    print("=" * 72)
    print("CSDN 私信发送记录")
    print("=" * 72)
    rows = con.execute(
        "SELECT run_id, started, mode, target, limit_n, planned, sent, failed "
        "FROM runs ORDER BY started DESC LIMIT 20").fetchall()
    if not rows:
        print("（没有任何一次运行的记录）")
    for r in rows:
        print(f"[{r[1]}] {r[2]:8} to={r[3]:9} limit={r[4]:5} "
              f"计划 {r[5]} / 成功 {r[6]} / 失败 {r[7]}")
    print("-" * 72)
    print("按收件人累计（最近 30 人）：")
    for r in con.execute(
            "SELECT user, name, COUNT(*), MAX(ts), "
            "SUM(CASE WHEN status LIKE 'sent%' THEN 1 ELSE 0 END) "
            "FROM sends GROUP BY user ORDER BY MAX(ts) DESC LIMIT 30"):
        print(f"  {r[0]:24} {r[1][:12]:14} 共 {r[2]} 次，最后一次 {r[3]}，成功 {r[4]}")
    print("-" * 72)
    n = con.execute("SELECT COUNT(DISTINCT user) FROM sends").fetchone()[0]
    m = con.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
    print(f"累计触达 {n} 个不同用户，共 {m} 条发送记录")
    con.close()
    return 0


def write_report(con, run_id, meta, rows, issues):
    MSG_DIR.mkdir(parents=True, exist_ok=True)

    def cell(s, n):
        """表格单元格：转义 | 和换行，否则平台提示里的「 | 」会把这一行拆成好几列。"""
        t = str(s if s is not None else "").replace("|", "\\|")
        t = " ".join(t.split())
        return t[:n]

    lines = [f"# CSDN 私信 · {meta.get('started', '')}", ""]
    lines.append(f"- 模式：`{meta.get('mode')}`")
    lines.append(f"- 对象：{meta.get('target_cn', meta.get('target'))}")
    lines.append(f"- 名额：{meta.get('limit_n')}（实际名单 {meta.get('planned')} 人）")
    lines.append(f"- 正文：`{meta.get('msg_file')}`（{meta.get('msg_len')} 字，"
                 f"hash {str(meta.get('msg_hash'))[:10]}）")
    if meta.get("mode") in ("yes",):
        lines.append(f"- 结果：成功 **{meta.get('sent')}** / 失败 **{meta.get('failed')}**")
    lines.append("")
    lines.append("## 名单与结果")
    lines.append("")
    lines.append("| # | 用户名 | 昵称 | 来源 | 状态 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        lines.append(f"| {i} | `{cell(r['user'], 40)}` | {cell(r.get('name', ''), 16)} | "
                     f"{TO_CN.get(r.get('source'), r.get('source', ''))} | "
                     f"{cell(r.get('status', ''), 24)} | {cell(r.get('detail', ''), 80)} |")
    # 没送到的人单独列一遍：方便下次 `--limit all --yes --skip-sent` 只补这批
    unsent = [r for r in rows
              if not str(r.get("status", "")).startswith("sent")
              and r.get("status") not in ("dry_run", "pending")]
    if unsent and meta.get("mode") == "yes":
        lines.append("")
        lines.append(f"## 未送达清单（{len(unsent)} 人）")
        lines.append("")
        lines.append("| # | 用户名 | 昵称 | 状态 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for i, r in enumerate(unsent, 1):
            lines.append(f"| {i} | `{cell(r['user'], 40)}` | {cell(r.get('name', ''), 16)} | "
                         f"{cell(r.get('status', ''), 24)} | {cell(r.get('detail', ''), 100)} |")
        lines.append("")
        lines.append("> 补发这批：`--limit all --yes --skip-sent`"
                     "（--skip-sent 会跳过已经成功发过的人，剩下的就是这批）。"
                     "完整清单见 `output/message/failed.json`。")
    if issues:
        lines.append("")
        lines.append("## 过程记录")
        for k, v in issues:
            lines.append(f"- `{k}`: {v}")
    lines.append("")
    lines.append("> 名单来源：CSDN 名单接口（`getFansOffsetList` / `getFollowOffsetList`，"
                 "pageSize 调大一次取全）；"
                 "接口不可用时才退回个人主页「关注/订阅/互动」DOM 抓取（那个视图最多 20 条）。")
    lines.append("> 发送走的是 CSDN 私信页 `https://im.csdn.net/chat/<用户名>`，"
                 "正文原样取 `templates/message.txt`。")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    log(f"报告: {REPORT_PATH}")
    return REPORT_PATH


# ============================================================ 主流程
def parse_to(s):
    if not s:
        return None, ("必须用 --to 指定发给谁：`following`（我关注的人）/ `follower`（我的粉丝）"
                      "/ 两个都发就写 `following,follower`。\n"
                      "  例：--to follower --limit 20 --wait")
    kinds, bad = [], []
    for part in str(s).replace("，", ",").replace("+", ",").replace("、", ",").split(","):
        p = part.strip().lower()
        if not p:
            continue
        k = TO_ALIAS.get(p) or TO_ALIAS.get(part.strip())
        if not k:
            bad.append(part.strip())
            continue
        if k == "both":
            kinds = ["following", "follower"]
        elif k not in kinds:
            kinds.append(k)
    if bad:
        return None, (f"看不懂 --to 里的这些值: {bad}\n"
                      "  只能写 following / follower / following,follower")
    if not kinds:
        return None, "必须用 --to 指定发给谁（following / follower / following,follower）"
    kinds = [k for k in ("following", "follower") if k in kinds]
    return kinds, None


def parse_limit(s):
    if s is None:
        return None, ("必须用 --limit 指定这次发几个人：`20` 或 `all`。\n"
                      "  例：--to follower --limit 20 --wait")
    t = str(s).strip().lower()
    if t in ("all", "全部", "不限", "0"):
        return "all", None
    try:
        n = int(t)
    except ValueError:
        return None, f"--limit 只能是数字或 all，收到 {s!r}"
    if n <= 0:
        return None, f"--limit 必须大于 0，收到 {n}"
    return n, None


def pending_key(targets, limit, msg_hash, skip_sent=False):
    """--wait 存名单时的"条件指纹"。--skip-sent 也算条件：
    它会把"以前发过的人"剔掉，名单就不一样了，必须让 --yes 重新抓、而不是复用旧名单。"""
    return {"targets": targets, "limit": str(limit), "msg_hash": msg_hash,
            "skip_sent": bool(skip_sent)}


def load_pending(key):
    """--yes 时优先复用 --wait 存下的那份名单，保证"你看过的就是发出去的"。"""
    if not PENDING_PATH.exists():
        return None, "没有 --wait 存下的名单"
    try:
        obj = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"pending.json 读不了（{e.__class__.__name__}），将重新抓名单"
    ttl = float(MCFG.get("pendingTtlMin", 120))
    try:
        age_min = (_now() - datetime.fromisoformat(obj["created"])).total_seconds() / 60
    except Exception:
        age_min = 0
    if age_min > ttl:
        return None, f"pending.json 已过期（{age_min:.0f} 分钟 > {ttl:.0f} 分钟），将重新抓名单"
    if obj.get("key") != key:
        return None, "pending.json 的条件（对象/名额/正文）和这次不一致，将重新抓名单"
    return obj, None


def main():
    ap = argparse.ArgumentParser(
        description="CSDN 私信群发（给「我关注的人 / 我的粉丝」发同一条私信）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", metavar="KIND",
                    help="发给谁：following（我关注的人）/ follower（我的粉丝）/ "
                         "following,follower（两个都发）。中文写法也认（粉丝、关注的人）")
    ap.add_argument("--limit", metavar="N",
                    help="这次最多发几个人：数字（如 20）或 all（全部）")
    ap.add_argument("--wait", action="store_true",
                    help="只抓名单给你看并存下来，不发任何私信（确认后再用 --yes 发）")
    ap.add_argument("--yes", action="store_true",
                    help="直接按名单发送（会优先用 --wait 存下的那份名单）")
    ap.add_argument("--file", type=Path, default=None,
                    help=f"私信正文文件（默认 {DEFAULT_FILE}）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只演练：把正文填进输入框并截图，但绝不点发送")
    ap.add_argument("--stats", action="store_true", help="只看历史发送记录，不发私信")
    ap.add_argument("--skip-sent", action="store_true",
                    help="跳过「以前已经成功发过私信」的人（跨多次运行去重）。"
                         "想只补发上次没送到的，就用：--limit all --yes --skip-sent")
    ap.add_argument("--max-pages", type=int, default=int(DEF.get("maxPages", 10)),
                    help="名单最多翻几页（默认 10；只在接口不可用、退回 DOM 抓取时才有意义）")
    pc.add_robustness_args(ap, with_crash=False)
    args = ap.parse_args()

    if args.stats:
        return cmd_stats()

    # ---------------- 硬闸门：不写 --yes / --wait 就什么都不发 ----------------
    # --dry-run 单独用是允许的：它只把正文填进输入框并截图，**绝不点发送**，
    # 所以不违反"没明确表态就不发"的原则（这也是校准选择器的标准手段）。
    if not (args.yes or args.wait or args.dry_run):
        print("⛔ 没有指定发送方式，本次不执行任何私信操作。\n")
        print("  二选一（--dry-run 只演练、不发送，可以单独用）：")
        print("    --wait     只抓名单给你确认（不发私信）")
        print("    --yes      直接按名单发送")
        print("    --dry-run  只把正文填进输入框并截图，不点发送")
        print("\n  例：python csdn_message.py --to follower --limit 20 --wait")
        return 2
    if args.yes and args.wait:
        print("⛔ --yes 和 --wait 只能选一个。")
        return 2

    targets, err = parse_to(args.to)
    if err:
        print(f"⛔ {err}")
        return 2
    limit, err = parse_limit(args.limit)
    if err:
        print(f"⛔ {err}")
        return 2

    msg_file = args.file or (ROOT / DEFAULT_FILE)
    if not Path(msg_file).is_absolute():
        msg_file = ROOT / msg_file
    msg_file = Path(msg_file)
    text, err = load_message_text(msg_file)
    if err:
        print(f"⛔ {err}")
        return 2
    msg_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    log(f"私信正文: {msg_file}（{js_len(text)} 字，hash {msg_hash}）")
    log(f"正文预览: {text[:60]!r}{'…' if len(text) > 60 else ''}")

    mode = "wait" if args.wait else ("dry" if args.dry_run else "yes")
    run_id = f"{_now():%Y%m%d-%H%M%S}-{mode}"
    issues = []
    con = db_open()
    meta = {"started": _now().strftime("%Y-%m-%d %H:%M:%S"), "mode": mode,
            "target": ",".join(targets), "target_cn": "+".join(TO_CN[t] for t in targets),
            "limit_n": str(limit), "msg_file": str(msg_file.relative_to(ROOT))
            if str(msg_file).startswith(str(ROOT)) else str(msg_file),
            "msg_hash": msg_hash, "msg_len": js_len(text), "planned": 0, "sent": 0, "failed": 0}

    with sync_playwright() as pw:
        pc.apply_cli_overrides(args)
        b = launch_browser(pw, issues, clean_first=not args.no_clean_profile)
        if not b:
            print("⛔ 浏览器起不来，什么都没发。详见上面的日志。")
            return 1
        try:
            logged, hits = b.is_logged_in()
            if not logged:
                print("⛔ 登录态失效。请先跑：.\\csdn.bat login（要扫码，只能你本人做）")
                return 1
            log(f"登录态有效: {hits}")

            page = b.page()
            pc.guard(page, b, "私信首页")

            key = pending_key(targets, limit, msg_hash, skip_sent=args.skip_sent)
            pending = None
            caps = []
            if mode == "yes":
                pending, why = load_pending(key)
                if pending:
                    log(f"✓ 使用 --wait 时确认过的名单（{len(pending['users'])} 人，"
                        f"存于 {pending['created'][:16]}）")
                else:
                    log(f"（{why}）")

            if pending:
                users = pending["users"]
                caps = pending.get("caps") or []
            else:
                users, me, totals, per_kind = collect_recipients(page, b, targets, issues,
                                                                 max_pages=args.max_pages)
                if users is None:
                    print("⛔ 拿不到自己的账号 ID / 列表页不可信，本次什么都没发。详见上面日志。")
                    write_report(con, run_id, meta, [], issues)
                    return 1
                raw_n = sum(per_kind.values())
                breakdown = " + ".join(f"{TO_CN[k]} {per_kind.get(k, 0)}"
                                       for k in targets)
                log(f"名单抓取完成：{breakdown} = {raw_n} 人，"
                    f"按用户名去重后 {len(users)} 人")
                issues.append(("list-summary",
                               f"{breakdown} = {raw_n} 人，跨来源按用户名去重后 {len(users)} 人"))
                # ★ 对账用 per_kind（各来源**抓到的原始条数**），不能用 users 里的计数：
                #   users 已经跨来源去过重，粉丝若大半也在「关注的人」里，这里会只剩个位数，
                #   会把"其实抓全了"误报成"没抓全"。
                caps = [f"{TO_CN[k]} 主页显示 {v} 人，只抓到 {per_kind.get(k, 0)} 人"
                        for k, v in (totals or {}).items()
                        if v is not None and v > (per_kind.get(k) or 0)]
                if caps:
                    log("⚠ " + "；".join(caps))

            # ---- 去重 ①：跨运行去重（可选，--skip-sent）----
            # 「以前已经成功发过的人」剔掉。想只补发上次没送到的，就是 --limit all --yes --skip-sent。
            if args.skip_sent and users:
                sent_before = db_sent_users(con)
                if sent_before:
                    keep = [r for r in users if r["user"] not in sent_before]
                    skipped = len(users) - len(keep)
                    if skipped:
                        log(f"--skip-sent：跳过 {skipped} 个以前已成功发过私信的人，"
                            f"剩 {len(keep)} 人")
                        issues.append(("skip-sent",
                                       f"跳过 {skipped} 个以前已成功发过的人（剩 {len(keep)} 人）"))
                    users = keep
                else:
                    log("--skip-sent：历史记录里没有「已成功发过」的人，不用跳过")

            # ---- 名额裁剪：先剔重、再截前 N（这样 --limit 20 拿到的是 20 个"该发的人"）----
            if limit != "all" and len(users) > int(limit):
                log(f"按 --limit {limit} 取前 {limit} 人（其余 {len(users) - int(limit)} 人这次不发）")
                users = users[:int(limit)]

            if not users:
                msg = ("名单是空的，没有任何人可以发。可能原因：\n"
                       "  · 你的粉丝数 / 关注数本来就是 0（看你主页上的数字）；\n"
                       "  · 名单接口没通、页面结构变了（人工开一次页面确认）；\n"
                       "  · 加了 --skip-sent，而这些人以前都已经成功发过了；\n"
                       "  · 登录态失效。\n"
                       "  ⚠ 我宁可报空，也不会拿别的页面上的用户凑数。")
                print(f"⛔ {msg}")
                issues.append(("empty-list", msg.replace("\n", " ")))
                write_report(con, run_id, meta, [], issues)
                return 1

            meta["planned"] = len(users)
            if caps:
                print()
                print("⚠ 名单没抓全（抓到多少就发多少，不会拿别人凑数）：")
                for c in caps:
                    print(f"    · {c}")
                print("    · 这种时候建议先别群发，人工核对一下再说。")
            if mode == "wait":
                MSG_DIR.mkdir(parents=True, exist_ok=True)
                PENDING_PATH.write_text(json.dumps(
                    {"created": _now().isoformat(), "key": key, "users": users, "caps": caps},
                    ensure_ascii=False, indent=2), encoding="utf-8")
                print()
                print("=" * 72)
                print(f"待发名单（{len(users)} 人）—— 本次【没有发送任何私信】")
                print("=" * 72)
                for i, r in enumerate(users, 1):
                    print(f"  {i:>3}. {r['user']:<26} {r.get('name', '')[:18]:<20} "
                          f"[{TO_CN.get(r['source'], r['source'])}]")
                print("=" * 72)
                print(f"正文（{js_len(text)} 字）：")
                print("-" * 72)
                print(text)
                print("-" * 72)
                print(f"名单已存: {PENDING_PATH}")
                print(f"确认后执行：python csdn_message.py --to {','.join(targets)} "
                      f"--limit {limit} --yes" + (" --skip-sent" if args.skip_sent else ""))
                print("=" * 72)
                for r in users:
                    r["status"] = "pending"
                db_run(con, run_id=run_id, started=meta["started"], mode=mode,
                       target=meta["target"], limit_n=meta["limit_n"],
                       msg_file=meta["msg_file"], msg_hash=msg_hash, msg_len=meta["msg_len"],
                       planned=len(users), sent=0, failed=0, note="只列名单，未发送")
                write_report(con, run_id, meta, users, issues)
                return 0

            # ---------------- 真发 ----------------
            print()
            print("=" * 72)
            print(f"即将给 {len(users)} 人发私信（{'演练，不发送' if mode == 'dry' else '真实发送'}）")
            for i, r in enumerate(users, 1):
                print(f"  {i:>3}. {r['user']:<26} {r.get('name', '')[:18]:<20} "
                      f"[{TO_CN.get(r['source'], r['source'])}]")
            print("=" * 72)

            # 本次正文的"身份证"（文章 ID 优先），发送判定与复核脚本用同一把尺子
            markers = markers_of(text)
            log(f"本次判定标记: {markers}")
            dmin = float(DEF.get("delayMinSec", 5))
            dmax = float(DEF.get("delayMaxSec", 12))
            streak_max = int(DEF.get("limitStreak", 3))
            # 失败重试策略（别乱重试，按失败原因分开处理）：
            #   · failed_dm_limit / failed_freq / failed_block = 平台明说了不给你发，
            #     重试没有意义，还可能把风控坐实 → **不重试**；
            #   · failed_unknown / failed_no_input / failed_fill = 原因不明（页面没渲染完、
            #     输入框没就绪之类，多为瞬时）→ 隔久一点**重试一次**。
            retry_transient = bool(DEF.get("retryTransientOnce", True))
            retry_wait_s = float(DEF.get("retryWaitSec", 8))
            sent = failed = 0
            limit_streak = 0
            for i, r in enumerate(users, 1):
                if not b.alive():
                    issues.append(("browser-died", f"发到第 {i}/{len(users)} 人时浏览器没了"))
                    log("⛔ 浏览器中途关闭，剩下的不发了（已发的都记在报告里）")
                    for rest in users[i - 1:]:
                        rest["status"] = "not_attempted"
                        rest["detail"] = "浏览器中途关闭，未尝试"
                    break
                ok, status, detail = send_one(page, b, r["user"], text, markers,
                                              dry=(mode == "dry"))
                if not ok and mode != "dry" and retry_transient \
                        and status in RETRYABLE_STATUS:
                    log(f"  ↻ {r['user']} 失败原因不明确（{status}），{retry_wait_s:.0f}s 后重试一次")
                    page.wait_for_timeout(int(retry_wait_s * 1000))
                    ok2, status2, detail2 = send_one(page, b, r["user"], text, markers)
                    detail = f"{detail} / 重试后：{detail2}"
                    if ok2:
                        ok, status = True, (status2 if status2.startswith("sent")
                                            else "sent_on_retry")
                    else:
                        status = status2
                r["status"] = status
                r["detail"] = detail
                db_record(con, run_id, r)
                # 截图留证：演练 / 第一条 / 失败的那条
                if mode == "dry" or i == 1 or not ok:
                    try:
                        b.shot(page, f"message-{i}-{r['user']}"[:80])
                    except Exception:
                        pass
                if ok:
                    sent += 1
                    limit_streak = 0
                    log(f"[{i}/{len(users)}] ✓ {r['user']} {status} — {detail}")
                else:
                    failed += 1
                    log(f"[{i}/{len(users)}] ✗ {r['user']} {status} — {detail}")
                    if status == "failed_dm_limit":
                        # 陌生人限额是**按"对方跟你有没有历史会话"**判的，所以它不该一命中就全停：
                        # 还有一大批有历史会话的人能正常收到。但连续命中说明今天的额度确实没了。
                        limit_streak += 1
                        issues.append((status, f"{r['user']}: {detail}"))
                        if limit_streak >= streak_max:
                            log(f"  连续 {limit_streak} 个私信限额，继续试下去大概率也是白试，停止本轮")
                            for rest in users[i:]:
                                rest["status"] = "stopped_stranger_limit"
                                rest["detail"] = (f"连续 {limit_streak} 人被私信限额拦下，"
                                                  f"主动停止（陌生人限额 24 小时后重置；「对方尚未回复」则要等对方回你）")
                            break
                        log(f"  这是私信限额（第 {limit_streak}/{streak_max} 次），继续试下一个")
                    elif "freq" in status or "block" in status:
                        issues.append((status, f"{r['user']}: {detail}"))
                        log("  平台在拦，停止本次群发（避免把风控坐实）")
                        for rest in users[i:]:
                            rest["status"] = "stopped_platform"
                            rest["detail"] = f"上一条被平台拦下（{detail[:60]}），主动停止"
                        break
                if i < len(users):
                    gap = random.uniform(dmin, dmax)
                    log(f"  等 {gap:.1f} 秒再发下一个…")
                    page.wait_for_timeout(int(gap * 1000))
            meta["sent"] = sent
            meta["failed"] = failed
            if mode == "dry":
                meta["note"] = "演练模式：填入输入框但未点发送"
            if mode != "dry":
                write_failed_manifest(run_id, users)
            db_run(con, run_id=run_id, started=meta["started"], mode=mode, target=meta["target"],
                   limit_n=meta["limit_n"], msg_file=meta["msg_file"], msg_hash=msg_hash,
                   msg_len=meta["msg_len"], planned=len(users), sent=sent, failed=failed,
                   note=meta.get("note", ""))
            write_report(con, run_id, meta, users, issues)
            print()
            print("=" * 72)
            print(f"完成：成功 {sent} / 失败 {failed} / 名单 {len(users)}")
            if mode != "dry" and failed:
                print(f"没送到的 {failed} 人已单独记下（output/message/failed.json）；"
                      f"补发用：--limit all --yes --skip-sent")
            print(f"报告: {REPORT_PATH}")
            print("=" * 72)
            if mode != "dry" and PENDING_PATH.exists():
                try:
                    PENDING_PATH.unlink()
                except Exception:
                    pass
            return 0 if failed == 0 else 1
        finally:
            try:
                b.close()
            except Exception:
                pass
            con.close()


if __name__ == "__main__":
    sys.exit(main())
