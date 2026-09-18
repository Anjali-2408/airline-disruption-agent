# Airline Disruption Resolution Agent

Customer-facing airline disruption support agent built with Python + Streamlit,
for Placement Assignment 3 (AIONOS). The agent is **rule-based only** — no LLM
calls — so every decision is deterministic, explainable, and traceable back to
an exact clause in the policy data pack.

## Run it (one command)

pip install -r requirements.txt
streamlit run app.py

The app opens at `http://localhost:8501`.

## What it does

Three tabs inside the app:

- **💬 Chat** — talk to the agent as a customer. Pick a sample customer (or
  enter a PNR) in the sidebar, then type a message. Every assistant reply has
  a collapsible **Policy trace** showing the exact rule ID(s) applied.
- **🧪 Test Suite** — runs 18 automated test cases (including the 3 assignment
  scenarios, edge cases, and Hinglish input) through the same `build_response()`
  the chat uses, and shows a pass/fail table.
- **📊 Metrics** — live counters: total turns, escalation rate, containment
  rate, and how many replies were caught by the output guardrail.

Sidebar also has: customer lookup, audit trail, **escalation tickets**
(auto-generated, downloadable as JSON), and a **full transcript export**.

## Architecture

customer message
→ detect_intents() — keyword/regex matching (English + Hinglish)
→ _build_response_core() — applies the matched policy rule, drafts a reply,
  records a policy trace entry for every rule checked
→ run_output_guardrail() — re-scans the DRAFTED REPLY TEXT itself, independent
  of which intent produced it, for language that would promise something
  outside policy
→ final reply to customer

The guardrail is a second, independent safety layer. It doesn't know which
intent path produced a reply — it only looks at the words the customer is
about to see. This matters because intent detection is keyword-based and can
have gaps; the guardrail catches a prohibited promise even if a future bug
lets it slip past intent detection.

## Service rules (from the data pack)

| Rule | Summary |
|---|---|
| Cancellation | Free rebooking within 24h, or full refund — customer's choice |
| Delay < 3h | Meal voucher (Rs. 500) |
| Delay 3–5h | Meal voucher + lounge access |
| Delay > 5h | Meal voucher + hotel (delayed hours only, not full night) |
| Refunds | Full amount within 7 business days, original payment method only |
| Fare difference | Customer pays; agent cannot waive above Rs. 1500 |
| Loyalty tier | Gold/Platinum get priority rebooking, no extra compensation |

## Allowed actions (handled automatically)

1. Free rebooking within 24h (airline-caused disruption)
2. Meal voucher / lounge access, per delay tier
3. Hotel accommodation, for delays over 5 hours
4. Refund initiation
5. Flight status / booking info

## Prohibited actions (always escalated to a human)

1. Compensation beyond stated policy amounts
2. Fare difference waiver above Rs. 1500
3. Exceptions for non-airline-caused disruptions
4. Legal action / formal complaint threats
5. Refund to a different payment method

Every one of these is checked twice: once by intent detection, and again by
the output guardrail on the drafted reply text.

## Test scenarios

The 3 scenarios from the assignment data pack, plus 15 additional edge cases
(comma-formatted amounts, Hinglish phrasing, empty messages, unknown PNRs,
a false-positive guard for "my flight was late", and the fare-difference gap
found while building the guardrail) are all in `agent.py::TEST_CASES` and
runnable from the **🧪 Test Suite** tab.

## Sample customers

| Name | Tier | PNR | Situation |
|---|---|---|---|
| Priya Nair | Gold | SK4821X | Flight cancelled |
| Arvind Kulkarni | Silver | TR1190B | 4-hour delay |
| Meher Kaur | Platinum | WL7742 | 6-hour delay |

## Project structure

airline-agent/
├── app.py             # Streamlit UI (chat, test suite, metrics, tickets)
├── agent.py           # All decision logic, guardrail, tests, metrics
├── data.json          # Customers, bookings, policy rules, policy catalog
├── requirements.txt
└── README.md