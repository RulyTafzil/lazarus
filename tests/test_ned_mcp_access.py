"""Unit tests for AccountPolicy and access control in ned-mcp."""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from ned_mcp.access import AccessDeniedError, AccountPolicy


def test_account_policy_init_and_scoping():
    """Test policy creation and query scoping."""
    policy = AccountPolicy("work", allowed_tags=["todo", "followup"])
    assert policy.primary_account == "work"
    assert policy.accounts == ("work",)
    assert policy.allowed_tags == frozenset({"todo", "followup"})
    assert policy.allow_send is False
    assert policy.full_tags is False

    # Scoped queries
    assert policy.scoped_query("tag:inbox") == "(path:work/**) and (tag:inbox)"
    assert policy.scoped_query("") == "(path:work/**)"
    assert policy.scoped_query("*") == "(path:work/**)"


def test_account_policy_multiple_accounts():
    """Test multi-account policy scoping."""
    policy = AccountPolicy(["work", "client_a"], full_tags=True, allow_send=True)
    assert policy.accounts == ("work", "client_a")
    assert policy.scoped_query("tag:unread") == "(path:work/** or path:client_a/**) and (tag:unread)"
    assert policy.full_tags is True
    assert policy.allow_send is True


def test_account_policy_empty_accounts_raises():
    """Test that creating policy without accounts raises ValueError."""
    with pytest.raises(ValueError, match="At least one account"):
        AccountPolicy([])


def test_validate_tag_mutation_whitelist():
    """Test tag mutation against a tag whitelist."""
    policy = AccountPolicy("work", allowed_tags=["todo", "archive"])

    # Permitted tags
    add, rem = policy.validate_tag_mutation(add=["todo"], remove=["archive"])
    assert add == ["todo"]
    assert rem == ["archive"]

    # Disallowed tags raise AccessDeniedError
    with pytest.raises(AccessDeniedError) as exc:
        policy.validate_tag_mutation(add=["unread"], remove=["inbox"])
    assert "adding unauthorized tags: ['unread']" in str(exc.value)
    assert "removing unauthorized tags: ['inbox']" in str(exc.value)


def test_validate_tag_mutation_full_tags():
    """Test tag mutation when full_tags is enabled."""
    policy = AccountPolicy("work", full_tags=True)
    add, rem = policy.validate_tag_mutation(add=["custom1", "custom2"], remove=["inbox"])
    assert add == ["custom1", "custom2"]
    assert rem == ["inbox"]


def test_validate_send():
    """Test send authorization."""
    policy_no_send = AccountPolicy("work", allow_send=False)
    with pytest.raises(AccessDeniedError, match="Sending email is disabled"):
        policy_no_send.validate_send("work")

    policy_send = AccountPolicy(["work", "personal"], allow_send=True)
    assert policy_send.validate_send() == "work"
    assert policy_send.validate_send("personal") == "personal"

    with pytest.raises(AccessDeniedError, match="Cannot send from account 'other'"):
        policy_send.validate_send("other")


def test_validate_thread_in_account():
    """Test checking if a thread belongs to the allowed account."""
    policy = AccountPolicy("work")
    mock_client = MagicMock()

    # Match found
    mock_client.count.return_value = 2
    policy.validate_thread_in_account(mock_client, "thread:t123")
    mock_client.count.assert_called_with("thread:t123 and (path:work/**)", output="messages")

    # Match not found
    mock_client.count.return_value = 0
    with pytest.raises(AccessDeniedError, match="does not belong to allowed account"):
        policy.validate_thread_in_account(mock_client, "thread:t999")


def test_validate_message_in_account():
    """Test checking if a message belongs to the allowed account."""
    policy = AccountPolicy("work")
    mock_client = MagicMock()

    # Match found
    mock_client.count.return_value = 1
    policy.validate_message_in_account(mock_client, "<msg-100@test>")
    mock_client.count.assert_called_with("id:msg-100@test and (path:work/**)", output="messages")

    # Match not found
    mock_client.count.return_value = 0
    with pytest.raises(AccessDeniedError, match="does not belong to allowed account"):
        policy.validate_message_in_account(mock_client, "id:msg-missing")


def test_assert_no_expunge():
    """Verify that expunge is permanently blocked."""
    policy = AccountPolicy("work", full_tags=True, allow_send=True)
    with pytest.raises(AccessDeniedError, match="Expunge operations are permanently disabled"):
        policy.assert_no_expunge()


def test_parse_spec():
    """Test parsing spec strings."""
    p1 = AccountPolicy.parse_spec("work")
    assert p1.primary_account == "work"
    assert p1.allow_send is False
    assert p1.full_tags is False

    p2 = AccountPolicy.parse_spec("work:full_tags,send")
    assert p2.primary_account == "work"
    assert p2.allow_send is True
    assert p2.full_tags is True

    p3 = AccountPolicy.parse_spec("personal:tags=todo+followup,send=false")
    assert p3.primary_account == "personal"
    assert p3.allowed_tags == frozenset({"todo", "followup"})
    assert p3.allow_send is False


def test_from_dict():
    """Test creating policy from dictionary."""
    data = {
        "account": "work",
        "allowed_tags": ["todo", "archive"],
        "allow_send": True,
    }
    policy = AccountPolicy.from_dict(data)
    assert policy.primary_account == "work"
    assert policy.allowed_tags == frozenset({"todo", "archive"})
    assert policy.allow_send is True


def test_deny_by_default_policy():
    """Test that default policy is strictly read-only."""
    policy = AccountPolicy("work")
    assert policy.primary_account == "work"
    assert policy.can_mutate_tags is False
    assert policy.allow_send is False
    assert policy.allow_archive is False
    assert policy.allow_trash is False
    assert policy.full_tags is False
    assert policy.allowed_tags == frozenset()

    with pytest.raises(AccessDeniedError, match="Tag modification is disabled"):
        policy.validate_tag_mutation(add=["todo"])

    with pytest.raises(AccessDeniedError, match="Archive operations are disabled"):
        policy.validate_archive()

    with pytest.raises(AccessDeniedError, match="Trash operations are disabled"):
        policy.validate_trash()

    with pytest.raises(AccessDeniedError, match="Sending email is disabled"):
        policy.validate_send()


def test_comma_separated_accounts():
    """Test that comma-separated account strings are parsed correctly."""
    policy = AccountPolicy("work,personal", allow_trash=True)
    assert policy.accounts == ("work", "personal")
    assert policy.primary_account == "work"
    assert policy.allow_trash is True
    assert policy.scoped_query("tag:inbox") == "(path:work/** or path:personal/**) and (tag:inbox)"


def test_tags_wildcard_string():
    """Test passing '*' as allowed_tags string."""
    policy = AccountPolicy("work", allowed_tags="*")
    assert policy.full_tags is True
    assert policy.can_mutate_tags is True
    add, rem = policy.validate_tag_mutation(add=["custom"], remove=["inbox"])
    assert add == ["custom"]
    assert rem == ["inbox"]


def test_parse_spec_opt_in():
    """Test parse_spec with opt-in flags."""
    p = AccountPolicy.parse_spec("work:tags=*,trash,send")
    assert p.full_tags is True
    assert p.allow_trash is True
    assert p.allow_send is True
    assert p.allow_archive is False

    p2 = AccountPolicy.parse_spec("work,personal:trash,archive")
    assert p2.accounts == ("work", "personal")
    assert p2.allow_trash is True
    assert p2.allow_archive is True
    assert p2.can_mutate_tags is False


def test_account_policy_aliases_expansion():
    """Test that account aliases expand search paths and resolve SMTP accounts."""
    policy = AccountPolicy("clanker", allow_send=True)
    policy.register_aliases(
        {"clanker": ["clanker", "clanker@example.com"]},
        smtp_account_map={"clanker@example.com": "clanker", "clanker": "clanker"},
    )

    # Scoped paths should cover both label and email Maildir directory
    assert set(policy.account_paths) == {"clanker", "clanker@example.com"}
    assert "path:clanker/**" in policy.account_path_query
    assert "path:clanker@example.com/**" in policy.account_path_query

    # Query scoping
    query = policy.scoped_query("tag:inbox")
    assert "tag:inbox" in query
    assert "path:clanker/**" in query
    assert "path:clanker@example.com/**" in query

    # Send validation with short label, email alias, and unauthorized target
    assert policy.validate_send() == "clanker"
    assert policy.validate_send("clanker") == "clanker"
    assert policy.validate_send("clanker@example.com") == "clanker"

    with pytest.raises(AccessDeniedError, match="Cannot send from account 'other'"):
        policy.validate_send("other")


def test_account_policy_init_with_email_address():
    """Test policy initialized directly with email address mapping to short SMTP name."""
    policy = AccountPolicy("bot@example.com", allow_send=True)
    policy.register_aliases(
        {"bot": ["bot", "bot@example.com"]},
        smtp_account_map={"bot@example.com": "bot", "bot": "bot"},
    )

    assert set(policy.account_paths) == {"bot", "bot@example.com"}
    assert policy.validate_send() == "bot"
    assert policy.validate_send("bot") == "bot"
    assert policy.validate_send("bot@example.com") == "bot"


def test_account_policy_from_dict_with_aliases():
    """Test AccountPolicy.from_dict parses aliases correctly."""
    data = {
        "account": "work",
        "aliases": {"work": ["work", "work@corp.com"]},
        "allow_send": True,
    }
    policy = AccountPolicy.from_dict(data)
    assert set(policy.account_paths) == {"work", "work@corp.com"}
    assert policy.validate_send("work@corp.com") == "work"


