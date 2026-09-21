# TypeSafe System One (Jev) Email Classification Taxonomy
# Location: ~/.config/ned/plugins/typesafe_jev/taxonomy.py
#
# This file defines the exact two-facet taxonomy, rubrics, and thresholds used by
# the TypeSafe Jev plugin. Edit this file to customize how your email is classified.
# Note: The plugin STRICTLY transmits sender ('from') and 'subject' lines to the
# model; email body content is never read or transmitted.

# =============================================================================
# AUTHENTICATION
# =============================================================================
# TypeSafe API Key. Paste your key below, or leave empty to read from
# the TYPESAFE_API_KEY environment variable.
API_KEY = ""

# =============================================================================
# FACET 1: DOCUMENT TYPE (Functional role of the email)
# =============================================================================
DOC_TYPE_CRITERIA = {
    "Statements": "Periodic financial bills, monthly account summaries, credit card statements, billing/payment due reminders, utility bills/invoices due.",
    "Receipts": "Proof of payment, incoming/outgoing money transfers (e.g. PayPal 'You have got money'), merchant purchase receipts, order confirmations, order received notices, payment authorizations, credit card transaction alerts / card-not-present purchase notices, rent payment receipts.",
    "Delivery": "Shipment notifications, package tracking updates, out-for-delivery alerts, package delivered confirmations, order pickup/readiness notices, USPS Informed Delivery mail digests.",
    "Support": "Customer service inquiries, support tickets, help desk cases, warranty claims, repair status, and RMA updates.",
    "Marketing": "Promotional offers, credit card rewards or cashback category activations (e.g. 'activation required'), merchant discounts, feature pitches.",
    "Registrations": "New account sign-up welcome/activation emails (Welcome!, Activate), vehicle/driver licensing, professional licensing, domain registrations. Not credit card rewards activations.",
    "Urgent": "Time-sensitive security notices, fraud alerts, suspicious login warnings, account lockouts, payment decline/failure notices requiring prompt action. Not routine card-not-present transaction alerts (which are Receipts) or rewards activations (Marketing).",
    "Personal": "Direct interpersonal correspondence between family, friends, or individuals.",
    "ai-trash": "Pure junk advertising, unsolicited sales spam, commercial marketing flyers, review/survey prompts (rate your stay, survey polls) with zero actionable value.",
    "None": "Informational broadcasts, terms of service / privacy updates, corporate annual meetings / proxy notices, institutional bulletins, non-transactional correspondence.",
}

# =============================================================================
# FACET 2: DOMAIN (Subject matter / life sphere)
# =============================================================================
DOMAIN_CRITERIA = {
    "Utilities": "Electric, gas, water, sewer, trash/recycling, home internet, mobile phone/cellular plans (PG&E, SFPUC Water, Sonic, Recology, AT&T, Verizon, T-Mobile, Mint Mobile).",
    "Housing": "Tenant and housing matters, residential rent transfers, leases, HOA, mortgage payments, building maintenance.",
    "Medical": "Healthcare providers, Kaiser Permanente, doctors, prescriptions, medical bills, health plan updates, vaccine appointments.",
    "Investments": "Brokerage accounts, stocks, mutual funds, 401(k)/IRA, investment statements, proxy voting, shareholder annual meetings (Vanguard, Fidelity, Schwab).",
    "TravelBookings": "Confirmed flight e-tickets, hotel reservations, car rental slips, boarding passes (United, Delta, Airbnb).",
    "TravelRewards": "Airline/hotel loyalty programs (KrisFlyer, MileagePlus, Bonvoy), mileage balances, points updates. Not tickets.",
    "StudentLoans": "Student loan accounts, servicers (Nelnet, Aidvantage, MOHELA, CRI), repayment, 1098-E tax forms.",
    "Taxes": "IRS, Franchise Tax Board, property taxes, 1099/W-2 tax form delivery.",
    "None": "Emails that do not belong to any of the specific domains above (e.g. general retail shopping, standard credit cards/banking).",
}

# =============================================================================
# THRESHOLDS & SETTINGS
# =============================================================================
# Minimum model confidence required to accept a concrete document type or domain tag (0.0 to 1.0)
CONFIDENCE_THRESHOLD = 0.60

# Minimum model confidence required to tag an email with 'ai-trash' (set high for safety)
TRASH_THRESHOLD = 0.70

# Tag applied to mark threads that have been processed by the tagger
TAGGED_MARKER = "ai-tagged"
