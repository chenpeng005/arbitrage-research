#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BASE = Path('/home/admin/projects/etf-primary-shadow')
WEB = BASE / 'web'
MANIFEST = WEB / 'UI_CANONICAL.json'
PUBLIC_INDEX = Path('/var/www/opportunity-portal/etf-shadow/index.html')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def load_contract():
    m = json.loads(MANIFEST.read_text(encoding='utf-8'))
    expected = str(m['sha256'])
    source = BASE / m['source_file']
    snapshot = BASE / m['canonical_snapshot']
    if not snapshot.exists() or sha256(snapshot) != expected:
        raise RuntimeError('canonical snapshot missing or hash mismatch')
    return m, expected, source, snapshot


def verify():
    m, expected, source, snapshot = load_contract()
    return {
        'canonical_id': m['canonical_id'],
        'expected_sha256': expected,
        'source_sha256': sha256(source) if source.exists() else None,
        'snapshot_sha256': sha256(snapshot),
        'public_sha256': sha256(PUBLIC_INDEX) if PUBLIC_INDEX.exists() else None,
        'source_matches': source.exists() and sha256(source) == expected,
        'public_matches': PUBLIC_INDEX.exists() and sha256(PUBLIC_INDEX) == expected,
    }


def guarded_run(command):
    m, expected, source, snapshot = load_contract()
    source_mismatch = (not source.exists()) or sha256(source) != expected
    backup = None
    if source_mismatch:
        fd, tmp = tempfile.mkstemp(prefix='etf-ui-source-', suffix='.html')
        os.close(fd)
        backup = Path(tmp)
        if source.exists():
            shutil.copy2(source, backup)
        shutil.copy2(snapshot, source)

    rc = 1
    try:
        rc = subprocess.run(command).returncode
    finally:
        # Public UI must stay canonical regardless of child result.
        if (not PUBLIC_INDEX.exists()) or sha256(PUBLIC_INDEX) != expected:
            PUBLIC_INDEX.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(snapshot, PUBLIC_INDEX)
        # Preserve an accidental/unapproved source edit for inspection; never publish it.
        if source_mismatch:
            if backup and backup.exists() and backup.stat().st_size:
                shutil.copy2(backup, source)
            elif source.exists():
                source.unlink()
            if backup and backup.exists():
                backup.unlink()

    result = verify()
    result.update({'child_returncode': rc, 'source_was_mismatched': source_mismatch})
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return rc


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('verify')
    r = sub.add_parser('run')
    r.add_argument('command', nargs=argparse.REMAINDER)
    args = p.parse_args()
    if args.action == 'verify':
        print(json.dumps(verify(), ensure_ascii=False, indent=2))
        return 0
    command = list(args.command)
    if command and command[0] == '--':
        command = command[1:]
    if not command:
        raise SystemExit('missing command')
    return guarded_run(command)


if __name__ == '__main__':
    raise SystemExit(main())
