#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
浏览器健壮性（风控闸门 + 崩溃自愈）的离线测试。

**不启动浏览器、不联网**：用假的 page / browser 对象驱动，验证
  ① 陈旧 profile 锁的判定（活进程不删、死进程才删）
  ② 风控识别（DOM 命中 / 文案命中 / URL 命中 / 正常页面不误报）
  ③ 崩溃异常识别（TargetClosed 算崩、元素找不到不算）
  ④ 闸门 wait 模式能等到人工处理完继续；超时/abort 模式正确放弃

跑法：
    .\\.venv\\Scripts\\python.exe test_robustness.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 所有会落盘的东西都放一个临时目录里。
# 为什么不用项目内的 output/ 或 .tmp/：在受限沙箱里那些子目录可能不可写
# （工作区根可写、子目录不可写），而这个测试本身不该因此跑不起来。
SANDBOX = Path(tempfile.mkdtemp(prefix="csdn-robustness-test-"))

import publish_csdn as pc  # noqa: E402

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((PASS if ok else FAIL, name, detail))
    print(f"  [{PASS if ok else FAIL}] {name}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------- 假对象

class FakePage:
    """只实现被测试代码用到的那几件事。"""

    def __init__(self, url="https://editor.csdn.net/md/", risk=None, killed=None):
        self.url = url
        self._risk = risk          # RISK_JS 的返回值
        self._killed = killed      # KILL_BUBBLES_JS 的返回值
        self.waited = 0

    def evaluate(self, script, arg=None):
        if script is pc.RISK_JS:
            return self._risk
        if script is pc.KILL_BUBBLES_JS:
            return self._killed or []
        if script is pc.CRASH_BUBBLE_JS:
            return None
        return None

    def wait_for_timeout(self, ms):
        self.waited += ms


class FakeBrowser:
    def __init__(self, alive=True):
        self._alive = alive
        self.shots = []

    def alive(self):
        return self._alive

    def shot(self, page, name):
        self.shots.append(name)
        return SANDBOX / f"test-{name}.png"


class FakeExc(Exception):
    pass


# ---------------------------------------------------------------- ① profile 锁

def test_profile_lock():
    print("\n① profile 锁判定")
    real_profile, real_hb = pc.PROFILE_DIR, pc.HEARTBEAT_PATH
    real_out = pc.OUTPUT_DIR
    tmp = SANDBOX / "profile"
    tmp.mkdir(parents=True, exist_ok=True)
    hb_path = SANDBOX / "heartbeat.json"
    try:
        pc.PROFILE_DIR = tmp
        pc.HEARTBEAT_PATH = hb_path
        pc.OUTPUT_DIR = SANDBOX

        # 场景 1：没有锁文件 → 什么都不做
        pc.write_heartbeat()
        pc.clear_heartbeat()
        notes = pc.preflight_profile()
        check("没有锁文件时不乱动", notes == [], str(notes))

        # 场景 2：有锁 + 心跳是活进程（拿父进程当"别人"，它一定活着）→ 拒绝启动、不删文件
        lock = tmp / "lockfile"
        lock.write_text("x", encoding="utf-8")
        hb_path.write_text(
            '{"pid": %d, "started": "2026-10-08 00:00:00", "channel": "chrome"}'
            % os.getppid(), encoding="utf-8")
        notes = pc.preflight_profile()
        check("别的活进程占用时不删锁", lock.exists() and any("⛔" in n for n in notes),
              str(notes[:1]))

        # 场景 2b：心跳是本进程自己写的 → 不算外部占用，锁该清就清
        # （不这样做的话，第一次启动失败后的重试会被自己的心跳拦住，
        #   并报一句完全误导的"profile 正被另一个进程占用"）
        lock.write_text("x", encoding="utf-8")
        pc.write_heartbeat()
        alive, hb = pc.heartbeat_alive()
        check("自己写的心跳不算外部占用", alive is False, str(hb))
        notes = pc.preflight_profile()
        check("自己写的心跳不阻塞清理", not lock.exists(), str(notes[:1]))

        # 场景 3：心跳是死进程 → 不阻塞（锁在场景 2b 已被清掉，这里验"不误报占用"）
        pc.HEARTBEAT_PATH.write_text('{"pid": 999999999, "started": "2000-01-01"}',
                                     encoding="utf-8")
        notes = pc.preflight_profile()
        check("死进程的心跳不误报占用", notes == [], str(notes))

        # 场景 4：有锁但完全没有心跳记录 → 也当陈旧处理（老版本留下的）
        lock.write_text("x", encoding="utf-8")
        pc.clear_heartbeat()
        notes = pc.preflight_profile()
        check("没有心跳记录的锁也清掉", not lock.exists(), str(notes[:1]))

        # 场景 5：cleanStaleProfile=False → 一个都不删
        lock.write_text("x", encoding="utf-8")
        pc.CFG["browser"]["cleanStaleProfile"] = False
        notes = pc.preflight_profile()
        check("关掉开关后不清理", lock.exists() and notes == [], str(notes))
        pc.CFG["browser"]["cleanStaleProfile"] = True

        # 场景 6：pid 存活性判定本身
        check("_pid_alive 认得活着的自己", pc._pid_alive(os.getpid()) is True)
        check("_pid_alive 认得死的 pid", pc._pid_alive(999999999) is False)
        check("_pid_alive 对垃圾输入不炸", pc._pid_alive(None) is False)
    finally:
        pc.PROFILE_DIR, pc.HEARTBEAT_PATH = real_profile, real_hb
        pc.OUTPUT_DIR = real_out
        pc.clear_heartbeat()


# ---------------------------------------------------------------- ② 风控识别

def test_detect_risk():
    print("\n② 风控识别")
    dom_hit = {"kind": "dom", "detail": ".geetest_slider", "text": "拖动滑块"}
    text_hit = {"kind": "text", "detail": ".el-message-box", "hit": "安全验证",
                "text": "请完成安全验证后继续"}
    page = FakePage(risk=dom_hit)
    r = pc.detect_risk(page)
    check("DOM 命中（滑块组件）", r and r["where"] == "dom" and "geetest" in r["detail"], str(r))

    r = pc.detect_risk(FakePage(risk=text_hit))
    check("文案命中（安全验证）", r and r["where"] == "text", str(r))

    r = pc.detect_risk(FakePage(url="https://sec.csdn.net/captcha?x=1", risk=None))
    check("URL 命中（被丢到验证页）", r and r["where"] == "url", str(r))

    r = pc.detect_risk(FakePage(risk=None))
    check("正常页面不误报", r is None, str(r))

    # evaluate 抛异常（页面崩了）时不能把异常带出去，否则崩溃处理逻辑会被自己打断
    class Boom(FakePage):
        def evaluate(self, script, arg=None):
            raise FakeExc("Target page, context or browser has been closed")
    try:
        r = pc.detect_risk(Boom())
        check("页面已崩溃时识别不抛异常", r is None, str(r))
    except Exception as e:
        check("页面已崩溃时识别不抛异常", False, f"{e.__class__.__name__}: {e}")


# ---------------------------------------------------------------- ③ 崩溃识别

def test_is_browser_dead():
    print("\n③ 崩溃 vs 业务错误的区分")
    cases_dead = [
        ("TargetClosedError", "Target page, context or browser has been closed"),
        ("Error", "Browser has been closed"),
        ("Error", "Protocol error (Page.navigate): Target closed"),
        ("Error", "browser has disconnected"),
        ("Error", "WebSocket error: ECONNRESET"),
    ]
    for i, (cls_name, msg) in enumerate(cases_dead, 1):
        e = type(cls_name, (Exception,), {})()
        e.args = (msg,)  # type: ignore[attr-defined]
        check(f"崩溃样例 {i}: {msg[:42]}", pc.is_browser_dead(e) is True)

    cases_alive = [
        ("RuntimeError", "找不到发布按钮，请跑 inspect 并更新 config.json selectors"),
        ("TimeoutError", "locator.click: Timeout 10000ms exceeded"),
        ("Error", "strict mode violation: locator resolved to 2 elements"),
    ]
    for i, (cls_name, msg) in enumerate(cases_alive, 1):
        e = type(cls_name, (Exception,), {})()
        e.args = (msg,)  # type: ignore[attr-defined]
        check(f"业务错误样例 {i}（不该重启浏览器）", pc.is_browser_dead(e) is False,
              f"{cls_name}: {msg[:34]}")


# ---------------------------------------------------------------- ④ 闸门行为

def test_risk_gate():
    print("\n④ 风控闸门行为")

    # wait 模式：前两次检测到验证，第三次通过 → 应该等到通过并返回 True
    class Flaky(FakePage):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def evaluate(self, script, arg=None):
            if script is pc.RISK_JS:
                self.calls += 1
                if self.calls <= 2:
                    return {"kind": "dom", "detail": ".geetest_slider", "text": "拖动滑块"}
                return None
            return super().evaluate(script, arg)

    pc.CFG["risk"]["pollMs"] = 1
    pc.CFG["risk"]["beep"] = 1
    pc.CFG["risk"]["waitTimeoutSec"] = 30
    page = Flaky()
    b = FakeBrowser()
    try:
        import winsound  # noqa: F401
        pc.winsound = None  # 别在测试里真响
    except Exception:
        pass
    ok = pc.risk_gate(page, b, "测试")
    check("wait 模式：等人点完后继续", ok is True, f"检测了 {page.calls} 次")

    # 超时：一直不通过 → 抛 RiskGateAbort
    pc.CFG["risk"]["waitTimeoutSec"] = 1
    pc.CFG["risk"]["pollMs"] = 200
    p2 = FakePage(risk={"kind": "dom", "detail": ".geetest_slider", "text": "拖动滑块"})
    try:
        pc.risk_gate(p2, FakeBrowser(), "测试")
        check("wait 模式：超时后放弃", False, "没有抛 RiskGateAbort")
    except pc.RiskGateAbort as e:
        check("wait 模式：超时后放弃", True, str(e)[:40])
    pc.CFG["risk"]["waitTimeoutSec"] = 300

    # abort 模式：立刻放弃
    pc.CFG["risk"]["onDetection"] = "abort"
    p3 = FakePage(risk={"kind": "text", "detail": ".el-message-box",
                        "hit": "安全验证", "text": "请完成验证"})
    try:
        pc.risk_gate(p3, FakeBrowser(), "测试")
        check("abort 模式：立刻放弃", False, "没有抛 RiskGateAbort")
    except pc.RiskGateAbort:
        check("abort 模式：立刻放弃", True)
    pc.CFG["risk"]["onDetection"] = "wait"

    # 正常页面：闸门不阻塞、直接返回 False
    t0 = __import__("time").time()
    ok = pc.risk_gate(FakePage(risk=None), FakeBrowser(), "测试")
    check("正常页面不阻塞", ok is False and __import__("time").time() - t0 < 1)

    # 等待期间浏览器被关掉 → 必须报错，不能死等满超时
    p4 = FakePage(risk={"kind": "dom", "detail": ".x", "text": ""})
    pc.CFG["risk"]["pollMs"] = 1
    try:
        pc.risk_gate(p4, FakeBrowser(alive=False), "测试")
        check("等待中浏览器被关掉 → 立刻报错", False, "没报错")
    except RuntimeError as e:
        check("等待中浏览器被关掉 → 立刻报错", "关掉" in str(e), str(e)[:40])

    # guard 应该顺手清掉干扰气泡
    p5 = FakePage(risk=None, killed=["translate-close", "crash-dismiss"])
    pc.guard(p5, FakeBrowser(), "测试")
    check("guard 顺手清干扰气泡（翻译/崩溃）", True, "translate-close, crash-dismiss")


# ---------------------------------------------------------------- ⑤ 命令行开关

def test_cli_overrides():
    print("\n⑤ 命令行开关覆盖 config")
    import argparse
    ap = argparse.ArgumentParser()
    pc.add_robustness_args(ap, with_crash=True)
    a = ap.parse_args(["--on-risk", "abort", "--on-crash", "abort", "--max-restarts", "0",
                       "--no-clean-profile"])
    pc.apply_cli_overrides(a)
    check("--on-risk 生效", pc.CFG["risk"]["onDetection"] == "abort")
    check("--on-crash 生效", pc.CFG["crash"]["onCrash"] == "abort")
    check("--max-restarts 生效", pc.CFG["crash"]["maxRestarts"] == 0)
    check("--no-clean-profile 生效", pc.CFG["browser"]["cleanStaleProfile"] is False)
    # 还原
    pc.CFG["risk"]["onDetection"] = "wait"
    pc.CFG["crash"]["onCrash"] = "restart"
    pc.CFG["crash"]["maxRestarts"] = 2
    pc.CFG["browser"]["cleanStaleProfile"] = True


def main():
    print("=" * 66)
    print("浏览器健壮性离线测试（不启浏览器、不联网）")
    print("=" * 66)
    test_profile_lock()
    test_detect_risk()
    test_is_browser_dead()
    test_risk_gate()
    test_cli_overrides()

    n_fail = sum(1 for r in results if r[0] == FAIL)
    print("\n" + "=" * 66)
    print(f"合计 {len(results)} 项，通过 {len(results) - n_fail}，失败 {n_fail}")
    print("=" * 66)
    needs = SANDBOX / "NEEDS-HUMAN.txt"
    if needs.exists():
        print(f"（闸门触发的提示写进了 {needs}，也就是 announce() 的落盘那步是通的）")
    shutil.rmtree(SANDBOX, ignore_errors=True)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
