"""栏目硬闸门的离线自测（不需要浏览器、不需要登录）。

做法：用一个假的 page 对象顶替 Playwright 的 page，只实现 select_categories()
用到的两个方法：evaluate(js, arg) 和 wait_for_timeout(ms)。
按传入脚本里的特征串决定返回什么，从而构造四种场景：

  1) 名字对得上、点完能勾上        -> 应该成功（不抛异常）
  2) 名字对不上（大小写/别名）      -> 应该抛 CategoryGateAbort
  3) 页面里根本没有栏目控件        -> 应该抛 CategoryGateAbort
  4) 点成功了但回读是空的（Vue 没响应）-> 应该抛 CategoryGateAbort

跑法：.venv/Scripts/python.exe test_category_gate.py
"""
import publish_csdn as pc

FAILED = []


class FakePage:
    """最小 page 替身：只认 select_categories 里会发的那几个 evaluate。"""

    def __init__(self, available, checked_after_click):
        self.available = list(available)      # 页面上可选的栏目名
        self.checked_after_click = list(checked_after_click)
        self.clicked = []

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, arg=None):
        # available_categories()
        if "].map(i => i.value)" in js and "filter(i => i.checked)" not in js:
            return list(self.available)
        # read_categories()
        if "filter(i => i.checked)" in js:
            return list(self.checked_after_click)
        # 点击那一段
        if "inp.closest('label').click()" in js:
            if arg not in self.available:
                return "not-found"
            self.clicked.append(arg)
            return "clicked"
        raise AssertionError(f"没预料到的 evaluate 脚本: {js[:60]}")


def case(name, available, checked_after_click, category, should_abort):
    page = FakePage(available, checked_after_click)
    try:
        pc.select_categories(page, category)
    except pc.CategoryGateAbort as e:
        if should_abort:
            print(f"  ✅ {name}: 按预期中止 -> {str(e)[:70]}…")
        else:
            FAILED.append(name)
            print(f"  ❌ {name}: 不该中止却中止了 -> {e}")
        return
    except Exception as e:
        FAILED.append(name)
        print(f"  ❌ {name}: 抛了非预期异常 {e.__class__.__name__}: {e}")
        return
    if should_abort:
        FAILED.append(name)
        print(f"  ❌ {name}: 该中止却放行了")
    else:
        print(f"  ✅ {name}: 按预期放行")


def main():
    print("栏目硬闸门自测（离线，无浏览器）\n")

    real = ["redis", "mysql", "技术栈学习笔记", "算法题目解析", "GO项目分享"]

    case("1 名字对得上、勾上了 → 放行",
         real, ["redis"], "redis", should_abort=False)

    case("2 名字对不上（Redis 大写）→ 中止",
         real, [], "Redis", should_abort=True)

    case("3 页面没有栏目控件 → 中止",
         [], [], "redis", should_abort=True)

    case("4 点了但回读为空（Vue 没响应）→ 中止",
         real, [], "redis", should_abort=True)

    case("5 留空 → 放行（本就不放入任何栏目）",
         real, [], "", should_abort=False)

    case("6 目标 2 个只勾上 1 个 → 中止",
         real, ["redis"], "redis,mysql", should_abort=True)

    print()
    if FAILED:
        print(f"❌ {len(FAILED)} 个用例失败: {FAILED}")
        return 1
    print("✅ 全部用例通过：栏目没勾上一定中止，勾上了才放行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
