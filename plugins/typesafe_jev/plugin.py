#     Lazarus - A fork of Dodo, a graphical, hackable email client based on notmuch
#     Copyright (C) 2026 - Ruly Tafzil
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
"""TypeSafe System One (Jev) Post-Sync Plugin for NED.

Evaluates email metadata (strictly sender and subject) using TypeSafe System One
across two facets (doc_type and domain) and applies Notmuch category tags.

Invariants:
- STRICT PRIVACY: Transmits strictly 'from' and 'subject'. Never inspects or sends email bodies.
- NO HIDDEN DEFAULTS: Always reads taxonomy and rubrics from taxonomy.py located next to this plugin.
- ASYNCHRONOUS & NON-BLOCKING: Executes in the background after sync completes and emits live SSE
  invalidation events so tags refresh in Lazarus desktop UI without blocking user interaction.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import os
from pathlib import Path
import random
import subprocess
import time
from typing import Any, Optional
import urllib.request

from ned.plugins.base import PostSyncContext

logger = logging.getLogger(__name__)


_TYPESAFE_API_KEY_CACHE: Optional[str] = None
_TYPESAFE_API_KEY_CHECKED: bool = False


def get_typesafe_api_key() -> Optional[str]:
    """Retrieve TYPESAFE_API_KEY from environment or shell without leaking."""
    global _TYPESAFE_API_KEY_CACHE, _TYPESAFE_API_KEY_CHECKED
    if _TYPESAFE_API_KEY_CHECKED:
        return _TYPESAFE_API_KEY_CACHE

    key = os.environ.get("TYPESAFE_API_KEY")
    if key and key.strip():
        _TYPESAFE_API_KEY_CACHE = key.strip()
        _TYPESAFE_API_KEY_CHECKED = True
        return _TYPESAFE_API_KEY_CACHE

    for shell_cmd in (
        ["fish", "-c", "echo -n $TYPESAFE_API_KEY"],
        ["bash", "-c", "echo -n $TYPESAFE_API_KEY"],
    ):
        try:
            res = subprocess.run(
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.stdout and res.stdout.strip():
                _TYPESAFE_API_KEY_CACHE = res.stdout.strip()
                _TYPESAFE_API_KEY_CHECKED = True
                return _TYPESAFE_API_KEY_CACHE
        except Exception:
            pass

    _TYPESAFE_API_KEY_CHECKED = True
    _TYPESAFE_API_KEY_CACHE = None
    return None


class TypeSafeJevPlugin:
    """Post-sync plugin that classifies incoming emails via TypeSafe System One."""

    name = "typesafe_jev"

    def __init__(
        self,
        taxonomy_path: Optional[str] = None,
        scope_query: str = "not tag:ai-tagged and (tag:inbox or tag:unread)",
        concurrency: int = 3,
        batch_limit: int = 100,
        api_key: Optional[str] = None,
        timeout: float = 25.0,
    ) -> None:
        if taxonomy_path:
            self.taxonomy_path = str(Path(os.path.expanduser(taxonomy_path)).resolve())
        else:
            local_tax = Path(__file__).parent / "taxonomy.py"
            if local_tax.exists():
                self.taxonomy_path = str(local_tax.resolve())
            else:
                self.taxonomy_path = str(Path(os.path.expanduser("~/.config/ned/plugins/typesafe_jev/taxonomy.py")).resolve())

        self.scope_query = scope_query
        self.concurrency = max(1, concurrency)
        self.batch_limit = batch_limit
        self.api_key = api_key
        self.timeout = timeout

    def load_taxonomy(self) -> dict[str, Any]:
        """Load taxonomy rubrics and settings from the user's explicit taxonomy.py file."""
        path = Path(self.taxonomy_path)
        if not path.exists():
            raise FileNotFoundError(f"Taxonomy file not found at {path}")

        namespace: dict[str, Any] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                code = f.read()
            exec(code, namespace)  # pylint: disable=exec-used
        except Exception as e:
            raise RuntimeError(f"Failed to load TypeSafe taxonomy from {path}: {e}") from e

        doc_types = namespace.get("DOC_TYPE_CRITERIA")
        domains = namespace.get("DOMAIN_CRITERIA")
        if not isinstance(doc_types, dict) or not isinstance(domains, dict):
            raise ValueError(f"{path} must define DOC_TYPE_CRITERIA and DOMAIN_CRITERIA dictionaries.")

        raw_key = namespace.get("API_KEY") or namespace.get("TYPESAFE_API_KEY")
        api_key_val = str(raw_key).strip() if raw_key else None

        return {
            "api_key": api_key_val,
            "doc_types": doc_types,
            "domains": domains,
            "confidence_threshold": float(namespace.get("CONFIDENCE_THRESHOLD", 0.60)),
            "trash_threshold": float(namespace.get("TRASH_THRESHOLD", 0.70)),
            "tagged_marker": str(namespace.get("TAGGED_MARKER", "ai-tagged")),
        }

    def evaluate_email(
        self,
        api_key: str,
        sender: str,
        subject: str,
        doc_type_criteria: dict[str, str],
        domain_criteria: dict[str, str],
        max_retries: int = 4,
    ) -> dict[str, Any]:
        """Call TypeSafe System One API to classify email metadata with backoff retries."""
        payload = {
            "model": "jev-latest",
            "state": {
                "from": sender,
                "subject": subject,
            },
            "questions": {
                "doc_type": {
                    "type": "choice",
                    "instructions": "What is the transactional or functional document type of this email according to the rubric?",
                    "criteria": doc_type_criteria,
                },
                "domain": {
                    "type": "choice",
                    "instructions": "Select the specific life domain or subject matter this email belongs to according to the rubric.",
                    "criteria": domain_criteria,
                },
            },
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.typesafe.ai/v1/systemone",
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "ned-typesafe-plugin/1.0",
            },
        )

        last_error = ""
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                    res = json.loads(body)
                    answers = res.get("answers", {})

                    dt_ans = answers.get("doc_type", {})
                    dm_ans = answers.get("domain", {})

                    return {
                        "doc_type": dt_ans.get("choice"),
                        "doc_type_conf": float(dt_ans.get("confidence", 0.0)),
                        "domain": dm_ans.get("choice"),
                        "domain_conf": float(dm_ans.get("confidence", 0.0)),
                        "model": res.get("model", "jev-latest"),
                    }
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    jitter = random.uniform(0.5, 1.5)
                    time.sleep(1.5 * (attempt + 1) + jitter)

        return {
            "doc_type": None,
            "doc_type_conf": 0.0,
            "domain": None,
            "domain_conf": 0.0,
            "error": last_error,
        }

    def decide_tags(
        self,
        eval_result: dict[str, Any],
        current_tags: list[str],
        taxonomy: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        """Determine tags to add and remove based on judgment and taxonomy thresholds."""
        if eval_result.get("error"):
            return [], []

        dt = eval_result.get("doc_type")
        dt_conf = eval_result.get("doc_type_conf", 0.0)
        dm = eval_result.get("domain")
        dm_conf = eval_result.get("domain_conf", 0.0)

        conf_thresh = taxonomy["confidence_threshold"]
        trash_thresh = taxonomy["trash_threshold"]
        tagged_marker = taxonomy["tagged_marker"]

        tags_to_add: list[str] = []
        tags_to_remove: list[str] = []

        # 1. Trash check
        if dt == "ai-trash" and dt_conf >= trash_thresh:
            if "ai-trash" not in current_tags:
                tags_to_add.append("ai-trash")
        # 2. Personal check
        elif dt == "Personal" and dt_conf >= conf_thresh:
            pass  # Personal correspondence carries no archive tag
        else:
            # 3. Document type
            if dt and dt not in ("None", "ai-trash", "Personal") and dt_conf >= conf_thresh:
                if dt not in current_tags:
                    tags_to_add.append(dt)

            # 4. Domain
            if dm and dm != "None" and dm_conf >= conf_thresh:
                if dm not in current_tags:
                    tags_to_add.append(dm)

        # Always add tagged marker
        if tagged_marker not in current_tags:
            tags_to_add.append(tagged_marker)

        return tags_to_add, tags_to_remove

    def on_post_sync(self, context: PostSyncContext) -> None:
        """Asynchronous post-sync execution entrypoint."""
        try:
            taxonomy = self.load_taxonomy()
        except Exception as e:
            context.logger.error("Failed to load taxonomy for typesafe_jev plugin: %s", e)
            return

        api_key = self.api_key or taxonomy.get("api_key") or get_typesafe_api_key()
        if not api_key:
            context.logger.info(
                "TypeSafe API key not configured (set API_KEY in %s or TYPESAFE_API_KEY env); skipping post-sync classification",
                self.taxonomy_path,
            )
            return

        tagged_marker = taxonomy["tagged_marker"]
        effective_query = f"({self.scope_query}) and not tag:{tagged_marker}"

        try:
            threads = context.client.search_threads(effective_query, limit=self.batch_limit)
        except Exception as e:
            context.logger.warning("Search failed for query %r: %s", effective_query, e)
            return

        if not threads:
            context.logger.debug("No untagged threads found matching %r", effective_query)
            return

        context.logger.info("Classifying %d incoming thread(s) via TypeSafe System One...", len(threads))

        modified_count = 0

        def _process_thread(t: dict[str, Any]) -> tuple[str, list[str], list[str]]:
            tid = t.get("thread", t.get("thread_id", ""))
            sender = t.get("authors", "(unknown)")
            subject = t.get("subject", "(no subject)")
            current_tags = t.get("tags", [])

            eval_res = self.evaluate_email(
                api_key=api_key,
                sender=sender,
                subject=subject,
                doc_type_criteria=taxonomy["doc_types"],
                domain_criteria=taxonomy["domains"],
            )
            to_add, to_remove = self.decide_tags(eval_res, current_tags, taxonomy)
            return tid, to_add, to_remove

        batch_results: list[tuple[str, list[str], list[str]]] = []
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = {executor.submit(_process_thread, t): t for t in threads}
            for fut in as_completed(futures):
                try:
                    tid, to_add, to_remove = fut.result()
                    if to_add or to_remove:
                        batch_results.append((tid, to_add, to_remove))
                except Exception as e:
                    context.logger.warning("Failed processing thread: %s", e)

        if not batch_results:
            return

        batch_tagged = False
        if hasattr(context.client, "modify_tags_batch") and callable(getattr(context.client, "modify_tags_batch")):
            batch_lines: list[str] = []
            for tid, to_add, to_remove in batch_results:
                clean_id = tid.removeprefix("thread:")
                parts = [f"+{t.lstrip('+-')}" for t in to_add if t] + [f"-{t.lstrip('+-')}" for t in to_remove if t]
                if parts:
                    batch_lines.append(f"{' '.join(parts)} -- thread:{clean_id}")
            try:
                ok = context.client.modify_tags_batch(batch_lines)
                if ok:
                    modified_count = len(batch_results)
                    batch_tagged = True
            except Exception as e:
                context.logger.warning("Batch tagging failed, falling back: %s", e)

        if not batch_tagged:
            for tid, to_add, to_remove in batch_results:
                kwargs: dict[str, Any] = {"threads": [tid]}
                if to_add:
                    kwargs["add"] = to_add
                    kwargs["add_tags"] = to_add
                if to_remove:
                    kwargs["remove"] = to_remove
                    kwargs["remove_tags"] = to_remove

                if hasattr(context.client, "modify_tags"):
                    ok = context.client.modify_tags(**kwargs)
                elif hasattr(context.client, "tag_thread"):
                    ok = context.client.tag_thread(tid, to_add, to_remove)
                else:
                    ok = False

                if ok:
                    modified_count += 1

        if modified_count > 0:
            context.logger.info("Successfully classified and tagged %d thread(s)", modified_count)
            context.invalidate("threads", reason="plugin:typesafe_jev")
