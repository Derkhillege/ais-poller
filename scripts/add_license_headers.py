#!/usr/bin/env python3
# ais-poller - For polling sockets and parsing them into flat vessel records.
# Copyright (C) 2026  Derk Hillege
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Insert the GNU GPL notice at the top of every source file that is missing it.

The notice text is built from `pyproject.toml` (project name, description,
author, `[tool.ais-poller] copyright-year`), so editing that file and running
this script updates every header. Used by the `.githooks/pre-commit` hook, and
runnable by hand:

    python3 scripts/add_license_headers.py            # whole repo, insert or refresh
    python3 scripts/add_license_headers.py --check     # report only, exit 1 if any differ
    python3 scripts/add_license_headers.py path/to/file.py ...
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ais_poller.metadata import ProjectMetadata, project_metadata  # noqa: E402

#: SPDX expression matching the `license` field in `pyproject.toml`.
LICENSE_ID = "GPL-3.0-or-later"

#: Last line of the notice, and the marker used to recognise one already there.
MARKER = f"SPDX-License-Identifier: {LICENSE_ID}"

#: The licence text, unchanged from the GPL's "How to Apply" appendix.
GPL_BODY = """\
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>."""

#: File types that take a `#` comment header.
HASH_SUFFIXES = {".py", ".pyi", ".sh", ".bash", ".zsh", ".toml", ".yml", ".yaml", ".cfg", ".ini"}

#: Directories never touched (vendored, generated, or owned elsewhere).
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".devcontainer",
    "build",
    "dist",
    "node_modules",
    "local-runs",
    "data",
}

#: Individual files never touched.
SKIP_FILES = {"LICENSE", "COPYING", "uv.lock", ".python-version"}


def notice(meta: ProjectMetadata | None = None) -> str:
    """The notice body, without comment markers, built from `pyproject.toml`."""
    meta = meta if meta is not None else project_metadata(ROOT / "pyproject.toml")
    title = f"{meta.name} - {meta.summary}" if meta.summary else meta.name
    copyright_line = f"Copyright (C) {meta.copyright_year}  {meta.author}".rstrip()
    return "\n".join(
        [
            *textwrap.wrap(title, width=76),
            copyright_line,
            "",
            GPL_BODY,
            "",
            MARKER,
        ]
    )


def comment_prefix(path: Path) -> str | None:
    """Comment marker for `path`, or None when the file type takes no header."""
    if path.name in SKIP_FILES or path.name.startswith(".env"):
        return None
    if path.suffix in HASH_SUFFIXES:
        return "#"
    if path.suffix == "":  # extensionless: only scripts with a shebang
        try:
            with path.open(encoding="utf-8") as f:
                if f.readline().startswith("#!"):
                    return "#"
        except (OSError, UnicodeDecodeError):
            return None
    return None


def header_for(prefix: str, meta: ProjectMetadata | None = None) -> str:
    """The notice as a comment block, one `prefix` per line, ending in a newline."""
    lines = [f"{prefix} {line}".rstrip() for line in notice(meta).splitlines()]
    return "\n".join(lines) + "\n"


def insert_point(lines: list[str]) -> int:
    """Index of the line the header goes before: after any shebang and coding line."""
    at = 0
    for line in lines[:2]:
        stripped = line.strip()
        if at == 0 and stripped.startswith("#!"):
            at += 1
        elif stripped.startswith("#") and "coding" in stripped and "=" in stripped:
            at += 1
        else:
            break
    return at


def existing_span(lines: list[str], prefix: str) -> tuple[int, int] | None:
    """Line range `[start, end)` of the comment block holding the notice, if any.

    Only comment lines count, so a string literal mentioning the marker (as this
    script itself has) is never mistaken for a notice.
    """
    for i, line in enumerate(lines[:40]):
        if MARKER not in line or not line.startswith(prefix):
            continue
        start = i
        while start > 0:
            above = lines[start - 1]
            if not above.startswith(prefix) or above.startswith("#!"):
                break
            start -= 1
        return start, i + 1
    return None


def _rendered(path: Path) -> tuple[str, str] | None:
    """`(current text, text with an up-to-date notice)`, or None when not applicable."""
    prefix = comment_prefix(path)
    if prefix is None:
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip():
        return None

    lines = text.splitlines(keepends=True)
    bare = [line.rstrip("\n") for line in lines]
    header = header_for(prefix)
    span = existing_span(bare, prefix)
    if span is not None:
        start, end = span
        rest = lines[end:]
        if not rest or rest[0].strip():  # keep exactly one blank line after the notice
            rest = ["\n", *rest]
        updated = "".join(lines[:start]) + header + "".join(rest)
    else:
        at = insert_point(bare)
        head, tail = lines[:at], lines[at:]
        while tail and not tail[0].strip():
            tail = tail[1:]
        updated = "".join(head) + header + "\n" + "".join(tail)
    return text, updated


def status(path: Path) -> str | None:
    """`"add"`, `"update"` or None, for what `path` needs."""
    rendered = _rendered(path)
    if rendered is None:
        return None
    text, updated = rendered
    if text == updated:
        return None
    return "update" if MARKER in text else "add"


def apply(path: Path) -> str | None:
    """Write an up-to-date notice into `path`. Returns what it did, or None."""
    rendered = _rendered(path)
    if rendered is None:
        return None
    text, updated = rendered
    if text == updated:
        return None
    path.write_text(updated, encoding="utf-8")
    return "update" if MARKER in text else "add"


def _skipped(path: Path, root: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        parts = path.parts
    return any(part in SKIP_DIRS for part in parts[:-1])


def tracked_files(root: Path) -> list[Path]:
    """Every git-tracked or new (not ignored) file under `root`, minus the skipped dirs."""
    out = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = [root / name for name in out.split("\0") if name]
    return [p for p in paths if p.is_file() and not _skipped(p, root)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path, help="files to check (default: all tracked)")
    parser.add_argument(
        "--check", action="store_true", help="only report files to fix (exit 1 if any)"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="do not list the files that were changed"
    )
    args = parser.parse_args(argv)

    files = args.files or tracked_files(ROOT)
    files = [p for p in files if p.is_file() and not _skipped(p, ROOT)]

    if args.check:
        wording = {"add": "missing GPL notice", "update": "outdated GPL notice"}
        bad = [(p, action) for p in files if (action := status(p))]
        for path, action in bad:
            print(f"{wording[action]}: {path}")
        return 1 if bad else 0

    done = {"add": "added GPL notice", "update": "updated GPL notice"}
    for path in files:
        action = apply(path)
        if action and not args.quiet:
            print(f"{done[action]}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
