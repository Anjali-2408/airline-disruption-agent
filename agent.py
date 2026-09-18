"""
Airline Disruption Resolution Agent - core logic.

This module is intentionally rule-based (no external LLM calls) so that every
decision is deterministic, explainable, and traceable back to a specific rule
in the data pack. All facts (customers, bookings, policies) are read from
data.json only - nothing here is invented.

Every response carries a "trace": an ordered list of the exact policy rules
that were evaluated to produce it, each with its rule ID, the rule text, and
the outcome of applying it. This makes the agent auditable - a reviewer can
see not just what the agent said, but which clause of the policy it came from.

Architecture:
  message -> detect_intents() -> rule application (_build_response_core)
          -> output guardrail (run_output_guardrail) -> final reply

The output guardrail is a second, independent layer. It does not know or
care which intent path produced the reply - it re-reads the drafted text
itself and checks it against the same prohibited-action list. If intent
detection ever has a gap, the guardrail is the safety net that stops a
prohibited promise from reaching the customer anyway.

Also included: escalation ticket generation, a self-test suite, and
conversation metrics - all built on top of the same build_response() used
by the live chat UI, so nothing here is a separate code path that could
drift out of sync with what customers actually see.
"""

import json
import os
import re
from datetime import datetime

DATA_PATH = os.path.join(os.path.dirname(__file__), "data.json")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    DATA = json.load(f)

POLICY_CATALOG = DATA.get("policy_catalog", {})


# ---------------------------------------------------------------------------
# Policy trace helpers
# ---------------------------------------------------------------------------

def add_trace(trace, rule_id, outcome):
    """
    Record that a policy rule was evaluated.

    trace:    the running list for this turn (modified in place)
    rule_id:  a key from data.json -> policy_catalog (e.g. "SR-01", "P-02")
    outcome:  plain-English result of applying that rule on this turn
    """
    entry = POLICY_CATALOG.get(rule_id, {})
    trace.append({
        "rule_id": rule_id,
        "name": entry.get("name", "Unknown rule"),
        "text": entry.get("text", ""),
        "outcome": outcome,
    })
    return trace


def delay_rule_id(delay_hours):
    """Return the policy_catalog rule ID for a given delay duration."""
    if delay_hours < 3:
        return "SR-02"
    elif delay_hours <= 5:
        return "SR-03"
    else:
        return "SR-04"


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def find_customer_by_pnr(pnr):
    """Look up a customer profile by their booking reference (PNR)."""
    if not pnr:
        return None
    pnr = pnr.strip().upper()
    for cust in DATA["customers"].values():
        if cust["booking_reference"].upper() == pnr:
            return cust
    return None


def find_bookings_by_pnr(pnr):
    """Return all flight bookings associated with a given PNR."""
    if not pnr:
        return []
    pnr = pnr.strip().upper()
    return [b for b in DATA["bookings"] if b["pnr"].upper() == pnr]


def compute_delay_compensation(delay_hours):
    """Map a delay duration to the compensation tier defined in service_rules."""
    rules = DATA["service_rules"]["delay_compensation_rule"]
    if delay_hours < 3:
        return rules["under_3_hours"]
    elif delay_hours <= 5:
        return rules["more_than_3_hours"]
    else:
        return rules["more_than_5_hours"]


def is_priority_tier(customer):
    """Gold and Platinum tiers get priority rebooking per loyalty_tier_rule."""
    return customer and customer.get("loyalty_tier") in ("Gold", "Platinum")


# ---------------------------------------------------------------------------
# Intent detection (English + common Hinglish phrasing)
# ---------------------------------------------------------------------------

def detect_intents(message):
    """
    Lightweight keyword-based intent detection.

    Covers both English and common romanized Hindi/Hinglish phrasing, since
    real Indian customers frequently mix languages ("meri flight cancel ho
    gayi", "paise wapas chahiye"). Keyword matching stays on English words
    where those already work naturally in Hinglish (e.g. "cancel", "refund",
    "delay" are commonly used as-is); pure-Hindi equivalents are added
    explicitly where the English word wouldn't appear at all.

    Returns a list of intents found in the message. Order doesn't imply
    priority - build_response() decides which intents take precedence
    (e.g. legal threats and prohibited-action requests are checked first
    and short-circuit everything else).
    """
    msg = message.lower()
    intents = []

    legal_signals = [
        "legal action", "lawyer", "sue", "complaint", "formal complaint",
        "case karunga", "case karenge", "shikayat", "consumer court",
        "vakil", "adalat"
    ]
    if any(k in msg for k in legal_signals):
        intents.append("escalate_legal")

    # Non-airline-caused signals.
    #
    # These are matched as regex patterns anchored on the CUSTOMER as the
    # subject ("I missed", "I was late"), not on bare phrases like "was late".
    # A bare "was late" would wrongly fire on "my flight was late", which is an
    # airline-caused delay and must NOT be treated as the customer's fault.
    non_airline_caused_patterns = [
        r"\bi missed (my|the) flight\b",
        r"\bi missed it\b",
        r"\bmissed my flight\b",
        r"\bmissed the flight\b",
        r"\bi overslept\b",
        r"\boverslept\b",
        r"\bi was late\b",
        r"\bi got late\b",
        r"\bi reached late\b",
        r"\bi came late\b",
        r"\bi arrived late\b",
        r"\bi was stuck in traffic\b",
        r"\bstuck in traffic\b",
        r"\bmy fault\b",
        r"\bmy own fault\b",
        r"\bi got to the airport late\b",
        r"\bi reached the airport late\b",
        # Hinglish
        r"\bmeri flight chhoot gayi\b",
        r"\bmera flight chhoot gaya\b",
        r"\bflight miss ho gayi\b",
        r"\bflight miss kar di\b",
        r"\bmaine flight miss kar di\b",
        r"\bmain late ho gaya\b",
        r"\bmain late ho gayi\b",
        r"\bmeri galti thi\b",
        r"\bmeri hi galti\b",
    ]
    if any(re.search(p, msg) for p in non_airline_caused_patterns):
        intents.append("non_airline_caused_request")

    different_payment_signals = [
        "different card", "another card", "different account", "another account",
        "different payment method", "different bank account", "other card",
        "paypal", "send it to my", "credit it to my other",
        "my friend's account", "someone else's account", "new card", "upi",
        # Hinglish
        "dusre card", "dusra card", "alag card", "alag account",
        "doosre account", "doosre khaate", "kisi aur ke account"
    ]
    if "refund" in msg and any(k in msg for k in different_payment_signals):
        intents.append("refund_different_method")

    cancellation_signals = ["cancel", "cancelled", "cancellation"]
    if any(k in msg for k in cancellation_signals):
        intents.append("cancellation")

    delay_signals = ["delay", "delayed", "late", "der se", "der ho gayi"]
    if any(k in msg for k in delay_signals):
        intents.append("delay")

    refund_signals = [
        "refund", "cash back", "money back",
        # Hinglish
        "paise wapas", "paisa wapas", "rupaye wapas", "paise vapis"
    ]
    if any(k in msg for k in refund_signals):
        intents.append("refund")

    free_upgrade_signals = [
        "for the trouble", "free upgrade", "complimentary", "as compensation",
        "for my trouble", "no charge", "waive", "for free",
        # Hinglish
        "muft mein", "free mein", "bina paise ke", "muft"
    ]
    # "fare difference" / "price difference" is included here: without it, a
    # message like "can you waive the Rs 2000 fare difference?" matched no
    # intent at all (no "upgrade"/"business class" keyword present), so the
    # agent fell through to a generic clarifying question instead of
    # escalating a clearly prohibited request. Found while building the
    # output guardrail - fixed at the source, not just papered over.
    upgrade_signals = [
        "upgrade", "business class", "higher fare", "higher-fare",
        "different flight", "fare difference", "price difference"
    ]

    if any(k in msg for k in upgrade_signals):
        if any(k in msg for k in free_upgrade_signals):
            intents.append("goodwill_compensation_request")
        else:
            intents.append("fare_upgrade")

    hotel_signals = ["hotel", "accommodation", "full night", "overnight stay"]
    if any(k in msg for k in hotel_signals):
        intents.append("hotel")

    status_signals = [
        "status", "where is my flight", "flight status",
        # Hinglish
        "kahan hai meri flight", "meri flight kahan hai", "flight kahan hai"
    ]
    if any(k in msg for k in status_signals):
        intents.append("status")

    return intents if intents else ["unknown"]


def is_angry(message):
    """Detect frustrated tone so the reply can open with an empathy line."""
    angry_keywords = [
        "furious", "angry", "unacceptable", "ridiculous", "worst",
        "frustrated", "disgusted", "disappointed", "no one told me",
        # Hinglish
        "bahut gussa", "bekar service", "ganda experience"
    ]
    return any(k in message.lower() for k in angry_keywords)


# ---------------------------------------------------------------------------
# Output guardrail (second, independent layer)
# ---------------------------------------------------------------------------

def run_output_guardrail(reply_text):
    """
    Scan a DRAFTED reply's actual text for language that would promise
    something outside policy - regardless of which intent path produced it.

    This is intentionally decoupled from detect_intents(): it re-derives its
    judgement from the words the customer is about to see, not from internal
    state. Returns a list of human-readable violation reasons (empty list
    means the reply looks clean).
    """
    violations = []
    text = reply_text.lower()

    free_language = [
        "free upgrade", "complimentary upgrade", "on the house",
        "at no extra cost", "free of charge", "waived for you",
        "i've waived", "i have waived"
    ]
    if any(w in text for w in free_language):
        violations.append("reply appears to grant something free/waived without escalation")

    amounts = re.findall(r'rs\.?\s?(\d{1,3}(?:,\d{3})*)', text)
    for a in amounts:
        val = int(a.replace(",", ""))
        if val > 1500 and "escalat" not in text and " pay" not in text:
            violations.append(
                f"reply references Rs. {val} (above the Rs. 1500 limit) "
                "without escalation or a payment requirement"
            )

    if "refund" in text and any(k in text for k in [
        "different card", "another card", "different account", "another account",
        "different payment method"
    ]) and "escalat" not in text:
        violations.append(
            "reply appears to process a refund to a different payment method without escalation"
        )

    return violations


# ---------------------------------------------------------------------------
# Main response builder
# ---------------------------------------------------------------------------

def _build_response_core(pnr, message, already_escalated=False):
    """
    Core decision logic (rule-based intent handling). This is wrapped by
    build_response(), which adds the output guardrail as a final check
    before anything is returned to the caller.
    """
    log = []
    trace = []
    customer = find_customer_by_pnr(pnr)

    if not customer:
        add_trace(trace, "SYS-01", f"No customer matched PNR '{pnr}'. Asked customer to re-check.")
        return {
            "reply": "I couldn't find a booking with that reference. Could you double-check your PNR?",
            "action": "none",
            "escalated": False,
            "log": ["No customer found for PNR: " + str(pnr)],
            "trace": trace,
        }

    if not message or not message.strip():
        add_trace(trace, "SYS-02", "Empty message received. Asked customer to restate their request.")
        return {
            "reply": "Sorry, I didn't catch that - could you tell me what you need help with?",
            "action": "none",
            "escalated": False,
            "log": ["Empty message received"],
            "trace": trace,
        }

    if already_escalated:
        add_trace(trace, "SYS-03", "Conversation already escalated. No new automated action taken.")
        return {
            "reply": "This conversation has already been escalated to our specialist team, "
                     "and they'll be in touch shortly. I don't want to take any further "
                     "automated action on it in the meantime.",
            "action": "already_escalated",
            "escalated": True,
            "log": ["Conversation already escalated - no further automated action taken"],
            "trace": trace,
        }

    bookings = find_bookings_by_pnr(pnr)
    relevant_booking = bookings[0] if bookings else None
    intents = detect_intents(message)
    angry = is_angry(message)
    priority = is_priority_tier(customer)
    log.append(f"Detected intents: {intents}")
    log.append(f"Angry/frustrated tone detected: {angry}")
    log.append(f"Priority tier ({customer['loyalty_tier']}): {priority}")

    empathy_prefix = ""
    if angry:
        empathy_prefix = "I completely understand your frustration, and I'm sorry for the inconvenience. "

    # --- Prohibited actions: these always take priority and short-circuit everything else ---

    if "escalate_legal" in intents:
        add_trace(trace, "P-04",
                  "TRIGGERED - legal action / formal complaint language detected. "
                  "Handed off to a human specialist immediately.")
        return {
            "reply": empathy_prefix + "I want to make sure this gets the right attention immediately. "
                     "I'm escalating this to our specialist support team, and they will reach out to you directly.",
            "action": "escalate_to_human",
            "escalated": True,
            "log": log + ["Escalated: legal/formal complaint threat detected"],
            "trace": trace,
        }

    if "non_airline_caused_request" in intents:
        add_trace(trace, "P-03",
                  "TRIGGERED - customer states the disruption was not airline-caused. "
                  "Agent has no authority to grant an exception. Escalated to supervisor.")
        return {
            "reply": empathy_prefix + "I'm sorry to hear that. Since this wasn't caused by the airline "
                     "(the flight itself operated as scheduled), I'm not able to offer a free rebooking, "
                     "refund, or waiver on my own for this. I'm escalating this to a supervisor who can "
                     "review the specifics of your situation.",
            "action": "escalate_non_airline_caused",
            "escalated": True,
            "log": log + ["Escalated: customer-caused disruption, not covered by standard policy"],
            "trace": trace,
        }

    if "refund_different_method" in intents:
        add_trace(trace, "SR-05", "Refunds are restricted to the original payment method only.")
        add_trace(trace, "P-05",
                  "TRIGGERED - refund requested to a different payment method. Escalated to supervisor.")
        return {
            "reply": empathy_prefix + "I'm not able to process a refund to a different payment method - "
                     "our policy requires refunds to go back to the original payment method only. "
                     "I'm escalating this to a supervisor to review your request.",
            "action": "escalate_refund_different_method",
            "escalated": True,
            "log": log + ["Escalated: refund requested to a different payment method"],
            "trace": trace,
        }

    # --- Allowed actions: handled automatically ---

    reply_parts = []
    actions_taken = []
    needs_escalation = False

    if "cancellation" in intents and relevant_booking and "cancelled" in relevant_booking["status"].lower():
        rebooking_line = (
            "Your flight " + relevant_booking["flight"] + " (" + relevant_booking["route"] +
            ") was cancelled due to operational reasons. As per policy, you're entitled to a free "
            "rebooking on the next available flight within 24 hours, or a full refund - whichever you prefer."
        )
        add_trace(trace, "SR-01",
                  f"APPLIES - flight {relevant_booking['flight']} status is "
                  f"'{relevant_booking['status']}'. Offered free rebooking within 24h or full refund.")
        add_trace(trace, "AA-01", "Action authorised - rebooking offered at no charge.")
        if priority:
            rebooking_line += (
                f" As a {customer['loyalty_tier']} tier member, you also get priority access "
                "to the next available seats."
            )
            add_trace(trace, "SR-07",
                      f"APPLIES - {customer['loyalty_tier']} tier. Priority seat access granted. "
                      "No extra compensation beyond standard policy.")
        reply_parts.append(rebooking_line)
        actions_taken.append("offer_rebooking_or_refund")
        log.append("Applied cancellation_rebooking_rule" + (" + loyalty_tier_rule (priority)" if priority else ""))

    delay_hours = 0
    if relevant_booking and "delayed" in relevant_booking["status"].lower():
        status_text = relevant_booking["status"]
        digits = "".join(filter(str.isdigit, status_text.split("Delayed")[1].split("h")[0]))
        delay_hours = int(digits) if digits else 0

    if ("delay" in intents or "hotel" in intents) and relevant_booking and delay_hours > 0:
        comp = compute_delay_compensation(delay_hours)
        rid = delay_rule_id(delay_hours)
        delay_line = (
            f"Your flight {relevant_booking['flight']} is delayed by {delay_hours} hours. "
            f"Under our delay compensation policy, you're entitled to: {comp}."
        )
        add_trace(trace, rid,
                  f"APPLIES - delay of {delay_hours}h falls in this band. Entitlement: {comp}.")
        add_trace(trace, "AA-03" if rid == "SR-04" else "AA-02",
                  "Action authorised - compensation issued per the band above.")
        if priority:
            delay_line += (
                f" As a {customer['loyalty_tier']} tier member, you also get priority rebooking "
                "if you'd prefer to move to another flight."
            )
            add_trace(trace, "SR-07",
                      f"APPLIES - {customer['loyalty_tier']} tier. Priority rebooking offered. "
                      "No extra compensation beyond standard policy.")
        reply_parts.append(delay_line)
        actions_taken.append("issue_delay_compensation")
        log.append(f"Delay hours: {delay_hours}, Compensation: {comp}" + (" + loyalty_tier_rule (priority)" if priority else ""))

        if "hotel" in intents and delay_hours <= 5:
            reply_parts.append(
                "A full night's hotel stay isn't something I can offer here - our policy only covers "
                "accommodation for the delayed hours themselves when the delay is over 5 hours."
            )
            add_trace(trace, "SR-04",
                      f"NOT MET - hotel accommodation requires a delay over 5 hours; this delay is "
                      f"{delay_hours}h. Full-night stay declined.")
            log.append("Denied full-night stay (delay <= 5 hours)")
        elif "hotel" in intents and delay_hours > 5:
            add_trace(trace, "SR-04",
                      "Clarified scope - accommodation covers the delayed hours only, "
                      "not a full night's stay.")

    if "goodwill_compensation_request" in intents:
        reply_parts.append(
            "I understand you'd like additional compensation, but I'm not authorized to approve "
            "anything beyond our standard policy on my own. I'm escalating this specific request to "
            "a supervisor for review."
        )
        add_trace(trace, "P-01",
                  "TRIGGERED - customer asked for goodwill compensation beyond policy "
                  "(e.g. a free upgrade 'for the trouble', or waiving a fare difference). "
                  "Escalated to supervisor.")
        actions_taken.append("escalate_goodwill_compensation")
        needs_escalation = True
        log.append("Escalated: customer requested free/goodwill compensation beyond policy")

    if "fare_upgrade" in intents:
        numbers = re.findall(r'\d{1,3}(?:,\d{3})+|\d+', message)
        fare_diff = int(numbers[-1].replace(",", "")) if numbers else None

        if fare_diff is not None and fare_diff > 1500:
            reply_parts.append(
                f"I can see you'd like to move to a higher-fare flight, but the fare difference of "
                f"Rs. {fare_diff} is above what I'm authorized to approve on my own (limit is Rs. 1,500). "
                f"I'm escalating this specific request to a supervisor for approval."
            )
            add_trace(trace, "SR-06", f"Fare difference parsed from message: Rs. {fare_diff}.")
            add_trace(trace, "P-02",
                      f"TRIGGERED - Rs. {fare_diff} exceeds the Rs. 1500 agent waiver limit. "
                      "Escalated to supervisor for approval.")
            actions_taken.append("escalate_fare_difference")
            needs_escalation = True
            log.append(f"Fare difference Rs.{fare_diff} exceeds Rs.1500 limit - escalating")
        else:
            reply_parts.append(
                "I can help you move to a different flight. Since this isn't airline-caused, any fare "
                "difference (up to Rs. 1,500) would need to be paid by you before I can confirm it."
            )
            add_trace(trace, "SR-06",
                      "APPLIES - voluntary rebooking. Fare difference payable by customer; "
                      + (f"Rs. {fare_diff} is within the Rs. 1500 agent limit."
                         if fare_diff is not None else
                         "no specific amount stated by the customer."))
            actions_taken.append("check_fare_difference")
            log.append("Applied fare_difference_rule, within authorization limit")

    if "refund" in intents:
        reply_parts.append(
            "I've started a refund request for you. Refunds for airline-caused cancellations are "
            "processed in full within 7 business days, and will go back to your original payment method."
        )
        add_trace(trace, "SR-05",
                  "APPLIES - refund processed in full within 7 business days to the original "
                  "payment method.")
        add_trace(trace, "AA-04", "Action authorised - refund request initiated.")
        actions_taken.append("initiate_refund")
        log.append("Applied refund_processing_rule")

    if "status" in intents and relevant_booking:
        reply_parts.append(
            f"Your flight {relevant_booking['flight']} ({relevant_booking['route']}) "
            f"status: {relevant_booking['status']}."
        )
        add_trace(trace, "AA-05",
                  f"Action authorised - disclosed own booking status for PNR {relevant_booking['pnr']}.")
        actions_taken.append("provide_status")
        log.append("Provided booking status")

    if not reply_parts:
        add_trace(trace, "SYS-04",
                  f"Intents detected: {intents}. None mapped to an authorised action. "
                  "Asked a clarifying question rather than guessing.")
        return {
            "reply": empathy_prefix + "Could you tell me a bit more about what you need help with - "
                     "for example, your flight status, a delay, or a cancellation?",
            "action": "clarify",
            "escalated": False,
            "log": log + ["No confident intent match, asked for clarification"],
            "trace": trace,
        }

    final_reply = empathy_prefix + " ".join(reply_parts)
    if needs_escalation:
        final_reply += " A specialist will follow up with you directly on that part."

    return {
        "reply": final_reply,
        "action": " + ".join(actions_taken),
        "escalated": needs_escalation,
        "log": log,
        "trace": trace,
    }


def build_response(pnr, message, already_escalated=False):
    """
    Public entry point. Runs the core rule-based logic, then passes the
    drafted reply through the output guardrail before returning anything
    to the caller (app.py).
    """
    result = _build_response_core(pnr, message, already_escalated)

    if result["escalated"]:
        add_trace(result["trace"], "GRD-01",
                  "SKIPPED - reply was already escalated by an upstream rule; "
                  "no unescalated promise can reach the customer.")
        return result

    violations = run_output_guardrail(result["reply"])

    if violations:
        reason = "; ".join(violations)
        add_trace(result["trace"], "GRD-01",
                  f"BLOCKED - {reason}. Reply overridden and escalated to a supervisor.")
        result["reply"] = (
            "I want to make sure this is handled correctly, so I'm escalating this to a "
            "supervisor for review before we go any further."
        )
        result["action"] = (result["action"] + " + guardrail_blocked").strip(" +")
        result["escalated"] = True
        result["log"].append("OUTPUT GUARDRAIL: blocked drafted reply - " + reason)
    else:
        add_trace(result["trace"], "GRD-01",
                  "PASSED - drafted reply scanned, no policy violations found.")

    return result


# ---------------------------------------------------------------------------
# Escalation ticket generation
# ---------------------------------------------------------------------------

PRIORITY_BY_TIER = {"Platinum": "P1", "Gold": "P2", "Silver": "P3"}

ESCALATION_REASONS = {
    "escalate_to_human": "Legal action / formal complaint threat",
    "escalate_non_airline_caused": "Non-airline-caused disruption - exception requested",
    "escalate_refund_different_method": "Refund requested to a different payment method",
    "escalate_goodwill_compensation": "Goodwill / free compensation requested beyond policy",
    "escalate_fare_difference": "Fare difference waiver above Rs. 1500",
    "guardrail_blocked": "Blocked by output guardrail - policy violation detected in drafted reply",
}


def get_escalation_reason(action_str):
    """Map an internal action string to a human-readable escalation reason."""
    for key, desc in ESCALATION_REASONS.items():
        if key in action_str:
            return key.upper(), desc
    return "OTHER", "Escalated for supervisor review"


def generate_ticket(customer, pnr, action_str, customer_message, ticket_number):
    """
    Build an escalation ticket for a newly-escalated conversation.

    ticket_number: a 1-based counter supplied by the caller (app.py keeps
    this in session state) so ticket IDs are sequential within a session.
    """
    now = datetime.now()
    reason_code, reason_desc = get_escalation_reason(action_str)
    return {
        "ticket_id": f"ESC-{now.strftime('%Y%m%d')}-{ticket_number:03d}",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "customer_name": customer["name"] if customer else "Unknown",
        "pnr": pnr,
        "loyalty_tier": customer["loyalty_tier"] if customer else "Unknown",
        "priority": PRIORITY_BY_TIER.get(customer["loyalty_tier"] if customer else "", "P3"),
        "reason_code": reason_code,
        "reason_description": reason_desc,
        "customer_message": customer_message,
    }


# ---------------------------------------------------------------------------
# Automated test suite
# ---------------------------------------------------------------------------
# Every case below runs through the exact same build_response() the live
# chat UI uses - there is no separate "test mode" logic that could drift
# out of sync with what a real customer sees.

TEST_CASES = [
    {
        "name": "Cancellation -> free rebooking (allowed action)",
        "pnr": "SK4821X",
        "message": "My flight got cancelled, what should I do?",
        "expect_escalated": False,
        "expect_rule": "SR-01",
    },
    {
        "name": "Scenario 1: Priya asks for free upgrade 'for the trouble'",
        "pnr": "SK4821X",
        "message": "Can I get a free business class upgrade for the trouble?",
        "expect_escalated": True,
        "expect_rule": "P-01",
    },
    {
        "name": "Delay compensation, 4h band (allowed action)",
        "pnr": "TR1190B",
        "message": "My flight is delayed, what compensation do I get?",
        "expect_escalated": False,
        "expect_rule": "SR-03",
    },
    {
        "name": "6h delay -> hotel accommodation (allowed action)",
        "pnr": "WL7742",
        "message": "6 hour delay, can I get a hotel?",
        "expect_escalated": False,
        "expect_rule": "SR-04",
    },
    {
        "name": "Scenario 3: Meher wants Rs 2,000 fare diff waived (comma parsing)",
        "pnr": "WL7742",
        "message": "Please waive the Rs 2,000 fare difference for me.",
        "expect_escalated": True,
        "expect_rule": "P-01",
    },
    {
        "name": "Fare diff waiver without the word 'upgrade' (guardrail-found gap)",
        "pnr": "TR1190B",
        "message": "Can you waive the Rs 2500 fare difference for me?",
        "expect_escalated": True,
        "expect_rule": "P-01",
    },
    {
        "name": "Fare diff within Rs 1500 limit - not escalated",
        "pnr": "TR1190B",
        "message": "I want to move to a different flight, the difference is Rs 800",
        "expect_escalated": False,
        "expect_rule": "SR-06",
    },
    {
        "name": "Non-airline-caused: customer missed their own flight",
        "pnr": "SK4821X",
        "message": "I missed my flight, can you rebook me for free?",
        "expect_escalated": True,
        "expect_rule": "P-03",
    },
    {
        "name": "False-positive guard: airline delay must NOT be treated as customer's fault",
        "pnr": "TR1190B",
        "message": "My flight was late by 4 hours, what do I get?",
        "expect_escalated": False,
        "expect_rule": "SR-03",
    },
    {
        "name": "Refund to a different payment method",
        "pnr": "SK4821X",
        "message": "Please refund my cancelled flight to a different card.",
        "expect_escalated": True,
        "expect_rule": "P-05",
    },
    {
        "name": "Legal action threat",
        "pnr": "WL7742",
        "message": "This is unacceptable, I will sue if this isn't resolved.",
        "expect_escalated": True,
        "expect_rule": "P-04",
    },
    {
        "name": "Refund initiation (allowed action)",
        "pnr": "SK4821X",
        "message": "I want a refund for my cancelled flight.",
        "expect_escalated": False,
        "expect_rule": "SR-05",
    },
    {
        "name": "Flight status lookup (allowed action)",
        "pnr": "WL7742",
        "message": "What is the status of my flight?",
        "expect_escalated": False,
        "expect_rule": "AA-05",
    },
    {
        "name": "Empty message - graceful handling",
        "pnr": "SK4821X",
        "message": "",
        "expect_escalated": False,
        "expect_rule": "SYS-02",
    },
    {
        "name": "Unknown PNR - graceful handling",
        "pnr": "ZZ0000",
        "message": "Where is my flight?",
        "expect_escalated": False,
        "expect_rule": "SYS-01",
    },
    {
        "name": "Hinglish: cancellation + refund in one message",
        "pnr": "SK4821X",
        "message": "meri flight cancel ho gayi, paise wapas chahiye",
        "expect_escalated": False,
        "expect_rule": "SR-01",
    },
    {
        "name": "Hinglish: missed flight, not airline's fault",
        "pnr": "TR1190B",
        "message": "meri flight chhoot gayi, kya aap mujhe free mein rebook kar denge?",
        "expect_escalated": True,
        "expect_rule": "P-03",
    },
    {
        "name": "Hinglish: legal threat",
        "pnr": "WL7742",
        "message": "agar solve nahi hua to main case karunga",
        "expect_escalated": True,
        "expect_rule": "P-04",
    },
]


def run_test_suite():
    """
    Run every case in TEST_CASES through build_response() and compare the
    actual outcome (escalated flag + whether the expected rule ID appears
    in the trace) against what's expected. Returns a list of result dicts,
    ready to render as a pass/fail table.
    """
    results = []
    for tc in TEST_CASES:
        r = build_response(tc["pnr"], tc["message"], already_escalated=False)
        rule_ids = [t["rule_id"] for t in r["trace"]]
        escalation_ok = (r["escalated"] == tc["expect_escalated"])
        rule_ok = (tc["expect_rule"] in rule_ids) if tc.get("expect_rule") else True
        results.append({
            "name": tc["name"],
            "pnr": tc["pnr"],
            "message": tc["message"],
            "expected_escalated": tc["expect_escalated"],
            "actual_escalated": r["escalated"],
            "expected_rule": tc.get("expect_rule"),
            "actual_rules": rule_ids,
            "passed": escalation_ok and rule_ok,
            "reply": r["reply"],
        })
    return results


# ---------------------------------------------------------------------------
# Conversation metrics
# ---------------------------------------------------------------------------

def compute_metrics(audit_log):
    """
    Summarise an audit_log (list of turn dicts, as built by app.py) into
    headline numbers for the Metrics dashboard.
    """
    total = len(audit_log)
    if total == 0:
        return {
            "total_turns": 0,
            "escalated_turns": 0,
            "escalation_rate": 0.0,
            "containment_rate": 0.0,
            "guardrail_blocks": 0,
        }
    escalated = sum(1 for e in audit_log if e.get("escalated"))
    guardrail_blocks = sum(1 for e in audit_log if "guardrail_blocked" in (e.get("action") or ""))
    escalation_rate = round(escalated / total * 100, 1)
    return {
        "total_turns": total,
        "escalated_turns": escalated,
        "escalation_rate": escalation_rate,
        "containment_rate": round(100 - escalation_rate, 1),
        "guardrail_blocks": guardrail_blocks,
    }