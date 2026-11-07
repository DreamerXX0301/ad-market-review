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
    parser.add_argument("--diagnose", action="store_true", help="用隔离空库检查运行时、页面与工作进程")
    args = parser.parse_args(argv)
    package = Path(__file__).resolve().parents[2]
    try:
        if args.diagnose:
            print(json.dumps(diagnose(package), ensure_ascii=False, indent=2))
        else:
            run_preview(package)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"工作台预览启动失败。{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
