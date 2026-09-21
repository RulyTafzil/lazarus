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
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
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
# Taxonomy & TypeSafe Question Rubric
# ---------------------------------------------------------------------------

TAG_CRITERIA: dict[str, Any] = {
    "Statements": {
        "description": "Recurring financial bills, bank account statements, credit card notices, and utility invoices.",
        "examples_and_keywords": [
            "statement available", "bill ready", "payment scheduled", "direct deposit",
            "Prime Visa payment", "AutoPay reminder", "mortgage payment"
        ],
        "typical_senders": [
            "chase.com", "bankofamerica.com", "att-mail.com", "pge.com", "vanguard.com",
            "etrade.com", "recology.com", "sonic.net", "nelnet.com", "paypal.com"
        ]
    },
    "Receipts": {
        "description": "Merchant and retail purchase receipts, order confirmations, shipped/delivered package notifications.",
        "exclusions": "Do NOT use for government fees or vehicle DMV renewals (use Registrations instead). Do NOT use for rent transfers (use Housing).",
        "typical_senders": [
            "amazon.com", "aliexpress.com", "wholefoods.com", "walmart.com", "ebay.com"
        ],
        "typical_subjects": ["order confirmed", "shipped", "delivered", "order #"]
    },
    "Housing": {
        "description": "Tenant correspondence, rent payments (including Google Pay/Zelle/Venmo rent transfers from individuals), lease documents, housing authority (SFHA / HAP), building maintenance, HOA.",
        "typical_senders_and_parties": [
            "tenants", "landlords", "SFHA", "housing authority", "property managers",
            "Google Pay transfers from tenants/roommates"
        ],
        "keywords": ["rent", "lease", "HAP", "SFHA", "vacancy", "tenancy", "turn down music", "water running"]
    },
    "Registrations": {
        "description": "Official government registrations, motor vehicle/driver licensing, state licensing boards, domain registrations.",
        "inclusions": [
            "DoNotReply@dmv.ca.gov (CA DMV renewals, receipts, registration)",
            "The State Bar of California (attorney licensing, member records)",
            "Domain registration renewals"
        ]
    },
    "TravelBookings": {
        "description": "Flight reservations, airline tickets, hotel bookings, car rental confirmations, boarding passes.",
        "typical_senders": ["united.com", "singaporeair.com", "delta.com", "hotels.com", "airbnb.com"]
    },
    "FrequentFlyer": {
        "description": "Airline and hotel loyalty program newsletters, mileage statements, award program updates (non-itinerary).",
        "typical_senders": ["krisflyer", "mileageplus", "marriott bonvoy", "points insider"]
    },
    "USPS": {
        "description": "US Postal Service mail notifications, Informed Delivery daily mail digests, package tracking.",
        "typical_senders": ["USPS Informed Delivery", "usps.com"]
    },
    "Client": {
        "description": "Legal case matters, court filings, estates, probate correspondence, professional client files.",
        "typical_senders": ["tylertech.cloud (efiling)", "court notices", "client names"]
    },
    "CellPhone": {
        "description": "Cellular phone equipment, mobile carrier service quotes, SIM cards, device plans.",
        "typical_subjects": ["AT&T service quote", "mobile recap", "SIM activation"]
    },
    "Taxes": {
        "description": "Tax returns, IRS notifications, Franchise Tax Board, 1099/W-2 tax form delivery."
    },
    "StudentLoans": {
        "description": "Student loan payment statements, loan servicer correspondence (Nelnet, Aidvantage, Dept of Education)."
    },
    "ai-trash": {
        "description": "Delete candidates: automated promotional offers, advertising newsletters, discount codes, rating/review prompts ('rate your experience'), intermediate delivery estimates, or transient cancellation notices.",
        "typical_subjects": ["rate your experience", "deals", "summer rewards", "have you seen this?", "delivery estimate update"]
    },
    "Personal": {
        "description": "Direct personal correspondence between family, friends, or individuals that does not fit business or legal archive tags.",
        "note": "When selected, no archive category tag is applied; only marked as personal."
    }
}


def build_systemone_payload(sender: str, subject: str) -> dict[str, Any]:
    """Construct minimal-metadata TypeSafe payload adhering to System One best practices."""
    return {
        "model": "jev-latest",
        "state": {
            "from": sender,
            "subject": subject,
        },
        "questions": {
            "category": {
                "type": "choice",
                "instructions": (
                    "Based strictly on the sender address and subject line, select the single most accurate "
                    "archive tag for this email according to the rubric criteria."
                ),
                "criteria": TAG_CRITERIA,
            },
            "is_deletable": {
                "type": "noul",
                "instructions": (
                    "Is this an automated marketing advertisement, sales promotion, discount offer, "
                    "or transient rating/survey request that should be deleted?"
                ),
            },
        },
    }


# ---------------------------------------------------------------------------
# TypeSafe API Client
# ---------------------------------------------------------------------------

def evaluate_email(
    api_key: str,
    sender: str,
    subject: str,
    timeout: float = 12.0,
) -> dict[str, Any]:
    """Call TypeSafe System One API to classify an email metadata tuple."""
    payload = build_systemone_payload(sender, subject)
    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        "https://api.typesafe.ai/v1/systemone",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lazarus-typesafe-tagger/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            res = json.loads(body)
            answers = res.get("answers", {})

            cat_ans = answers.get("category", {})
            junk_ans = answers.get("is_junk", {})

            choice = cat_ans.get("choice")
            confidence = float(cat_ans.get("confidence", 0.0))
            is_junk_prob = float(junk_ans.get("noul", 0.0))

            return {
                "choice": choice,
                "confidence": confidence,
                "is_junk_prob": is_junk_prob,
                "model": res.get("model", "jev-latest"),
            }
    except Exception as e:
        return {
            "choice": None,
            "confidence": 0.0,
            "is_junk_prob": 0.0,
            "error": str(e),
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
) -> tuple[list[str], str]:
    """Determine tags to add and rationale based on TypeSafe judgment."""
    if eval_result.get("error"):
        return [], f"Error: {eval_result['error']}"

    choice = eval_result["choice"]
    confidence = eval_result["confidence"]
    junk_prob = eval_result["is_junk_prob"]

    tags_to_add: list[str] = []
    rationale: str = ""

    # Check for deletion / junk
    if choice == "ai-trash" or junk_prob >= 0.85:
        if "ai-trash" not in current_tags:
            tags_to_add.append("ai-trash")
        rationale = f"Mark as junk ({junk_prob:.0%} junk prob, {confidence:.0%} conf)"
    elif choice and choice != "Personal":
        if choice not in current_tags:
            tags_to_add.append(choice)
        rationale = f"Classify as {choice} ({confidence:.0%} conf)"
    else:
        rationale = f"Personal / No archive tag ({confidence:.0%} conf)"

    # Always add 'ai-tagged' marker
    if "ai-tagged" not in current_tags:
        tags_to_add.append("ai-tagged")

    return tags_to_add, rationale


def run_tagger(
    limit: int = 50,
    query: str = "not tag:ai-tagged",
    date_filter: str = "date:2020-01-01..2020-12-31",
    apply_changes: bool = False,
    concurrency: int = 5,
) -> None:
    """Execute evaluation and optional tag application on untagged threads."""
    api_key = get_typesafe_api_key()
    client = get_ned_client()

    full_query = f"({query}) and ({date_filter})" if date_filter else query
    print(f"\n🔍 Searching Notmuch: {full_query} (limit={limit})")

    threads = client.search(full_query, limit=limit, offset=0)
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
        to_add, rationale = decide_proposed_tags(judgments, existing_tags)

        return {
            "thread_id": tid,
            "sender": sender,
            "subject": subject,
            "date": date_str,
            "existing_tags": existing_tags,
            "judgments": judgments,
            "tags_to_add": to_add,
            "rationale": rationale,
        }

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_eval_item, t): t for t in threads}
        for fut in as_completed(futures):
            results.append(fut.result())

    elapsed = time.time() - start_time
    print(f"⚡ Evaluated {len(results)} threads in {elapsed:.2f}s (~{len(results)/elapsed:.1f} req/s)\n")

    # Display results
    print(f"{'Date':<11} | {'TypeSafe Judgment':<22} | {'Tags to Add':<24} | {'Sender / Subject':<45}")
    print("-" * 110)

    for item in sorted(results, key=lambda x: x.get("date", "")):
        judg = item["judgments"]
        choice_str = f"{judg['choice']} ({judg['confidence']:.0%})" if judg.get("choice") else "error"
        tags_disp = "+ " + ", ".join(item["tags_to_add"]) if item["tags_to_add"] else "(none)"
        summary = f"{item['sender'][:18]} — {item['subject'][:24]}"
        print(f"{item['date']:<11} | {choice_str:<22} | {tags_disp:<24} | {summary:<45}")

    # Apply changes if requested
    if apply_changes:
        print("\n📝 Applying tags to Notmuch via NED API...")
        applied_count = 0
        for item in results:
            add_list = item["tags_to_add"]
            if not add_list:
                continue
            ok = client.modify_tags(threads=item["thread_id"], add=add_list)
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
    parser = argparse.ArgumentParser(description="TypeSafe AI Email Tagger for Notmuch/NED")
    parser.add_argument("--limit", type=int, default=50, help="Number of threads to process (default: 50)")
    parser.add_argument("--query", default="not tag:ai-tagged", help="Notmuch query (default: not tag:ai-tagged)")
    parser.add_argument("--date", default="date:2020-01-01..2020-12-31", help="Date range filter (default: 2020)")
    parser.add_argument("--apply", action="store_true", help="Apply proposed tags to Notmuch (default: dry run)")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent HTTP workers (default: 5)")

    args = parser.parse_args()
    run_tagger(
        limit=args.limit,
        query=args.query,
        date_filter=args.date,
        apply_changes=args.apply,
        concurrency=args.concurrency,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
