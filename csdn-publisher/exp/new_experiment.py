#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新实验脚手架：把"每篇文章配一个可复现实测"变成一条命令。

为什么要有它：这一轮两篇文章之所以能和全网通稿区分开，靠的就是"我自己跑过、有数字、
脚本能复现"。但每次从零搭实验目录很烦，容易偷懒省掉。所以固化成三件套：

    exp/<slug>/
      README.md     实验登记：问题 / 方法 / 怎么复现 / 结论（发布前必须填结论）
      run.py        可跑脚本：算完落盘 result.json，并打印可直接贴进文章的 markdown 表格
      numbers.md    给 csdn_image.py render --table 用的表格源（图就从这里生成）

    python exp/new_experiment.py rag-chunking --title "切片大小对召回的影响"
    python exp/new_experiment.py verify-spec  --title "验收规格强度 vs 假完成拦截率"

跑完实验后：`python csdn_image.py render --table exp/<slug>/numbers.md --out img/x.png`
"""

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RUN_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{title}

跑法: python exp/{slug}/run.py
产出: result.json（原始结果，用于对账）+ 终端打印的 markdown 表格（直接贴进文章）
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main():
    rows = []
    # ---- 在这里写你的实验 ----
    # 例子：遍历若干参数组合，记录每个组合的指标
    for param in [1, 2, 3]:
        metric_a, metric_b = param * 0.1, param * 0.2      # ← 换成真实测量
        rows.append({{"param": param, "metric_a": round(metric_a, 3), "metric_b": round(metric_b, 3)}})

    out = {{"title": "{title}", "rows": rows}}
    (HERE / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {{HERE / 'result.json'}}（{{len(rows)}} 行）")

    print("\\n可直接贴进文章的表格：")
    print("| 参数 | 指标A | 指标B |")
    print("|---|---:|---:|")
    for r in rows:
        print(f"| {{r['param']}} | {{r['metric_a']}} | {{r['metric_b']}} |")
    print("\\n提示：把上面这张表抄进 numbers.md，再用 csdn_image.py render --table 出图。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

README_TEMPLATE = '''# 实验：{title}

> 这个目录是文章的"证据来源"。发表前请把下面四项都填实，**尤其是结论那一栏**。

## 1. 要回答的问题

（一句话：这个实验想验证什么？例：切片大小从 256 调到 1024，召回率是涨还是跌？）

## 2. 方法

- 数据：
- 参数：
- 判分标准：
- 已知偏差：（写清楚哪些结论不能从这个实验里得出 —— 这一栏最容易被自己忽略）

## 3. 怎么复现

```powershell
python exp/{slug}/run.py
```

## 4. 结论（发布前必填）

- 现象（可复现的数字）：
- 已排除的解释（做过什么实验否掉了它）：
- 尚未证实的猜测：

---
生成时间：{ts}
'''

NUMBERS_TEMPLATE = """| 参数 | 指标A | 指标B |
|---|---:|---:|
| 1 | 0.100 | 0.200 |
| 2 | 0.200 | 0.400 |
| 3 | 0.300 | 0.600 |

<!-- 把 run.py 打印出来的表格替换到这里；**加粗**的单元格会在图上高亮 -->
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", help="实验目录名，例如 rag-chunking")
    ap.add_argument("--title", required=True, help="实验标题（写进 README 与图标题）")
    ap.add_argument("--force", action="store_true", help="目录已存在时覆盖模板")
    args = ap.parse_args()

    d = HERE / args.slug
    if d.exists() and not args.force:
        raise SystemExit(f"{d} 已存在；要覆盖加 --force")
    d.mkdir(parents=True, exist_ok=True)

    from datetime import datetime
    (d / "run.py").write_text(RUN_TEMPLATE.format(slug=args.slug, title=args.title), encoding="utf-8")
    (d / "README.md").write_text(README_TEMPLATE.format(slug=args.slug, title=args.title,
                                                       ts=f"{datetime.now():%Y-%m-%d %H:%M}"),
                                 encoding="utf-8")
    (d / "numbers.md").write_text(NUMBERS_TEMPLATE, encoding="utf-8")

    print(f"已创建实验目录: {d}")
    for f in ("run.py", "README.md", "numbers.md"):
        print(f"  - {f}")
    print("\n接下来：")
    print(f"  1) 把实验写进 {d / 'run.py'} 并跑一遍：python exp/{args.slug}/run.py")
    print(f"  2) 用真实数字替换 {d / 'numbers.md'}（**加粗**要强调的单元格）")
    print(f"  3) 出图并上传：python csdn_image.py render --table exp/{args.slug}/numbers.md "
          f"--out img/{args.slug}.png --title \"{args.title}\"")
    print("  4) 文章里引用这些数字，并在文末给出 run.py 的复现方式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
