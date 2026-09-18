import streamlit as st
from agent import build_response, find_customer_by_pnr

st.set_page_config(page_title="Airline Support Agent", page_icon="✈️")

st.title("✈️ Airline Disruption Support Agent")
st.caption("Customer-facing resolution agent — Assignment 3 prototype")

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

pnr = st.sidebar.text_input("Enter Booking Reference (PNR)", key="pnr_input")

customer = find_customer_by_pnr(pnr)
if customer:
    st.sidebar.success(f"Found: {customer['name']} ({customer['loyalty_tier']} tier)")
else:
    st.sidebar.error("No customer found for this PNR")

# --- Chat history ---
if "messages" not in st.session_state:
    st.session_state["messages"] = []

if "audit_log" not in st.session_state:
    st.session_state["audit_log"] = []

for msg in st.session_state["messages"]:
    with st.chat_message(msg["role"]):
        st.write(msg["content"])

# --- Chat input ---
user_input = st.chat_input("Type the customer's message here...")

if user_input:
    st.session_state["messages"].append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.write(user_input)

    result = build_response(pnr, user_input)

    st.session_state["messages"].append({"role": "assistant", "content": result["reply"]})
    with st.chat_message("assistant"):
        st.write(result["reply"])
        if result["escalated"]:
            st.warning("🚨 This conversation has been escalated to a human agent.")

    st.session_state["audit_log"].append({
        "pnr": pnr,
        "message": user_input,
        "action": result["action"],
        "escalated": result["escalated"],
        "log": result["log"],
    })

# --- Audit trail ---
with st.sidebar.expander("📋 Audit Trail / Action Record"):
    if st.session_state["audit_log"]:
        for i, entry in enumerate(st.session_state["audit_log"], 1):
            st.markdown(f"**Turn {i}** — PNR: `{entry['pnr']}`")
            st.write(f"Action taken: `{entry['action']}`")
            st.write(f"Escalated: {entry['escalated']}")
            for line in entry["log"]:
                st.caption("• " + line)
            st.divider()
    else:
        st.caption("No actions yet.")

if st.sidebar.button("Clear conversation"):
    st.session_state["messages"] = []
    st.session_state["audit_log"] = []
    st.rerun()