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

"""GPL notices: the short interactive-startup notice and the `license` subcommand text."""

import sys
from typing import TextIO

from ais_poller import __version__
from ais_poller.metadata import project_metadata

_META = project_metadata()

PROGRAM = _META.name
COPYRIGHT_YEAR = _META.copyright_year
COPYRIGHT_HOLDER = _META.author

#: `Copyright (C) <year>  <holder>`, both taken from `pyproject.toml`.
COPYRIGHT_LINE = f"Copyright (C) {COPYRIGHT_YEAR}  {COPYRIGHT_HOLDER}".rstrip()

#: Short notice the GPL asks interactive programs to print at startup.
STARTUP_NOTICE = f"""\
{PROGRAM} {{version}}  {COPYRIGHT_LINE}
This program comes with ABSOLUTELY NO WARRANTY; for details run
`{PROGRAM} license --warranty'.  This is free software, and you are welcome
to redistribute it under certain conditions; run
`{PROGRAM} license --conditions' for details."""

WARRANTY = """\
GNU General Public License, version 3, sections 15-17 (Disclaimer of Warranty,
Limitation of Liability, Interpretation of Sections 15 and 16):

  15. Disclaimer of Warranty.

  THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY
APPLICABLE LAW.  EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT
HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM "AS IS" WITHOUT WARRANTY
OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO,
THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
PURPOSE.  THE ENTIRE RISK AS TO THE QUALITY AND PERFORMANCE OF THE PROGRAM
IS WITH YOU.  SHOULD THE PROGRAM PROVE DEFECTIVE, YOU ASSUME THE COST OF
ALL NECESSARY SERVICING, REPAIR OR CORRECTION.

  16. Limitation of Liability.

  IN NO EVENT UNLESS REQUIRED BY APPLICABLE LAW OR AGREED TO IN WRITING
WILL ANY COPYRIGHT HOLDER, OR ANY OTHER PARTY WHO MODIFIES AND/OR CONVEYS
THE PROGRAM AS PERMITTED ABOVE, BE LIABLE TO YOU FOR DAMAGES, INCLUDING ANY
GENERAL, SPECIAL, INCIDENTAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE
USE OR INABILITY TO USE THE PROGRAM (INCLUDING BUT NOT LIMITED TO LOSS OF
DATA OR DATA BEING RENDERED INACCURATE OR LOSSES SUSTAINED BY YOU OR THIRD
PARTIES OR A FAILURE OF THE PROGRAM TO OPERATE WITH ANY OTHER PROGRAMS),
EVEN IF SUCH HOLDER OR OTHER PARTY HAS BEEN ADVISED OF THE POSSIBILITY OF
SUCH DAMAGES.

  17. Interpretation of Sections 15 and 16.

  If the disclaimer of warranty and limitation of liability provided above
cannot be given local legal effect according to their terms, reviewing
courts shall apply local law that most closely approximates an absolute
waiver of all civil liability in connection with the Program, unless a
warranty or assumption of liability accompanies a copy of the Program in
return for a fee.

The full license is in the LICENSE file shipped with this program, and at
<https://www.gnu.org/licenses/gpl-3.0.html>."""

CONDITIONS = f"""\
{PROGRAM} is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

In short, you may copy, distribute and modify it provided that you:

  * keep this copyright notice and the license notices intact (GPL section 4);
  * state that you changed the files and when, and release your modified
    version under this same license (GPL section 5);
  * pass on the complete corresponding source code with any binary you
    convey, or a written offer for it (GPL section 6);
  * do not impose further restrictions on the rights this license grants
    (GPL section 10).

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  Run `{PROGRAM} license --warranty' for details.

The full license is in the LICENSE file shipped with this program, and at
<https://www.gnu.org/licenses/gpl-3.0.html>."""


def startup_notice() -> str:
    """The short GPL startup notice, with the running version filled in."""
    return STARTUP_NOTICE.format(version=__version__)


def is_interactive(stream: TextIO | None = None) -> bool:
    """True when the program does terminal interaction, i.e. stdin and stderr are a tty."""
    stream = sys.stderr if stream is None else stream
    try:
        return sys.stdin.isatty() and stream.isatty()
    except (AttributeError, ValueError):  # detached or closed streams
        return False


def print_startup_notice(stream: TextIO | None = None) -> bool:
    """Print the startup notice to `stream` (stderr) when running interactively.

    Returns True when the notice was printed. It goes to stderr so that piping
    `parse` output stays clean.
    """
    stream = sys.stderr if stream is None else stream
    if not is_interactive(stream):
        return False
    print(startup_notice(), file=stream)
    print(file=stream)
    return True
