#!/usr/bin/env python3
"""Generate a new Compose file-secret; never print/read an existing master key."""
import argparse,base64,os,secrets,stat
from pathlib import Path

def create_secret(path):
    path=Path(path).expanduser().absolute();directory=path.parent
    if directory.is_symlink() or path.is_symlink():raise ValueError('Secret paths cannot be symlinks')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if stat.S_IMODE(directory.stat().st_mode)&0o077:raise ValueError('Choose a private directory with mode 0700; existing directory permissions are not changed')
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o444)
    with os.fdopen(descriptor,'wb') as writer:
        writer.write(base64.urlsafe_b64encode(secrets.token_bytes(32)));writer.flush();os.fsync(writer.fileno())
    path.chmod(0o444)
    return str(path)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('path');a=p.parse_args();print('Created new private secret file: '+create_secret(a.path))
