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
# Lazarus is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Lazarus. If not, see <https://www.gnu.org/licenses/>.
"""Mail filter rules.

A :class:`Rule` is a notmuch query plus a set of tags to add/remove
and an optional target folder to move matching mail into. Configure a
list of them as :func:`~lazarus.settings.filter_rules` in ``config.py``
-- see the "Mail filters" section of README.md for a worked example.

Rules are applied automatically after every successful sync (see the
``done()`` callback in :func:`lazarus.controller.AppController.sync_mail`), scoped by
:func:`~lazarus.settings.filter_scope_query` so a full mailbox isn't
re-tagged on every run. Tag operations are idempotent -- safe to
re-apply to a message that already has the tags. File moves (for rules
with a ``move_to`` folder) are enqueued to the same background worker
used by :func:`~lazarus.actions.move_files`, so they're serialised and
``notmuch new`` runs after each batch lands on disk.

Re-run the whole rule set by hand with ``C-r``
(:func:`~lazarus.controller.AppController.apply_filter_rules`) -- useful for testing a
new rule against existing mail without waiting for the next sync.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import List

from . import actions
from . import notmuch

logger = logging.getLogger(__name__)


@dataclass
class Rule:
    """A single filter rule.

    :param query: a notmuch query (same syntax as the search bar) that
        selects which messages this rule applies to
    :param tag_add: tags to add to matching messages
    :param tag_remove: tags to remove from matching messages
    :param move_to: optional path to a Maildir folder -- matching
        messages are moved into ``<move_to>/cur/`` after any tags
        are applied (e.g. ``'~/Mail/Archive'`` or
        ``'~/Mail/Work/Projects'``)
    :param name: optional label, used only in log messages
    """
    query: str
    tag_add: List[str] = field(default_factory=list)
    tag_remove: List[str] = field(default_factory=list)
    move_to: str = ''
    name: str = ''

    def describe(self) -> str:
        return self.name or self.query


def apply_rules(rules: List[Rule], scope_query: str) -> int:
    """Apply each rule in ``rules``, in order, to messages matching
    ``(scope_query) and (rule.query)``.

    :param rules: the rules to apply, e.g. ``settings.filter_rules``
    :param scope_query: a notmuch query limiting which mail rules are
        allowed to touch, e.g. ``'tag:inbox and tag:unread'`` -- this
        keeps a rule change from silently re-tagging your entire
        archive the next time it runs
    :returns: the number of rules that matched at least one thread
    """
    matched = 0
    for rule in rules:
        if not rule.tag_add and not rule.tag_remove and not rule.move_to:
            logger.warning('Filter rule %r has no actions, skipping',
                           rule.describe())
            continue

        combined_query = f'({scope_query}) and ({rule.query})'
        count = notmuch.count(combined_query)
        if count == 0:
            continue

        # Collect files BEFORE tagging: if tag_remove drops a tag that
        # scope_query or rule.query itself depends on (e.g. removing
        # 'unread' when scope is 'tag:inbox and tag:unread'), a search
        # for combined_query run *after* tagging would match nothing,
        # and a move_to folder would silently receive no files even
        # though the tags were applied correctly.
        files = actions.collect_files(combined_query) if rule.move_to else []

        if rule.tag_add or rule.tag_remove:
            tag_expr = (' '.join(f'+{t}' for t in rule.tag_add)
                        + ' ' + ' '.join(f'-{t}' for t in rule.tag_remove))
            r = notmuch.tag(tag_expr.strip(), combined_query)
            if r.returncode != 0:
                logger.warning('Filter rule %r failed: %s',
                               rule.describe(), r.stderr.strip())
                continue

        if rule.move_to:
            if not files:
                logger.warning(
                    'Filter rule %r: matched %d thread(s) but no files '
                    'collected for move to %r — notmuch file paths may '
                    'be stale or unresolvable on disk.  Run `notmuch new` '
                    'and try again.',
                    rule.describe(), count, rule.move_to)
            else:
                try:
                    actions.move_specific_files(files, rule.move_to)
                except OSError as e:
                    logger.warning(
                        'Filter rule %r file move to %r failed: %s',
                        rule.describe(), rule.move_to, e)
                    continue

        matched += 1
        logger.info('Filter rule %r matched %d thread(s)%s',
                     rule.describe(), count,
                     f' → {len(files)} file(s) moved to {rule.move_to}'
                     if rule.move_to and files else '')

    return matched
