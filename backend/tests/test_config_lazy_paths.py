"""Explicit storage overrides do not resolve an unused default data root."""
from pathlib import Path

import pytest

from app.infrastructure import config


PATHS = [
    ('sqlite_path', 'SHIXU_SQLITE_PATH', Path('business/app.sqlite3')),
    ('published_dir', 'SHIXU_PUBLISHED_DIR', Path('published')),
    ('raw_dir', 'SHIXU_RAW_DIR', Path('raw')),
    ('work_dir', 'SHIXU_WORK_DIR', Path('work')),
]


@pytest.mark.parametrize('function,variable,suffix', PATHS)
@pytest.mark.parametrize('empty', [False, True])
def test_explicit_storage_path_never_resolves_default_root(monkeypatch, tmp_path, function, variable, suffix, empty):
    monkeypatch.chdir(tmp_path)
    configured = '' if empty else str(tmp_path / 'explicit' / suffix)
    monkeypatch.setenv(variable, configured)
    def unused_default(): pytest.fail('explicit storage paths must not resolve an unused data root')
    monkeypatch.setattr(config, 'data_dir', unused_default)
    assert getattr(config, function)() == Path(configured).resolve()


@pytest.mark.parametrize('function,variable,suffix', PATHS)
def test_absent_override_uses_current_default_root(monkeypatch, tmp_path, function, variable, suffix):
    monkeypatch.delenv(variable, raising=False)
    for root in (tmp_path / 'first', tmp_path / 'second'):
        monkeypatch.setenv('SHIXU_DATA_DIR', str(root))
        assert getattr(config, function)() == (root / suffix).resolve()


@pytest.mark.parametrize('function,variable,suffix', PATHS)
def test_explicit_path_changes_take_effect_without_restarting(monkeypatch, tmp_path, function, variable, suffix):
    for root in (tmp_path / 'first', tmp_path / 'second'):
        monkeypatch.setenv(variable, str(root / suffix))
        assert getattr(config, function)() == (root / suffix).resolve()


def test_unchanged_override_still_resolves_replaced_symlink(monkeypatch, tmp_path):
    first = tmp_path / 'first'; second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    pointer = tmp_path / 'current'; pointer.symlink_to(first, target_is_directory=True)
    monkeypatch.setenv('SHIXU_WORK_DIR', str(pointer))
    assert config.work_dir() == first
    pointer.unlink(); pointer.symlink_to(second, target_is_directory=True)
    assert config.work_dir() == second
