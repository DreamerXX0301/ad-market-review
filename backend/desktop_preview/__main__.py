"""Launch the private Windows workbench preview or diagnose its runtime."""
from __future__ import annotations

import argparse
import json
import multiprocessing
from pathlib import Path
import sys

from .runtime import diagnose, run_preview


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--diagnose", action="store_true", help="用隔离空库检查运行时、页面与工作进程")
    operation.add_argument("--import-previous", action="store_true", help="从已关闭的旧版导入独立数据副本")
    args = parser.parse_args(argv)
    package = Path(__file__).resolve().parents[2]
    if sys.platform == "win32":
        multiprocessing.set_executable(str(package / "runtime" / "pythonw.exe"))
    try:
        if args.diagnose:
            print(json.dumps(diagnose(package), ensure_ascii=False, indent=2))
        elif args.import_previous:
            receipt = run_preview(package, import_previous=True)
            print(f"导入完成。已核验 {receipt['files_verified']} 个恢复文件。\n请双击 EXE 打开工作台。")
        else:
            run_preview(package)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"工作台预览启动失败。{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
