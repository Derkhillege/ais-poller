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

"""Project metadata for the licensing notices, resolved from `pyproject.toml`.

Name, summary and author come from the `[project]` table (or, for an installed
copy without a source tree, from the distribution metadata built from it). The
copyright year comes from `[tool.ais-poller] copyright-year`. Nothing here is
hardcoded, so editing `pyproject.toml` changes both the notice the CLI prints
and the headers `scripts/add_license_headers.py` writes.
"""

import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import metadata as distribution_metadata
from pathlib import Path

#: Distribution name, derived from the package name rather than spelled out.
DIST_NAME = (__package__ or "").replace("_", "-")


@dataclass(frozen=True)
class ProjectMetadata:
    """The fields the GPL notices need."""

    name: str
    summary: str
    author: str
    copyright_year: str


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def find_pyproject(start: Path | None = None) -> Path | None:
    """The nearest `pyproject.toml` at or above `start` that describes this project."""
    here = (start or Path(__file__).resolve()).parent
    for parent in [here, *here.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.is_file() and _project_table(candidate) is not None:
            return candidate
    return None


def _load(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _project_table(path: Path) -> dict | None:
    """The `[project]` table of `path`, if it names this distribution."""
    project = _load(path).get("project")
    if not isinstance(project, dict) or "name" not in project:
        return None
    if DIST_NAME and _normalise(str(project["name"])) != _normalise(DIST_NAME):
        return None
    return project


def _author_name(authors: object, author_email: str | None = None) -> str:
    """First author's name, from the `[project] authors` table or an Author-email header."""
    if isinstance(authors, list):
        for entry in authors:
            if isinstance(entry, dict) and entry.get("name"):
                return str(entry["name"])
    if author_email:
        return author_email.split(",")[0].split("<")[0].strip()
    return ""


def _year_from_own_header() -> str | None:
    """The year in this file's own GPL header, written from `pyproject.toml`.

    The fallback for an installed copy: the copyright year is not part of
    standard distribution metadata, but every source file carries it.
    """
    try:
        head = "\n".join(Path(__file__).read_text(encoding="utf-8").splitlines()[:40])
    except OSError:
        return None
    match = re.search(r"Copyright \(C\)\s+(\d{4}(?:-\d{4})?)", head)
    return match.group(1) if match else None


def _from_pyproject(path: Path) -> ProjectMetadata | None:
    project = _project_table(path)
    if project is None:
        return None
    data = _load(path)
    tool = data.get("tool", {})
    settings = tool.get(str(project["name"]), {}) if isinstance(tool, dict) else {}
    year = settings.get("copyright-year") if isinstance(settings, dict) else None
    return ProjectMetadata(
        name=str(project["name"]),
        summary=str(project.get("description", "")),
        author=_author_name(project.get("authors")),
        copyright_year=str(year or _year_from_own_header() or datetime.now(UTC).year),
    )


def _header(md: object, name: str) -> str:
    """One distribution-metadata header, or "" when it is absent."""
    try:
        value = md.get(name)  # type: ignore[attr-defined]
    except (KeyError, AttributeError):
        return ""
    return str(value) if value else ""


def _from_distribution() -> ProjectMetadata | None:
    try:
        md = distribution_metadata(DIST_NAME)
    except (PackageNotFoundError, ValueError):
        return None
    return ProjectMetadata(
        name=_header(md, "Name") or DIST_NAME,
        summary=_header(md, "Summary"),
        author=_header(md, "Author") or _author_name(None, _header(md, "Author-email")),
        copyright_year=str(_year_from_own_header() or datetime.now(UTC).year),
    )


@cache
def project_metadata(pyproject: Path | None = None) -> ProjectMetadata:
    """Project metadata from `pyproject.toml` (preferred) or the installed distribution.

    `pyproject` names the file explicitly; without it the nearest one above this
    module is used, and an installed copy without a source tree falls back to
    the distribution metadata.
    """
    path = pyproject or find_pyproject()
    found = _from_pyproject(path) if path is not None else None
    if found is None:
        found = _from_distribution()
    if found is None:  # neither a source tree nor an installed distribution
        found = ProjectMetadata(
            name=DIST_NAME,
            summary="",
            author="",
            copyright_year=str(_year_from_own_header() or datetime.now(UTC).year),
        )
    return found
