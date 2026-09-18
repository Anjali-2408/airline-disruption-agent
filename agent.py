import json
import os
from datetime import datetime
import re

DATA_PATH = os.path.join(os.path.dirname(__file__), "data.json")

with open(DATA_PATH, "r") as f:
    DATA = json.load(f)


def find_customer_by_pnr(pnr):
    pnr = pnr.strip().upper()
    for key, cust in DATA["customers"].items():
        if cust["booking_reference"].upper() == pnr:
            return cust
    return None


def find_bookings_by_pnr(pnr):
    pnr = pnr.strip().upper()
    return [b for b in DATA["bookings"] if b["pnr"].upper() == pnr]


def compute_delay_compensation(delay_hours):
    if delay_hours < 3:
        return DATA["service_rules"]["delay_compensation_rule"]["under_3_hours"]
    elif delay_hours <= 5:
        return DATA["service_rules"]["delay_compensation_rule"]["more_than_3_hours"]
    else:
        return DATA["service_rules"]["delay_compensation_rule"]["more_than_5_hours"]


def detect_intents(message):
    msg = message.lower()
    intents = []

    if any(k in msg for k in ["legal action", "lawyer", "sue", "complaint", "formal complaint"]):
        intents.append("escalate_legal")
    if any(k in msg for k in ["cancel", "cancelled", "cancellation"]):
        intents.append("cancellation")
    if any(k in msg for k in ["delay", "delayed", "late"]):
        intents.append("delay")
    if any(k in msg for k in ["refund", "cash back", "money back"]):
        intents.append("refund")

    # --- FIX 1: separate "free/goodwill upgrade" from "paid voluntary upgrade" ---
    free_upgrade_signals = ["for the trouble", "free upgrade", "complimentary", "as compensation",
                             "for my trouble", "no charge", "waive", "for free"]
    upgrade_signals = ["upgrade", "business class", "higher fare", "higher-fare", "different flight"]

    if any(k in msg for k in upgrade_signals):
        if any(k in msg for k in free_upgrade_signals):
            intents.append("goodwill_compensation_request")
        else:
            intents.append("fare_upgrade")

    if any(k in msg for k in ["hotel", "accommodation", "full night", "overnight stay"]):
        intents.append("hotel")
    if any(k in msg for k in ["status", "where is my flight", "flight status"]):
        intents.append("status")

    return intents if intents else ["unknown"]


def is_angry(message):
    angry_keywords = ["furious", "angry", "unacceptable", "ridiculous", "worst",
                       "frustrated", "disgusted", "disappointed", "no one told me"]
    return any(k in message.lower() for k in angry_keywords)


def build_response(pnr, message):
    log = []
    customer = find_customer_by_pnr(pnr)

    if not customer:
        return {
            "reply": "I couldn't find a booking with that reference. Could you double check your PNR?",
            "action": "none",
            "escalated": False,
            "log": ["No customer found for PNR: " + pnr]
        }

    bookings = find_bookings_by_pnr(pnr)
    relevant_booking = bookings[0] if bookings else None
    intents = detect_intents(message)
    angry = is_angry(message)
    log.append(f"Detected intents: {intents}")
    log.append(f"Angry/frustrated tone detected: {angry}")

    empathy_prefix = ""
    if angry:
        empathy_prefix = "I completely understand your frustration, and I'm sorry for the inconvenience. "

    if "escalate_legal" in intents:
        return {
            "reply": empathy_prefix + "I want to make sure this gets the right attention immediately. "
                      "I'm escalating this to our specialist support team, and they will reach out to you directly.",
            "action": "escalate_to_human",
            "escalated": True,
            "log": log + ["Escalated: legal/formal complaint threat detected"]
        }

    reply_parts = []
    actions_taken = []
    needs_escalation = False

    if "cancellation" in intents and relevant_booking and "cancelled" in relevant_booking["status"].lower():
        reply_parts.append(
            "Your flight " + relevant_booking["flight"] + " (" + relevant_booking["route"] +
            ") was cancelled due to operational reasons. As per policy, you're entitled to a free "
            "rebooking on the next available flight within 24 hours, or a full refund - whichever you prefer."
        )
        actions_taken.append("offer_rebooking_or_refund")
        log.append("Applied cancellation_rebooking_rule")

    delay_hours = 0
    if relevant_booking and "delayed" in relevant_booking["status"].lower():
        status_text = relevant_booking["status"]
        delay_hours = int("".join(filter(str.isdigit, status_text.split("Delayed")[1].split("h")[0])))

    if ("delay" in intents or "hotel" in intents) and relevant_booking and delay_hours > 0:
        comp = compute_delay_compensation(delay_hours)
        reply_parts.append(
            f"Your flight {relevant_booking['flight']} is delayed by {delay_hours} hours. "
            f"Under our delay compensation policy, you're entitled to: {comp}."
        )
        actions_taken.append("issue_delay_compensation")
        log.append(f"Delay hours: {delay_hours}, Compensation: {comp}")

        if "hotel" in intents and delay_hours <= 5:
            reply_parts.append(
                "A full night's hotel stay isn't something I can offer here - our policy only covers "
                "accommodation for the delayed hours themselves when the delay is over 5 hours."
            )
            log.append("Denied full-night stay")

    # --- FIX 1 continued: goodwill/free compensation requests must escalate, never be auto-handled ---
    if "goodwill_compensation_request" in intents:
        reply_parts.append(
            "I understand you'd like additional compensation, but I'm not authorized to approve "
            "anything beyond our standard policy on my own. I'm escalating this specific request to "
            "a supervisor for review."
        )
        actions_taken.append("escalate_goodwill_compensation")
        needs_escalation = True
        log.append("Escalated: customer requested free/goodwill compensation beyond policy (e.g. free upgrade)")

    if "fare_upgrade" in intents:
        # --- FIX 2: handle comma-separated numbers like "2,000" correctly ---
        numbers = re.findall(r'\d{1,3}(?:,\d{3})+|\d+', message)
        fare_diff = int(numbers[-1].replace(",", "")) if numbers else None

        if fare_diff is not None and fare_diff > 1500:
            reply_parts.append(
                f"I can see you'd like to move to a higher-fare flight, but the fare difference of "
                f"Rs. {fare_diff} is above what I'm authorized to approve on my own (limit is Rs. 1,500). "
                f"I'm escalating this specific request to a supervisor for approval."
            )
            actions_taken.append("escalate_fare_difference")
            needs_escalation = True
            log.append(f"Fare difference Rs.{fare_diff} exceeds Rs.1500 limit - escalating")
        else:
            reply_parts.append(
                "I can help you move to a different flight. Since this isn't airline-caused, any fare "
                "difference (up to Rs. 1,500) would need to be paid by you before I can confirm it."
            )
            actions_taken.append("check_fare_difference")
            log.append("Applied fare_difference_rule, within authorization limit")

    if "refund" in intents:
        reply_parts.append(
            "I've started a refund request for you. Refunds for airline-caused cancellations are "
            "processed in full within 7 business days, and will go back to your original payment method."
        )
        actions_taken.append("initiate_refund")
        log.append("Applied refund_processing_rule")

    if "status" in intents and relevant_booking:
        reply_parts.append(
            f"Your flight {relevant_booking['flight']} ({relevant_booking['route']}) "
            f"status: {relevant_booking['status']}."
        )
        actions_taken.append("provide_status")
        log.append("Provided booking status")

    if not reply_parts:
        return {
            "reply": empathy_prefix + "Could you tell me a bit more about what you need help with - "
                      "for example, your flight status, a delay, or a cancellation?",
            "action": "clarify",
            "escalated": False,
            "log": log + ["No confident intent match, asked for clarification"]
        }

    final_reply = empathy_prefix + " ".join(reply_parts)
    if needs_escalation:
        final_reply += " A specialist will follow up with you directly on that part."

    return {
        "reply": final_reply,
        "action": " + ".join(actions_taken),
        "escalated": needs_escalation,
        "log": log
    }