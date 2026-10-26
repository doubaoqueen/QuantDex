"""零依赖测试运行器（环境无 pytest 时使用）。

用法:
    python tests/run_tests.py            # 运行 tests/ 下全部 test_*.py
    python -m pytest tests/              # 若安装 pytest 也可直接跑
"""
import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

passed = failed = 0
failures = []


def main() -> int:
    global passed, failed
    here = Path(__file__).resolve().parent
    for path in sorted(here.glob("test_*.py")):
        name = path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:  # noqa: BLE001
            failed += 1
            failures.append((name, "<import>", traceback.format_exc()))
            print(f"IMPORT FAIL {name}")
            continue
        for fn_name in dir(mod):
            if not fn_name.startswith("test_"):
                continue
            fn = getattr(mod, fn_name)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
                print(f"PASS {name}::{fn_name}")
            except Exception:  # noqa: BLE001
                failed += 1
                failures.append((name, fn_name, traceback.format_exc()))
                print(f"FAIL {name}::{fn_name}")
    print()
    for mod_name, fn_name, tb in failures:
        print(f"===== {mod_name}::{fn_name} =====")
        print(tb)
    print(f"结果: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
