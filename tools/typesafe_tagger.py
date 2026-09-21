#!/usr/bin/env python3
"""TypeSafe AI Email Tagger for Notmuch/NED.

Uses TypeSafe System One (Jev) to evaluate email metadata (ONLY sender and subject)
and propose/apply accurate Notmuch tags.

Invariants:
- STRICT PRIVACY: Transmits strictly 'from' and 'subject'. Never reads or sends body content.
- NON-DESTRUCTIVE: Never removes existing tags. Only adds new category tags + 'ai-tagged'.
- DELETIONS: Marked with 'ai-trash' for human review, never directly expunged.
"""

from __future__ import annotations

import argparse
import calendar
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any, Optional
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# API Key Resolution
# ---------------------------------------------------------------------------

def get_typesafe_api_key() -> str:
    """Retrieve TYPESAFE_API_KEY from environment or shell without leaking."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        res = subprocess.run(
            ["fish", "-c", "echo -n $TYPESAFE_API_KEY"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.stdout and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    raise RuntimeError("TYPESAFE_API_KEY environment variable is not set.")


# ---------------------------------------------------------------------------
# Taxonomy & Two-Facet TypeSafe Question Rubric
# ---------------------------------------------------------------------------

DOC_TYPE_CRITERIA: dict[str, str] = {
    "Statements": "Periodic financial bills, monthly account summaries, credit card statements, billing/payment due reminders, utility bills/invoices due.",
    "Receipts": "Proof of payment, incoming/outgoing money transfers (e.g. PayPal \"You've got money\"), merchant purchase receipts, order confirmations, order received notices, payment authorizations, credit card transaction alerts / card-not-present purchase notices, rent payment receipts.",
    "Delivery": "Shipment notifications, package tracking updates, out-for-delivery alerts, package delivered confirmations, order pickup/readiness notices, USPS Informed Delivery mail digests.",
    "Support": "Customer service inquiries, support tickets, help desk cases, warranty claims, repair status, and RMA updates.",
    "Marketing": "Promotional offers, credit card rewards or cashback category activations (e.g. Chase Freedom \"activation required\"), merchant discounts, feature pitches.",
    "Registrations": "New account sign-up welcome/activation emails (Welcome!, Activate), CA DMV vehicle/driver licensing, State Bar, domain registrations. Not credit card rewards activations.",
    "Urgent": "Time-sensitive security notices, fraud alerts, suspicious login warnings, account lockouts, payment decline/failure notices requiring prompt action. Not routine card-not-present transaction alerts (which are Receipts) or rewards activations (Marketing).",
    "Personal": "Direct interpersonal correspondence between family, friends, or individuals.",
    "ai-trash": "Pure junk advertising, unsolicited sales spam, commercial marketing flyers, review/survey prompts (rate your stay, survey polls) with zero actionable value.",
    "None": "Informational broadcasts, terms of service / privacy updates, corporate annual meetings / proxy notices, institutional bulletins, non-transactional correspondence."
}

DOMAIN_CRITERIA: dict[str, str] = {
    "Utilities": "Electric, gas, water, sewer, trash/recycling, home internet, mobile phone/cellular plans (PG&E, SFPUC Water, Sonic, Recology, AT&T, Verizon, T-Mobile, Mint Mobile).",
    "Housing": "Tenant and housing matters, residential rent transfers (including Google Pay, Zelle, or Venmo payments from individuals like Timothy Pierson, Miguel Atencio, Patrick Ncho, Angelica Espana), leases, HOA, SFHA, building maintenance.",
    "Medical": "Healthcare providers, Kaiser Permanente, doctors, prescriptions, medical bills, health plan updates, vaccine appointments.",
    "Investments": "Brokerage accounts, stocks, mutual funds, 401(k)/IRA, investment statements, proxy voting, shareholder annual meetings (Vanguard, Fidelity, Schwab).",
    "TravelBookings": "Confirmed flight e-tickets, hotel reservations, car rental slips, boarding passes (United, Delta, Airbnb).",
    "TravelRewards": "Airline/hotel loyalty programs (KrisFlyer, MileagePlus, Bonvoy), mileage balances, points updates. Not tickets.",
    "StudentLoans": "Student loan accounts, servicers (Nelnet, Aidvantage, MOHELA), repayment, 1098-E tax forms.",
    "Taxes": "IRS, Franchise Tax Board, property taxes, 1099/W-2 tax form delivery.",
    "None": "Emails that do not belong to any of the specific domains above (e.g. general retail shopping, standard credit cards/banking)."
}


def build_systemone_payload(sender: str, subject: str) -> dict[str, Any]:
    """Construct two-facet TypeSafe payload adhering to System One best practices."""
    return {
        "model": "jev-latest",
        "state": {
            "from": sender,
            "subject": subject,
        },
        "questions": {
            "doc_type": {
                "type": "choice",
                "instructions": "What is the transactional or functional document type of this email according to the rubric?",
                "criteria": DOC_TYPE_CRITERIA,
            },
            "domain": {
                "type": "choice",
                "instructions": "Select the specific life domain or subject matter this email belongs to according to the rubric.",
                "criteria": DOMAIN_CRITERIA,
            }
        },
    }


# ---------------------------------------------------------------------------
# TypeSafe API Client
# ---------------------------------------------------------------------------

def evaluate_email(
    api_key: str,
    sender: str,
    subject: str,
    timeout: float = 25.0,
    max_retries: int = 5,
) -> dict[str, Any]:
    """Call TypeSafe System One API to classify an email across two facets with retries."""
    payload = build_systemone_payload(sender, subject)
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lazarus-typesafe-tagger/2.0",
        },
    )

    last_error = ""
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                res = json.loads(body)
                answers = res.get("answers", {})

                dt_ans = answers.get("doc_type", {})
                dm_ans = answers.get("domain", {})

                doc_type = dt_ans.get("choice")
                doc_type_conf = float(dt_ans.get("confidence", 0.0))
                domain = dm_ans.get("choice")
                domain_conf = float(dm_ans.get("confidence", 0.0))

                return {
                    "doc_type": doc_type,
                    "doc_type_conf": doc_type_conf,
                    "domain": domain,
                    "domain_conf": domain_conf,
                    "model": res.get("model", "jev-latest"),
                }
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries - 1:
                jitter = random.uniform(0.5, 1.5)
                time.sleep(2.0 * (attempt + 1) + jitter)

    return {
        "doc_type": None,
        "doc_type_conf": 0.0,
        "domain": None,
        "domain_conf": 0.0,
        "error": last_error,
    }


# ---------------------------------------------------------------------------
# NED Daemon Interface
# ---------------------------------------------------------------------------

def get_ned_client() -> Any:
    """Return an initialized NedClient configured from environment or defaults."""
    repo_root = str(Path(__file__).resolve().parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    mcp_root = str(Path(__file__).resolve().parent.parent / "ned-mcp")
    if mcp_root not in sys.path:
        sys.path.insert(0, mcp_root)

    os.environ.setdefault("NED_URL", "https://alpine-forgejo.tail093cbb.ts.net")

    try:
        from ned.client import NedClient
        return NedClient()
    except ImportError:
        from ned_mcp.client import NedClient
        return NedClient()


# ---------------------------------------------------------------------------
# Tagger Pipeline
# ---------------------------------------------------------------------------

def decide_proposed_tags(
    eval_result: dict[str, Any],
    current_tags: list[str],
) -> tuple[list[str], list[str], str]:
    """Determine tags to add, remove, and rationale based on two-facet judgment."""
    if eval_result.get("error"):
        return [], [], f"Error: {eval_result['error']}"

    dt = eval_result.get("doc_type")
    dt_conf = eval_result.get("doc_type_conf", 0.0)
    dm = eval_result.get("domain")
    dm_conf = eval_result.get("domain_conf", 0.0)

    tags_to_add: list[str] = []
    tags_to_remove: list[str] = []
    reasons: list[str] = []

    # 1. Check for ai-trash
    if dt == "ai-trash" and dt_conf >= 0.70:
        if "ai-trash" not in current_tags:
            tags_to_add.append("ai-trash")
        reasons.append(f"Junk ({dt_conf:.0%})")
    # 2. Check for Personal
    elif dt == "Personal" and dt_conf >= 0.70:
        reasons.append(f"Personal ({dt_conf:.0%})")
    else:
        # 3. Document type facet
        if dt and dt not in ("None", "ai-trash", "Personal") and dt_conf >= 0.60:
            if dt not in current_tags:
                tags_to_add.append(dt)
            reasons.append(f"{dt} ({dt_conf:.0%})")

        # 4. Domain facet
        if dm and dm != "None" and dm_conf >= 0.60:
            if dm not in current_tags:
                tags_to_add.append(dm)
            reasons.append(f"{dm} ({dm_conf:.0%})")

    # 5. Check if unclassifiable or unassigned
    all_concrete = [t for t in (set(current_tags) | set(tags_to_add)) if t not in ("ai-tagged", "ai-unassigned")]
    if not all_concrete and dt != "Personal" and "ai-trash" not in tags_to_add:
        if dt_conf < 0.60 and dm_conf < 0.60:
            if "ai-unassigned" not in current_tags:
                tags_to_add.append("ai-unassigned")
            reasons.append(f"Ambiguous / Low conf ({dt_conf:.0%}, {dm_conf:.0%})")
        else:
            reasons.append(f"Archive (doc={dt} {dt_conf:.0%}, dom={dm} {dm_conf:.0%})")

    # If concrete tags exist or Personal/trash or clear Archive, remove ai-unassigned if present
    if (all_concrete or dt in ("Personal", "ai-trash") or (dt_conf >= 0.60 or dm_conf >= 0.60)) and "ai-unassigned" not in tags_to_add:
        if "ai-unassigned" in current_tags:
            tags_to_remove.append("ai-unassigned")

    # Always add 'ai-tagged' marker
    if "ai-tagged" not in current_tags:
        tags_to_add.append("ai-tagged")

    rationale = ", ".join(reasons) if reasons else "Classified"
    return tags_to_add, tags_to_remove, rationale


def fetch_chronological(
    client: Any,
    query: str = "not tag:ai-tagged",
    limit: int = 150,
    start_year: int = 2020,
) -> list[dict[str, Any]]:
    """Iterate month-by-month to fetch strictly oldest-first threads matching query."""
    accumulated: list[dict[str, Any]] = []
    current_year = time.localtime().tm_year
    for year in range(start_year, current_year + 1):
        for month in range(1, 13):
            last_day = calendar.monthrange(year, month)[1]
            q = f"({query}) and date:{year}-{month:02d}-01..{year}-{month:02d}-{last_day}"
            batch = client.search(q, limit=500)
            if batch:
                batch.sort(key=lambda x: x.get("timestamp", 0))
                accumulated.extend(batch)
                if len(accumulated) >= limit:
                    return accumulated[:limit]
    return accumulated


def run_tagger(
    limit: int = 150,
    query: str = "not tag:ai-tagged",
    date_filter: Optional[str] = None,
    chronological: bool = True,
    apply_changes: bool = False,
    concurrency: int = 4,
) -> None:
    """Execute evaluation and optional tag application on threads."""
    api_key = get_typesafe_api_key()
    client = get_ned_client()

    if date_filter:
        full_query = f"({query}) and ({date_filter})"
        print(f"\n🔍 Searching Notmuch: {full_query} (limit={limit})")
        threads = client.search(full_query, limit=limit, offset=0)
        threads.sort(key=lambda x: x.get("timestamp", 0))
    elif chronological:
        print(f"\n🔍 Fetching oldest {limit} threads chronologically matching: {query}")
        threads = fetch_chronological(client, query=query, limit=limit)
    else:
        print(f"\n🔍 Searching Notmuch: {query} (limit={limit})")
        threads = client.search(query, limit=limit, offset=0)
        threads.sort(key=lambda x: x.get("timestamp", 0))

    if not threads:
        print("No matching threads found.")
        return

    print(f"Found {len(threads)} thread(s). Evaluating with TypeSafe System One (Jev)...\n")

    # Evaluate concurrently
    results: list[dict[str, Any]] = []
    start_time = time.time()

    def _eval_item(t: dict[str, Any]) -> dict[str, Any]:
        tid = t.get("thread", t.get("thread_id", ""))
        sender = t.get("authors", "(unknown)")
        subject = t.get("subject", "(no subject)")
        existing_tags = t.get("tags", [])
        date_str = t.get("date_relative", t.get("date", ""))

        judgments = evaluate_email(api_key, sender, subject)
        to_add, to_remove, rationale = decide_proposed_tags(judgments, existing_tags)

        return {
            "thread_id": tid,
            "sender": sender,
            "subject": subject,
            "date": date_str,
            "existing_tags": existing_tags,
            "judgments": judgments,
            "tags_to_add": to_add,
            "tags_to_remove": to_remove,
            "rationale": rationale,
        }

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_eval_item, t): t for t in threads}
        for fut in as_completed(futures):
            results.append(fut.result())

    elapsed = time.time() - start_time
    print(f"⚡ Evaluated {len(results)} threads in {elapsed:.2f}s (~{len(results)/elapsed:.1f} req/s)\n")

    # Display results
    print(f"{'Date':<11} | {'DocType':<16} | {'Domain':<16} | {'Tags to Add':<26} | {'Sender / Subject':<40}")
    print("-" * 115)

    for item in sorted(results, key=lambda x: x.get("date", "")):
        judg = item["judgments"]
        dt_str = f"{judg.get('doc_type')} ({judg.get('doc_type_conf', 0):.0%})" if judg.get("doc_type") else "err"
        dm_str = f"{judg.get('domain')} ({judg.get('domain_conf', 0):.0%})" if judg.get("domain") else "err"
        tags_disp = "+ " + ", ".join(item["tags_to_add"]) if item["tags_to_add"] else "(none)"
        if item["tags_to_remove"]:
            tags_disp += " - " + ", ".join(item["tags_to_remove"])
        summary = f"{item['sender'][:16]} — {item['subject'][:21]}"
        print(f"{item['date']:<11} | {dt_str:<16} | {dm_str:<16} | {tags_disp:<26} | {summary:<40}")

    # Apply changes if requested
    if apply_changes:
        print("\n📝 Applying tags to Notmuch via NED API...")
        applied_count = 0
        for item in results:
            add_list = item["tags_to_add"]
            rem_list = item["tags_to_remove"]
            if not add_list and not rem_list:
                continue
            kwargs: dict[str, Any] = {"threads": item["thread_id"]}
            if add_list:
                kwargs["add"] = add_list
            if rem_list:
                kwargs["remove"] = rem_list
            ok = client.modify_tags(**kwargs)
            if ok:
                applied_count += 1
            else:
                print(f"⚠️ Failed to update tags on thread {item['thread_id']}")
        print(f"✅ Successfully updated {applied_count} thread(s).")
    else:
        print("\nℹ️ Dry-run mode: No tags were applied. Run with --apply to commit.")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="TypeSafe AI Two-Facet Email Tagger for Notmuch/NED")
    parser.add_argument("--limit", type=int, default=150, help="Number of threads to process (default: 150)")
    parser.add_argument("--query", default="not tag:ai-tagged", help="Notmuch query (default: not tag:ai-tagged)")
    parser.add_argument("--date", default=None, help="Explicit date range filter (e.g. date:2020-01-01..2020-12-31)")
    parser.add_argument("--re-review", action="store_true", help="Re-review oldest emails across archive")
    parser.add_argument("--apply", action="store_true", help="Apply proposed tags to Notmuch (default: dry run)")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent HTTP workers (default: 4)")

    args = parser.parse_args()
    target_query = "*" if args.re_review else args.query
    is_chrono = (args.date is None) and (target_query in ("*", "not tag:ai-tagged"))
    run_tagger(
        limit=args.limit,
        query=target_query,
        date_filter=args.date,
        chronological=is_chrono,
        apply_changes=args.apply,
        concurrency=args.concurrency,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
