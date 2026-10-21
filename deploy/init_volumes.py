"""One-shot container volume ownership and migration, then no long-running root process."""
import os
from pathlib import Path

if __name__=='__main__':
    root=Path('/var/lib/shixu')
    for name in ('business','published','raw','work','credentials'):
        directory=root/name;directory.mkdir(parents=True,exist_ok=True)
        os.chown(directory,10001,10001);directory.chmod(0o700 if name in ('business','credentials') else 0o750)
    os.setgid(10001);os.setuid(10001)
    from app.core import ensure_ready
    ensure_ready()
    print('Storage ownership and migrations are ready; no data sync was started.')
