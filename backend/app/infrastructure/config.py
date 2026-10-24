"""Runtime paths. Import has no filesystem side effects."""
import os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_NAME = "冒险的梦想家"
APP_ID = "ad-market-review"
APP_VERSION = "0.1.2"

def data_dir() -> Path:
    return Path(os.environ.get("SHIXU_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()


def sqlite_path() -> Path:
    return Path(os.environ.get("SHIXU_SQLITE_PATH", str(data_dir() / "business" / "app.sqlite3"))).expanduser().resolve()


def published_dir() -> Path:
    return Path(os.environ.get("SHIXU_PUBLISHED_DIR", str(data_dir() / "published"))).expanduser().resolve()


def raw_dir() -> Path:
    return Path(os.environ.get("SHIXU_RAW_DIR", str(data_dir() / "raw"))).expanduser().resolve()


def work_dir() -> Path:
    return Path(os.environ.get("SHIXU_WORK_DIR", str(data_dir() / "work"))).expanduser().resolve()
