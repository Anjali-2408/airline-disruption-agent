# Airline Disruption Support Agent

Customer-facing resolution agent prototype — Assignment 3 (AIONOS)

A Streamlit chat app that helps customers with airline disruptions (cancellations, delays, refunds) 
strictly following the service rules and allowed/prohibited actions defined in the data pack. 
The agent auto-resolves everything it's authorized to handle, and escalates anything it isn't.

## How to run locally (one command)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the local URL Streamlit prints (usually `http://localhost:8501`).

## How to use

- Pick a sample customer from the sidebar (Priya Nair, Arvind Kulkarni, or Meher Kaur), or enter their PNR manually.
- Type a message as that customer in the chat box.
- The agent replies based only on the data and rules in the data pack.
- The sidebar's **Audit Trail** expander shows the intent detection, rule applied, and action taken for every turn — useful for reviewing why the agent responded the way it did.

## What the agent handles

**Auto-resolves (allowed actions):**
- Free rebooking or full refund on airline-caused cancellation
- Meal voucher / lounge access / hotel (delayed-hours only) based on delay length
- Refund initiation to the original payment method
- Booking and flight status lookups

**Escalates to a human agent (prohibited actions):**
- Any compensation request beyond stated policy (e.g. "free upgrade for the trouble")
- Fare difference waivers above ₹1,500
- Non-airline-caused disruptions (e.g. customer missed their flight)
- Threats of legal action / formal complaints
- Refund requests to a different payment method

## Test scenarios (from the data pack)

1. **Priya Nair (SK4821X)** — cancelled flight; also asks for a free business-class upgrade "for the trouble" → rebooking/refund offered, upgrade request escalated.
2. **Arvind Kulkarni (TR1190B)** — 4-hour delay; asks for hotel → voucher + lounge given, hotel correctly denied (delay is under 5 hours).
3. **Meher Kaur (WL7742)** — 6-hour delay; asks for full-night hotel and a ₹2,000 fare-difference waiver → hotel limited to delayed hours only, fare waiver escalated (exceeds ₹1,500 limit).

## Tech

- Python (rule-based intent detection, no external LLM calls — deterministic and fully auditable)
- Streamlit for the chat UI
- All customer, booking, and policy data sourced from `data.json` (built strictly from the assignment data pack — no invented data)