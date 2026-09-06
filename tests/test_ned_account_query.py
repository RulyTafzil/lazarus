"""Unit tests for build_account_query helper in NED service and client."""

from __future__ import annotations

import pytest

from ned.client import build_account_query as client_build_account_query
from ned.service import build_account_query as service_build_account_query


@pytest.mark.parametrize("builder", [client_build_account_query, service_build_account_query])
def test_build_account_query_single_account(builder):
    """Test scoping query to a single account."""
    res = builder("work", "tag:inbox and tag:unread")
    assert res == "(path:work/**) and (tag:inbox and tag:unread)"

    # Empty query or wildcard
    assert builder("work", "") == "(path:work/**)"
    assert builder("work", "*") == "(path:work/**)"


@pytest.mark.parametrize("builder", [client_build_account_query, service_build_account_query])
def test_build_account_query_multiple_accounts(builder):
    """Test scoping query to multiple accounts."""
    res = builder(["work", "personal"], "tag:flagged")
    assert res == "(path:work/** or path:personal/**) and (tag:flagged)"

    # Empty query
    assert builder(["work", "personal"], "") == "(path:work/** or path:personal/**)"


@pytest.mark.parametrize("builder", [client_build_account_query, service_build_account_query])
def test_build_account_query_clean_slashes(builder):
    """Test stripping leading and trailing slashes from account names."""
    res = builder(["/work/", "personal/"], "tag:inbox")
    assert res == "(path:work/** or path:personal/**) and (tag:inbox)"


@pytest.mark.parametrize("builder", [client_build_account_query, service_build_account_query])
def test_build_account_query_empty_accounts(builder):
    """Test empty account list returns original query."""
    assert builder([], "tag:inbox") == "tag:inbox"
    assert builder("", "tag:inbox") == "tag:inbox"
