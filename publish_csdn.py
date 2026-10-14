#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CSDN 文章自动发布工具（Playwright 驱动真实浏览器）

CSDN 没有对外开放的发布接口，所以本工具的做法是：
用 Playwright 启动一个带持久化用户目录的 Chrome，复用你自己的登录态，
在 CSDN Markdown 编辑器里填写标题/正文，然后按真实 UI 流程发布。

用法:
    python publish_csdn.py login                     首次登录，只需一次
    python publish_csdn.py check                     检查登录态是否有效
    python publish_csdn.py inspect                   打开编辑器导出页面结构（校准选择器用）
    python publish_csdn.py config                    打印全部可配置参数（job 字段 + config.json）
    python publish_csdn.py publish posts/demo.md     发布文章
    python publish_csdn.py publish posts/demo.md --draft     只存草稿（标签/摘要/栏目照样填好）
    python publish_csdn.py publish posts/demo.md --schedule "2026-10-09 19:30"   定时发布
    python publish_csdn.py publish posts/demo.md --dry-run   填完不提交（联调用）

健壮性（2026-10-08 新增，针对"弹滑块"和"浏览器异常关闭"）：
    遇到 CSDN 安全验证时会**停下来等你在浏览器里手动点完**，然后自动接着跑，不自动过码。
    浏览器异常关闭时会清理陈旧 profile 锁并自动重新拉起续跑，不再一崩就整条链路作废。
    相关开关：--on-risk wait|abort、--on-crash restart|abort、--max-restarts N、
    --no-clean-profile；持久配置见 config.json 的 browser / risk / crash 三节。

定时发布（CSDN 官方限制：时间必须落在当前时间之后 4 小时 ~ 7 天之间）：
    --schedule "2026-10-09 19:30"   绝对时间
    --schedule "+26h"               相对现在（h/m/d）
    --schedule "2026-10-09"         只给日期 = 当天 09:00
"""

import argparse
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 基础设置

ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / ".profile"          # 浏览器用户目录（含登录态，别提交到 git）
OUTPUT_DIR = ROOT / "output"             # 截图与调试产物
CONFIG_PATH = ROOT / "config.json"

# 浏览器进程存活性标记：写 {pid, name, started}，用来判断 profile 锁是不是陈旧的。
# 放在 ROOT 而不是 profile 里面 —— 免得自己往浏览器用户目录里塞垃圾文件。
HEARTBEAT_PATH = ROOT / ".browser-heartbeat.json"
# Chrome 在用户目录里留下的锁/崩溃残留（2026-10-08 实测存在于 .profile 下）
PROFILE_LOCK_NAMES = ("lockfile", "SingletonLock", "SingletonSocket", "SingletonCookie")

# ---------------------------------------------------------------- 环境修正
# Playwright 会启动一个 Node 驱动进程，并在系统临时目录下 mkdtemp 存放运行产物。
# 在有文件访问限制的环境里这一步会直接 EPERM（报错 mkdtemp playwright-artifacts-XXXXXX），
# 表现为"浏览器起不来"。把临时目录和浏览器目录都收到项目内部即可绕开。
# 必须在 import playwright / 启动驱动之前设置。
_TMP_DIR = ROOT / ".tmp"
_BROWSERS_DIR = ROOT / ".pw-browsers"
for _d in (_TMP_DIR, _BROWSERS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(_TMP_DIR)
os.environ["TMP"] = str(_TMP_DIR)
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_BROWSERS_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DEFAULT_CONFIG = {
    # "chrome" 用系统已装的 Chrome，无需 playwright install chromium
    # 若启动失败会自动回退到 Playwright 自带 chromium
    "browser_channel": "chrome",
    "headless": False,

    "urls": {
        "home": "https://www.csdn.net/",
        "editor": "https://editor.csdn.net/md/",
    },

    # 登录成功后 CSDN 会写入这些 cookie 之一
    "login_cookies": ["UserName", "UserToken", "UserInfo"],

    # 默认标签 / 分类（文章 front-matter 未指定时使用）
    "default_tags": ["Python"],
    "default_category": "",          # 留空 = 不主动改分类，沿用编辑器默认值
    "max_tags": 5,                   # CSDN 标签数量上限，超出会被拒绝

    # 每一步之间的等待，单位毫秒。网络慢就调大
    "step_wait_ms": 1200,

    # ---- 浏览器健壮性（2026-10-08 新增）----------------------------------
    # 背景：实测两个反复出现的现象
    #   ① 右下/右上弹「Chrome 未正常关闭，是否恢复」——上次浏览器崩了，profile 被污染，
    #      下次 launch_persistent_context 会直接起不来（profile 被锁）；
    #   ② 弹「是否翻译此页面」——Chrome 内置翻译气泡会盖在编辑器上**拦截点击**，
    #      表现成"元素点不到"，其实元素好好的。
    # 下面这组开关就是治这两个的。全部默认开启，不需要的话设 false。
    "browser": {
        # 启动时清理上次崩溃留下的 profile 锁（lockfile / SingletonLock / SingletonSocket）
        # 安全前提：只有确认没有活着的浏览器在用这个 profile 时才删（见 preflight_profile）
        "cleanStaleProfile": True,
        # 关掉 Chrome 内置翻译：启动参数 + 页面级兜底关闭（翻译气泡会挡点击）
        "disableTranslate": True,
        # 关掉「恢复页面」崩溃气泡：启动参数 + 页面级兜底关闭
        "disableCrashBubble": True,
        # 关掉「Chrome 正在被自动化测试软件控制」信息栏之类
        "hideAutomationInfoBar": True,
        # channel 启动失败时额外重试次数（每次之间等一下再试）
        "launchRetries": 2,
        # 每次重试之间的等待毫秒数
        "launchRetryWaitMs": 2500,
        # 启动前杀掉"用着本项目 .profile 的残留 Chrome"。
        # 为什么必须有：实测一次启动失败（或浏览器崩溃）会留下十几个 chrome 子进程占着
        # user_data_dir。Chrome 的单例机制看到已有实例在跑，就直接"在已有会话中打开"
        # 然后立刻退出 —— 于是**重试每一次都必然失败**，看起来像"profile 被永久锁死"。
        # 只杀命令行里带本项目 .profile 路径的进程，绝不动用户自己的其他 Chrome。
        "killLingeringOnLaunch": True,
        # 浏览器进程存活性心跳：写一个带 pid 的标记文件，用于判断锁是不是陈旧
        # （False 则退化成"只看锁文件是否存在"，风险更高，不建议关）
        "heartbeat": True,
    },

    # ---- 风控闸门（2026-10-08 新增）--------------------------------------
    # CSDN 的安全验证（滑动拼图 / 点选）是**风控**不是 bug：越是用自动化硬闯，
    # 越容易升级到更严的验证。所以这里的策略是：**识别出来 → 停下来 → 让真人点一下
    # → 自动接着往下跑**，绝不尝试自动过验证码。
    "risk": {
        # detection: wait（默认，等人点完自动续跑）| abort（直接放弃，留给任务队列重跑）
        "onDetection": "wait",
        # 等人工处理的最长秒数，超时按放弃处理
        "waitTimeoutSec": 300,
        # 检测轮询间隔毫秒
        "pollMs": 1500,
        # 命中后响铃几声（Windows 会响，其他平台忽略失败）
        "beep": 3,
        # 每次开始一步之前先检查一次页面（防止在上一步的间隙被拦）
        "checkBeforeStep": True,
    },

    # ---- 崩溃自愈（2026-10-08 新增）--------------------------------------
    # 浏览器崩了（TargetClosed / 进程没了 / 恢复气泡）时，是否重新拉起浏览器再跑一遍。
    # 草稿有 CSDN 服务端恢复兜底（实测会记住上次内容），所以重跑是安全的；
    # 但**重跑前会先确认这次到底提交成功没有**，避免重复发布。
    "crash": {
        # restart（默认，重启浏览器续跑一遍）| abort
        "onCrash": "restart",
        # 整条发布链路最多重试几次（1 = 崩了就放弃）
        "maxRestarts": 2,
        # 重启之间的等待秒数，给 CSDN 服务端落草稿留时间
        "restartWaitSec": 5,
    },

    # 选择器按顺序尝试，第一个可见的胜出。
    # 以下均为 2026-10 实测验证过的值，CSDN 改版后只需要改这里，不用动代码。
    "selectors": {
        # 标题输入框默认 display:none，需要先点 title_display 占位区把它切出来
        "title": [
            "input.article-bar__title",
            "input[placeholder*='请输入文章标题']",
        ],
        "title_display": [
            ".article-bar__title-display",
        ],
        # 正文是 Cledit 的 contenteditable <pre>，不是 CodeMirror
        "editor": [
            "pre.editor__inner",
            ".editor__inner",
            ".cm-content",
        ],
        # 编辑器自带的 .md 导入入口（隐藏的 file input），比注入文本可靠
        "import_file": [
            "#import-markdown-file-input",
        ],
        # 注意：页面上还有 button.simulation-button 也叫「发布文章」，必须锁定 .btn-publish
        "publish_button": [
            "button.btn.btn-publish",
        ],
        "save_draft": [
            "button.btn.btn-save",
        ],
        # 发布弹窗里的字段（2026-10 实测）
        # 标签不是自由文本框：必须点开「添加文章标签」浮层，在其搜索框里回车添加
        "tag_button": [
            "button.tag__btn-tag:has-text('添加文章标签')",
            "button:has-text('添加文章标签')",
        ],
        "tag_input": [
            "input[placeholder*='请输入文字搜索']",
            "input[placeholder*='Enter键可添加自定义标签']",
        ],
        "category_trigger": [
            ".form-entry:has-text('分类专栏') .el-select",
            ".mark_category .select-box",
        ],
        # 弹窗里那个红色的「发布文章」；注意与页面上 button.btn.btn-publish 区分
        "confirm_publish": [
            "button.btn-b-red",
            "button.button.btn-b-red.ml16",
        ],
        # 封面上传：上传后 CSDN 会弹一个图片裁剪窗，必须再点「确认上传」。
        # 注意「确认上传」是个 div 而不是 button，用 button 选择器永远找不到。
        "cover_input": [
            "input.el-upload__input",
        ],
        "cover_confirm": [
            ".vicp-operate-btn",
            "text=确认上传",
        ],
        # 文章摘要（选填）
        "summary_input": [
            ".modal__publish-article textarea.el-textarea__inner",
            "textarea.el-textarea__inner",
        ],
    },
}


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_config():
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        # 补齐新增的默认键，方便版本升级
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        for k, v in cfg.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged
    CONFIG_PATH.write_text(
        json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"已生成默认配置: {CONFIG_PATH}")
    return json.loads(json.dumps(DEFAULT_CONFIG))


CFG = load_config()


# ---------------------------------------------------------------- Markdown 解析

def parse_front_matter(text):
    """解析极简 YAML front-matter，返回 (meta, body)。"""
    meta, body = {}, text
    # Windows 上的记事本 / PowerShell 常给 UTF-8 文件加 BOM，
    # 不清掉的话开头的 "---" 判断会失败，整个 front-matter 被当成正文。
    text = text.lstrip("\ufeff")
    if text.lstrip().startswith("---"):
        stripped = text.lstrip()
        end = stripped.find("\n---", 3)
        if end != -1:
            fm = stripped[3:end]
            body = stripped[end + 4:].lstrip("\n")
            for line in fm.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, val = line.split(":", 1)
                key, val = key.strip(), val.strip()
                if val.startswith("[") and val.endswith("]"):
                    meta[key] = [
                        x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()
                    ]
                elif "," in val and key == "tags":
                    meta[key] = [x.strip() for x in val.split(",") if x.strip()]
                else:
                    meta[key] = val.strip("'\"")
    return meta, body


def load_article(path: Path):
    # utf-8-sig：有 BOM 就自动吃掉，没有也正常读
    raw = path.read_text(encoding="utf-8-sig")
    meta, body = parse_front_matter(raw)

    title = meta.get("title") or ""
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            title = m.group(1).strip()
            body = body.replace(m.group(0), "", 1).lstrip("\n")
    if not title:
        title = path.stem

    tags = meta.get("tags") or CFG["default_tags"]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "title": title,
        "body": body.strip() + "\n",
        "tags": tags,
        "category": meta.get("category") or CFG["default_category"],
        "cover": meta.get("cover") or "",
        "summary": meta.get("summary") or meta.get("description") or "",
        "base": path.parent,
        "source": str(path),
    }


# ---------------------------------------------------------------- 浏览器封装

# ------------------------------------------------- profile 卫生 / 心跳 / 提示音

def _pid_alive(pid) -> bool:
    """这个 pid 现在活着吗？（Windows 上用 OpenProcess 查，避免 tasklist 起进程）"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_heartbeat():
    try:
        return json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_heartbeat(extra=None):
    """记下"这个 profile 正被本进程占着"，供下次启动判断锁是否陈旧。"""
    if not CFG.get("browser", {}).get("heartbeat", True):
        return
    try:
        data = {
            "pid": os.getpid(),
            "started": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "channel": CFG.get("browser_channel"),
        }
        if extra:
            data.update(extra)
        HEARTBEAT_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def clear_heartbeat():
    try:
        HEARTBEAT_PATH.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def heartbeat_alive():
    """心跳指向的进程还活着吗？返回 (是否活着, 心跳内容)。

    **自己写的心跳不算"别人在用"**：正常启动流程里就是本进程写的心跳，
    如果把它当成"被占用"，第一次启动失败之后的重试会全部被自己拦下，
    并打出一句完全误导的"profile 正被另一个进程占用（pid=自己）"。
    """
    hb = read_heartbeat()
    if not hb:
        return False, None
    pid = hb.get("pid")
    if pid == os.getpid():
        return False, hb          # 是本进程这一轮写的，不是外部占用
    if _pid_alive(pid):
        return True, hb
    return False, hb


def _proc_exe_name(pid) -> str:
    """取进程名（小写，不含路径）。取不到返回空串。"""
    if not isinstance(pid, int) or pid <= 0:
        return ""
    if os.name == "nt":
        try:
            import subprocess
            ps = (f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue)."
                  "ProcessName")
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            return (out.stdout or "").strip().lower()
        except Exception:
            return ""
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip().lower()
    except Exception:
        return ""


def _heartbeat_is_own_stale(hb) -> bool:
    """心跳说"有进程占着 profile"，但那个进程其实是本项目上一轮留下的壳？

    判据：进程名是 python（不是 chrome/chromium）。
    真正占着 user_data_dir 的是浏览器进程；python 只是写心跳的那一层壳，
    它退出慢一点（或还挂在后台）不该把下一次启动挡在门外。
    """
    if not hb:
        return False
    name = _proc_exe_name(hb.get("pid"))
    if not name:
        return False
    return name in ("python", "python3", "python.exe", "py")


def resolve_chromium() -> str:
    """解析 Playwright 自带 chromium 的可执行文件路径；没装就返回空串（结果缓存）。

    实测这个项目里 `.pw-browsers` 可能是空的（从没跑过 `playwright install`），
    这时回退重试纯属浪费时间，还会打出一堆"请运行 playwright install"的噪音。
    """
    if "_chromium_exe" in CFG:
        return CFG["_chromium_exe"]
    p = ""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            exe = pw.chromium.executable_path
        p = exe if exe and Path(exe).exists() else ""
    except Exception:
        p = ""
    CFG["_chromium_exe"] = p
    return p


def kill_lingering_browser():
    """杀掉"用着本项目 .profile"的残留浏览器进程。

    典型场景：一次启动失败/浏览器崩溃之后，user_data_dir 被十来个 chrome 子进程占着，
    下一次启动 Chrome 走单例握手 → "在已有会话中打开" → 客户端进程立刻退出 →
    Playwright 报 TargetClosedError。**不清这些进程，重试多少次都没用。**

    安全边界（宁可不杀也不误杀）：
      * 只匹配命令行里同时含本项目 .profile 绝对路径的进程；
      * 只杀 chrome / chromium 相关名字；
      * 绝不碰用户自己开的、用别的 user-data-dir 的 Chrome。
    """
    if not CFG.get("browser", {}).get("killLingeringOnLaunch", True):
        return 0
    if os.name != "nt":
        return 0
    profile = str(PROFILE_DIR).lower()
    try:
        import subprocess
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' or "
            "Name='chromium.exe'\" | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains('"
            + str(PROFILE_DIR).replace("'", "''").lower()
            + "') } | Select-Object -ExpandProperty ProcessId"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
        pids = [int(x) for x in (out.stdout or "").split() if x.strip().isdigit()]
    except Exception as e:
        log(f"查残留浏览器进程失败（忽略）: {e.__class__.__name__}")
        return 0
    if not pids:
        return 0

    log(f"发现 {len(pids)} 个残留浏览器进程还占着本项目 profile，清理掉（只用本项目的 .profile）")
    killed = 0
    for pid in pids:
        try:
            import subprocess
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
            killed += 1
        except Exception:
            pass
    # 给 Windows 一点时间真正回收句柄与锁文件
    time.sleep(1.5)
    for name in PROFILE_LOCK_NAMES:
        f = PROFILE_DIR / name
        try:
            if f.exists() and not f.is_dir():
                f.unlink()
        except Exception:
            # 沙箱/安全策略会拦下"删除走回收站"，尤其对空的 lockfile。
            # 改名让位等价于删除（Chrome 只看文件名在不在），不依赖删除权限。
            try:
                os.replace(str(f), str(f.with_name(f"{name}.stale-{int(time.time())}")))
            except Exception:
                pass
    # 关键：被清掉的这些进程里，就有"上一轮自己"写的那个心跳。
    # 不一起清掉的话，`preflight_profile()` 会读到"pid 还活着"而拒绝启动，
    # 表现成"同一批文章连发，第 N 篇开始启动不了"（实测 2026-10-11）。
    clear_heartbeat()
    log(f"已清理残留进程 {killed} 个，并移除其留下的锁文件与心跳")
    return killed


def preflight_profile():
    """启动前清理"上次浏览器没退干净"留下的锁。返回人类可读的处理说明列表。

    为什么必须做：Playwright 的 launch_persistent_context 拿到一个被锁住的
    user_data_dir 时，报的是底层 Chromium 的一行 stderr（进程已被杀/用户目录已被占用），
    表现为"浏览器起不来但看不出为什么"。实测这就是"Chrome 未正常关闭"之后的下一次运行。

    安全策略（宁可少删，绝不误删）：
      * 心跳显示还有活着的进程占着 profile → 什么都不动，直接报错让用户先关掉；
      * 心跳是死的 / 没有心跳，但有锁文件 → 是陈旧残留，删掉；
      * 没有锁文件 → 什么都不用做。
    """
    notes = []
    if not CFG.get("browser", {}).get("cleanStaleProfile", True):
        return notes

    locks = [PROFILE_DIR / n for n in PROFILE_LOCK_NAMES]
    existing = [p for p in locks if p.exists()]
    if not existing:
        return notes

    alive, hb = heartbeat_alive()
    if alive and _heartbeat_is_own_stale(hb):
        # 心跳指向的进程虽然"活着"，但它只是本项目上一轮留下的 python/父进程，
        # 不是真正占着 profile 的浏览器。放行并清掉心跳，否则同一批文章连发时
        # 第 N 篇会莫名启动不了（实测 2026-10-11：pid 被判活，其实是上一轮的壳）。
        notes.append(f"忽略心跳（pid={hb.get('pid')} 是本项目上一轮残留，不是浏览器占用）")
        clear_heartbeat()
        alive = False
    if alive:
        notes.append(
            f"⛔ profile 正被另一个进程占用（pid={hb.get('pid')}，启动于 {hb.get('started')}）。"
            "请先关掉那个浏览器/脚本，或者换一个 profile。"
        )
        notes.append("   没有动任何文件。")
        return notes

    why = f"上次的进程 pid={hb.get('pid')} 已经不在了" if hb else "没有心跳记录"
    for p in existing:
        if p.is_dir():
            continue
        try:
            p.unlink()
            notes.append(f"已清理陈旧 profile 锁: {p.name}（{why}）")
            continue
        except Exception as e:
            _unlink_err = f"{e.__class__.__name__}: {e}"
        # unlink 可能被外部安全策略（沙箱的"删除走回收站"钩子）拦下：
        # 空的 lockfile 经常让 trash 操作失败，于是"删不掉"。这时退化为**改名让位**——
        # 效果等价于删掉（Chrome 只看该文件名在不在），而且不依赖删除权限。
        try:
            side = p.with_name(f"{p.name}.stale-{int(time.time())}")
            os.replace(str(p), str(side))
            notes.append(f"已改名让位陈旧 profile 锁: {p.name} → {side.name}（{why}）")
        except Exception as e2:
            notes.append(
                f"清理 {p.name} 失败（unlink: {_unlink_err}；rename: "
                f"{e2.__class__.__name__}: {e2}），继续尝试启动"
            )
    clear_heartbeat()
    return notes


def beep(times=3):
    """提示用户"需要你点一下浏览器"。响不了就算了，绝不能因此中断流程。"""
    for i in range(max(1, int(times or 1))):
        try:
            if os.name == "nt":
                import winsound
                winsound.Beep(880, 220)
            else:
                sys.stdout.write("\a")
                sys.stdout.flush()
        except Exception:
            pass
        if i + 1 < times:
            time.sleep(0.25)


# ------------------------------------------------- 风控识别 / 崩溃识别（纯函数）

# 出现这些说明 CSDN 把我们丢到"安全验证"上去了。**不要试图自动过验证码**：
# 滑块是行为风控，自动化痕迹只会让验证升级。策略永远是"识别 → 交给真人 → 续跑"。
RISK_TEXT_HINTS = (
    "安全验证", "滑动验证", "拖动滑块", "拖动下方滑块",
    "请完成验证", "人机验证", "验证码", "行为验证",
    "点击进行验证", "请依次点击", "向右滑动填充拼图",
)
RISK_URL_HINTS = ("captcha", "verify", "callback", "risk", "sec.csdn.net")

# Chrome 自己弹的"未正常关闭，是否恢复"气泡。它会盖住页面并且让状态不可信。
CRASH_BUBBLE_HINTS = ("未正常关闭", "是否恢复", "恢复页面", "还原页面", "restore pages")

RISK_JS = """
(kws) => {
  const vis = el => !!(el && el.getClientRects && el.getClientRects().length > 0);

  // 1) 结构特征：主流验证码组件的容器（极验 .geetest_*、网易、CSDN 自带 .verify-*）
  const sels = ['.geetest_panel', '.geetest_slider', '.geetest_holder',
                '.yidun_panel', '.yidun_slider', '[class*="yidun"]',
                '.verify-slider', '.verify-box', '.slider-verify',
                '.nc_container', '.nc_scale', '#nc_1_wrapper',
                '[class*="captcha"]', '[id*="captcha"]',
                'iframe[src*="captcha"]', 'iframe[src*="verify"]',
                'iframe[src*="geetest"]', 'iframe[src*="yidun"]'];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) {
      if (vis(el)) return {kind: 'dom', detail: s, text: (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120)};
    }
  }

  // 2) 文案特征：只在"像弹窗/卡片/全屏遮罩"的元素里找，绝不扫整页
  //    —— 扫整页会把帮助文档、侧栏广告里的"安全验证"四个字也命中。
  const cands = ['.el-message-box', '.el-dialog', '.el-message', '.el-notification',
                 '[role=dialog]', '.modal', '[class*="verify"]', '[class*="captcha"]',
                 '[class*="mask"]', '[class*="overlay"]'];
  const seen = new Set();
  for (const s of cands) {
    for (const el of document.querySelectorAll(s)) {
      if (!vis(el)) continue;
      const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
      if (!t) continue;
      const hit = kws.find(k => t.includes(k));
      if (!hit) continue;
      const key = t.slice(0, 60);
      if (seen.has(key)) continue;
      seen.add(key);
      return {kind: 'text', detail: s, hit, text: t.slice(0, 200)};
    }
  }
  return null;
}
"""

CRASH_BUBBLE_JS = """
(kws) => {
  const vis = el => !!(el && el.getClientRects && el.getClientRects().length > 0);
  for (const el of document.querySelectorAll(
      '#customBubble, [class*="restore"], [id*="restore"], .crash-bubble')) {
    if (!vis(el)) continue;
    const t = (el.innerText || '').replace(/\\s+/g, ' ').trim();
    if (kws.some(k => t.includes(k))) return t.slice(0, 200);
  }
  return null;
}
"""

# 翻译气泡 / 崩溃气泡都是在 shadow DOM 里的，Playwright 的 CSS 选择器进不去，
# 必须自己递归 shadowRoot 再点。这里只做"点关闭/不恢复"，不点"恢复"。
KILL_BUBBLES_JS = """
() => {
  const out = [];
  const vis = el => !!(el && el.getClientRects && el.getClientRects().length > 0);
  const walk = (root, depth) => {
    if (!root || depth > 12) return;
    let nodes = [];
    try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      const id = el.id || '';
      const cls = (typeof el.className === 'string') ? el.className : '';
      const tag = el.tagName ? el.tagName.toLowerCase() : '';
      // 翻译气泡：<translate-notice> / #translate / .translate-*
      if (/^translate/i.test(id) || /^translate-(notice|bubble|icon)/i.test(tag) ||
          /translate/i.test(cls)) {
        const btn = el.querySelector('#close, .close-button, [aria-label*="关闭"]');
        if (btn && vis(btn)) { btn.click(); out.push('translate-close'); }
        else if (tag === 'translate-notice' || /^translate/i.test(id)) {
          el.remove(); out.push('translate-remove');
        }
      }
      // 崩溃恢复气泡：只点"不恢复"，绝不点"恢复"
      if (id === 'customBubble' || /restore/i.test(id) || /restore/i.test(cls)) {
        const no = el.querySelector('#dontRestore, .dont-restore, [id*="dont"]');
        if (no && vis(no)) { no.click(); out.push('crash-dismiss'); }
        else { el.remove(); out.push('crash-remove'); }
      }
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    }
  };
  walk(document, 0);
  return out;
}
"""


def detect_risk(page):
    """当前页面是不是被丢到安全验证上了？返回 dict 或 None。"""
    try:
        url = (page.url or "").lower()
    except Exception:
        return None
    try:
        hit = page.evaluate(RISK_JS, list(RISK_TEXT_HINTS))
    except Exception:
        hit = None
    if hit:
        return {"where": "dom" if hit.get("kind") == "dom" else "text",
                "detail": hit.get("detail") or hit.get("hit") or "",
                "text": hit.get("text") or ""}
    if any(k in url for k in RISK_URL_HINTS):
        return {"where": "url", "detail": url, "text": ""}
    return None


def detect_crash_bubble(page):
    """Chrome 那个"未正常关闭，是否恢复"气泡在不在？"""
    try:
        return page.evaluate(CRASH_BUBBLE_JS, list(CRASH_BUBBLE_HINTS))
    except Exception:
        return None


def kill_bubbles(page):
    """关掉翻译气泡 / 崩溃恢复气泡（会挡点击的那种）。返回关掉了什么。"""
    try:
        killed = page.evaluate(KILL_BUBBLES_JS) or []
    except Exception:
        killed = []
    if killed:
        log(f"已关闭干扰气泡: {', '.join(sorted(set(killed)))}")
    return killed


# Playwright 抛这些，说明浏览器/页面已经没了，重试单个元素是没意义的，必须重启浏览器。
DEAD_MARKERS = (
    "target closed", "target page, context or browser has been closed",
    "browser has been closed", "browser closed", "context closed",
    "page closed", "connection closed", "browser has disconnected",
    "websocket", "protocol error", "econnreset", "browser process",
    "has been closed", "crash",
)


def is_browser_dead(exc) -> bool:
    """这个异常是不是"浏览器相关的断开"（而不是"这个元素没找到"）？

    ⚠ 注意它**不能**区分"标签页被关"和"整个浏览器进程死了" —— 两者抛的都是
    TargetClosedError。要区分必须去看 context 还能不能用，见 browser_process_alive()
    和 page_recoverable()。
    """
    name = exc.__class__.__name__.lower()
    if name in ("targetclosederror", "browserclosederror", "error", "timeouterror"):
        # Error 是 Playwright 的通用错误（连接断了走这个），所以还要看文案
        msg = str(exc).lower()
        return any(m in msg for m in DEAD_MARKERS)
    msg = str(exc).lower()
    return any(m in msg for m in DEAD_MARKERS)


def page_recoverable(browser):
    """标签页被关掉时，能不能在同一个 context 里新开一个？能就返回新 page。

    ★ 为什么要有这一层（2026-10-08 实测）：CSDN 风控会对编辑器页**关掉标签页**，
    但浏览器进程完全健康。早期版本把这种 TargetClosedError 当成"浏览器崩了"，
    去重启整个浏览器 —— 多花十几秒、还可能撞上 profile 锁，方向是错的。
    正确恢复是原地新开一个 page 继续跑。

    它同时是"浏览器进程还活着吗"的**唯一可靠判据**：只问 `ctx.pages` 是问不出来的
    —— 进程已经死了，Playwright 的本地对象还在，照样返回 True。只有真去 new_page()
    才能确定 context 到底能不能用。
    """
    if not browser or not browser.ctx:
        return None
    try:
        for p in list(browser.ctx.pages):
            try:
                if p.is_closed():
                    continue
                p.evaluate("() => 1")     # 能真操作才算活着
                return p
            except Exception:
                continue
        return browser.ctx.new_page()
    except Exception as e:
        log(f"context 不可用（{e.__class__.__name__}），只能重启浏览器")
        return None


class RiskGateAbort(Exception):
    """风控闸门判定放弃（无人值守模式 / 等超时）。"""


class Browser:

    def __init__(self, pw):
        self.pw = pw
        self.ctx = None
        self.channel_used = None
        # 本次运行里"崩了又重来"的次数，由外层流程读写
        self.restarts = 0

    # ---- 启动 --------------------------------------------------------

    def _launch_args(self):
        args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            # 语言固定中文：否则 Chrome 会按 IP/系统语言猜出英文，触发"是否翻译"
            "--lang=zh-CN",
        ]
        bcfg = CFG.get("browser", {})
        if bcfg.get("disableTranslate", True):
            # 实测这两个一起给才彻底安静：只关 TranslateUI 仍会弹气泡
            args.append("--disable-features=Translate,TranslateUI,TranslateNewUX")
            args.append("--disable-translate")
        if bcfg.get("disableCrashBubble", True):
            # 关掉"未正常关闭，是否恢复"：正常退出标记 + 禁用会话恢复提示
            args.append("--disable-session-crashed-bubble")
            args.append("--hide-crash-restore-bubble")
        if bcfg.get("hideAutomationInfoBar", True):
            args.append("--disable-infobars")
        return args

    def _launch_once(self, channel=None):
        common = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=CFG["headless"],
            no_viewport=True,
            args=self._launch_args(),
            permissions=["clipboard-read", "clipboard-write"],
            locale="zh-CN",
        )
        if channel:
            common["channel"] = channel
        self.ctx = self.pw.chromium.launch_persistent_context(**common)
        self.ctx.set_default_timeout(30000)
        self.channel_used = channel or "chromium"
        return self.ctx

    def launch(self):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        bcfg = CFG.get("browser", {})

        # ① 先清"上一轮崩掉/启动失败留下的残留 Chrome"。
        #    这一步必须最先做：残留进程占着 user_data_dir 时，Chrome 会走单例握手，
        #    直接"在已有会话中打开"然后退出，Playwright 只会报一句 TargetClosedError
        #    —— 不清进程的话，后面重试多少次都必然失败。
        kill_lingering_browser()

        # ② 清陈旧锁（心跳显示有外部进程在用就拒绝启动，一个文件都不动）
        for note in preflight_profile():
            log(note)
            if note.startswith("⛔"):
                raise SystemExit("浏览器启动被前置检查拦下：profile 正被占用。")

        # ③ 记心跳：告诉"下一次运行"这个 profile 是谁在占
        write_heartbeat()

        # ④ 按 channel 启动，失败重试；仍失败再看自带 chromium 到底装没装
        channel = CFG.get("browser_channel")
        retries = max(0, int(bcfg.get("launchRetries", 2)))
        wait_ms = max(0, int(bcfg.get("launchRetryWaitMs", 2500)))
        errors = []

        if channel:
            for attempt in range(retries + 1):
                try:
                    self._launch_once(channel)
                    log(f"已启动系统浏览器: {channel}")
                    return self.ctx
                except Exception as e:
                    errors.append(f"{channel}#{attempt + 1}: {e.__class__.__name__}: {e}")
                    log(f"channel={channel} 启动失败（第 {attempt + 1}/{retries + 1} 次，"
                        f"{e.__class__.__name__}）")
                    if attempt < retries:
                        # 每次失败后再清一遍：失败的启动自己也会留下残留进程和锁
                        kill_lingering_browser()
                        for note in preflight_profile():
                            log(f"  {note}")
                        write_heartbeat()
                        time.sleep(wait_ms / 1000)

        # ⑤ 回退前先确认自带 chromium 真的在（否则重试毫无意义，只会刷一堆安装提示）
        exe = resolve_chromium()
        if not exe:
            clear_heartbeat()
            raise SystemExit(
                f"系统浏览器（channel={channel!r}）起不来，而 Playwright 自带 chromium 也没装。\n"
                "请任选其一：\n"
                "  1) 装好 Chrome 并确认 config.json 里 browser_channel = \"chrome\"（推荐）\n"
                "  2) 执行: python -m playwright install chromium（会下载约 150MB）\n"
                "原始错误：\n  " + "\n  ".join(errors[-4:])
            )
        log(f"自带 chromium 可用: {exe}")

        for attempt in range(retries + 1):
            try:
                self._launch_once(None)
                log("已启动自带 chromium")
                if channel:
                    log(f"  （系统 {channel} 起不来，这次用的是 Playwright 自带 chromium；"
                        "登录态仍在同一个 .profile 里）")
                return self.ctx
            except Exception as e:
                errors.append(f"chromium#{attempt + 1}: {e.__class__.__name__}: {e}")
                log(f"自带 chromium 启动失败（第 {attempt + 1}/{retries + 1} 次，"
                    f"{e.__class__.__name__}）")
                if attempt < retries:
                    kill_lingering_browser()
                    for note in preflight_profile():
                        log(f"  {note}")
                    write_heartbeat()
                    time.sleep(wait_ms / 1000)

        clear_heartbeat()
        raise SystemExit(
            "无法启动浏览器（已清理残留进程与陈旧锁，并重试过）。请依次确认：\n"
            "  1) 有没有别的 Chrome/脚本正占着 .profile（先全部关掉）\n"
            f"  2) 手动删掉 {PROFILE_DIR} 下的 lockfile / SingletonLock（会丢登录态，需重新 login）\n"
            "  3) 安装 Chrome 并确认 config.json 里 browser_channel = \"chrome\"\n"
            "  4) 执行: python -m playwright install chromium\n"
            "原始错误：\n  " + "\n  ".join(errors[-4:])
        )

    # ---- 运行中 ------------------------------------------------------

    def alive(self) -> bool:
        """浏览器上下文还活着吗？（页面被关掉不算，那只是需要重开一个 tab）"""
        try:
            if not self.ctx:
                return False
            _ = self.ctx.pages
            return True
        except Exception:
            return False

    def page(self):
        if self.ctx.pages:
            return self.ctx.pages[0]
        return self.ctx.new_page()

    def is_logged_in(self):
        names = {c.get("name") for c in self.ctx.cookies()}
        hits = names & set(CFG["login_cookies"])
        return bool(hits), sorted(hits)

    def shot(self, page, name):
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUTPUT_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{name}.png"
        try:
            page.screenshot(path=str(path), full_page=False)
            log(f"截图: {path.name}")
        except Exception as e:
            log(f"截图失败: {e}")
        return path

    def close(self):
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:
            pass
        clear_heartbeat()


# ---------------------------------------------------------------- 风控闸门

def announce(lines):
    """把一段话醒目地打出来，并放进 output 下的提示文件（人不在终端也能看到）。"""
    bar = "!" * 66
    log(bar)
    for ln in lines:
        log(ln)
    log(bar)
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        p = OUTPUT_DIR / "NEEDS-HUMAN.txt"
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}]\n")
            for ln in lines:
                f.write(ln + "\n")
    except Exception:
        pass


def risk_gate(page, browser, where="", announce_only=False):
    """风控闸门：发现安全验证就停下来等真人处理，处理完自动接着跑。

    为什么是"等"而不是"过"：滑块/点选是行为风控，自动化痕迹只会让验证升级，
    而且过验证码属于打码对抗，成本高、易被封。真人点一下 10 秒钟的事，
    点完脚本原地续跑，比任何自动方案都稳。

    参数 announce_only=True 时只提示不阻塞（给 login 这种纯人工步骤用）。
    返回 True 表示"可以继续"，抛 RiskGateAbort 表示放弃。
    """
    hit = detect_risk(page)
    if not hit:
        return False

    mode = (CFG.get("risk", {}).get("onDetection") or "wait").lower()
    detail = hit["detail"]
    text = (hit["text"] or "").strip()

    lines = [
        "⚠ 遇到 CSDN 安全验证（这是风控，不是脚本坏了）",
        f"  识别依据: {hit['where']} -> {detail}",
    ]
    if text:
        lines.append(f"  页面文案: {text[:160]}")
    lines.append("")
    lines.append("  请在弹出的浏览器窗口里手动完成验证（拖动滑块 / 点选）。")
    lines.append("  完成后不用管脚本，它会自己检测到并接着往下跑。")
    lines.append("  ⛔ 不要按 Escape，也不要把浏览器关掉。")
    announce(lines)

    if announce_only:
        return True

    if mode == "abort":
        raise RiskGateAbort(
            f"config.json 里 risk.onDetection=abort，遇到安全验证直接放弃（{detail}）"
        )

    beep(CFG.get("risk", {}).get("beep", 3))
    timeout = int(CFG.get("risk", {}).get("waitTimeoutSec", 300))
    poll = int(CFG.get("risk", {}).get("pollMs", 1500))
    deadline = time.time() + timeout
    shot_done = False
    while time.time() < deadline:
        page.wait_for_timeout(poll)
        if not browser.alive():
            raise RuntimeError("等待人工验证期间浏览器被关掉了")
        if not detect_risk(page):
            log(f"✓ 安全验证已通过（等了 {int(timeout - (deadline - time.time()))} 秒），继续执行")
            return True
        if not shot_done and time.time() > deadline - timeout + 20:
            browser.shot(page, "risk-blocked")
            shot_done = True
            log(f"  仍在等待人工验证……最多再等 {int(deadline - time.time())} 秒")
    browser.shot(page, "risk-timeout")
    raise RiskGateAbort(
        f"等待人工完成安全验证超时（{timeout} 秒）。"
        "可以调大 config.json 的 risk.waitTimeoutSec，或改用 risk.onDetection=abort 交给任务队列重跑。"
    )


def guard(page, browser, what="", announce_only=False):
    """每个步骤动手之前统一过一遍：清干扰气泡 + 风控闸门。"""
    kill_bubbles(page)
    if CFG.get("risk", {}).get("checkBeforeStep", True):
        try:
            risk_gate(page, browser, where=what, announce_only=announce_only)
        except RiskGateAbort:
            raise


# ---------------------------------------------------------------- 页面操作

def find_visible(page, selectors, timeout_ms=10000, what="元素"):
    """按顺序尝试选择器，返回第一个可见元素。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    log(f"命中{what}: {sel}")
                    return loc, sel
            except Exception:
                continue
        page.wait_for_timeout(250)
    return None, None


def fill_title(page, title):
    # 标题输入框初始是 display:none，页面上先显示的是 .article-bar__title-display 占位区，
    # 点它之后两者互换。实测不点这一下 fill() 会直接失败。
    disp, _ = find_visible(page, CFG["selectors"]["title_display"], 8000, "标题占位区")
    if disp:
        disp.click()
        page.wait_for_timeout(600)

    loc, _ = find_visible(page, CFG["selectors"]["title"], 10000, "标题框")
    if not loc:
        raise RuntimeError("找不到标题输入框，请先跑 inspect 并更新 config.json selectors.title")
    loc.click()
    loc.fill("")
    loc.fill(title)
    log(f"标题已填: {title}")


def _norm(s):
    """去掉所有空白，用于宽松比较编辑器内容与目标正文。"""
    return re.sub(r"\s+", "", s or "")


def import_body(page, body):
    """用编辑器自带的 Markdown 导入功能写入正文。

    比往 contenteditable 里注入文本可靠得多：不依赖光标位置、不吃快捷键、
    大文档也不会因为逐字输入而超时。失败时回退到直接注入。

    注意：不能用「内容有没有变化」判断成功。CSDN 编辑器会自动恢复上次未发布的
    草稿，重复导入同一篇文章时内容本来就一样，那样会误判成失败。
    正确做法是判断编辑器内容是否等于（或包含）目标正文。
    """
    tmp = OUTPUT_DIR / "_import.md"
    tmp.write_text(body, encoding="utf-8")

    sel = CFG["selectors"]["import_file"][0]
    fi = page.locator(sel)
    if fi.count() == 0:
        log(f"未找到导入入口 {sel}，回退到直接注入正文")
        return inject_body(page, body)

    editor = page.locator(CFG["selectors"]["editor"][0]).first
    want = _norm(body)
    fi.set_input_files(str(tmp))
    log("已触发 Markdown 导入，等待编辑器更新…")

    deadline = time.time() + 15
    while time.time() < deadline:
        page.wait_for_timeout(400)
        got = _norm(editor.inner_text())
        if want and want[:120] in got:
            log(f"导入完成，正文 {len(editor.inner_text())} 字符")
            return True
    log("导入未生效，回退到直接注入")
    return inject_body(page, body)


def inject_body(page, body):
    """回退方案：直接往 contenteditable 里注入文本。"""
    loc, _ = find_visible(page, CFG["selectors"]["editor"], 15000, "正文编辑器")
    if not loc:
        raise RuntimeError("找不到正文编辑器，请先跑 inspect 并更新 config.json selectors.editor")
    loc.click()
    page.wait_for_timeout(400)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.wait_for_timeout(200)
    page.keyboard.insert_text(body)
    page.wait_for_timeout(CFG["step_wait_ms"])
    log(f"正文已注入: {len(body)} 字符")
    return True


def clear_tags(page):
    """清掉 CSDN 记忆的历史标签。

    实测：CSDN 会记住你上次选过的标签，下次打开发布弹窗时它们**已经勾好了**。
    不清掉的话，最终文章的标签 = 历史标签 + 本次标签的混合，
    会混进「android」这种和文章完全无关的标签。
    """
    try:
        n = page.evaluate(
            """() => {
                const box = document.querySelector('.modal__publish-article')
                            || document.querySelector('.modal');
                if (!box) return 0;
                const row = [...box.querySelectorAll('.form-entry')]
                    .find(e => (e.innerText || '').includes('添加文章标签'));
                if (!row) return 0;
                const xs = [...row.querySelectorAll('.el-tag__close')];
                xs.forEach(x => x.click());
                return xs.length;
            }"""
        )
        if n:
            log(f"已清掉 {n} 个历史遗留标签（CSDN 会记住上次选过的标签）")
            page.wait_for_timeout(900)
        return n
    except Exception as e:
        log(f"清理历史标签失败（忽略）: {e.__class__.__name__}")
        return 0


def fill_tags(page, tags):
    """填写文章标签。

    CSDN 的标签不是一个可以直接输入的文本框：必须点开「＋添加文章标签」浮层，
    在浮层内的搜索框输入并回车，才能把标签加上去（也支持自定义标签）。
    """
    if not tags:
        return False

    opener, _ = find_visible(page, CFG["selectors"]["tag_button"], 8000, "添加标签按钮")
    if not opener:
        log(f"未找到「添加文章标签」按钮，跳过标签: {tags}")
        return False

    # 先把 CSDN 记忆的历史标签清掉，否则会混进无关标签
    clear_tags(page)

    # 点开「添加文章标签」之前，必须再把封面裁剪窗的残留遮罩清一遍。
    # 实测（2026-10-11）：上一步 dismiss_overlays 清过之后，Vue 重渲染会把
    # `.vue-image-crop-upload` 又挂回来，作为 position:fixed 遮罩拦住这次点击，
    # 表现成"栏目/摘要都填好了，卡在标签这里 30 秒超时"。所以这里再清 + 兜一层
    # JS 直接 click（绕开 Playwright 的命中测试）。
    _kill_crop_overlay(page)
    try:
        opener.click(timeout=8000)
    except Exception:
        log("「添加文章标签」常规点击被挡，改用 JS 直接触发")
        try:
            opener.evaluate("el => el.click()")
        except Exception as e:
            log(f"「添加文章标签」JS 点击也失败: {e.__class__.__name__}")
            return False
    page.wait_for_timeout(1200)

    box, _ = find_visible(page, CFG["selectors"]["tag_input"], 8000, "标签搜索框")
    if not box:
        log("未找到标签搜索框，跳过标签")
        return False

    added = []
    for tag in tags[: CFG["max_tags"]]:
        try:
            # 不用 box.click()：弹窗里偶尔还有别的浮层（如封面裁剪的残留节点）
            # 拦截指针事件，click 会直接超时。用 JS 聚焦 + 键盘输入，绕开命中测试，
            # 键盘事件发给已聚焦的元素，效果和手打一致。
            box.evaluate("el => el.focus()")
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.wait_for_timeout(120)
            page.keyboard.type(tag, delay=60)
            page.wait_for_timeout(700)
            page.keyboard.press("Enter")     # 浮层里回车 = 添加当前输入的标签
            page.wait_for_timeout(700)
            added.append(tag)
        except Exception as e:
            log(f"标签 {tag!r} 添加失败: {e}")
    log(f"标签已填: {added}")

    # 实测：标签浮层加完后会自己留在页面上，但它不遮挡底部的「发布文章」按钮
    # （elementFromPoint 验证过）。千万不要按 Escape —— 那关掉的是外层的
    # 发布弹窗，会让后面所有字段白填。这里只做一次回读校验。
    got = read_selected_tags(page)
    missing = [t for t in added if t not in got]
    if missing:
        log(f"警告：这些标签可能没加上，请核对截图: {missing}")
        log(f"当前已选标签: {got}")
    else:
        log(f"回读校验通过，已选标签: {got}")
    return True


def read_selected_tags(page):
    """回读发布弹窗里已选中的标签，用于校验标签是否真的加上了。"""
    try:
        return page.evaluate(
            """() => {
                const box = document.querySelector('.form-tag-box');
                if (!box) return [];
                // 只认「已选中」的标签：它们带一个删除用的 × 图标；
                // 推荐标签是普通 chip，没有 ×，不要算进来。
                return [...box.querySelectorAll('.el-tag')]
                  .filter(e => e.getClientRects().length > 0 && e.querySelector('.el-tag__close'))
                  .map(e => (e.innerText || '').replace(/[\\s\\u00d7\\u2715]+$/, '').trim())
                  .filter(Boolean);
            }"""
        )
    except Exception:
        return []


def set_cover(page, cover, base=None):
    """上传文章封面。

    流程：把图片塞进隐藏的 file input → CSDN 弹出「图片编辑」裁剪窗 →
    点裁剪窗右下角的「确认上传」。注意那个确认元素是个 div（.vicp-operate-btn），
    不是 button，用 button 选择器永远找不到。
    """
    src = Path(cover)
    if not src.is_absolute():
        for cand in filter(None, [base, ROOT]):
            if (cand / src).exists():
                src = cand / src
                break
    if not src.exists():
        log(f"封面文件不存在，跳过封面: {src}")
        return False

    fi = page.locator(CFG["selectors"]["cover_input"][0])
    if fi.count() == 0:
        log("找不到封面上传入口，跳过封面")
        return False

    fi.first.set_input_files(str(src.resolve()))
    log(f"已提交封面图片 {src.name}，等待裁剪窗…")

    confirm, _ = find_visible(page, CFG["selectors"]["cover_confirm"], 15000, "封面确认上传")
    if not confirm:
        log("裁剪窗未出现，封面可能没设置成功，请看截图核对")
        return False
    confirm.click()
    # 裁剪窗的关闭是异步的，而且关闭后常留下一个不可见的 .vue-image-crop-upload
    # 节点继续拦截鼠标事件——实测它会把后面「添加文章标签」的点击全部挡掉
    # （30 秒超时 × 每个标签，最后只剩一半标签）。所以这里必须等它真的不参与命中测试，
    # 而不是睡固定时长。
    crop_js = """() => [...document.querySelectorAll('.vue-image-crop-upload')]
        .filter(e => {
            const s = getComputedStyle(e);
            return s.display !== 'none' && s.visibility !== 'hidden'
                   && s.pointerEvents !== 'none'
                   && e.getBoundingClientRect().width > 0;
        }).length"""
    left = 1
    for _ in range(50):                      # 最多等 20 秒（关窗是异步的，慢的时候要等久一点）
        try:
            left = page.evaluate(crop_js)
        except Exception:
            left = 0
            break
        if not left:
            break
        page.wait_for_timeout(400)
    if left:
        # 裁剪浮层没自动关闭。只把 pointerEvents 设为 none 是不够的——
        # 它是 position:fixed 的遮罩，Playwright 的 actionability 命中测试仍会判
        # 「intercepts pointer events」（实测 10 篇里有 2 篇栽在这，最后一步
        # 「保存为草稿」被挡 30 秒超时）。必须让它真正从布局里消失：display:none。
        n = page.evaluate("""() => {
            const els = [...document.querySelectorAll('.vue-image-crop-upload')];
            els.forEach(e => {
                e.style.pointerEvents = 'none';
                e.style.display = 'none';
                e.style.visibility = 'hidden';
            });
            return els.length;
        }""")
        log(f"裁剪浮层没自动消失，已强制隐藏 {n} 个残留节点（display:none，避免挡住后续点击）")
        page.wait_for_timeout(300)
    log("封面已确认上传")
    return True


def fill_summary(page, summary):
    """填写文章摘要（选填）。留空时 CSDN 会自动截取正文前 256 字。"""
    if not summary:
        return False
    box, _ = find_visible(page, CFG["selectors"]["summary_input"], 6000, "摘要输入框")
    if not box:
        log("未找到摘要输入框，跳过摘要")
        return False
    box.click()
    box.fill(summary)
    log(f"摘要已填: {summary[:40]}…")
    return True


class CategoryGateAbort(RuntimeError):
    """栏目没选上，主动中止，避免"front-matter 写着栏目、页面其实没勾"的静默错位。"""


def read_categories(page):
    """回读当前已选的分类专栏（栏目）。"""
    try:
        return page.evaluate(
            """() => {
                const box = document.querySelector('.modal__publish-article')
                            || document.querySelector('.modal');
                if (!box) return [];
                const row = [...box.querySelectorAll('.form-entry')]
                    .find(e => (e.innerText || '').includes('分类专栏'));
                if (!row) return [];
                return [...row.querySelectorAll('input.tag__option-chk')]
                    .filter(i => i.checked).map(i => i.value);
            }"""
        )
    except Exception:
        return []


def available_categories(page):
    """列出该账号下所有可选的分类专栏名。"""
    try:
        return page.evaluate(
            """() => {
                const box = document.querySelector('.modal__publish-article')
                            || document.querySelector('.modal');
                if (!box) return [];
                const row = [...box.querySelectorAll('.form-entry')]
                    .find(e => (e.innerText || '').includes('分类专栏'));
                if (!row) return [];
                return [...row.querySelectorAll('input.tag__option-chk')].map(i => i.value);
            }"""
        )
    except Exception:
        return []


def select_categories(page, names):
    """选择分类专栏（栏目）。

    留空 = 不做任何操作，也就是不放在任何栏目里（选项默认全不勾选）。

    坑：这些选项的 <label> 是 display:inline 且宽高为 0，Playwright 算不出
    点击坐标，用 locator.click() 一定超时。但真实元素结构是
    <label><input type=checkbox hidden><i class="tag__option-icon"></label>，
    直接派发 label.click() 能正常触发 Vue 的勾选。所以这里走 JS 点击。
    CSDN 限制最多选 3 个栏目。
    """
    if not names:
        log("栏目: 留空，不选任何分类专栏")
        return True
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]

    have = available_categories(page)
    if not have:
        # 以前这里是 return False（同样没人看返回值）→ 栏目静默留空。
        # 现在和"名字对不上"一样，直接中止：宁可这一篇不提交，也不要发一篇没栏目的稿子。
        raise CategoryGateAbort(
            f"页面上没找到分类专栏选项（弹窗结构可能变了，或这次弹窗没加载完），"
            f"已中止本篇（不提交）。目标栏目={names}"
        )

    ok_all = True
    for name in names[:3]:
        if name not in have:
            log(f"栏目「{name}」不在你的专栏列表里，可选: {have}")
            ok_all = False
            continue
        # 点击 + 确认：CSDN 的 Vue 勾选是异步落地的，实测点完 900ms 有时还没生效。
        # 所以这里「点一次 → 等一下 → 回读确认」，没生效就再点一次（最多 3 轮）。
        # 这是 2026-10-10「栏目静默留空」的第二个成因：不是名字写错，
        # 而是点击与回读之间存在竞态，回读那一刻还没勾上，而旧代码只把回读打日志。
        ok_name = False
        for attempt in range(1, 4):
            res = page.evaluate(
                """(name) => {
                    const box = document.querySelector('.modal__publish-article')
                                || document.querySelector('.modal');
                    const row = [...box.querySelectorAll('.form-entry')]
                        .find(e => (e.innerText || '').includes('分类专栏'));
                    if (!row) return 'no-row';
                    const inp = [...row.querySelectorAll('input.tag__option-chk')]
                        .find(i => i.value === name);
                    if (!inp) return 'not-found';
                    if (inp.checked) return 'already';
                    inp.closest('label').click();
                    return 'clicked';
                }""", name)
            log(f"栏目「{name}」-> {res}（第 {attempt} 次）")
            if res in ("clicked", "already"):
                # 逐次加长等待：900ms → 1500ms → 2200ms
                page.wait_for_timeout(900 + (attempt - 1) * 650)
                if name in (read_categories(page) or []):
                    ok_name = True
                    break
                if res == "already":
                    # 报告已勾选、回读却没有 → 页面状态异常，再点也没用，直接判失败
                    log(f"栏目「{name}」页面报告已勾选，但回读不含它，判定未生效")
                    break
                log(f"栏目「{name}」点了但回读没生效，重试…")
                continue
            ok_all = False
            break
        if not ok_name:
            ok_all = False
        page.wait_for_timeout(300)

    got = read_categories(page)
    log(f"栏目回读: {got}")

    # 硬闸门：只在"真的选完了"之后才放行。
    # 历史教训（2026-10-10）：这里以前只 return ok_all，调用方又不看返回值，
    # 于是当栏目名不在账号专栏列表里（比如写成了大写的 Redis）时，
    # 日志里只有一行「不在你的专栏列表里」，页面按「不放入任何栏目」保存，
    # 而 front-matter 里依然写着 category: redis —— 草稿是成功的、栏目是空的，
    # 上游完全看不到失败。现在改成：目标栏目没全部勾上就直接抛异常，中止这一篇。
    want = [n for n in names[:3]]
    missing = [n for n in want if n not in (got or [])]
    if missing or not ok_all:
        raise CategoryGateAbort(
            f"栏目没选上，已中止本篇（不提交）。目标={want} 回读={got} "
            f"未勾上={missing}；该账号可选栏目: {have}"
        )
    return True


# ---------------------------------------------------------------- 定时发布

TIME_STEP_MIN = 15           # CSDN 的时间选择器是 15 分钟一档
SCHEDULE_LEAD_MIN = 4 * 60   # 官方限制：定时时间至少在当前时间之后 4 小时
SCHEDULE_MARGIN_MIN = 10     # 贴着下限容易被判越界，留一点余量
SCHEDULE_MAX_DAYS = 6        # 实测 getScheduledReleaseTime 给的上限约 6 天（弹窗文案写 7 天）


def align_step(dt):
    """把时间往下对齐到 15 分钟一档 —— CSDN 的时间选择器只有 15 分钟刻度。"""
    return dt.replace(minute=dt.minute - dt.minute % TIME_STEP_MIN,
                      second=0, microsecond=0)


def parse_schedule_when(when, now=None):
    """把配置/命令行里的定时时间解析成 datetime。

    支持：
        2026-10-09 19:30 / 2026-10-09 19:30:00 / 2026-10-09T19:30
        +26h / +90m / +3d        相对现在（h=小时 / m=分钟 / d=天）
        2026-10-09               只给日期 = 当天 09:00
    """
    now = now or datetime.now()
    s = str(when).strip()
    if not s:
        return None
    m = re.fullmatch(r"\+?\s*(\d+)\s*([hmd])", s, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return now + {"h": timedelta(hours=n), "m": timedelta(minutes=n),
                      "d": timedelta(days=n)}[unit]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(hour=9, minute=0, second=0)
    except ValueError:
        raise ValueError(
            f"看不懂的定时时间: {when!r}；支持 “2026-10-09 19:30” / “+26h” / “+3d” / “2026-10-09”")


def schedule_window(now=None):
    """CSDN 允许的定时区间（保守值，实测接口返回 start=now+4h、end=now+6d）。"""
    now = now or datetime.now()
    return (now + timedelta(minutes=SCHEDULE_LEAD_MIN + SCHEDULE_MARGIN_MIN),
            now + timedelta(days=SCHEDULE_MAX_DAYS))


def check_schedule_window(dt, now=None):
    """校验时间是否落在 CSDN 的定时区间里。返回 (ok, 说明)。"""
    lo, hi = schedule_window(now)
    if dt < lo:
        return False, (f"{dt:%Y-%m-%d %H:%M} 距现在不足 4 小时 10 分钟；"
                       f"CSDN 要求定时时间晚于 {lo:%Y-%m-%d %H:%M}")
    if dt > hi:
        return False, (f"{dt:%Y-%m-%d %H:%M} 超出 CSDN 的定时上限（约 6 天）；"
                       f"最晚只能到 {hi:%Y-%m-%d %H:%M}")
    return True, f"{dt:%Y-%m-%d %H:%M}"


def open_schedule_dialog(page):
    """点发布弹窗底部的「定时发布」，等它自己的子弹窗出来。

    实测（2026-10）：点下去会先请求
    bizapi.csdn.net/blog-console-api/v1/editor/getScheduledReleaseTime
    拿到可选区间，然后弹 .el-dialog.publish-timer-dialog。
    这个请求失败时子弹窗不会出现，所以这里要给出可读的原因。
    """
    btn, _ = find_visible(page, CFG["selectors"]["schedule_button"], 8000, "定时发布按钮")
    if not btn:
        return None, "找不到发布弹窗底部的「定时发布」按钮（弹窗结构可能又变了）"
    btn.click()
    dlg, _ = find_visible(page, CFG["selectors"]["schedule_dialog"], 12000, "定时发布子弹窗")
    if not dlg:
        return None, ("点了「定时发布」但子弹窗没出现：通常是没有拿到 "
                      "getScheduledReleaseTime 的返回（网络/接口改动）")
    return dlg, ""


def read_scheduled_time(page):
    """回读子弹窗里已经填好的「选择日期 / 选择时间」。"""
    try:
        return page.evaluate(
            """() => {
                const d = document.querySelector('.publish-timer-dialog');
                if (!d) return null;
                const inputs = [...d.querySelectorAll('input')];
                const pick = kw => {
                    const el = inputs.find(i => (i.placeholder || '').includes(kw));
                    return el ? el.value : null;
                };
                return {date: pick('日期'), time: pick('时间')};
            }"""
        )
    except Exception:
        return None


def fill_schedule_dialog(page, dt):
    """在子弹窗里填好日期和时间（只填，不提交）。返回 True/False。

    两个坑：
      1) 「选择时间」在选完日期之前是禁用的（父元素带 is-disabled），必须等它解锁；
      2) 时间选择器是按 15 分钟列出的，填的值先对齐到 15 分钟刻度最稳。
    """
    d = dt
    date_in = page.locator(CFG["selectors"]["schedule_date_input"][0]).last
    try:
        date_in.click()
        page.wait_for_timeout(700)
        date_in.fill(f"{d:%Y-%m-%d}")
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
    except Exception as e:
        log(f"填「选择日期」失败: {e.__class__.__name__}: {e}")
        return False

    try:
        page.wait_for_function(
            """() => {
                const el = document.querySelector(".publish-timer-dialog input[placeholder*='选择时间']")
                        || [...document.querySelectorAll('input')]
                             .find(i => (i.placeholder || '').includes('选择时间'));
                if (!el) return false;
                const box = el.closest('.el-date-editor') || el.parentElement;
                return !box.classList.contains('is-disabled');
            }""", timeout=10000)
    except Exception:
        log("「选择时间」一直是禁用状态（日期可能没选上）")

    time_in = page.locator(CFG["selectors"]["schedule_time_input"][0]).last
    try:
        time_in.click()
        page.wait_for_timeout(800)
        # 时间框下拉是个「列表」（00:00 / 00:15 / …），点中对应那一项最稳：
        # 既设置值又顺手收起浮层。早期版本只 fill + 回车，值是对的，但浮层一直开着，
        # 后面点「定时发布」时被这个浮层的列表项挡住 → 点击超时（真实跑一次才暴露）。
        opt = page.locator(f".time-select-item:text-is('{d:%H:%M}')").last
        if opt.count():
            opt.click(timeout=5000)
            page.wait_for_timeout(500)
            log(f"已在时间下拉里点选 {d:%H:%M}")
        else:
            log(f"时间下拉里没找到 {d:%H:%M}，回退到直接输入")
            time_in.fill(f"{d:%H:%M}:00")
            page.keyboard.press("Enter")
            page.wait_for_timeout(800)
    except Exception as e:
        log(f"填「选择时间」失败: {e.__class__.__name__}: {e}")
        return False
    return True


def close_open_pickers(page):
    """收起可能还开着的日期/时间下拉浮层。

    浮层挂在 body 上、位置又正好压在子弹窗按钮上，不关掉就会「元素可见却点不到」。
    点子弹窗自己的标题区即可（在弹窗内部，不会关掉弹窗）。
    """
    for sel in (".publish-timer-dialog .el-dialog__header",
                ".publish-timer-dialog .el-dialog__title"):
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=3000)
                page.wait_for_timeout(500)
                return True
        except Exception:
            continue
    return False


def verify_scheduled_time(page, dt):
    """回读并校验子弹窗里的时间确实等于目标时间。"""
    got = read_scheduled_time(page)
    if not got:
        return False, "读不到定时发布子弹窗的时间输入框"
    want_date, want_hm = f"{dt:%Y-%m-%d}", f"{dt:%H:%M}"
    ok = (got.get("date") == want_date
          and str(got.get("time") or "").startswith(want_hm))
    detail = f"日期={got.get('date')!r} 时间={got.get('time')!r}（目标 {want_date} {want_hm}）"
    return ok, detail


def confirm_schedule(page):
    """点子弹窗里的红色「定时发布」——这是真正提交的那一下。

    实测坑：日期/时间下拉的浮层挂在 body 上，位置会压住这个按钮，
    Playwright 的常规点击会一直等"元素可点"直到超时（报 intercepts pointer events）。
    所以这里三步走：常规点 → 先收起浮层再点 → 最后用 JS 直接点。
    """
    btn, sel = find_visible(page, CFG["selectors"]["schedule_confirm"], 8000,
                            "定时发布子弹窗里的确认按钮")
    if not btn:
        raise RuntimeError(
            "找不到定时发布子弹窗里的红色「定时发布」按钮，请查看截图并更新 "
            "config.json selectors.schedule_confirm")
    try:
        btn.click(timeout=8000)
    except Exception as e:
        log(f"常规点击失败（{e.__class__.__name__}），先收起可能挡住按钮的下拉浮层")
        close_open_pickers(page)
        try:
            btn.click(timeout=8000)
        except Exception as e2:
            log(f"仍点不到（{e2.__class__.__name__}: {e2}），改用 JS 直接触发点击")
            page.evaluate(
                """() => {
                    const d = document.querySelector('.publish-timer-dialog');
                    const b = d && d.querySelector('button.btn-b-red');
                    if (b) b.click();
                }"""
            )
    log(f"已点击子弹窗的「定时发布」（{sel}），等待提交结果…")


# 平台自己弹的"拦住提交"的提示。实测最常见的是发文额度：
#   当前已达发文上限，可开通会员提升额度
# 这不是工具故障，也不会在草稿箱/内容管理里留下任何东西 —— 必须明确区分，
# 否则只会看到"结果未确认"，让人以为是脚本坏了。
BLOCKING_HINTS = ("已达发文上限", "发文上限", "发文额度", "开通会员提升额度",
                  "今日发文额度", "发布次数已达", "请勿频繁")
# 频率限制要单独识别：它是"等一会儿再来"，不是"今天不行"，重试有意义
FREQ_HINTS = ("文章频繁发布", "频繁发布", "稍后再试", "操作过于频繁", "请求过于频繁")


def read_blocking_notice(page, hints=None):
    """读「平台拦住提交」的提示弹窗/toast 文案；没有就返回 None。

    只扫**弹窗 / toast 容器**，绝不扫整页文本：编辑器底部常驻一条
    `.publish-quota-tip`「今日发文额度已用完，」（看结构它一直在），
    扫整页会把"提交成功"也误判成"被拒绝"。
    实测拦截弹窗是 `.el-message-box`（当前已达发文上限…）；
    频率限制是一条 `.el-message` toast（文章频繁发布，请稍后再试）。
    """
    try:
        return page.evaluate(
            """(kws) => {
                const vis = el => el.getClientRects().length > 0;
                const boxes = [...document.querySelectorAll(
                    '.el-message-box, .el-message, .el-notification, .el-message-box__wrapper')];
                for (const b of boxes) {
                    if (!vis(b)) continue;
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (t && kws.some(k => t.includes(k))) return t.slice(0, 200);
                }
                return null;
            }""", list(hints or BLOCKING_HINTS))
    except Exception:
        return None


def read_any_notice(page, timeout_ms=4000):
    """在 timeout 内轮询，抓住一闪而过的 toast（频率限制那种只显示两三秒）。

    返回 (文案, 类型)；类型是 'freq' | 'block' | None。
    """
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        t = read_blocking_notice(page, FREQ_HINTS)
        if t:
            return t, "freq"
        t = read_blocking_notice(page)
        if t:
            return t, "block"
        page.wait_for_timeout(400)
    return None, None


def wait_schedule_success(page, timeout_ms=45000):
    """等定时提交的结果。

    没有已知的固定文案，所以三条路一起看：
      * 出现「成功」字样的提示（toast / notification）；
      * 定时子弹窗消失；
      * 外面那个发布弹窗也消失。
    另外一有"发文上限"这类拦截提示就立刻返回，不白等满 45 秒。
    """
    deadline = time.time() + timeout_ms / 1000
    closed_at = None
    while time.time() < deadline:
        notice = read_blocking_notice(page)
        if notice:
            return False, f"平台拦下：{notice}"
        try:
            toast = page.evaluate(
                """() => {
                    const vis = el => el.getClientRects().length > 0;
                    const nodes = [...document.querySelectorAll(
                        '.el-message, .el-notification, .el-message-box')].filter(vis);
                    return nodes.map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim())
                                .filter(Boolean).join(' | ');
                }"""
            )
        except Exception:
            toast = ""
        if toast and ("成功" in toast or "已提交" in toast or "审核" in toast):
            return True, toast[:120]
        gone = page.evaluate(
            """() => !document.querySelector('.publish-timer-dialog')""")
        if gone:
            if closed_at is None:
                closed_at = time.time()
            elif time.time() - closed_at > 4:
                return True, "定时子弹窗已关闭（未捕获到提示文案）"
        else:
            closed_at = None
        page.wait_for_timeout(500)
    return False, "没等到明确的成功信号，请按截图核对定时发布列表"


def wait_publish_success(page, timeout_ms=90000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        url = page.url
        if "/article/details/" in url:
            return url
        try:
            if page.locator("text=发布成功").first.is_visible():
                page.wait_for_timeout(2500)
                return page.url
        except Exception:
            pass
        page.wait_for_timeout(500)
    return None


# ---------------------------------------------------------------- 子命令

# CONFIG.md 的两个真实落点（按优先级）：
#   1) 项目内技能副本   <项目根>/skills/csdn-publish/CONFIG.md
#   2) 用户级技能       ~/.workbuddy/skills/csdn-publish/CONFIG.md   ← 真正被加载的那份
# 用 Path.home() 而不是写死 C:\Users\ZYX，换机器/换用户名都不用改代码。
CONFIG_DOC_CANDIDATES = (
    ROOT / "skills" / "csdn-publish" / "CONFIG.md",
    Path.home() / ".workbuddy" / "skills" / "csdn-publish" / "CONFIG.md",
)


def find_config_doc():
    """找「全部可配置参数」那份文档。

    单一来源放在技能目录（skills/csdn-publish/CONFIG.md），
    这样 `/csdn-publish config` 和 `.\\csdn.bat config` 打印的是同一份内容，
    不会出现两份清单各自漂移。想换位置就设环境变量 DSH_CSDN_CONFIG_DOC。
    """
    override = os.environ.get("DSH_CSDN_CONFIG_DOC", "").strip()
    paths = ([Path(override)] if override else []) + list(CONFIG_DOC_CANDIDATES)
    for p in paths:
        try:
            if p and Path(p).is_file():
                return Path(p)
        except OSError:
            continue
    return None


def cmd_config():
    """打印全部可配置参数（job 字段、config.json、命令行开关、优先级）。"""
    doc = find_config_doc()
    if not doc:
        log("没找到 CONFIG.md。已在这些位置找过：")
        for p in CONFIG_DOC_CANDIDATES:
            log(f"    {p}")
        log("也可以用环境变量 DSH_CSDN_CONFIG_DOC 指定别的位置。")
        return 1
    log(f"全部可配置参数（来自 {doc}）:")
    print()
    print(doc.read_text(encoding="utf-8-sig"))
    return 0


def cmd_login(pw):
    b = Browser(pw)
    b.launch()
    page = b.page()
    try:
        logged, hits = b.is_logged_in()
    except Exception as e:
        log(f"读取登录态失败: {e.__class__.__name__}: {e}")
        b.close()
        return 1
    if logged:
        log(f"已经是登录状态（{hits}），无需重复登录")
        b.close()
        return 0

    page.goto(CFG["urls"]["home"], wait_until="domcontentloaded")
    log("=" * 58)
    log("请在弹出的浏览器里完成登录（扫码 / 账号密码都行）")
    log("登录成功后本脚本会自动检测并保存登录态，然后自动关闭浏览器")
    log("=" * 58)

    deadline = time.time() + 300
    while time.time() < deadline:
        logged, hits = b.is_logged_in()
        if logged:
            log(f"登录成功，检测到 cookie: {hits}")
            page.wait_for_timeout(2000)
            b.shot(page, "login-ok")
            b.close()
            return 0
        # 登录过程中 CSDN 也会弹安全验证：这里只提示、不阻塞（本来就是人工步骤）
        try:
            kill_bubbles(page)
            risk_gate(page, b, "登录", announce_only=True)
        except Exception:
            pass
        page.wait_for_timeout(1500)
    log("等待登录超时（5 分钟），请重新运行 login")
    b.shot(page, "login-timeout")
    b.close()
    return 1


def cmd_check(pw):
    b = Browser(pw)
    b.launch()
    try:
        logged, hits = b.is_logged_in()
    except Exception as e:
        log(f"读取登录态失败: {e.__class__.__name__}: {e}")
        log("（浏览器刚启动就断开，通常是上次崩溃残留；重跑一次 check 即可）")
        b.close()
        return 1
    if logged:
        log(f"登录态有效: {hits}")
        rc = 0
    else:
        log("登录态无效或已过期，请运行: python publish_csdn.py login")
        rc = 1
    b.close()
    return rc


INSPECT_JS = """
() => {
  const out = {url: location.href, title: document.title, nodes: [], inputs: [],
               buttons: [], editables: [], articleBarHtml: '(未找到 .article-bar)'};

  const desc = (el) => {
    const cls = (el.className && typeof el.className === 'string')
      ? el.className.split(/\\s+/).filter(Boolean).slice(0, 4).join('.') : '';
    return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : '');
  };
  // 注意：不能用 offsetParent===null 判断可见性，position:fixed 的元素也会是 null，
  // 之前正是这个原因漏掉了标题输入框。
  const vis = (el) => el.getClientRects().length > 0;

  document.querySelectorAll('input, textarea').forEach(el => {
    out.inputs.push({sel: desc(el), placeholder: el.placeholder || '',
                     type: el.type || '', visible: vis(el), value: (el.value || '').slice(0, 30)});
  });

  document.querySelectorAll('button').forEach(el => {
    const t = (el.innerText || '').trim().slice(0, 20);
    if (!t) return;
    out.buttons.push({sel: desc(el), text: t, visible: vis(el)});
  });

  document.querySelectorAll('[contenteditable="true"]').forEach(el => {
    out.editables.push({sel: desc(el), visible: vis(el),
                        text: (el.innerText || '').trim().slice(0, 40)});
  });

  ['div.editor', 'pre.editor__inner', '.cledit-section', '.article-bar',
   '.CodeMirror', '.cm-editor'].forEach(s => {
    document.querySelectorAll(s).forEach(el => out.nodes.push({sel: s, found: desc(el)}));
  });

  const bar = document.querySelector('.article-bar');
  if (bar) out.articleBarHtml = bar.outerHTML.slice(0, 6000);

  // 紧凑元素树：整页 HTML 是一整行没法读，树状结构才看得出标题框在哪
  const tree = (el, depth) => {
    if (!el || depth > 5) return null;
    const o = {el: desc(el)};
    if (el.placeholder) o.ph = el.placeholder;
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') o.leaf = true;
    const kids = [...el.children].map(c => tree(c, depth + 1)).filter(Boolean);
    if (kids.length) o.kids = kids;
    return o;
  };
  out.articleBarTree = bar ? tree(bar, 0) : null;
  return out;
}
"""


def cmd_inspect(pw):
    b = Browser(pw)
    b.launch()
    page = b.page()
    page.goto(CFG["urls"]["editor"], wait_until="domcontentloaded")
    page.wait_for_timeout(6000)

    logged, _ = b.is_logged_in()
    if not logged:
        log("警告：当前未登录，导出的结构可能只是登录页")
    if "passport" in page.url or "login" in page.url:
        log(f"被重定向到登录页: {page.url}")
        log("请先运行: python publish_csdn.py login")
        b.shot(page, "inspect-redirect")
        b.close()
        return 1

    data = page.evaluate(INSPECT_JS)
    b.shot(page, "inspect-editor")

    html_path = OUTPUT_DIR / "editor.html"
    html_path.write_text(page.content(), encoding="utf-8")

    json_path = OUTPUT_DIR / "inspect.json"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"编辑器 URL: {data['url']}")
    log(f"输入框/文本域 {len(data['inputs'])} 个:")
    for i in data["inputs"]:
        flag = "" if i.get("visible") else "  [隐藏]"
        log(f"    {i['sel']}  placeholder={i['placeholder']!r} type={i['type']!r}{flag}")
    log(f"可编辑区域 {len(data['editables'])} 个:")
    for i in data["editables"]:
        flag = "" if i.get("visible") else "  [隐藏]"
        log(f"    {i['sel']}{flag}  开头文本={i['text']!r}")
    log(f"按钮 {len(data['buttons'])} 个:")
    for i in data["buttons"]:
        log(f"    {i['text']!r}  <- {i['sel']}")
    log(f"结构详情: {json_path}")
    log(f"完整 HTML: {html_path}")
    bar_html = OUTPUT_DIR / "article-bar.html"
    bar_html.write_text(data.get("articleBarHtml", ""), encoding="utf-8")

    def dump(node, depth=0):
        if not node:
            return
        mark = "   <-- 输入元素" if node.get("leaf") else ""
        ph = f"  placeholder={node['ph']!r}" if node.get("ph") else ""
        log("    " + "  " * depth + node["el"] + ph + mark)
        for kid in node.get("kids", []):
            dump(kid, depth + 1)

    log("编辑栏结构树 (.article-bar):")
    dump(data.get("articleBarTree"))
    b.close()
    return 0


def _kill_crop_overlay(page):
    """把封面裁剪窗的残留遮罩真正从布局里挪走。返回处理掉的节点数。

    为什么单独抽出来：`dismiss_overlays` 只调用一次，而 Vue 会在后续重渲染里
    把 `.vue-image-crop-upload` 重新挂回 DOM（实测 2026-10-11：清完再点「添加文章
    标签」仍被挡 30 秒）。所以在每个"关键点击"之前都要再清一遍。
    """
    try:
        return page.evaluate("""() => {
            const els = [...document.querySelectorAll('.vue-image-crop-upload')]
                .filter(e => e.getClientRects().length > 0);
            els.forEach(e => {
                e.style.pointerEvents = 'none';
                e.style.display = 'none';
                e.style.visibility = 'hidden';
            });
            return els.length;
        }""")
    except Exception:
        return 0


def dismiss_overlays(page):
    """关掉可能挡住点击的浮层。

    这里多了一层"浏览器自己的气泡"：实测 Chrome 的
      * 「是否翻译此页面」气泡 —— 盖在编辑器上会**拦截点击**，表现成"元素点不到"；
      * 「Chrome 未正常关闭，是否恢复」气泡 —— 让页面状态不可信。
    这两个都在 shadow DOM 里，普通选择器点不到，交给 kill_bubbles 处理。

    另外还兜一层「图片裁剪窗的残留遮罩」：set_cover 点完「确认上传」后，CSDN 的
    .vue-image-crop-upload 是异步关闭的。慢的时候（网络/风控）它会一直挂在 DOM 里，
    作为 position:fixed 遮罩拦住后续所有点击——实测 2026-10-10 那批 10 篇里有 2 篇
    因此卡在最后一步「保存为草稿」30 秒超时。这里在每次关浮层时顺手把它清掉。
    """
    kill_bubbles(page)
    # 清掉图片裁剪窗的残留遮罩（真正从布局里移除，光改 pointer-events 不够）
    try:
        stale = page.evaluate("""() => {
            const els = [...document.querySelectorAll('.vue-image-crop-upload')]
                .filter(e => e.getClientRects().length > 0);
            els.forEach(e => {
                e.style.pointerEvents = 'none';
                e.style.display = 'none';
                e.style.visibility = 'hidden';
            });
            return els.length;
        }""")
        if stale:
            log(f"已强制隐藏 {stale} 个残留的图片裁剪窗遮罩")
    except Exception:
        pass
    for sel in ["button.cfw-secondary-btn", "button:has-text('我知道了')"]:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click()
                log(f"已关闭浮层: {sel}")
                page.wait_for_timeout(600)
        except Exception:
            pass


DIALOG_JS = """
() => {
  const vis = el => el.getClientRects().length > 0;
  const desc = el => el.tagName.toLowerCase()
    + (el.id ? '#' + el.id : '')
    + ((typeof el.className === 'string' && el.className.trim())
        ? '.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.') : '');
  const roots = [...document.querySelectorAll('.el-dialog, .el-message-box, [role=dialog], .modal')]
                  .filter(vis);
  return {
    dialogCount: roots.length,
    dialogs: roots.map(d => ({
      cls: (typeof d.className === 'string' ? d.className : '').slice(0, 80),
      text: (d.innerText || '').replace(/\\s+/g, ' ').slice(0, 400),
      inputs: [...d.querySelectorAll('input, textarea')].map(el => ({
        sel: desc(el), ph: el.placeholder || '', type: el.type || '', vis: vis(el)})),
      buttons: [...d.querySelectorAll('button')].filter(vis).map(el => ({
        sel: desc(el), text: (el.innerText || '').trim().slice(0, 16)})),
      widgets: [...d.querySelectorAll('.el-select, select, .el-radio, .el-checkbox, .el-tag')]
        .filter(vis).map(el => ({sel: desc(el), text: (el.innerText || '').trim().slice(0, 30)}))
    }))
  };
}
"""


DIALOG_STATE_JS = """
() => {
  const box = document.querySelector('.modal__publish-article') || document.querySelector('.modal');
  if (!box) return null;
  const rowOf = (kw) => [...box.querySelectorAll('.form-entry')]
      .find(e => (e.innerText || '').includes(kw));
  const out = {};
  // 已选标签
  const tagRow = rowOf('添加文章标签') || rowOf('文章标签');
  out.tags = tagRow ? [...tagRow.querySelectorAll('.el-tag')]
      .filter(e => e.querySelector('.el-tag__close'))
      .map(e => (e.innerText || '').replace(/[\\s\\u00d7\\u2715]+$/, '').trim()) : [];
  // 封面：有真实图片说明封面已设置
  const coverRow = rowOf('添加封面');
  out.coverImages = coverRow ? [...coverRow.querySelectorAll('img')]
      .filter(i => (i.src || '').startsWith('data:image') ||
                   (/^https?:/.test(i.src || '') && !i.src.includes('editor.csdn.net')))
      .map(i => (i.src || '').slice(0, 32)) : [];
  // 摘要
  const ta = box.querySelector('textarea.el-textarea__inner');
  out.summary = ta ? (ta.value || '').slice(0, 60) : null;
  // 分类专栏（栏目）
  const catRow = rowOf('分类专栏');
  out.categories = catRow ? [...catRow.querySelectorAll('input.tag__option-chk')]
      .filter(i => i.checked).map(i => i.value) : [];
  return out;
}
"""


def dump_publish_dialog(page):
    """把发布弹窗的真实结构导出到 output/，CSDN 改版时照这个改 config.json。"""
    try:
        state = page.evaluate(DIALOG_STATE_JS)
        if state:
            log("发布弹窗字段回读：")
            log(f"  标签      : {state['tags']}")
            log(f"  封面图片  : {len(state['coverImages'])} 张 "
                f"{'(已设置)' if state['coverImages'] else '(未设置)'}")
            log(f"  摘要      : {state['summary']!r}")
            log(f"  栏目      : {state['categories'] or '(未选，不放入任何栏目)'}")
        sched = read_scheduled_time(page)
        if sched:
            log(f"  定时发布  : 日期={sched.get('date')!r} 时间={sched.get('time')!r}")
    except Exception as e:
        log(f"字段回读失败: {e}")

    try:
        data = page.evaluate(DIALOG_JS)
        path = OUTPUT_DIR / "publish-dialog.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"发布弹窗结构已导出: {path}")
        return data
    except Exception as e:
        log(f"导出发布弹窗结构失败: {e}")
        return None


def cmd_publish(pw, md_path, draft=False, dry_run=False, skip_lint=False, force=False,
                schedule=None):
    art = load_article(md_path)
    log(f"文章: {art['title']}  ({len(art['body'])} 字符, 标签={art['tags']})")

    # 发布前体检：单篇路径（csdn.bat publish）也要过一遍。
    # csdn_job.py 自己已经体检过了，会传 skip_lint=True，避免同一份问题打印两次。
    if not skip_lint:
        try:
            import csdn_lint as cl
            issues = cl.lint_article(
                title=art["title"], body=art["body"],
                summary=art.get("summary", ""), tags=art["tags"],
                cover=art.get("cover", ""), category=art.get("category", ""),
                cover_base=art.get("base"),
                published_titles=cl.load_published_titles())
            errs, warns = cl.summarize(issues)
            for line in cl.format_issues(warns, indent="  "):
                log(line)
            if errs:
                log("发布前体检没通过：")
                for line in cl.format_issues(errs, indent="  "):
                    log(line)
                if dry_run or force:
                    log("  这是 dry-run/--force，只提示不拦。")
                else:
                    log("  未提交任何内容。要照发加 --force，想改就改完再跑。")
                    return 1
        except ImportError:
            pass

    mode = (CFG.get("crash", {}).get("onCrash") or "restart").lower()
    max_restarts = max(0, int(CFG.get("crash", {}).get("maxRestarts", 2)))
    wait_sec = max(0, int(CFG.get("crash", {}).get("restartWaitSec", 5)))
    budget = max_restarts if mode == "restart" else 0

    b = None
    attempt = 0
    recover = None          # 下一轮是"只新开一个页面"还是"重启浏览器"
    while True:
        if b is None:
            b = Browser(pw)
        elif recover == "restart":
            b.close()          # 先把上一轮那份收拾干净（并清掉心跳）
            time.sleep(wait_sec)
            b = Browser(pw)
        # recover == "page" 时什么都不用做：context 还活着，直接复用
        try:
            rc = run_publish_flow(b, art, draft, dry_run, schedule)
            b.close()
            return rc
        except RiskGateAbort as e:
            # 风控不是故障：内容没提交，也不会留下半成品。明确说清楚，别让人以为脚本坏了。
            log(f"⛔ 安全验证未解决，本次没有提交任何内容：{e}")
            log("  内容不会进草稿箱，重新跑一次即可（建议这次人守在旁边点一下验证）。")
            b.close()
            return 5
        except CategoryGateAbort as e:
            # 栏目没勾上就中止：宁可不提交，也不要发出去一篇栏目空着的稿子。
            # rc=7 是"栏目校验失败"，与内容错误(1)、平台限制(3/4)、风控(5) 区分开。
            log(f"⛔ 栏目校验没通过，本次没有提交任何内容：{e}")
            log("  常见原因：job.json 里的 category 和账号真实专栏名不一致"
                "（大小写/空格/别名），照日志里的「可选栏目」清单改完再跑。")
            b.close()
            return 7
        except SystemExit:
            raise
        except Exception as e:
            log(f"出错: {e.__class__.__name__}: {e}")

            if not is_browser_dead(e):
                # 异常跟浏览器断开无关 → 真·业务错误（选择器改了、元素找不到），
                # 重试没有意义，直接报错，别浪费次数。
                try:
                    b.shot(b.page(), "error")
                except Exception:
                    pass
                b.close()
                return 1

            # 浏览器相关的断开。先用"能不能在同一个 context 里拿到可用页面"来分级：
            #   * 能  → 只是标签页被关（CSDN 风控常这么干），原地新开一个继续，别重启浏览器；
            #   * 不能 → context/进程真的没了，只能重启浏览器。
            newp = page_recoverable(b)
            if newp is not None:
                if attempt >= budget:
                    log("页面反复被关闭，且重试次数已用完。")
                    b.close()
                    return 6
                attempt += 1
                announce([
                    f"⚠ 标签页被关掉了（浏览器进程还活着，很可能是 CSDN 风控），"
                    f"正在新开页面续跑（第 {attempt}/{budget} 次）",
                    "  不重启浏览器：登录态和 context 都还在，新开一个标签页更快也更稳。",
                ])
                recover = "page"
                continue

            # context 真的不可用 → 重启浏览器
            try:
                b.shot(b.page(), "crash")
            except Exception:
                pass
            if attempt >= budget:
                log("浏览器异常关闭，且重试次数已用完。" if budget else
                    "浏览器异常关闭（crash.onCrash=abort，不自动重试）。")
                b.close()
                return 6
            attempt += 1
            announce([
                f"⚠ 浏览器异常关闭，正在自动恢复（第 {attempt}/{budget} 次）",
                "  为什么能安全重来：内容没提交时草稿箱不会留下半成品，",
                "  而 CSDN 编辑器会恢复上次内容，重跑不会变成重复发布。",
                f"  等 {wait_sec} 秒后重新拉起浏览器，然后从「打开编辑器」重来一遍。",
            ])
            recover = "restart"
            b.close()
            b = None


def run_publish_flow(b, art, draft=False, dry_run=False, schedule=None):
    """跑一遍发布链路，返回退出码。

    和以前唯一的区别：所有可能被风控/崩溃打断的地方都过了 `guard()` 闸门。
    退出码沿用原约定：0 成功 / 1 硬错误 / 2 结果未确认 / 3 平台拒绝(额度) /
    4 频率限制 / 5 安全验证未解决 / 6 浏览器反复崩溃。
    """
    b.launch()
    page = b.page()

    logged, _ = b.is_logged_in()
    if not logged:
        log("未登录，请先运行: python publish_csdn.py login")
        return 1

    page.goto(CFG["urls"]["editor"], wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    if "passport" in page.url or "/login" in page.url:
        log(f"被重定向到登录页，登录态可能已过期: {page.url}")
        b.shot(page, "publish-redirect")
        return 1
    b.shot(page, "01-editor")

    # 打开编辑器之后第一件事就是过闸门：如果 CSDN 把我们丢到验证页，
    # 后面所有"找不到元素"其实都是这个原因，越早识别越省事。
    guard(page, b, "打开编辑器")

    dismiss_overlays(page)

    # 顺序很重要：导入会把标题自动设成文件名，所以必须先导入、后写标题
    import_body(page, art["body"])
    page.wait_for_timeout(CFG["step_wait_ms"])
    guard(page, b, "导入正文")
    fill_title(page, art["title"])
    page.wait_for_timeout(800)
    b.shot(page, "02-filled")

    if draft:
        log("本次是「存草稿」模式：弹窗里照样把封面/摘要/栏目/标签填好，最后只点保存为草稿")

    btn, _ = find_visible(page, CFG["selectors"]["publish_button"], 10000, "发布按钮")
    if not btn:
        raise RuntimeError(
            "找不到发布按钮，请跑 inspect 并更新 config.json selectors.publish_button"
        )
    btn.click()
    page.wait_for_timeout(3500)
    b.shot(page, "03-publish-dialog")
    guard(page, b, "打开发布弹窗")

    # 顺序很关键：加完标签后标签浮层会一直留在页面上，挡住它下面的
    # 封面、摘要和栏目控件。所以封面、摘要、栏目必须先做，标签放最后
    # —— 而最后的「发布文章」按钮验证过不会被该浮层遮挡。
    if art.get("cover"):
        set_cover(page, art["cover"], art.get("base"))
    fill_summary(page, art.get("summary"))
    guard(page, b, "填写摘要")
    select_categories(page, art.get("category"))
    fill_tags(page, art["tags"])
    b.shot(page, "04-dialog-filled")

    # ---- 定时发布：走发布弹窗底部的「定时发布」→ 子弹窗选日期+时间 ----
    if schedule:
        target = parse_schedule_when(schedule)
        target = align_step(target)
        ok, why = check_schedule_window(target)
        log(f"定时发布时间: {target:%Y-%m-%d %H:%M}（{why}）")
        if not ok:
            log(f"定时时间不合法，未提交任何内容: {why}")
            return 1

        dlg, fail = open_schedule_dialog(page)
        if not dlg:
            log(f"打不开定时发布子弹窗: {fail}")
            b.shot(page, "04-schedule-failed")
            return 1
        b.shot(page, "05-schedule-dialog")

        if not fill_schedule_dialog(page, target):
            log("定时时间没填成功，未提交任何内容")
            b.shot(page, "05-schedule-fill-failed")
            return 1
        good, detail = verify_scheduled_time(page, target)
        log(f"定时时间回读: {detail} -> {'一致' if good else '不一致'}")
        b.shot(page, "06-schedule-filled")
        if not good:
            log("回读的时间和目标不一致，未提交任何内容（CSDN 的时间选择器可能改版了）")
            return 1

        # 提交是不可逆的那一下，提交前再过一次闸门
        guard(page, b, "提交定时发布前")

        if dry_run:
            dump_publish_dialog(page)
            log("--dry-run：定时发布子弹窗已填好，停在最后那次不可逆的点击之前，"
                "浏览器即将关闭")
            return 0

        confirm_schedule(page)
        page.wait_for_timeout(3000)
        b.shot(page, "07-scheduled")
        ok2, msg = wait_schedule_success(page)
        b.shot(page, "07-scheduled-result")
        if ok2:
            log(f"定时发布已提交: {target:%Y-%m-%d %H:%M}（{msg}）")
            return 0
        if msg.startswith("平台拦下"):
            log(f"CSDN 拒绝了这次定时发布：{msg}")
            log("  常见原因就是发布额度用尽（草稿不占额度、公开/定时发布占额度）。"
                "这属于账号限制，不是工具故障；草稿箱和内容管理页里都不会留下这篇。")
            return 3
        log(f"未能确认定时发布结果: {msg}")
        return 2

    if dry_run:
        dump_publish_dialog(page)
        log("--dry-run：弹窗已填好，停在最后那次不可逆的点击之前，浏览器即将关闭")
        return 0

    # 存草稿也必须走完弹窗：直接点编辑器里的「保存草稿」不会带上标签/摘要/栏目
    if draft:
        # 点最后那颗按钮前再清一次浮层：封面裁剪窗的遮罩可能刚在异步关闭，
        # 挡住「保存为草稿」会造成 30 秒点击超时（2026-10-10 实测栽过 2 篇）。
        dismiss_overlays(page)
        _kill_crop_overlay(page)
        btn, _ = find_visible(page, CFG["selectors"]["save_draft_dialog"], 8000,
                              "弹窗里的保存为草稿按钮")
        if not btn:
            raise RuntimeError(
                "找不到弹窗里的「保存为草稿」按钮（弹窗结构可能已变），请查看截图 "
                f"{OUTPUT_DIR} 并更新 config.json selectors.save_draft_dialog"
            )
        guard(page, b, "保存草稿前")
        try:
            btn.click(timeout=15000)
        except Exception as e:
            # 兜底：遮罩还没散就再清一次、重试一次点击，避免整篇白跑
            if is_browser_dead(e):
                raise
            log("保存草稿按钮点击被拦，清理浮层后重试一次…")
            dismiss_overlays(page)
            _kill_crop_overlay(page)
            page.wait_for_timeout(500)
            try:
                btn.click(timeout=15000)
            except Exception:
                log("常规点击仍被拦，改用 JS 直接触发「保存为草稿」")
                btn.evaluate("el => el.click()")
        page.wait_for_timeout(3000)
        b.shot(page, "05-draft-saved")
        # 存草稿也要校验：实测 CSDN 会弹「文章频繁发布，请稍后再试」把这次保存拒掉，
        # 但界面上什么都不缺，早期版本照样打"草稿已保存"（假成功，草稿箱计数不涨）。
        notice, kind = read_any_notice(page, 4000)
        if notice:
            log(f"CSDN 拒绝了这次存草稿：{notice}")
            return 4 if kind == "freq" else 3
        log("草稿已保存（标签/摘要/栏目已一并保存，未公开发布）")
        log("  提示：这只是「点完按钮」的日志；真正的验收是内容管理页「草稿箱(N)」计数 +1")
        return 0

    confirm, _ = find_visible(page, CFG["selectors"]["confirm_publish"], 10000, "确认发布按钮")
    if not confirm:
        raise RuntimeError(
            "找不到确认发布按钮（弹窗结构可能已变），请查看截图 "
            f"{OUTPUT_DIR} 并更新 config.json selectors.confirm_publish"
        )
    guard(page, b, "确认发布前")
    try:
        confirm.click(timeout=15000)
    except Exception as e:
        if is_browser_dead(e):
            raise
        # 兜底：万一标签浮层挡在按钮上，绕过可点击性检查再点一次
        log(f"常规点击失败（{e.__class__.__name__}），改用 force 点击")
        confirm.click(force=True)
    log("已点击确认发布，等待结果…")

    url = wait_publish_success(page)
    b.shot(page, "05-result")
    if url:
        log(f"发布成功: {url}")
        return 0
    notice, kind = read_any_notice(page, 4000)
    if notice:
        log(f"CSDN 拒绝了这次发布：{notice}")
        log("  额度用尽是账号限制、频率限制等一会儿重试即可；两种都不会留下半成品。")
        return 4 if kind == "freq" else 3
    log("未能确认发布结果，请查看 output/ 下截图确认是否已发布")
    return 2


# ---------------------------------------------------------------- 入口

def add_robustness_args(p, with_crash=True):
    """风控 / 崩溃这两组命令行开关。写在这里，login/check/inspect/publish 共用。"""
    p.add_argument("--on-risk", choices=["wait", "abort"], default=None,
                   help="遇到 CSDN 安全验证怎么办：wait=停下来等人工点完再续跑（默认），"
                        "abort=直接放弃留给任务队列重跑")
    if with_crash:
        p.add_argument("--on-crash", choices=["restart", "abort"], default=None,
                       help="浏览器异常关闭怎么办：restart=自动重新拉起再跑一遍（默认），"
                            "abort=放弃")
        p.add_argument("--max-restarts", type=int, default=None, metavar="N",
                       help="浏览器最多自动重启几次（默认取 crash.maxRestarts，0 等于不重启）")
    p.add_argument("--no-clean-profile", action="store_true",
                   help="启动前不清理上次崩溃留下的 profile 锁（默认会清理）")


def apply_cli_overrides(args):
    """把命令行开关写进 CFG。命令行优先级最高，和 --draft/--dry-run 一个道理。"""
    if getattr(args, "on_risk", None):
        CFG.setdefault("risk", {})["onDetection"] = args.on_risk
        log(f"命令行覆盖: risk.onDetection={args.on_risk}")
    if getattr(args, "on_crash", None):
        CFG.setdefault("crash", {})["onCrash"] = args.on_crash
        log(f"命令行覆盖: crash.onCrash={args.on_crash}")
    if getattr(args, "max_restarts", None) is not None:
        CFG.setdefault("crash", {})["maxRestarts"] = max(0, args.max_restarts)
        log(f"命令行覆盖: crash.maxRestarts={CFG['crash']['maxRestarts']}")
    if getattr(args, "no_clean_profile", False):
        CFG.setdefault("browser", {})["cleanStaleProfile"] = False
        log("命令行覆盖: browser.cleanStaleProfile=False")


def install_cleanup_handlers():
    """被 Ctrl+C / 终止时也要把心跳标记清掉，否则下次启动会以为 profile 还被占着。"""
    def _cleanup(signum, frame):
        clear_heartbeat()
        raise KeyboardInterrupt(f"收到信号 {signum}")
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _cleanup)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(
        description="CSDN 文章自动发布工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    add_robustness_args(sub.add_parser("login", help="打开浏览器登录 CSDN（只需一次）"),
                        with_crash=False)
    add_robustness_args(sub.add_parser("check", help="检查登录态是否有效"),
                        with_crash=False)
    add_robustness_args(sub.add_parser("inspect", help="打开编辑器并导出页面结构，用于校准选择器"),
                        with_crash=False)
    sub.add_parser("config", help="打印全部可配置参数（job 字段 + config.json + 命令行开关）")

    p = sub.add_parser("publish", help="发布一篇文章")
    p.add_argument("markdown", type=Path, help="Markdown 文件路径")
    p.add_argument("--draft", action="store_true", help="只存草稿，不公开发布")
    p.add_argument("--schedule", metavar="WHEN",
                   help="定时发布：\"2026-10-09 19:30\" / \"+26h\" / \"2026-10-09\"")
    p.add_argument("--dry-run", action="store_true", help="填完内容但不提交，用于联调")
    p.add_argument("--force", action="store_true", help="体检发现硬伤时也照发")
    p.add_argument("--no-lint", action="store_true", help="跳过发布前体检")
    add_robustness_args(p)

    args = ap.parse_args()

    # config 只读一份文档，不需要 Playwright / 不需要临时目录，
    # 所以在起浏览器之前就处理掉（沙箱里也能跑）。
    if args.cmd == "config":
        return cmd_config()

    if args.cmd == "publish" and not args.markdown.exists():
        raise SystemExit(f"文件不存在: {args.markdown}")

    apply_cli_overrides(args)
    install_cleanup_handlers()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("缺少依赖，请先执行: python -m pip install playwright")

    try:
        with sync_playwright() as pw:
            if args.cmd == "login":
                return cmd_login(pw)
            if args.cmd == "check":
                return cmd_check(pw)
            if args.cmd == "inspect":
                return cmd_inspect(pw)
            return cmd_publish(pw, args.markdown, args.draft, args.dry_run,
                               skip_lint=args.no_lint, force=args.force,
                               schedule=args.schedule)
    finally:
        # 无论正常结束、报错还是被 Ctrl+C，都别把心跳留下（留下会被当成"profile 还被占用"）
        clear_heartbeat()


if __name__ == "__main__":
    sys.exit(main())
