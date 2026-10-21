"""Explicit, offline import of already-authorized audit files; no source API calls."""
from pathlib import Path
import argparse
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('audit_dir');parser.add_argument('--data-dir',default=str(ROOT/'data'))
    args=parser.parse_args()
    os.environ['SHIXU_DATA_DIR']=str(Path(args.data_dir).expanduser().resolve())
    from app.services.bootstrap import import_validated_audit
    result=import_validated_audit(args.audit_dir)
    status=result.get('status',{})
    print(json.dumps({k:v for k,v in result.items() if k!='status'} | {'status':{k:v for k,v in status.items() if k!='manifest'}},ensure_ascii=False,indent=2))
