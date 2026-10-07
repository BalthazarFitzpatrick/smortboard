"""filesystem helpers for behaviour that differs between posix and windows"""

import os
import shutil
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _clear_readonly_and_retry(func: Callable[..., Any], path: str, *_: Any) -> None:
    # windows refuses to delete read-only files, and git marks its objects read-only
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: str | Path) -> None:
    """rmtree that never raises and also removes read-only files such as git objects"""
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)
    else:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
