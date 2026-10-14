#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""只读探测：CSDN 工具需要的目录现在到底能不能写？

**只读、不创建、不删除任何东西**（会临时写一个探针文件，立刻删掉）。
用途：遇到「浏览器起不来 / 截图没生成 / 草稿箱计数读不到」时，先分清
是**环境写不进去**还是**工具真的坏了** —— 这两种现象的日志很像，但处置完全不同。

跑法（在项目根目录下执行）：
    .\.venv\Scripts\python.exe probe_writable.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# CSDN 工具真正需要写的目录（每一个都对应一类失败现象）
TARGETS = [
    (ROOT / ".profile", "浏览器用户目录（登录态 cookie 在这里）"),
    (ROOT / "output", "截图与调试产物"),
    (ROOT / ".tmp", "Playwright 运行产物"),
    (ROOT / "posts", "正文母版"),
    (ROOT / "jobs", "任务文件（job*.json）"),
    (ROOT, "项目根：config.json + 浏览器心跳 .browser-heartbeat.json"),
    (ROOT / "log", "运行日志"),
]


def can_write(d: Path):
    """试写一个探针文件然后删掉。返回 (能不能写, 说明)。"""
    if not d.exists():
        return None, "目录不存在"
    probe = d / ".probe-writable.tmp"
    try:
        probe.write_text("x", encoding="utf-8")
    except Exception as e:
        return False, f"{e.__class__.__name__}"
    finally:
        pass
    try:
        probe.unlink()
    except Exception:
        return True, "能写，但探针文件删不掉（请手动删 .probe-writable.tmp）"
    return True, ""


def main():
    print(f"当前进程 pid={os.getpid()}  临时目录={tempfile.gettempdir()}")
    print("=" * 62)
    bad = []
    for d, why in TARGETS:
        ok, detail = can_write(d)
        if ok is None:
            mark, extra = "  --  ", detail
        elif ok:
            mark, extra = "  OK  ", ""
        else:
            mark, extra = " FAIL ", detail
            bad.append(d)
        print(f"[{mark}] {d}")
        print(f"         用途: {why} {extra}")

    print("=" * 62)
    print("结论：")
    if not bad:
        print("  需要的目录都可写，工具能正常工作。")
        return 0
    print(f"  {len(bad)} 个目录写不进去，CSDN 工具在里面会失败。")
    print("  典型现象：浏览器起不来（.profile 写不了）、截图没生成（output 写不了）、")
    print("           草稿箱计数读不到（.tmp 写不了）。")
    print()
    print("  这属于**环境限制**，不是工具故障。处置顺序：")
    print("    1) 把本会话切到「完全权限」(full access) —— 最快，且不影响别的会话")
    print("    2) 如果只是 ACL 问题，用 diagnose-windows-sandbox-acl 那个 skill 跑一条命令修")
    print("    3) 验证方式：切完权限重跑本脚本，应该全部 OK")
    return 1


if __name__ == "__main__":
    sys.exit(main())
