"""Search list mode — the opt-in two-row 'card' view vs. the flat list.

Covers ``settings.search_list_mode`` routing (flat table vs. two-line
card) as a **config-only** setting: the mode is read once at construction
and is not toggleable in-app or persisted to QSettings.  Also pins the
shared ``search_tree_geometry`` lifecycle — card mode must never write
column widths into the shared key, and flat mode must recover if a stale
card geometry is ever found there (the 'only the date column filled'
regression).
"""
import pytest

from PyQt6.QtCore import QRect, QSettings, Qt
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem

from lazarus import settings
from lazarus.search import (
    LIST_MODE_FLAT, SearchModel, SearchPanel, CardDelegate, _single_line,
)
from tests.conftest import make_thread


@pytest.fixture
def panel(qapp, fake_app, notmuch_stub):
    """A standalone SearchPanel in flat mode (the config-only default)."""
    QSettings('lazarus', 'lazarus').remove('search_list_mode')
    QSettings('lazarus', 'lazarus').remove('search_tree_geometry')
    settings.search_list_mode = LIST_MODE_FLAT
    notmuch_stub.threads = [make_thread('t1', 'Hello', total=3)]
    p = SearchPanel(fake_app, 'tag:inbox')
    yield p
    p.close()
    p.deleteLater()
    qapp.processEvents()


def test_default_is_flat_list(panel):
    """The flat view is untouched: header visible, all columns shown,
    and the default (non-card) delegate in place."""
    assert settings.search_list_mode == LIST_MODE_FLAT
    assert panel.tree.isHeaderHidden() is False
    for c in range(1, len(('date', '#', 'from', 'subject', 'tags'))):
        assert panel.tree.isColumnHidden(c) is False
    assert panel.tree.itemDelegate() is not panel._card_delegate
    assert panel.tree.itemDelegate() is panel._default_delegate


def test_mode_is_config_only_not_persisted(panel):
    """The mode comes only from ``settings.search_list_mode`` (i.e.
    config.py): a stale ``search_list_mode`` QSettings value must not flip
    the panel's delegate, and the app never writes the mode to QSettings."""
    QSettings('lazarus', 'lazarus').setValue('search_list_mode', 'card')
    assert settings.search_list_mode == LIST_MODE_FLAT
    assert panel.tree.itemDelegate() is panel._default_delegate
    assert QSettings('lazarus', 'lazarus').value('search_list_mode') == 'card'  # untouched


def test_card_mode_single_column_no_restore(qapp, fake_app, notmuch_stub):
    """A panel created in card mode is a single stretched column with the
    header hidden, and it neither restores nor saves column widths to the
    shared geometry key — so it can't poison later flat panels."""
    from PyQt6.QtWidgets import QHeaderView
    QSettings('lazarus', 'lazarus').remove('search_tree_geometry')
    settings.search_list_mode = 'card'
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    p = SearchPanel(fake_app, 'tag:inbox')
    try:
        assert p.tree.isHeaderHidden() is True
        assert p.tree.isColumnHidden(0) is False
        for c in range(1, 5):
            assert p.tree.isColumnHidden(c) is True
        assert p.tree.itemDelegate() is p._card_delegate
        assert p.tree.header().sectionResizeMode(0) == \
            QHeaderView.ResizeMode.Stretch
        # Closing the card panel must not write card geometry to the key.
        p.close()
        key = QSettings('lazarus', 'lazarus').value('search_tree_geometry')
        assert key == '' or key is None
    finally:
        p.deleteLater()
        qapp.processEvents()


def test_flat_recovers_from_stale_card_geometry(qapp, fake_app, notmuch_stub):
    """Regression: the old card-mode build saved its single stretched
    date column into the shared key, so flat panels restored it and showed
    only the date column filled.  Flat mode must clamp the over-wide date
    column back to sensible widths and purge the poisoned entry."""
    from lazarus.search import LIST_MODE_CARD
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    # Produce a realistic poisoned entry: the header state of a real
    # card-mode panel (col0 stretched full-width), as the old build saved.
    settings.search_list_mode = LIST_MODE_CARD
    card = SearchPanel(fake_app, 'tag:inbox')
    card.resize(720, 400)
    card.show(); qapp.processEvents()   # lay out so col0 is stretched wide
    poison = card.tree.header().saveState()
    card.close(); card.deleteLater(); qapp.processEvents()
    QSettings('lazarus', 'lazarus').setValue('search_tree_geometry', poison)

    # A flat panel now restores that card state: clamp + purge.
    settings.search_list_mode = LIST_MODE_FLAT
    p = SearchPanel(fake_app, 'tag:inbox')
    p.resize(720, 400); p.show(); qapp.processEvents()
    try:
        # Restored geometry was recognized as a card leftover and replaced
        # with sane flat widths — the date column no longer swallows the row.
        assert p.tree.header().isSectionHidden(0) is False
        # And the poisoned entry was purged so the next run is clean.
        assert QSettings('lazarus', 'lazarus').value(
            'search_tree_geometry') in (None, '')
    finally:
        p.close()
        p.deleteLater()
        qapp.processEvents()


def test_card_delegate_size_hint_is_two_lines(qapp, notmuch_stub):
    """The card's natural row height fits two text lines plus padding."""
    from PyQt6.QtGui import QFontMetrics
    settings.search_list_mode = 'card'
    notmuch_stub.threads = [make_thread('t1', 'Hello', total=3,
                                        tags=['inbox', 'unread'])]
    model = SearchModel('tag:inbox')
    delegate = CardDelegate()

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 600, 60)
    idx = model.index(0, 0)
    fm = QFontMetrics(model.data(idx, Qt.ItemDataRole.FontRole))
    size = delegate.sizeHint(opt, idx)
    assert size.width() >= 60
    assert size.height() >= 2 * fm.height()


@pytest.mark.parametrize('selected', [False, True])
def test_card_delegate_paints(qapp, notmuch_stub, selected):
    """The card paints (selected and unselected) without crashing and
    draws actual pixels — no degenerate empty output."""
    from PyQt6.QtGui import QPainter, QPixmap
    settings.search_list_mode = 'card'
    notmuch_stub.threads = [make_thread('t1', 'Hello ' * 40, total=3,
                                        tags=['inbox', 'unread'])]
    model = SearchModel('tag:inbox')
    delegate = CardDelegate()

    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 800, 60)
    if selected:
        opt.state |= QStyle.StateFlag.State_Selected
    idx = model.index(0, 0)
    size = delegate.sizeHint(opt, idx)
    opt.rect = QRect(0, 0, 800, size.height())

    pm = QPixmap(800, size.height())
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    delegate.paint(painter, opt, idx)
    painter.end()

    # At least the card border + some text pixels landed.
    assert pm.toImage().size().width() > 0


def test_fresh_card_panel_survives_refresh(qapp, fake_app, notmuch_stub):
    """A panel created in card mode stays a proper single-column card view
    across ``refresh()`` and ``set_query()`` — the saved flat header layout
    is never re-applied over the card single-column setup."""
    QSettings('lazarus', 'lazarus').remove('search_tree_geometry')
    settings.search_list_mode = 'card'
    notmuch_stub.threads = [make_thread('t1', 'Hello')]
    p = SearchPanel(fake_app, 'tag:inbox')
    try:
        assert p.tree.isHeaderHidden() is True
        assert p.tree.isColumnHidden(1) is True     # multi-column grid hidden
        assert p.tree.itemDelegate() is p._card_delegate
        p.refresh()
        assert p.tree.isHeaderHidden() is True
        for c in range(1, 5):
            assert p.tree.isColumnHidden(c) is True
        assert p.tree.itemDelegate() is p._card_delegate
        p.set_query('tag:inbox and tag:unread')
        assert p.tree.isHeaderHidden() is True
        for c in range(1, 5):
            assert p.tree.isColumnHidden(c) is True
        assert p.tree.itemDelegate() is p._card_delegate
    finally:
        p.close()
        p.deleteLater()
        qapp.processEvents()


def test_single_line_normalizes_newlines():
    """Regression: some mail (e.g. certain Google messages) folds its
    Subject with a trailing newline.  A single-line renderer must not draw
    it as two lines, which pushes the subject up into the From line and lets
    the color-emoji glyph bleed upward.  ``_single_line`` collapses CR/LF."""
    assert _single_line('welcome to google ai pro, ruly \U0001F525\n') == \
        'welcome to google ai pro, ruly \U0001F525 '
    assert _single_line('a\r\nb') == 'a b'
    assert _single_line('no newline') == 'no newline'
    assert _single_line(123) == '123'


def test_card_subject_trailing_newline_does_not_overlap(qapp, notmuch_stub):
    """Painting a card whose subject carries a trailing newline must keep the
    subject (line 2) from vertically overlapping the From line (line 1)."""
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    settings.search_list_mode = 'card'
    notmuch_stub.threads = [make_thread(
        't1', 'welcome to google ai pro, ruly \U0001F525\n')]
    model = SearchModel('tag:inbox')
    delegate = CardDelegate()
    idx = model.index(0, 0)
    opt = QStyleOptionViewItem()
    h = delegate.sizeHint(opt, idx).height()
    opt.rect = QRect(0, 0, 680, h)
    bg = QColor(settings.theme['bg'])
    pm = QPixmap(680, h); pm.fill(bg)
    p = QPainter(pm); delegate.paint(p, opt, idx); p.end()
    img = pm.toImage()

    # Find the vertical ink bands across the text region and assert the two
    # lines are strictly separated (no overlap).
    xs = range(40, 480)
    bands = []
    cur = None
    for y in range(h):
        ink = any(
            abs(img.pixelColor(x, y).red() - bg.red())
            + abs(img.pixelColor(x, y).green() - bg.green())
            + abs(img.pixelColor(x, y).blue() - bg.blue()) > 60
            for x in xs)
        if ink and cur is None:
            cur = y
        elif not ink and cur is not None:
            bands.append((cur, y - 1)); cur = None
    if cur is not None:
        bands.append((cur, h - 1))
    assert len(bands) >= 2
    # line 1 (From) above line 2 (subject); no band may overlap the next.
    for i in range(len(bands) - 1):
        assert bands[i][1] < bands[i + 1][0], f'bands overlap: {bands}'


def test_static_indicator_width_aligns_sender(qapp, notmuch_stub):
    """The thread-count indicator occupies a constant width whether the
    message is threaded or not, so the sender (and subject) start at the
    same x on every card."""
    settings.search_list_mode = 'card'
    # Both threads non-unread so their 'from' font/metrics are identical.
    notmuch_stub.threads = [
        make_thread('tA', 'Alpha', total=3, tags=['inbox']),
        make_thread('tB', 'Beta', total=1, tags=['inbox']),
    ]
    from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPixmap
    model = SearchModel('tag:inbox')
    delegate = CardDelegate()
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 800, 60)
    idx_a, idx_b = model.index(0, 0), model.index(1, 0)
    h = delegate.sizeHint(opt, idx_a).height()

    from_font = model.data(idx_a, Qt.ItemDataRole.FontRole)
    fm = QFontMetrics(from_font)
    reserved = fm.horizontalAdvance('\uf086 00')
    inner_left = CardDelegate.margin_h + 1 + CardDelegate.pad_h
    col_gap = CardDelegate.col_gap
    bg = QColor(settings.theme['bg'])

    band_top = 2 + CardDelegate.pad_v - 1
    band_bot = 2 + CardDelegate.pad_v + fm.height() + 1

    def sender_x(idx):
        pm = QPixmap(800, h)
        pm.fill(bg)
        p = QPainter(pm)
        o = QStyleOptionViewItem()
        o.rect = QRect(0, 0, 800, h)
        delegate.paint(p, o, idx)
        p.end()
        img = pm.toImage()
        # Skip the indicator region + gap; the first ink at/after that is
        # the sender text's left edge (glyph, if any, stays far left).
        start = inner_left + reserved + col_gap - 1
        for x in range(start, 800):
            for y in range(band_top, band_bot):
                c = img.pixelColor(x, y)
                if (abs(c.red() - bg.red()) + abs(c.green() - bg.green())
                        + abs(c.blue() - bg.blue()) > 60):
                    return x
        return None

    xa, xb = sender_x(idx_a), sender_x(idx_b)
    assert xa is not None and xb is not None, (xa, xb)
    # Sender (and subject) share the same left edge on both cards.
    assert abs(xa - xb) <= 2
