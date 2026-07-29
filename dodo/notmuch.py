#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2025 - Ruly Tafzil
#
# This file is part of Lazarus
#
# Lazarus is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Dodo is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.

"""Thin wrapper around the ``notmuch`` command-line tool.

Before this module existed, every call site built its own
``subprocess.run(['notmuch', ...])`` invocation, and each picked its own
convention for capturing output (``capture_output=True, text=True`` vs.
``stdout=subprocess.PIPE`` with manual ``.decode()``), whether to pass
``check=True``, and how to turn a failure into a log message. This
module gives the common cases (``count``, ``tags``, ``search --output=
files``, ``search --format=json``, ``tag``) a single, consistent
implementation, while still exposing :func:`run` directly for the rare
call site with a real reason to deviate (e.g. binary/attachment output
that must not be decoded as text).
"""

from __future__ import annotations
import subprocess
from typing import List


def run(*args: str, check: bool = False,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run ``notmuch *args``, capturing stdout/stderr as text.

    Prefer the higher-level helpers below for common operations; use
    this directly only when a call site needs something they don't
    cover (e.g. a one-off flag combination).

    :param check: if True, raise :class:`subprocess.CalledProcessError`
        on a non-zero exit instead of returning it in ``returncode``.
    :param timeout: if given, raise :class:`subprocess.TimeoutExpired`
        after this many seconds (e.g. for ``notmuch address``, which
        scans every message and can take a while on a large mailbox).
    """
    return subprocess.run(['notmuch', *args], capture_output=True,
                          text=True, check=check, timeout=timeout)


def count(query: str, output: str = 'threads') -> int:
    """Return ``notmuch count --output=<output> -- <query>`` as an int.

    Returns 0 (rather than raising) if notmuch fails or returns
    something unparsable, since count is almost always used to decide
    whether it's worth doing more work, not as a correctness check.
    """
    r = run('count', f'--output={output}', '--', query)
    try:
        return int(r.stdout.strip() or '0')
    except ValueError:
        return 0


def tags() -> List[str]:
    """Return every tag known to notmuch (``notmuch search --output=tags *``)."""
    r = run('search', '--output=tags', '*')
    return [t for t in r.stdout.splitlines() if t]


def search_files(query: str, exclude_false: bool = False) -> List[str]:
    """Return file paths matching *query* (``notmuch search --output=files``).

    :param exclude_false: pass ``--exclude=false`` so results include
        files that would otherwise be hidden by
        :func:`~dodo.settings.exclude_tags` -- needed whenever the
        caller is about to act on files by tag (trash/archive/rules)
        rather than displaying a search result list.
    """
    args = ['search']
    if exclude_false:
        args.append('--exclude=false')
    args += ['--output=files', '--', query]
    r = run(*args)
    return [line for line in r.stdout.strip().split('\n') if line]


def search_json(query: str) -> str:
    """Return raw JSON text from ``notmuch search --format=json -- <query>``.

    Raises :class:`subprocess.CalledProcessError` on failure -- callers
    are expected to catch it and fall back to stale data plus an error
    message (see :class:`dodo.search.SearchModel`).
    """
    r = run('search', '--format=json', '--', query, check=True)
    return r.stdout


def tag(tag_expr: str, query: str, exclude_marked: bool = False) -> subprocess.CompletedProcess:
    """Apply a tag expression (e.g. ``'+trash -inbox -unread'``) to *query*.

    :param exclude_marked: also remove the ``marked`` tag, since most
        callers apply this right after acting on a set of
        user-marked threads.
    """
    args = ['tag'] + tag_expr.split()
    if exclude_marked:
        args.append('-marked')
    args += ['--', query]
    return run(*args)


def new(no_hooks: bool = True) -> None:
    """Run ``notmuch new`` to pick up files moved on disk.

    :param no_hooks: pass ``--no-hooks`` (the common case, used after
        a background file move where re-running post-new hooks would
        be redundant or unwanted).
    """
    args = ['new']
    if no_hooks:
        args.append('--no-hooks')
    run(*args)
