"""Runtime paths. Import has no filesystem side effects."""
import os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP_NAME = "冒险的梦想家"
APP_ID = "ad-market-review"
APP_VERSION = "0.1.29"

def topic_adjustments_enabled() -> bool:
    return os.environ.get('SHIXU_TOPIC_ADJUSTMENTS_ENABLED', '1') == '1'

def data_dir() -> Path:
    return Path(os.environ.get("SHIXU_DATA_DIR", str(PROJECT_ROOT / "data"))).expanduser().resolve()


def _storage_path(name: str, *default_parts: str) -> Path:
    configured = os.environ.get(name)
    # Keep an explicitly empty value as Path(''), matching the original path
    # contract. Only an absent override needs to resolve the default data root.
    path = data_dir().joinpath(*default_parts) if configured is None else Path(configured)
    return path.expanduser().resolve()


def sqlite_path() -> Path:
    return _storage_path("SHIXU_SQLITE_PATH", "business", "app.sqlite3")


def published_dir() -> Path:
    return _storage_path("SHIXU_PUBLISHED_DIR", "published")


def raw_dir() -> Path:
    return _storage_path("SHIXU_RAW_DIR", "raw")


def work_dir() -> Path:
    return _storage_path("SHIXU_WORK_DIR", "work")
