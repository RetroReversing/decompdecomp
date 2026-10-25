"""Read-only, recursive hash lookup for imported projects. No filename guesses or archive extraction."""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from .. import consoles


@lru_cache(maxsize=10000)
def _hash(path: str, size: int, mtime: int, ctime: int, platform: str) -> str:
    if platform == "raw":
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha1").hexdigest()
    return consoles.get(platform).library_sha1(Path(path))


def fingerprint(path: Path, platform: str) -> str | None:
    try:
        stat = path.stat()
        if not path.is_file():
            return None
        value = _hash(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, platform)
        after = path.stat()
        if (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            return None
        return value
    except (OSError, ValueError):
        return None


def find(project, needs: dict, folder: str, log=print) -> dict:
    expected = {str(v.get('sha1', '')).lower() for v in needs.get('rom_versions', [])}
    expected.add(str(needs.get('sha1') or '').lower())
    expected = {v for v in expected if re.fullmatch('[0-9a-f]{40}', v)}
    if not expected:
        return {'path': None, 'reason': 'This project does not declare an expected ROM SHA-1.'}
    platform = project.detect_platform()
    local = project.root / needs['path'] if needs.get('path') else None
    if local and fingerprint(local, platform) in expected:
        return {'path': str(local), 'in_project': fingerprint(local, 'raw') in expected}
    if not folder:
        return {'path': None, 'reason': 'Set a ROM library folder in Settings to find your game automatically.'}
    root = Path(folder).expanduser()
    if not root.is_dir():
        return {'path': None, 'reason': 'The ROM library folder is unavailable. Check its path in Settings.'}
    log('Searching the ROM library and its subfolders for the expected SHA-1…')
    count = 0
    skipped = 0
    def inaccessible(error):
        nonlocal skipped
        skipped += 1
    for parent, dirs, files in os.walk(root, followlinks=False, onerror=inaccessible):
        dirs.sort()
        for name in sorted(files):
            path = Path(parent) / name
            if path.is_symlink():
                continue
            count += 1
            if fingerprint(path, platform) in expected:
                log(f'Found a matching game: {path}')
                return {'path': str(path), 'in_project': False, 'scanned': count, 'skipped': skipped}
            if count % 100 == 0:
                log(f'Checked {count} files…')
    return {'path': None, 'scanned': count, 'skipped': skipped,
            'reason': f'No matching ROM found in {count} files. {skipped} folders could not be read. '
                      'Choose a file manually if it is archived or outside this folder.'}
