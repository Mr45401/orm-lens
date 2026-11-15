"""Path helpers shared by the Django and SQLAlchemy parsers.

Kept in a module of their own so that ``parser`` and ``sqlalchemy_parser``
need not import each other: ``parser`` calls into ``sqlalchemy_parser``, and
both walk the same directories and name apps the same way.
"""

from __future__ import annotations

import os

# Directory names skipped when walking a workspace for arbitrary ``.py`` files
# (n+1 scanner, signal graph). Kept broader than ``DEFAULT_EXCLUDES`` on purpose:
# those are glob patterns for the models.py walk, this is a dirname-based
# denylist for the whole-tree walk.
BROAD_SKIP_DIRS: frozenset = frozenset(
    {"migrations", "node_modules", "venv", ".venv", "env", ".git", "__pycache__"}
)


def _has_app_marker(directory: str) -> bool:
    return os.path.isdir(os.path.join(directory, "migrations")) or os.path.isfile(
        os.path.join(directory, "apps.py")
    )


def _app_dir_for(fs_path: str) -> tuple[str, str]:
    """Return ``(app_dir, app_name)`` for a model module path.

    Starts at the file's directory (or the parent of a ``models/`` package).
    When that directory lacks ``migrations/`` and ``apps.py``, walks upward
    while the parent directory is still a Python package (has ``__init__.py``)
    and stops at the first directory that has ``migrations/`` or ``apps.py``
    — so sub-package models such as ``io_storages/s3/models.py`` get the
    enclosing app's label rather than ``s3`` (#266). Falls back to the
    starting directory when nothing above it qualifies.
    """
    parent = os.path.dirname(fs_path)
    parent_name = os.path.basename(parent)
    cand = os.path.dirname(parent) if parent_name == "models" else parent
    cand_name = os.path.basename(cand)
    if not cand or _has_app_marker(cand):
        return cand, cand_name

    cur = cand
    while True:
        up = os.path.dirname(cur)
        up_name = os.path.basename(up)
        if up == cur or up_name in ("", ".", ".."):
            break
        if not os.path.isfile(os.path.join(up, "__init__.py")):
            break
        if _has_app_marker(up):
            return up, up_name
        cur = up

    return cand, cand_name
