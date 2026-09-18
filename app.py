import json
import streamlit as st
from agent import (
    build_response,
    find_customer_by_pnr,
    generate_ticket,
    run_test_suite,
    compute_metrics,
)

st.set_page_config(page_title="Airline Support Agent", page_icon="✈️")

# --- Theme toggle ---
if "theme" not in st.session_state:
    st.session_state["theme"] = "Dark"

theme_choice = st.sidebar.radio("Theme", ["Dark", "Light"], horizontal=True,
                                index=0 if st.session_state["theme"] == "Dark" else 1)
st.session_state["theme"] = theme_choice

if theme_choice == "Light":
    st.markdown("""
        <style>
        html, body {
            background-color: #ffffff !important;
        }
        .stApp {
            background-color: #ffffff;
            color: #1a1a1a;
        }
        .stApp, .stApp p, .stApp span, .stApp label, .stApp h1, .stApp h2, .stApp h3, .stApp div {
            color: #1a1a1a;
        }

        [data-testid="stSidebar"] {
            background-color: #f5f5f5;
        }
        [data-testid="stSidebar"] * {
            color: #1a1a1a !important;
        }

        input, textarea {
            color: #1a1a1a !important;
            -webkit-text-fill-color: #1a1a1a !important;
            caret-color: #1a1a1a !important;
            background-color: #ffffff !important;
        }
        input::placeholder, textarea::placeholder {
            color: #666666 !important;
            -webkit-text-fill-color: #666666 !important;
        }

        [role="combobox"] input,
        input[aria-autocomplete="list"] {
            color: #1a1a1a !important;
            -webkit-text-fill-color: #1a1a1a !important;
        }

        [data-baseweb="select"] > div {
            background-color: #ffffff !important;
            border: 1px solid #cccccc !important;
        }
        [data-baseweb="select"] span {
            color: #1a1a1a !important;
        }

        [data-baseweb="popover"], [data-baseweb="menu"] {
            background-color: #ffffff !important;
        }
        [data-baseweb="menu"] li, [data-baseweb="menu"] li * {
            color: #1a1a1a !important;
            background-color: #ffffff !important;
        }
        [data-baseweb="menu"] li:hover {
            background-color: #e6e6e6 !important;
        }

        [data-testid="stSidebar"] button {
            background-color: #ffffff !important;
            color: #1a1a1a !important;
            border: 1px solid #cccccc !important;
        }
        [data-testid="stSidebar"] button:hover {
            background-color: #eeeeee !important;
        }
        [data-testid="stSidebar"] button p {
            color: #1a1a1a !important;
        }

        [data-testid="stChatMessage"] {
            background-color: #f0f2f6;
            border-radius: 10px;
        }
        [data-testid="stAlertContainer"] {
            color: #1a1a1a !important;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            color: #1a1a1a !important;
        }
        [data-testid="stExpander"] summary {
            color: #1a1a1a !important;
            background-color: #ffffff !important;
        }

        /* Bottom chat input bar */
        [data-testid="stBottom"],
        [data-testid="stBottomBlockContainer"],
        [data-testid="stChatFloatingInputContainer"],
        [data-testid="stChatInput"],
        [data-testid="stChatInput"] > div,
        [data-testid="stChatInputContainer"],
        .stChatFloatingInputContainer,
        .stChatInput,
        section[data-testid="stBottom"] > div,
        div[class*="stBottom"],
        div[class*="stChatFloatingInputContainer"],
        div[class*="stChatInput"] {
            background-color: #ffffff !important;
        }
        [data-testid="stBottom"] *,
        [data-testid="stBottomBlockContainer"] *,
        [data-testid="stChatFloatingInputContainer"] * {
            background-color: transparent;
        }
        [data-testid="stChatInput"] {
            border: 1px solid #cccccc !important;
            border-radius: 8px !important;
        }
        [data-testid="stChatInput"] textarea {
            background-color: #ffffff !important;
            color: #1a1a1a !important;
            -webkit-text-fill-color: #1a1a1a !important;
        }
        [data-testid="stChatInput"] textarea::placeholder {
            color: #888888 !important;
            -webkit-text-fill-color: #888888 !important;
        }
        [data-testid="stChatInputSubmitButton"] {
            background-color: #f0f2f6 !important;
        }
        [data-testid="stChatInputSubmitButton"] svg {
            fill: #1a1a1a !important;
        }
        footer, [data-testid="stDecoration"] {
            background-color: #ffffff !important;
        }
        </style>
    """, unsafe_allow_html=True)

st.title("✈️ Airline Disruption Support Agent")
st.caption("Customer-facing resolution agent — Assignment 3 prototype")


def render_trace(trace):
    """Show the policy rules that produced a given reply."""
    if not trace:
        return
    with st.expander("Policy trace — why this response?"):
        for t in trace:
            st.markdown(f"**`{t['rule_id']}` · {t['name']}**")
            st.markdown(f"→ {t['outcome']}")
            if t.get("text"):
                st.caption("Policy: " + t["text"])
            st.divider()


# --- Sidebar: pick a customer / enter PNR ---
st.sidebar.header("Customer Lookup")

if "pnr_input" not in st.session_state:
    st.session_state["pnr_input"] = "SK4821X"

sample_pnrs = {
    "Priya Nair (Gold) - SK4821X": "SK4821X",
    "Arvind Kulkarni (Silver) - TR1190B": "TR1190B",
    "Meher Kaur (Platinum) - WL7742": "WL7742",
}
choice = st.sidebar.selectbox("Or pick a sample customer", list(sample_pnrs.keys()))
if st.sidebar.button("Use selected customer"):
    st.session_state["pnr_input"] = sample_pnrs[choice]
    st.session_state["messages"] = []
    st.session_state["audit_log"] = []
    st.session_state["tickets"] = []
    st.session_state["conversation_escalated"] = False
    st.rerun()

pnr = st.sidebar.text_input("Enter Booking Reference (PNR)", key="pnr_input")

customer = find_customer_by_pnr(pnr)
if customer:
    st.sidebar.success(f"Found: {customer['name']} ({customer['loyalty_tier']} tier)")
    st.sidebar.caption(
        f"Flights (12 mo): {customer['travel_history_last_12_months']['flights']} | "
        f"Prior complaints: {len(customer['travel_history_last_12_months']['prior_complaints'])}"
    )
else:
    st.sidebar.error("No customer found for this PNR")

# --- Session state ---
if "messages" not in st.session_state:
    st.session_state["messages"] = []
if "audit_log" not in st.session_state:
    st.session_state["audit_log"] = []
if "tickets" not in st.session_state:
    st.session_state["tickets"] = []
if "conversation_escalated" not in st.session_state:
    st.session_state["conversation_escalated"] = False

# --- Customer context banner ---
if customer:
    st.info(
        f"**{customer['name']}** · {customer['loyalty_tier']} tier · PNR `{customer['booking_reference']}`"
    )
    if st.session_state["conversation_escalated"]:
        st.warning("🚨 This conversation has been escalated to a human agent.")

# --- Tabs: Chat / Test Suite / Metrics ---
tab_chat, tab_tests, tab_metrics = st.tabs(["💬 Chat", "🧪 Test Suite", "📊 Metrics"])

with tab_chat:
    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            if msg["role"] == "assistant":
                render_trace(msg.get("trace"))

with tab_tests:
    st.subheader("Automated test suite")
    st.caption(
        "Every case below runs through the exact same build_response() the live chat "
        "uses — this is not a separate/simulated code path."
    )
    if st.button("▶️ Run Test Suite"):
        st.session_state["test_results"] = run_test_suite()

    results = st.session_state.get("test_results")
    if results:
        passed = sum(1 for r in results if r["passed"])
        total = len(results)
        if passed == total:
            st.success(f"{passed}/{total} tests passed ✅")
        else:
            st.error(f"{passed}/{total} tests passed — {total - passed} failing")

        for r in results:
            icon = "✅" if r["passed"] else "❌"
            with st.expander(f"{icon} {r['name']}"):
                st.write(f"**PNR:** `{r['pnr']}`")
                st.write(f"**Message:** {r['message'] if r['message'] else '_(empty)_'}")
                st.write(f"**Expected escalated:** {r['expected_escalated']} | "
                         f"**Actual escalated:** {r['actual_escalated']}")
                st.write(f"**Expected rule:** `{r['expected_rule']}` | "
                         f"**Rules fired:** {', '.join(r['actual_rules']) if r['actual_rules'] else '(none)'}")
                st.caption("Reply: " + r["reply"])
    else:
        st.caption("Click 'Run Test Suite' to execute all test cases.")

with tab_metrics:
    st.subheader("Conversation metrics")
    m = compute_metrics(st.session_state["audit_log"])
    col1, col2 = st.columns(2)
    col1.metric("Total turns", m["total_turns"])
    col2.metric("Escalated turns", m["escalated_turns"])
    col3, col4 = st.columns(2)
    col3.metric("Escalation rate", f"{m['escalation_rate']}%")
    col4.metric("Containment rate", f"{m['containment_rate']}%")
    st.metric("Guardrail blocks (P-01/P-02/P-05 caught at output stage)", m["guardrail_blocks"])
    if m["total_turns"] == 0:
        st.caption("No conversation turns yet — chat with the agent to populate this.")

# --- Chat input (kept at top level so it stays pinned to the bottom of the page) ---
user_input = st.chat_input("Type the customer's message here...")

if user_input:
    was_escalated_before = st.session_state["conversation_escalated"]

    result = build_response(
        pnr, user_input, already_escalated=st.session_state["conversation_escalated"]
    )

    st.session_state["messages"].append({"role": "user", "content": user_input})
    st.session_state["messages"].append({
        "role": "assistant",
        "content": result["reply"],
        "trace": result.get("trace", []),
    })

    if result["escalated"]:
        st.session_state["conversation_escalated"] = True

        # Generate a ticket only on the turn escalation newly happens,
        # not on every subsequent already-escalated turn.
        if not was_escalated_before:
            ticket = generate_ticket(
                customer, pnr, result["action"], user_input,
                len(st.session_state["tickets"]) + 1
            )
            st.session_state["tickets"].append(ticket)

    st.session_state["audit_log"].append({
        "pnr": pnr,
        "message": user_input,
        "action": result["action"],
        "escalated": result["escalated"],
        "log": result["log"],
        "trace": result.get("trace", []),
    })

    st.rerun()

# --- Audit trail ---
with st.sidebar.expander("📋 Audit Trail / Action Record"):
    if st.session_state["audit_log"]:
        for i, entry in enumerate(st.session_state["audit_log"], 1):
            st.markdown(f"**Turn {i}** — PNR: `{entry['pnr']}`")
            st.write(f"Action taken: `{entry['action']}`")
            st.write(f"Escalated: {entry['escalated']}")
            rule_ids = ", ".join(t["rule_id"] for t in entry.get("trace", []))
            if rule_ids:
                st.write(f"Rules applied: `{rule_ids}`")
            for line in entry["log"]:
                st.caption("• " + line)
            st.divider()
    else:
        st.caption("No actions yet.")

# --- Escalation tickets ---
with st.sidebar.expander("🎫 Escalation Tickets"):
    if st.session_state["tickets"]:
        for t in reversed(st.session_state["tickets"]):
            st.markdown(f"**{t['ticket_id']}** — Priority `{t['priority']}`")
            st.caption(f"{t['customer_name']} · {t['reason_description']}")
            st.download_button(
                label="Download ticket (JSON)",
                data=json.dumps(t, indent=2),
                file_name=f"{t['ticket_id']}.json",
                mime="application/json",
                key=f"dl_{t['ticket_id']}",
            )
            st.divider()
    else:
        st.caption("No tickets yet — tickets are created automatically on escalation.")

# --- Transcript export ---
transcript = {
    "pnr": pnr,
    "customer": customer["name"] if customer else None,
    "messages": st.session_state["messages"],
    "audit_log": st.session_state["audit_log"],
    "tickets": st.session_state["tickets"],
}
st.sidebar.download_button(
    "⬇️ Export full transcript (JSON)",
    data=json.dumps(transcript, indent=2, default=str),
    file_name=f"transcript_{pnr}.json",
    mime="application/json",
)

if st.sidebar.button("Clear conversation"):
    st.session_state["messages"] = []
    st.session_state["audit_log"] = []
    st.session_state["tickets"] = []
    st.session_state["conversation_escalated"] = False
    st.rerun()