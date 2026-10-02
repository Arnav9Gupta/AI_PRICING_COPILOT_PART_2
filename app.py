import os
import json
import html
from datetime import datetime

import pandas as pd
import streamlit as st

from backend.chatbot import (
    understand_question,
    execute_query,
    explain_result,
    make_query,
    format_amount,
)
from backend.data_engine import authenticate


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="AI Pricing Copilot",
    page_icon="💜",
    layout="wide"
)


# ============================================================
# CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>
    .stApp { background-color: #ffffff; }

    .main-title {
        color: #5B2C83; font-size: 32px; font-weight: 700;
        margin-bottom: 5px;
    }
    .subtitle { color: #666666; font-size: 16px; margin-bottom: 25px; }

    .answer-box {
        background-color: #F5EEFA; border-left: 5px solid #5B2C83;
        padding: 18px; border-radius: 8px;
        margin-top: 10px; margin-bottom: 15px;
    }
    .insight-box {
        background-color: #FAF7FC; border-left: 5px solid #8E5BB7;
        padding: 18px; border-radius: 8px; margin-bottom: 20px;
    }
    .error-box {
        background-color: #FDECEC; border-left: 5px solid #C0392B;
        padding: 18px; border-radius: 8px; margin: 10px 0 15px 0;
    }
    .section-title {
        color: #5B2C83; font-size: 20px; font-weight: 600; margin-top: 20px;
    }
    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# SESSION STATE
# ============================================================

st.session_state.setdefault("user", None)
st.session_state.setdefault("messages", [])
st.session_state.setdefault("last_query", None)      # for follow-ups
st.session_state.setdefault("page", "Chat")


# ============================================================
# PERSISTENT QUERY HISTORY
# ============================================================

HISTORY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Data",
    "query_history.json"
)


def load_history():

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(entries):

    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except OSError:
        pass                                    # history must never crash the app


def add_history(user, question, result):

    if result.get("error"):
        status = result["error"]
    elif result.get("empty"):
        status = "no results"
    else:
        status = "success"

    entries = load_history()

    entries.append(
        {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "user": user["username"],
            "question": question,
            "operation": result.get("operation"),
            "status": status,
        }
    )

    save_history(entries[-1000:])               # keep the last 1000


# ============================================================
# HELPERS
# ============================================================

def format_date(value):

    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y")

    return value


def to_dataframe(rows):

    df = pd.DataFrame(rows)

    if "Transaction Date" in df.columns:
        df["Transaction Date"] = df["Transaction Date"].apply(format_date)

    return df


def box(css_class, text):

    st.markdown(
        f'<div class="{css_class}">{html.escape(str(text))}</div>',
        unsafe_allow_html=True
    )


def section(title):

    st.markdown(
        f'<div class="section-title">{title}</div>',
        unsafe_allow_html=True
    )


# ============================================================
# LOGIN (access control)
# ============================================================

def login_screen():

    st.markdown(
        '<div class="main-title">💜 AI Pricing Copilot</div>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<div class="subtitle">Please sign in to continue.</div>',
        unsafe_allow_html=True
    )

    left, middle, right = st.columns([1, 1, 1])

    with middle:

        with st.form("login_form"):

            username = st.text_input("Username")
            password = st.text_input("Password", type="password")

            submitted = st.form_submit_button("Sign in")

        if submitted:

            user = authenticate(username, password)

            if user:
                st.session_state.user = user
                st.session_state.messages = []
                st.session_state.last_query = None
                st.rerun()
            else:
                st.error("Incorrect username or password.")


if st.session_state.user is None:
    login_screen()
    st.stop()

user = st.session_state.user


# ============================================================
# RESULT RENDERING
# ============================================================

def render_table(df, key, label):

    st.dataframe(df, use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{label}.csv",
        mime="text/csv",
        key=key
    )


def render_assistant(msg, idx):

    result = msg["result"]

    # ---- answer ----
    section("💡 Answer")

    if result.get("error"):
        box("error-box", msg["answer"])
        return

    box("answer-box", msg["answer"])

    # ---- explanation ----
    if msg.get("explanation"):
        section("📊 Explanation")
        box("insight-box", msg["explanation"])

    # ---- warnings ----
    for warning in result.get("warnings", []):
        st.caption(f"⚠️ {warning}")

    # ---- aggregated table + chart ----
    table = result.get("table")

    if table:

        section("📈 Summary Table")

        df = to_dataframe(table)

        chart = result.get("chart")

        if chart and all(c in df.columns for c in [chart["x"], *chart["y"]]):
            st.bar_chart(df.set_index(chart["x"])[chart["y"]])

        render_table(df, f"dl_table_{idx}", f"summary_{idx}")

    # ---- supporting rows ----
    rows = result.get("data")

    if rows:

        section("📋 Supporting Data")

        render_table(to_dataframe(rows), f"dl_rows_{idx}", f"transactions_{idx}")

    # ---- transparency ----
    with st.expander("🔍 How I understood your question"):
        st.json(msg["query"])


def process_question(question):
    """Runs the whole pipeline. Never raises."""

    try:
        query = understand_question(
            question,
            last_query=st.session_state.last_query
        )

        result = execute_query(query, user)

        explanation = explain_result(question, query, result)

    except Exception as exc:                    # last line of defence

        query = {"operation": "error"}

        result = {
            "operation": "error",
            "error": "internal",
            "message": "Something went wrong while processing your question.",
            "warnings": [],
            "data": [],
            "table": [],
        }

        explanation = ""

    # remember the query so the next question can be a follow-up
    if not result.get("error"):
        st.session_state.last_query = query

    add_history(user, question, result)

    return {
        "role": "assistant",
        "query": query,
        "result": result,
        "answer": result.get("message", ""),
        "explanation": explanation,
    }


# ============================================================
# SIDEBAR
# ============================================================

def go_to_chat_with(question):

    st.session_state.pending_question = question
    st.session_state.page = "Chat"


with st.sidebar:

    st.markdown("## 💜 AI Pricing Copilot")

    st.caption(f"👤 {user['name']} · {user['role']}")

    if user.get("departments"):
        st.caption(f"Access: {', '.join(user['departments'])} only")

    st.markdown("---")

    st.radio(
        "Navigation",
        ["Chat", "Dashboard", "Query History"],
        key="page"
    )

    st.markdown("---")

    st.markdown(
        """
        **Example questions**

        • Show me transactions from May 2026

        • What is the total spending?

        • Show me the top 5 expenses

        • Compare IT and HR spending

        • Budget vs actual by department

        • Which categories are over budget?

        • Expenses by month

        • *(follow-up)* What about June 2026?
        """
    )

    st.markdown("---")

    if st.button("🧹 Clear conversation"):
        st.session_state.messages = []
        st.session_state.last_query = None
        st.rerun()

    if st.button("🚪 Sign out"):
        st.session_state.user = None
        st.session_state.messages = []
        st.session_state.last_query = None
        st.rerun()


# ============================================================
# HEADER
# ============================================================

st.markdown(
    '<div class="main-title">💜 AI Pricing Copilot</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">Ask questions about your financial transaction '
    'data in simple English.</div>',
    unsafe_allow_html=True
)

page = st.session_state.page


# ============================================================
# CHAT PAGE
# ============================================================

if page == "Chat":

    section("💬 Ask your question")

    # previous messages (with their tables, charts and explanations)
    for idx, message in enumerate(st.session_state.messages):

        with st.chat_message(message["role"]):

            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_assistant(message, idx)

    question = st.chat_input(
        "Example: Compare IT and HR spending for May 2026"
    )

    # a question re-run from Query History
    if not question:
        question = st.session_state.pop("pending_question", None)

    if question:

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):

            with st.spinner("Finding the answer..."):
                reply = process_question(question)

            idx = len(st.session_state.messages) + 1
            render_assistant(reply, idx)

        st.session_state.messages.append(
            {"role": "user", "content": question}
        )
        st.session_state.messages.append(reply)


# ============================================================
# DASHBOARD
# ============================================================

elif page == "Dashboard":

    section("📊 Dashboard")

    summary = execute_query(make_query("income_expense_summary"), user)
    budget = execute_query(make_query("budget_vs_actual"), user)

    if summary.get("error"):
        st.error(summary["message"])

    else:

        m = summary["metrics"]
        b = budget["metrics"]

        c1, c2, c3, c4 = st.columns(4)

        c1.metric("Total Income", format_amount(m["income"]))
        c2.metric("Total Expense", format_amount(m["expense"]))
        c3.metric("Net", format_amount(m["net"]))
        c4.metric(
            "Budget Variance",
            format_amount(b.get("variance", 0)),
            help="Budget minus actual. Negative means overspending."
        )

        for group in ("Department", "Category", "Month"):

            result = execute_query(
                make_query("group_summary", group_by=group),
                user
            )

            if result.get("error") or not result.get("table"):
                continue

            section(f"By {group}")

            df = to_dataframe(result["table"])

            st.bar_chart(df.set_index(group)[["Income", "Expense"]])

        variance = execute_query(
            make_query("variance", group_by="Category"),
            user
        )

        if variance.get("table"):

            section("⚠️ Budget Variance by Category")

            st.dataframe(
                to_dataframe(variance["table"]),
                use_container_width=True,
                hide_index=True
            )


# ============================================================
# QUERY HISTORY
# ============================================================

elif page == "Query History":

    section("🕘 Query History")

    entries = load_history()

    # users only see their own history; Admin sees everyone's
    if user["role"] != "Admin":
        entries = [e for e in entries if e["user"] == user["username"]]

    if not entries:

        st.info("No questions have been asked yet.")

    else:

        search = st.text_input("Search your history")

        if search:
            entries = [
                e for e in entries
                if search.lower() in e["question"].lower()
            ]

        entries = list(reversed(entries))

        df = pd.DataFrame(entries).rename(
            columns={
                "time": "Time",
                "user": "User",
                "question": "Question",
                "operation": "Type",
                "status": "Status",
            }
        )

        if user["role"] != "Admin":
            df = df.drop(columns=["User"])

        st.dataframe(df, use_container_width=True, hide_index=True)

        st.markdown("**Ask a previous question again**")

        recent = [e["question"] for e in entries[:30]]

        if recent:

            choice = st.selectbox(
                "Choose a question",
                recent,
                label_visibility="collapsed"
            )

            st.button(
                "▶️ Ask again",
                on_click=go_to_chat_with,
                args=(choice,)
            )

        if st.button("🗑️ Clear my history"):

            remaining = [
                e for e in load_history()
                if e["user"] != user["username"]
            ]

            save_history(remaining)
            st.rerun()
