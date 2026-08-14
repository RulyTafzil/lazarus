"""Help window — display grouping and rendering sanity."""
from PyQt6.QtWidgets import QTextBrowser

from lazarus.helpwindow import _apply_groups, HelpWindow


def test_apply_groups_merges_shared_keys():
    mp = {
        'j': ('next thread', None),
        'k': ('previous thread', None),
        '<up>': ('next thread', None),
    }
    out = _apply_groups(mp)
    assert 'j / <up>' in out
    assert out['j / <up>'][0] == 'next thread'
    assert 'j' not in out and '<up>' not in out
    assert out['k'] == ('previous thread', None)


def test_apply_groups_preserves_position():
    mp = {
        'a': ('x', None),
        'j': ('next thread', None),
        '<up>': ('next thread', None),
        'b': ('y', None),
    }
    out = _apply_groups(mp)
    # the merged row appears where 'j' was, before 'b'
    assert list(out.keys()) == ['a', 'j / <up>', 'b']


def test_apply_groups_no_merge_when_member_missing():
    mp = {'j': ('next thread', None), 'k': ('previous thread', None)}
    out = _apply_groups(mp)
    assert 'j' in out and 'k' in out
    assert 'j / <up>' not in out


def test_apply_groups_noop_for_unrelated_keys():
    mp = {'z': ('zoom', None), 'q': ('quit', None)}
    assert _apply_groups(mp) == mp


def test_help_renders_without_navigation_and_with_groups(qapp):
    win = HelpWindow()
    html = ''.join(b.toHtml() for b in win.findChildren(QTextBrowser))
    # grouped rows from the display layer (QTextBrowser serialises the
    # key as 'j\xa0/\xa0&lt;up&gt;')
    assert 'j\xa0/\xa0&lt;up&gt;' in html
    assert 'k\xa0/\xa0&lt;down&gt;' in html
    # navigation section is gone
    assert 'Navigation' not in html
    # the message-level actions survive in the Global columns
    assert 'toggle unread (message)' in html
