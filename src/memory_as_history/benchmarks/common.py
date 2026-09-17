"""Shared provenance for reproducible benchmark reports."""
import hashlib
from importlib.metadata import version
from pathlib import Path
import platform
import sqlite3


def environment():
    from memory_as_history import storage
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'sqlite': sqlite3.sqlite_version, 'package': version('memory-as-history'),
            'storage_sha256': hashlib.sha256(Path(storage.__file__).read_bytes()).hexdigest(),
            'harness_sha256': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in sorted(Path(__file__).parent.glob('*.py'))}}
