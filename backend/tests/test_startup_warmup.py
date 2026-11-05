"""API readiness imports dependencies without starting any business activity."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import startup_warmup


def test_warmup_only_imports_the_required_read_dependencies(monkeypatch):
    imports = []
    monkeypatch.setattr(startup_warmup, 'import_module', imports.append)

    assert startup_warmup.warmup_read_dependencies() is None
    assert imports == ['pandas', 'pyarrow.dataset']


def test_lifespan_does_not_become_ready_before_warmup(monkeypatch):
    from app import main

    events = []
    monkeypatch.setattr(main, 'ensure_ready', lambda: events.append('schema-ready'))
    monkeypatch.setattr(main, 'warmup_read_dependencies', lambda: events.append('read-dependencies-ready'))

    async def enter():
        async with main.lifespan(main.app):
            events.append('serving')

    asyncio.run(enter())
    assert events == ['schema-ready', 'read-dependencies-ready', 'serving']


def test_missing_required_dependency_prevents_api_readiness(monkeypatch):
    from app import main

    def missing():
        raise ModuleNotFoundError('required read dependency unavailable')

    monkeypatch.setattr(main, 'ensure_ready', lambda: None)
    monkeypatch.setattr(main, 'warmup_read_dependencies', missing)

    async def enter():
        async with main.lifespan(main.app):
            pytest.fail('the API cannot serve before its required dependencies load')

    with pytest.raises(ModuleNotFoundError, match='required read dependency unavailable'):
        asyncio.run(enter())


def test_real_cold_imports_do_not_access_business_or_start_external_activity(tmp_path):
    business = tmp_path / 'untouched-business'
    code = r'''
import json, os, sys
from pathlib import Path

business = Path(os.environ['SHIXU_DATA_DIR']).resolve()
def guard(event, args):
    if event in ('sqlite3.connect', 'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                 'subprocess.Popen', 'os.system', 'os.fork', 'os.posix_spawn'):
        raise AssertionError('dependency warmup attempted external activity: ' + event)
    if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0])).resolve()
        if path == business or business in path.parents:
            raise AssertionError('dependency warmup accessed business files: ' + str(path))
sys.addaudithook(guard)
assert 'pandas' not in sys.modules and 'pyarrow.dataset' not in sys.modules
from app.startup_warmup import warmup_read_dependencies
warmup_read_dependencies()
warmup_read_dependencies()
assert 'app.worker' not in sys.modules and 'app.core' not in sys.modules
assert 'pandas' in sys.modules and 'pyarrow.dataset' in sys.modules
assert not business.exists()
print(json.dumps({'read_dependencies_loaded': True, 'business_untouched': True}))
'''
    environment = {**os.environ, 'SHIXU_DATA_DIR': str(business), 'PYTHONDONTWRITEBYTECODE': '1'}
    completed = subprocess.run([sys.executable, '-c', code], env=environment,
                               cwd=Path(__file__).resolve().parents[1],
                               text=True, capture_output=True, timeout=30, check=True)
    assert json.loads(completed.stdout) == {'read_dependencies_loaded': True, 'business_untouched': True}
    assert not business.exists()
