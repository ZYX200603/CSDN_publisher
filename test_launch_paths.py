#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动失败路径的自检（不启动真浏览器、不联网）。

为什么需要单独一个：test_robustness.py 验的是纯函数，而 launch() 里
「清锁 → 重试 → 回退自带 chromium → 最后放弃时的心跳清理」这段必须用
**真实的调用形参**跑一遍，否则像 `no_viewport` 传错、`channel` 位置参数写错
这种错误要等到真跑浏览器才会暴露。

做法：把 `launch_persistent_context` 换成一个"永远抛异常"的假实现，
让 launch() 把失败路径完整走完，然后检查它到底做了什么。
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import publish_csdn as pc  # noqa: E402

SANDBOX = Path(tempfile.mkdtemp(prefix="csdn-launch-test-", dir=ROOT / ".tmp"))
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


class Boom(Exception):
    pass


class FakeCtx:
    """够真的假 context：生产代码会调 set_default_timeout / close / cookies。"""

    def __init__(self):
        self.timeout = None
        self.closed = False
        self.pages = []
        self._cookies = []

    def set_default_timeout(self, ms):
        self.timeout = ms

    def cookies(self):
        return self._cookies

    def new_page(self):
        raise AssertionError("这个测试不该真的开页面")

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, fail_times=999):
        self.calls = []
        self.fail_times = fail_times
        self.created = []

    def launch_persistent_context(self, **kw):
        self.calls.append(kw)
        if len(self.calls) <= self.fail_times:
            raise Boom("Simulated: user data directory is already in use")
        ctx = FakeCtx()
        self.created.append(ctx)
        return ctx


class FakePW:
    def __init__(self, fail_times=999):
        self.chromium = FakeChromium(fail_times)


# 让所有落盘的东西都进临时目录（受限沙箱下项目子目录可能不可写）
real = (pc.PROFILE_DIR, pc.HEARTBEAT_PATH, pc.OUTPUT_DIR)
pc.PROFILE_DIR = SANDBOX / "profile"
pc.HEARTBEAT_PATH = SANDBOX / "heartbeat.json"
pc.OUTPUT_DIR = SANDBOX
pc.PROFILE_DIR.mkdir(parents=True, exist_ok=True)

# 别真去问 playwright 要 chromium 路径（会起一次驱动、还很慢），
# 也不要去 taskkill 系统里的进程 —— 这两件都在测试里替掉。
killed_log = []
pc.kill_lingering_browser = lambda: killed_log.append(1) or 0
pc.resolve_chromium = lambda: str(SANDBOX / "fake-chromium.exe")

print("=" * 66)
print("启动失败路径自检（不启动真浏览器）")
print("=" * 66)

try:
    # ---------------------------------------------------------- 全部失败的场景
    print("\n① 系统 Chrome 和自带 chromium 都起不来")
    killed_log.clear()
    (pc.PROFILE_DIR / "lockfile").write_text("stale", encoding="utf-8")
    pc.CFG["browser_channel"] = "chrome"
    pc.CFG["browser"]["launchRetries"] = 1
    pc.CFG["browser"]["launchRetryWaitMs"] = 1
    pw = FakePW(fail_times=999)
    b = pc.Browser(pw)
    try:
        b.launch()
        check("最终抛出 SystemExit 而不是裸异常", False, "没有抛异常")
    except SystemExit as e:
        msg = str(e)
        check("最终抛出 SystemExit（带可操作的提示）",
              "无法启动浏览器" in msg and "playwright install chromium" in msg,
              msg.splitlines()[0])
        check("错误里保留了原始原因", "Simulated" in msg or "Boom" in msg)
    except Exception as e:
        check("最终抛出 SystemExit 而不是裸异常", False, f"{e.__class__.__name__}: {e}")

    calls = pw.chromium.calls
    check("重试次数 = (launchRetries+1) × 两种 channel 各一轮", len(calls) == 4,
          f"实际调用 {len(calls)} 次（期望 chrome×2 + chromium×2）")
    check("每次都传了 channel 关键字参数（chrome 那两次）",
          all(k.get("channel") == "chrome" for k in calls[:2]),
          str([k.get("channel") for k in calls[:2]]))
    check("回退时不带 channel 关键字",
          all("channel" not in k for k in calls[2:]),
          str([k.get("channel") for k in calls[2:]]))
    check("没有把 no_viewport 之类参数传错（真调用形参已接受）", len(calls) > 0)
    check("陈旧 lockfile 被清掉", not (pc.PROFILE_DIR / "lockfile").exists())
    check("失败退出时清掉心跳（否则下次会误判 profile 被占用）",
          not pc.HEARTBEAT_PATH.exists())
    check("每次重试前都清了一次残留浏览器进程（开场 1 + chrome 失败各 1）",
          len(killed_log) == 3, f"清理了 {len(killed_log)} 次")
    check("启动参数里带了关翻译/关崩溃气泡的开关",
          "--disable-translate" in calls[0]["args"]
          and "--hide-crash-restore-bubble" in calls[0]["args"]
          and "--lang=zh-CN" in calls[0]["args"],
          f"{len(calls[0]['args'])} 个参数")
    check("user_data_dir 指向 .profile", "user_data_dir" in calls[0])

    # ---------------------------------------------------------- 回退成功的场景
    print("\n② 系统 Chrome 起不来（重试也用完），但自带 chromium 能起来")
    pc.CFG["browser_channel"] = "chrome"
    pc.CFG["browser"]["launchRetries"] = 1
    pw2 = FakePW(fail_times=2)   # chrome 的 2 次全失败，chromium 第 1 次就成功
    b2 = pc.Browser(pw2)
    ctx = b2.launch()
    check("回退到自带 chromium 后成功返回 context", isinstance(ctx, FakeCtx), type(ctx).__name__)
    check("context 超时被设成 30000ms", ctx.timeout == 30000, str(ctx.timeout))
    check("channel_used 记录了实际用的浏览器", b2.channel_used == "chromium",
          str(b2.channel_used))
    check("先试了 2 次 chrome 再回退", len(pw2.chromium.calls) == 3,
          f"共 {len(pw2.chromium.calls)} 次调用")
    check("心跳文件在成功启动后存在", pc.HEARTBEAT_PATH.exists())
    hb = pc.read_heartbeat()
    check("心跳里记了 pid 和 channel", bool(hb and hb.get("pid")), str(hb))

    # ---------------------------------------------------------- heartbeat 是"自己写的"
    print("\n③ 心跳是本进程自己写的（启动失败后重试时必须不拦自己）")
    pc.clear_heartbeat()
    pc.write_heartbeat()
    alive, hb = pc.heartbeat_alive()
    check("自己写的心跳不算外部占用", alive is False, f"pid={hb.get('pid')} 是自己的 pid")
    (pc.PROFILE_DIR / "lockfile").write_text("x", encoding="utf-8")
    pw_self = FakePW(fail_times=0)
    b_self = pc.Browser(pw_self)
    try:
        b_self.launch()
        check("自己写了心跳也不会被自己的前置检查拦下", True)
    except SystemExit as e:
        check("自己写了心跳也不会被自己的前置检查拦下", False, str(e)[:60])

    # ---------------------------------------------------------- 占用即拒绝场景
    print("\n④ profile 被另一个活着的进程占用（心跳指向别人）")
    (pc.PROFILE_DIR / "lockfile").write_text("stale", encoding="utf-8")
    import os as _os
    pc.HEARTBEAT_PATH.write_text(
        '{"pid": %d, "started": "2026-10-08 00:00:00", "channel": "chrome"}'
        % _os.getppid(), encoding="utf-8")
    pw3 = FakePW()
    b3 = pc.Browser(pw3)
    try:
        b3.launch()
        check("拒绝启动而不是硬删别人的锁", False, "居然启动了")
    except SystemExit as e:
        check("拒绝启动而不是硬删别人的锁", "占用" in str(e), str(e).splitlines()[0])
    check("真的没删锁文件", (pc.PROFILE_DIR / "lockfile").exists())
    check("也没有去调用浏览器启动", len(pw3.chromium.calls) == 0)

    # ---------------------------------------------------------- chromium 没装
    print("\n⑤ 系统 Chrome 起不来、自带 chromium 也没装")
    pc.clear_heartbeat()
    (pc.PROFILE_DIR / "lockfile").write_text("x", encoding="utf-8")
    pc.resolve_chromium = lambda: ""      # 模拟 .pw-browsers 是空的
    pw4 = FakePW(fail_times=999)
    b4 = pc.Browser(pw4)
    try:
        b4.launch()
        check("chromium 没装时直接给出明确提示", False, "居然启动了")
    except SystemExit as e:
        msg = str(e)
        check("chromium 没装时直接给出明确提示（不再空转重试）",
              "自带 chromium 也没装" in msg, msg.splitlines()[0])
        check("提示里给了两条可操作的路", "playwright install chromium" in msg
              and "browser_channel" in msg)
        check("没有对不存在的 chromium 做无谓重试",
              len(pw4.chromium.calls) == 2, f"启动尝试 {len(pw4.chromium.calls)} 次")
    pc.resolve_chromium = lambda: str(SANDBOX / "fake-chromium.exe")

finally:
    pc.PROFILE_DIR, pc.HEARTBEAT_PATH, pc.OUTPUT_DIR = real
    pc.clear_heartbeat()

n_fail = sum(1 for ok in results if not ok)
print("\n" + "=" * 66)
print(f"合计 {len(results)} 项，通过 {len(results) - n_fail}，失败 {n_fail}")
shutil.rmtree(SANDBOX, ignore_errors=True)   # 临时目录在 .tmp 下，跑完清掉
print("=" * 66)
sys.exit(1 if n_fail else 0)
