"""Verify published price, reference and action inputs before derived cache hits."""
from collections import OrderedDict
from os import lstat
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from threading import RLock

from app.domain.strategy_prices import compact_source_token


_source_proofs = OrderedDict()
_source_lock = RLock()


def _file_stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _PublishedSourceProof:
    """Reuse a SHA check only while every verified filesystem signature matches."""
    def __init__(self, source, declared, digest):
        _, root, generation, prices, files = source
        base = Path(root)
        self.declared = declared
        self.digest = digest
        self.files = {str(base / relative): (device, inode, size, modified, changed)
                      for _, relative, _, size, device, inode, modified, changed in files}
        # The actual daily reader may depend on objects outside an old head's
        # manifest list. In that case keep its full verifier on every request.
        self.covers_prices = all(self.files.get(path, ())[1:] == tuple(stamp)
                                 for path, *stamp in prices)
        legacy_prices = base / 'published' / generation / 'daily.parquet'
        self.absent_legacy_prices = None if str(legacy_prices) in self.files else legacy_prices
        self.directories = {str(parent) for path in self.files for parent in Path(path).parents
                            if parent.is_relative_to(base)}

    def matches(self, declared):
        if not self.covers_prices or self.declared != declared:
            return False
        try:
            if self.absent_legacy_prices is not None and self.absent_legacy_prices.is_file():
                return False
            if not all(S_ISDIR(lstat(path).st_mode) for path in self.directories):
                return False
            for path, stamp in self.files.items():
                info = lstat(path)
                if not S_ISREG(info.st_mode) or _file_stamp(info) != stamp:
                    return False
        except OSError:
            return False
        return True


def verified_price_source(market, status):
    """Return the verified fixed-source digest, or None for older sources."""
    verify = getattr(market, 'verify_condition_sources', None)
    if not status.get('snapshot_id') or not isinstance(status.get('files'), list) or not callable(verify):
        return None
    identity = (status.get('path'), status['snapshot_id'])
    declared = tuple((row.get('role'), row.get('path'), row.get('sha256'), row.get('bytes'))
                     for row in status['files'] if isinstance(row, dict))
    with _source_lock:
        previous = _source_proofs.get(identity)
    if previous is not None and previous.matches(declared):
        return previous.digest
    source = verify()
    proof = compact_source_token(source)
    if proof is None:
        return None
    source_digest = proof['verified_closure_sha256']
    if isinstance(source, tuple) and source and source[0] == 'verified-condition-sources-v1':
        if source[2] != status['snapshot_id']:
            return None
        bound = _PublishedSourceProof(source, declared, source_digest)
        if bound.covers_prices and len(bound.files) <= 1024:
            with _source_lock:
                _source_proofs[identity] = bound
                _source_proofs.move_to_end(identity)
                while len(_source_proofs) > 2:
                    _source_proofs.popitem(last=False)
    return source_digest
