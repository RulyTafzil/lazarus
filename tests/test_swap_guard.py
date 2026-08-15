"""_SwapGuard — double-buffer swap arbitration for the thread message view.

The guard is pure Python (view-agnostic ids), so the race that rapid
H/i toggling triggers in ThreadPanel can be exercised without WebEngine.
"""
from lazarus.thread import _SwapGuard


def test_happy_path_single_request():
    g = _SwapGuard()
    gen = g.request(0)
    assert gen == 1
    assert g.load_finished(0) == 1
    assert g.swap_may_run(1)


def test_stale_cross_view_completion_is_dropped():
    """An older request finishing after a newer one must not swap."""
    g = _SwapGuard()
    g.request(0)   # gen 1 — load into view 0
    g.request(1)   # gen 2 — newer request into view 1
    assert g.load_finished(0) is None  # gen 1 completion is stale
    assert g.load_finished(1) == 2
    assert g.swap_may_run(2)


def test_same_view_double_completion_swaps_once():
    """A cancelled load's loadFinished(false) plus the real completion
    of the same request must not double-swap."""
    g = _SwapGuard()
    g.request(0)   # gen 1
    g.request(0)   # gen 2 — same view, supersedes gen 1's load
    assert g.load_finished(0) == 2   # first completion schedules the swap
    assert g.load_finished(0) is None  # second completion is dropped
    assert g.swap_may_run(2)


def test_superseded_swap_is_cancelled_before_run():
    """A scheduled swap must not execute if a newer request arrives
    before the deferred timer fires."""
    g = _SwapGuard()
    g.request(0)   # gen 1
    g.request(1)   # gen 2
    assert g.load_finished(1) == 2   # swap for gen 2 scheduled
    g.request(0)   # gen 3 — newer request supersedes it
    assert not g.swap_may_run(2)     # gen 2's swap is void
    assert g.load_finished(0) == 3
    assert g.swap_may_run(3)


def test_fresh_completion_after_swap_can_schedule_again():
    """After a swap executes, the next request starts a new cycle."""
    g = _SwapGuard()
    g.request(0)
    assert g.load_finished(0) == 1
    assert g.swap_may_run(1)
    g.request(1)
    assert g.load_finished(1) == 2
    assert g.swap_may_run(2)
