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

"""Tests for the GPL startup notice and the license-header tool."""

import io
import subprocess
import sys
import tomllib
from pathlib import Path

from ais_poller.cli import main
from ais_poller.license import print_startup_notice, startup_notice
from ais_poller.metadata import DIST_NAME, project_metadata

ROOT = Path(__file__).resolve().parent.parent
ADDER = ROOT / "scripts" / "add_license_headers.py"


class _Tty(io.StringIO):
    """A StringIO that claims to be a terminal."""

    def isatty(self) -> bool:
        return True


def test_startup_notice_mentions_warranty_and_redistribution():
    text = startup_notice()
    assert "ABSOLUTELY NO WARRANTY" in text
    assert "ais-poller license --warranty" in text
    assert "ais-poller license --conditions" in text


def test_notice_printed_when_interactive(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _Tty())
    stream = _Tty()
    assert print_startup_notice(stream) is True
    assert "ABSOLUTELY NO WARRANTY" in stream.getvalue()


def test_notice_skipped_when_not_a_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO())  # not a tty
    stream = _Tty()
    assert print_startup_notice(stream) is False
    assert stream.getvalue() == ""


def test_license_command(capsys):
    assert main(["license"]) == 0
    assert "ABSOLUTELY NO WARRANTY" in capsys.readouterr().out


def test_license_warranty_and_conditions(capsys):
    assert main(["license", "--warranty"]) == 0
    assert "Disclaimer of Warranty" in capsys.readouterr().out
    assert main(["license", "--conditions"]) == 0
    assert "GNU General Public License" in capsys.readouterr().out


def test_every_tracked_source_file_has_the_notice():
    done = subprocess.run(
        [sys.executable, str(ADDER), "--check"], capture_output=True, text=True, cwd=ROOT
    )
    assert done.returncode == 0, done.stdout


def test_adder_inserts_after_shebang(tmp_path):
    script = tmp_path / "tool.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    subprocess.run([sys.executable, str(ADDER), str(script)], check=True)
    lines = script.read_text().splitlines()
    assert lines[0] == "#!/bin/sh"
    assert any(line.startswith("# Copyright (C)") for line in lines[1:])
    assert lines[-1] == "echo hi"


def test_adder_is_idempotent_and_skips_other_types(tmp_path):
    module = tmp_path / "mod.py"
    module.write_text('"""Doc."""\n')
    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    once = module.read_text()
    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    assert module.read_text() == once
    assert once.startswith("# ais-poller")
    assert once.endswith('"""Doc."""\n')

    data = tmp_path / "vessels.ndjson"
    data.write_text('{"mmsi": 1}\n')
    subprocess.run([sys.executable, str(ADDER), str(data)], check=True)
    assert data.read_text() == '{"mmsi": 1}\n'


def test_metadata_comes_from_pyproject():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    meta = project_metadata(ROOT / "pyproject.toml")
    assert meta.name == data["project"]["name"]
    assert meta.summary == data["project"]["description"]
    assert meta.author == data["project"]["authors"][0]["name"]
    assert meta.copyright_year == data["tool"]["ais-poller"]["copyright-year"]


def test_startup_notice_uses_pyproject_values():
    meta = project_metadata(ROOT / "pyproject.toml")
    text = startup_notice()
    assert text.startswith(f"{meta.name} ")
    assert f"Copyright (C) {meta.copyright_year}  {meta.author}" in text


def test_metadata_reads_a_given_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "ais-poller"\n'
        'description = "Example summary."\n'
        'authors = [{ name = "Someone Else" }]\n'
        "\n"
        "[tool.ais-poller]\n"
        'copyright-year = "1999"\n'
    )
    meta = project_metadata(pyproject)
    assert (meta.name, meta.summary, meta.author, meta.copyright_year) == (
        "ais-poller",
        "Example summary.",
        "Someone Else",
        "1999",
    )


def test_header_body_follows_pyproject(tmp_path):
    meta = project_metadata(ROOT / "pyproject.toml")
    module = tmp_path / "mod.py"
    module.write_text("x = 1\n")
    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    header = module.read_text()
    assert meta.name in header
    assert meta.summary.split()[0] in header
    assert f"# Copyright (C) {meta.copyright_year}  {meta.author}" in header


def test_adder_refreshes_a_stale_notice(tmp_path):
    meta = project_metadata(ROOT / "pyproject.toml")
    module = tmp_path / "mod.py"
    module.write_text("x = 1\n")
    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    stale = module.read_text().replace(f"Copyright (C) {meta.copyright_year}", "Copyright (C) 1998")
    module.write_text(stale)

    done = subprocess.run(
        [sys.executable, str(ADDER), "--check", str(module)], capture_output=True, text=True
    )
    assert done.returncode == 1
    assert "outdated GPL notice" in done.stdout

    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    refreshed = module.read_text()
    assert "1998" not in refreshed
    assert f"# Copyright (C) {meta.copyright_year}  {meta.author}" in refreshed
    assert refreshed.endswith("x = 1\n")
    assert refreshed.count("SPDX-License-Identifier") == 1


def test_adder_ignores_the_marker_in_code(tmp_path):
    """A string literal naming the SPDX marker must not be mistaken for a notice."""
    module = tmp_path / "mod.py"
    module.write_text('MARKER = "SPDX-License-Identifier: GPL-3.0-or-later"\nx = 1\n')
    subprocess.run([sys.executable, str(ADDER), str(module)], check=True)
    text = module.read_text()
    assert 'MARKER = "SPDX-License-Identifier: GPL-3.0-or-later"' in text
    assert text.startswith("# ")
    assert text.endswith("x = 1\n")


def test_metadata_falls_back_to_the_installed_distribution(tmp_path):
    """Without a source tree the notice still resolves, from distribution metadata."""
    meta = project_metadata(tmp_path / "absent" / "pyproject.toml")
    assert meta.name == DIST_NAME
    assert meta.copyright_year == project_metadata(ROOT / "pyproject.toml").copyright_year


def test_metadata_ignores_a_foreign_pyproject(tmp_path):
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "something-else"\ndescription = "Not us."\n')
    meta = project_metadata(other)
    assert meta.name == DIST_NAME
    assert meta.summary != "Not us."
