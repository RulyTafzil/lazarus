"""Account scoping and permission enforcement for ned-mcp.

Implements account-level sandboxing, tag whitelists, send authorization,
and guarantees that expunge is never permitted.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence


class AccessDeniedError(Exception):
    """Raised when an operation violates account scoping or permissions."""


class AccountPolicy:
    """Security policy restricting an agent to specific accounts and actions.

    Follows a deny-by-default model: baseline access is read-only.
    All mutations (tagging, trashing, archiving, sending) require express opt-in.
    """

    def __init__(
        self,
        accounts: str | Sequence[str],
        allowed_tags: Optional[Sequence[str] | set[str] | str] = None,
        full_tags: bool = False,
        allow_trash: bool = False,
        allow_archive: bool = False,
        allow_archive_to_local: bool = False,
        allow_send: bool = False,
    ) -> None:
        if isinstance(accounts, str):
            items = accounts.split(",")
        else:
            items = []
            for a in accounts:
                items.extend(a.split(","))

        clean_accounts = [a.strip() for a in items if a.strip()]
        if not clean_accounts:
            raise ValueError("At least one account must be specified in AccountPolicy.")

        self.accounts: tuple[str, ...] = tuple(clean_accounts)
        self.primary_account: str = clean_accounts[0]
        self.allow_trash: bool = allow_trash
        self.allow_archive: bool = allow_archive or allow_archive_to_local
        self.allow_archive_to_local: bool = allow_archive_to_local
        self.allow_send: bool = allow_send

        # Resolve tag mutation permissions
        if isinstance(allowed_tags, str):
            clean_tags_str = allowed_tags.strip()
            if clean_tags_str == "*":
                self.full_tags: bool = True
                self.allowed_tags: Optional[frozenset[str]] = None
            elif clean_tags_str:
                self.full_tags = False
                self.allowed_tags = frozenset(
                    t.strip().lstrip("+-") for t in clean_tags_str.split(",") if t.strip()
                )
            else:
                self.full_tags = False
                self.allowed_tags = frozenset()
        elif allowed_tags is not None:
            self.full_tags = full_tags
            if full_tags:
                self.allowed_tags = None
            else:
                self.allowed_tags = frozenset(
                    t.strip().lstrip("+-") for t in allowed_tags if t.strip()
                )
        else:
            self.full_tags = full_tags
            self.allowed_tags = None if full_tags else frozenset()

    @property
    def can_mutate_tags(self) -> bool:
        """Return True if any tag modification is permitted."""
        return self.full_tags or bool(self.allowed_tags)

    @property
    def account_path_query(self) -> str:
        """Return Notmuch query expression matching files in allowed accounts."""
        if len(self.accounts) == 1:
            return f"path:{self.accounts[0]}/**"
        return " or ".join(f"path:{acct}/**" for acct in self.accounts)

    def scoped_query(self, query: str = "") -> str:
        """Inject account path constraints into a Notmuch query.

        Guarantees that results are strictly confined to the allowed Maildir roots.
        """
        clean = (query or "").strip()
        path_expr = f"({self.account_path_query})"
        if not clean or clean == "*":
            return path_expr
        return f"{path_expr} and ({clean})"

    def validate_tag_mutation(
        self,
        add: Sequence[str] = (),
        remove: Sequence[str] = (),
    ) -> tuple[list[str], list[str]]:
        """Validate that all tags to be added or removed are allowed.

        Raises AccessDeniedError if tag modification is disabled or any tag is unauthorized.
        """
        clean_add = [t.strip().lstrip("+-") for t in add if t.strip()]
        clean_remove = [t.strip().lstrip("+-") for t in remove if t.strip()]

        if not self.can_mutate_tags:
            raise AccessDeniedError(
                f"Tag modification is disabled for account '{self.primary_account}'. "
                "No tags have been permitted."
            )

        if self.full_tags:
            return clean_add, clean_remove

        allowed = self.allowed_tags or frozenset()
        disallowed_add = [t for t in clean_add if t not in allowed]
        disallowed_remove = [t for t in clean_remove if t not in allowed]

        if disallowed_add or disallowed_remove:
            violations: list[str] = []
            if disallowed_add:
                violations.append(f"adding unauthorized tags: {disallowed_add}")
            if disallowed_remove:
                violations.append(f"removing unauthorized tags: {disallowed_remove}")
            allowed_desc = ", ".join(sorted(allowed)) if allowed else "none"
            raise AccessDeniedError(
                f"Tag modification denied ({'; '.join(violations)}). "
                f"Allowed tags for account '{self.primary_account}': [{allowed_desc}]."
            )

        return clean_add, clean_remove

    def validate_send(self, account: Optional[str] = None) -> str:
        """Validate outbound send permissions and resolve sending account.

        Raises AccessDeniedError if send is disabled or target account is forbidden.
        """
        if not self.allow_send:
            raise AccessDeniedError(
                f"Sending email is disabled for account '{self.primary_account}'."
            )

        target = (account or "").strip()
        if not target:
            target = self.primary_account

        if target not in self.accounts:
            raise AccessDeniedError(
                f"Cannot send from account '{target}'. Allowed accounts: {list(self.accounts)}."
            )

        return target

    def validate_archive(self, local: bool = False) -> None:
        """Validate that archive operations are permitted.

        Raises AccessDeniedError if archive is disabled for this account.
        """
        if local:
            if not self.allow_archive_to_local:
                raise AccessDeniedError(
                    f"Archiving to local folder is disabled for account '{self.primary_account}'."
                )
        else:
            if not self.allow_archive:
                raise AccessDeniedError(
                    f"Archive operations are disabled for account '{self.primary_account}'."
                )

    def validate_trash(self) -> None:
        """Validate that trash operations are permitted.

        Raises AccessDeniedError if trash is disabled for this account.
        """
        if not self.allow_trash:
            raise AccessDeniedError(
                f"Trash operations are disabled for account '{self.primary_account}'."
            )

    def validate_thread_in_account(self, client: Any, thread_id: str) -> None:
        """Verify that a thread contains messages belonging to the allowed accounts."""
        clean_id = thread_id.strip().removeprefix("thread:")
        if not clean_id:
            raise AccessDeniedError("Empty thread identifier.")

        count_query = f"thread:{clean_id} and ({self.account_path_query})"
        matches = client.count(count_query, output="messages")
        if matches <= 0:
            raise AccessDeniedError(
                f"Thread '{thread_id}' does not belong to allowed account(s): {list(self.accounts)}."
            )

    def validate_message_in_account(self, client: Any, message_id: str) -> None:
        """Verify that a message belongs to the allowed accounts."""
        clean_id = message_id.strip().removeprefix("id:")
        if clean_id.startswith("<") and clean_id.endswith(">"):
            clean_id = clean_id[1:-1]
        if not clean_id:
            raise AccessDeniedError("Empty message identifier.")

        count_query = f"id:{clean_id} and ({self.account_path_query})"
        matches = client.count(count_query, output="messages")
        if matches <= 0:
            raise AccessDeniedError(
                f"Message '{message_id}' does not belong to allowed account(s): {list(self.accounts)}."
            )

    def assert_no_expunge(self) -> None:
        """Expunge operations are strictly forbidden in ned-mcp."""
        raise AccessDeniedError("Expunge operations are permanently disabled in ned-mcp.")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountPolicy:
        """Create an AccountPolicy from a dictionary configuration."""
        account = data.get("account") or data.get("accounts")
        if not account:
            raise ValueError("Configuration must contain 'account' or 'accounts'.")

        tags = data.get("tags") or data.get("allowed_tags")
        full_tags = bool(data.get("full_tags", False)) or (tags == "*")
        if tags == "*":
            tags = None

        return cls(
            accounts=account,
            allowed_tags=tags,
            full_tags=full_tags,
            allow_trash=bool(data.get("allow_trash", False)),
            allow_archive=bool(data.get("allow_archive", False)),
            allow_archive_to_local=bool(data.get("allow_archive_to_local", False)),
            allow_send=bool(data.get("allow_send", False)),
        )

    @classmethod
    def parse_spec(cls, spec_str: str) -> AccountPolicy:
        """Parse account policy specification string.

        Formats supported:
            'work'
            'work:tags=*,trash,send'
            'work:tags=todo+urgent,trash'
            'work,personal:send'
        """
        parts = spec_str.strip().split(":", 1)
        accounts_part = parts[0].strip()
        if not accounts_part:
            raise ValueError(f"Invalid account policy specification: {spec_str!r}")

        if len(parts) == 1:
            return cls(accounts=accounts_part)

        opts_str = parts[1].strip()
        full_tags = False
        allow_send = False
        allow_archive = False
        allow_archive_to_local = False
        allow_trash = False
        allowed_tags: list[str] = []

        for item in opts_str.split(","):
            item = item.strip()
            if not item:
                continue
            if item in ("full_tags", "tags=*"):
                full_tags = True
            elif item in ("trash", "allow_trash"):
                allow_trash = True
            elif item in ("archive", "allow_archive"):
                allow_archive = True
            elif item in ("archive_to_local", "allow_archive_to_local"):
                allow_archive_to_local = True
            elif item in ("send", "send=true", "allow_send"):
                allow_send = True
            elif item.startswith("tags="):
                raw = item[len("tags=") :].strip()
                if raw == "*":
                    full_tags = True
                else:
                    raw_tags = raw.split("+")
                    allowed_tags.extend(t.strip() for t in raw_tags if t.strip())

        return cls(
            accounts=accounts_part,
            allowed_tags=allowed_tags if allowed_tags else None,
            full_tags=full_tags,
            allow_trash=allow_trash,
            allow_archive=allow_archive,
            allow_archive_to_local=allow_archive_to_local,
            allow_send=allow_send,
        )
