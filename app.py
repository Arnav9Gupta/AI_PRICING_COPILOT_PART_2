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
from backend.data_engine import (
    authenticate,
    allowed_datasets,
    DATASETS,
    LOAD_ERRORS,
)


# ============================================================
# PAGE CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="AI Pricing Copilot",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ============================================================
# THEME / CUSTOM CSS
# ============================================================

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    :root {
        --primary: #4B2380;
        --primary-dark: #2E1450;
        --primary-soft: #F3EDFA;
        --accent: #7A4BB5;
        --text: #1F2937;
        --muted: #6B7280;
        --border: #E5E7EB;
        --bg: #F6F5FA;
        --success: #15803D;
        --danger: #B42318;
        --warning: #B45309;
    }

    /* ---------- base ---------- */
    .stApp, .stMarkdown, p, label, input, textarea, button, h1, h2, h3, h4 {
        font-family: 'Inter', 'Segoe UI', sans-serif;
    }
    .stApp { background-color: var(--bg); color: var(--text); }

    /* hide Streamlit's own menu / deploy button, but NOT the whole toolbar:
       the arrow that re-opens a collapsed sidebar lives inside it */
    #MainMenu, footer, [data-testid="stDecoration"],
    [data-testid="stToolbarActions"], [data-testid="stMainMenu"],
    [data-testid="stAppDeployButton"], .stDeployButton {
        visibility: hidden; height: 0;
    }

    [data-testid="stExpandSidebarButton"],
    [data-testid="stSidebarCollapsedControl"] {
        visibility: visible !important;
    }

    header[data-testid="stHeader"] { background: transparent; }

    .block-container {
        padding-top: 1.6rem;
        padding-bottom: 5rem;
        max-width: 1280px;
    }

    /* ---------- top banner ---------- */
    .app-banner {
        background: linear-gradient(120deg, var(--primary-dark) 0%, var(--primary) 55%, var(--accent) 100%);
        color: #ffffff;
        padding: 26px 32px;
        border-radius: 14px;
        margin-bottom: 26px;
        box-shadow: 0 8px 24px rgba(46, 20, 80, 0.18);
    }
    .app-banner .banner-title {
        font-size: 26px; font-weight: 700; letter-spacing: -0.2px;
    }
    .app-banner .banner-sub {
        font-size: 14px; opacity: 0.85; margin-top: 4px; font-weight: 400;
    }

    /* ---------- section headings ---------- */
    .section-title {
        color: var(--primary-dark);
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.9px;
        text-transform: uppercase;
        margin: 26px 0 12px 0;
        padding-bottom: 8px;
        border-bottom: 2px solid var(--primary-soft);
    }

    /* ---------- answer cards ---------- */
    .answer-box {
        background: #ffffff;
        border: 1px solid var(--border);
        border-left: 5px solid var(--primary);
        padding: 20px 24px;
        border-radius: 10px;
        font-size: 16px;
        line-height: 1.6;
        box-shadow: 0 2px 8px rgba(17, 24, 39, 0.05);
        margin-bottom: 14px;
    }
    .insight-box {
        background: var(--primary-soft);
        border: 1px solid #E4D7F3;
        padding: 16px 22px;
        border-radius: 10px;
        font-size: 14.5px;
        line-height: 1.6;
        color: #3B2A57;
        margin-bottom: 14px;
    }
    .error-box {
        background: #FEF3F2;
        border: 1px solid #FECDCA;
        border-left: 5px solid var(--danger);
        color: #7A271A;
        padding: 18px 24px;
        border-radius: 10px;
        font-size: 15px;
        line-height: 1.55;
        margin: 8px 0 14px 0;
    }
    .notice {
        background: #FFFAEB;
        border: 1px solid #FEDF89;
        color: #93370D;
        padding: 10px 16px;
        border-radius: 8px;
        font-size: 13px;
        margin-bottom: 8px;
    }
    .card-label {
        font-size: 11px; font-weight: 700; letter-spacing: 0.9px;
        text-transform: uppercase; color: var(--muted); margin-bottom: 6px;
    }
    .dataset-tag {
        display: inline-block;
        background: var(--primary-soft);
        color: var(--primary-dark);
        border: 1px solid #E4D7F3;
        border-radius: 999px;
        padding: 3px 12px;
        font-size: 12px;
        font-weight: 600;
        margin-bottom: 8px;
    }

    /* ---------- empty state ---------- */
    .empty-state {
        background: #ffffff;
        border: 1px dashed #CFC3E3;
        border-radius: 12px;
        padding: 42px 24px;
        text-align: center;
        color: var(--muted);
        margin: 10px 0 20px 0;
    }
    .empty-state .empty-title {
        color: var(--primary-dark); font-size: 17px; font-weight: 600;
        margin-bottom: 6px;
    }

    /* ---------- KPI cards ---------- */
    .kpi-card {
        background: #ffffff;
        border: 1px solid var(--border);
        border-top: 4px solid var(--primary);
        border-radius: 12px;
        padding: 18px 20px;
        box-shadow: 0 2px 8px rgba(17, 24, 39, 0.05);
    }
    .kpi-label {
        font-size: 12px; font-weight: 600; letter-spacing: 0.6px;
        text-transform: uppercase; color: var(--muted);
    }
    .kpi-value {
        font-size: 24px; font-weight: 700; color: var(--primary-dark);
        margin-top: 6px;
    }
    .kpi-value.pos { color: var(--success); }
    .kpi-value.neg { color: var(--danger); }
    .kpi-note { font-size: 12px; color: var(--muted); margin-top: 4px; }

    /* ---------- tables ---------- */
    [data-testid="stDataFrame"] {
        border: 1px solid var(--border);
        border-radius: 10px;
        overflow: hidden;
        background: #ffffff;
    }
    .history-wrap {
        background: #ffffff; border: 1px solid var(--border);
        border-radius: 10px; overflow: hidden;
    }
    table.history {
        width: 100%; border-collapse: collapse; font-size: 13.5px;
    }
    table.history th {
        background: var(--primary-soft); color: var(--primary-dark);
        text-align: left; padding: 11px 16px; font-weight: 600;
        font-size: 12px; letter-spacing: 0.5px; text-transform: uppercase;
    }
    table.history td {
        padding: 11px 16px; border-top: 1px solid var(--border);
        color: var(--text);
    }
    table.history tr:hover td { background: #FAF8FD; }
    .pill {
        display: inline-block; padding: 3px 10px; border-radius: 999px;
        font-size: 12px; font-weight: 600;
    }
    .pill.ok { background: #DCFAE6; color: var(--success); }
    .pill.warn { background: #FEF0C7; color: var(--warning); }
    .pill.bad { background: #FEE4E2; color: var(--danger); }

    /* ---------- chat ---------- */
    [data-testid="stChatMessage"] {
        background: transparent;
        padding: 6px 0;
    }
    [data-testid="stChatInput"] textarea { font-size: 15px; }
    [data-testid="stChatInput"] {
        border-radius: 12px;
        border: 1px solid #D5CBE6;
    }

    /* ---------- buttons ---------- */
    .stButton > button, .stDownloadButton > button {
        border-radius: 8px;
        border: 1px solid #D5CBE6;
        background: #ffffff;
        color: var(--primary-dark);
        font-weight: 600;
        font-size: 13.5px;
        padding: 8px 18px;
        transition: all 0.15s ease;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        background: var(--primary);
        color: #ffffff;
        border-color: var(--primary);
    }
    [data-testid="stFormSubmitButton"] > button {
        width: 100%;
        background: var(--primary);
        color: #ffffff;
        border: none;
        padding: 10px 18px;
    }
    [data-testid="stFormSubmitButton"] > button:hover {
        background: var(--primary-dark);
        color: #ffffff;
    }

    /* ---------- tabs ---------- */
    .stTabs [data-baseweb="tab-list"] { gap: 6px; }
    .stTabs [data-baseweb="tab"] {
        font-weight: 600; font-size: 14px; padding: 8px 18px;
    }

    /* ---------- login ---------- */
    [data-testid="stForm"] {
        background: #ffffff;
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 26px 26px 18px 26px;
        box-shadow: 0 10px 30px rgba(46, 20, 80, 0.10);
    }
    .login-brand { text-align: center; margin: 40px 0 22px 0; }
    .login-mark {
        width: 54px; height: 54px; margin: 0 auto 14px auto;
        border-radius: 14px;
        background: linear-gradient(135deg, var(--primary-dark), var(--accent));
        color: #ffffff; font-weight: 700; font-size: 20px;
        display: flex; align-items: center; justify-content: center;
        letter-spacing: 1px;
    }
    .login-title {
        font-size: 24px; font-weight: 700; color: var(--primary-dark);
    }
    .login-sub { font-size: 14px; color: var(--muted); margin-top: 4px; }

    /* ---------- sidebar ---------- */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--primary-dark) 0%, #3A1B66 100%);
    }
    section[data-testid="stSidebar"] * { color: #EDE7F6; }
    section[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.15); }

    .side-brand { font-size: 19px; font-weight: 700; color: #ffffff !important; }
    .side-brand-sub { font-size: 12px; opacity: 0.7; margin-top: 2px; }

    .user-card {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.14);
        border-radius: 10px;
        padding: 12px 14px;
        margin: 6px 0 4px 0;
    }
    .user-name { font-weight: 600; font-size: 14px; color: #ffffff !important; }
    .user-meta { font-size: 12px; opacity: 0.75; margin-top: 2px; }

    .side-label {
        font-size: 11px; font-weight: 700; letter-spacing: 1px;
        text-transform: uppercase; opacity: 0.6; margin: 14px 0 4px 2px;
    }

    section[data-testid="stSidebar"] [role="radiogroup"] { gap: 4px; }
    section[data-testid="stSidebar"] [role="radiogroup"] label {
        padding: 9px 12px; border-radius: 8px; width: 100%;
        cursor: pointer; transition: background 0.15s ease;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label:hover {
        background: rgba(255,255,255,0.10);
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label > div:first-child {
        display: none;
    }
    section[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
        background: rgba(255,255,255,0.16);
        font-weight: 600;
    }
    section[data-testid="stSidebar"] .stButton > button {
        width: 100%;
        background: transparent;
        color: #EDE7F6;
        border: 1px solid rgba(255,255,255,0.28);
    }
    section[data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,0.14);
        color: #ffffff;
        border-color: rgba(255,255,255,0.5);
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
            "dataset": result.get("dataset_label") or "",
            "operation": result.get("operation"),
            "status": status,
        }
    )

    save_history(entries[-1000:])               # keep the last 1000


# ============================================================
# HELPERS
# ============================================================

def esc(value):

    return html.escape(str(value))


def format_date(value):

    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y")

    return value


def to_dataframe(rows):

    df = pd.DataFrame(rows)

    # every date column (Transaction Date, PO Date, Pay Date, Order Date, ...)
    for column in df.columns:
        if df[column].map(lambda v: isinstance(v, datetime)).any():
            df[column] = df[column].apply(format_date)

    return df


def box(css_class, text):

    st.markdown(
        f'<div class="{css_class}">{esc(text)}</div>',
        unsafe_allow_html=True
    )


def section(title):

    st.markdown(
        f'<div class="section-title">{esc(title)}</div>',
        unsafe_allow_html=True
    )


def banner(title, subtitle=""):

    sub = f'<div class="banner-sub">{esc(subtitle)}</div>' if subtitle else ""

    st.markdown(
        f"""
        <div class="app-banner">
            <div class="banner-title">{esc(title)}</div>
            {sub}
        </div>
        """,
        unsafe_allow_html=True
    )


def kpi(label, value, tone="", note=""):

    st.markdown(
        f"""
        <div class="kpi-card">
            <div class="kpi-label">{esc(label)}</div>
            <div class="kpi-value {tone}">{esc(value)}</div>
            <div class="kpi-note">{esc(note)}</div>
        </div>
        """,
        unsafe_allow_html=True
    )


def bar_chart(df, x, ys):

    data = df.set_index(x)[ys]

    try:
        st.bar_chart(
            data,
            color=["#4B2380", "#B794DB"][:len(ys)]
        )
    except TypeError:                           # older Streamlit versions
        st.bar_chart(data)


def dataset_options(user):
    """{label: key} of the datasets this user may open."""

    return {DATASETS[k]["label"]: k for k in allowed_datasets(user)}


def reset_user_state():

    st.session_state.messages = []
    st.session_state.last_query = None


# ============================================================
# LOGIN (access control)
# ============================================================

def login_screen():

    st.markdown(
        """
        <div class="login-brand">
            <div class="login-mark">AP</div>
            <div class="login-title">AI Pricing Copilot</div>
            <div class="login-sub">Sign in to access your financial data assistant</div>
        </div>
        """,
        unsafe_allow_html=True
    )

    left, middle, right = st.columns([1, 1.1, 1])

    with middle:

        with st.form("login_form"):

            username = st.text_input("Username")
            password = st.text_input("Password", type="password")

            submitted = st.form_submit_button("Sign in")

        if submitted:

            user = authenticate(username, password)

            if user:
                st.session_state.user = user
                reset_user_state()
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
        "Download CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{label}.csv",
        mime="text/csv",
        key=key
    )


def render_assistant(msg, idx):

    result = msg["result"]

    # ---- which dataset answered ----
    if result.get("dataset_label"):
        st.markdown(
            f'<span class="dataset-tag">Dataset: '
            f'{esc(result["dataset_label"])}</span>',
            unsafe_allow_html=True
        )

    # ---- answer ----
    section("Answer")

    if result.get("error"):
        box("error-box", msg["answer"])
        return

    box("answer-box", msg["answer"])

    # ---- explanation ----
    if msg.get("explanation"):
        section("Explanation")
        box("insight-box", msg["explanation"])

    # ---- notices ----
    for warning in result.get("warnings", []):
        box("notice", warning)

    # ---- aggregated table + chart ----
    table = result.get("table")

    if table:

        section("Summary")

        df = to_dataframe(table)

        chart = result.get("chart")

        if chart and all(c in df.columns for c in [chart["x"], *chart["y"]]):
            bar_chart(df, chart["x"], chart["y"])

        render_table(df, f"dl_table_{idx}", f"summary_{idx}")

    # ---- supporting rows ----
    rows = result.get("data")

    if rows:

        section("Supporting Data")

        render_table(to_dataframe(rows), f"dl_rows_{idx}", f"records_{idx}")


def process_question(question):
    """Runs the whole pipeline. Never raises."""

    try:
        query = understand_question(
            question,
            last_query=st.session_state.last_query,
            dataset=None,                       # the question decides
            user=user
        )

        result = execute_query(query, user)

        explanation = explain_result(question, query, result)

    except Exception:                           # last line of defence

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

    st.markdown(
        """
        <div class="side-brand">AI Pricing Copilot</div>
        """,
        unsafe_allow_html=True
    )

    st.markdown(
        f"""
        <div class="side-label">Signed in as</div>
        <div class="user-card">
            <div class="user-name">{esc(user['name'])}</div>
        </div>
        <div class="side-label">Menu</div>
        """,
        unsafe_allow_html=True
    )

    st.radio(
        "Navigation",
        ["Chat", "Dashboard", "Query History"],
        key="page",
        label_visibility="collapsed"
    )

    st.markdown("---")

    if st.button("Clear conversation"):
        st.session_state.messages = []
        st.session_state.last_query = None
        st.rerun()

    if st.button("Sign out"):
        st.session_state.user = None
        reset_user_state()
        st.rerun()

    for name, reason in LOAD_ERRORS.items():
        st.caption(f"Dataset '{name}' is unavailable: {reason}")


page = st.session_state.page


# ============================================================
# CHAT PAGE
# ============================================================

if page == "Chat":

    question = st.chat_input("Type your question here")

    # a question re-run from Query History
    if not question:
        question = st.session_state.pop("pending_question", None)

    if not st.session_state.messages and not question:

        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-title">No conversation yet</div>
                Type a question in the box below to search records,
                compare figures, or review budgets. Mention the data you
                mean (for example sales, payroll or purchase orders).
            </div>
            """,
            unsafe_allow_html=True
        )

    # previous messages (with their tables, charts and explanations)
    for idx, message in enumerate(st.session_state.messages):

        with st.chat_message(message["role"]):

            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_assistant(message, idx)

    if question:

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):

            with st.spinner("Analysing your data..."):
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

    banner("Dashboard")

    options = dataset_options(user)

    if not options:

        st.info("You do not have access to any dataset.")

    else:

        label = st.selectbox("Dataset", list(options), key="dash_dataset")

        key = options[label]
        cfg = DATASETS[key]

        summary = execute_query(make_query("income_expense_summary", key), user)
        budget = execute_query(make_query("budget_vs_actual", key), user)

        if summary.get("error"):
            st.error(summary["message"])

        else:

            m = summary["metrics"]
            b = budget.get("metrics", {})

            variance = b.get("variance", 0)

            c1, c2, c3, c4 = st.columns(4)

            if cfg["type_column"]:

                with c1:
                    kpi("Total Income", format_amount(m["income"]))

                with c2:
                    kpi("Total Expense", format_amount(m["expense"]))

                with c3:
                    kpi(
                        "Net Position",
                        format_amount(m["net"]),
                        tone="pos" if m["net"] >= 0 else "neg",
                        note="Income minus expense"
                    )

                with c4:
                    kpi(
                        "Budget Variance",
                        format_amount(variance),
                        tone="pos" if variance >= 0 else "neg",
                        note="Budget minus actual"
                    )

            else:

                with c1:
                    kpi(
                        cfg["amount_label"],
                        format_amount(b.get("actual", 0)),
                        note=f"{b.get('count', 0):,} {cfg['noun_plural']}"
                    )

                with c2:
                    kpi(cfg["budget_label"], format_amount(b.get("budget", 0)))

                with c3:
                    kpi(
                        "Variance",
                        format_amount(variance),
                        tone="pos" if variance >= 0 else "neg",
                        note=(
                            "Budget minus actual"
                            if cfg["primary_kind"] == "expense"
                            else "Actual minus target"
                        )
                    )

                with c4:
                    kpi("Status", str(b.get("status", "-")))

            section("Breakdown")

            groups = cfg["dashboard_groups"]
            tabs = st.tabs([f"By {g}" for g in groups])

            for tab, group in zip(tabs, groups):

                with tab:

                    result = execute_query(
                        make_query("group_summary", key, group_by=group),
                        user
                    )

                    if result.get("error") or not result.get("table"):
                        st.info(result.get("message") or "No data available.")
                        continue

                    df = to_dataframe(result["table"])

                    chart = result.get("chart")

                    if chart and all(c in df.columns for c in [chart["x"], *chart["y"]]):
                        bar_chart(df, chart["x"], chart["y"])

                    st.dataframe(
                        df,
                        use_container_width=True,
                        hide_index=True
                    )

            variance_result = execute_query(
                make_query("variance", key, group_by=cfg["default_group"]),
                user
            )

            if variance_result.get("table"):

                section(f"Variance by {cfg['default_group']}")

                st.dataframe(
                    to_dataframe(variance_result["table"]),
                    use_container_width=True,
                    hide_index=True
                )


# ============================================================
# QUERY HISTORY
# ============================================================

elif page == "Query History":

    banner("Query History")

    entries = load_history()

    # users only see their own history; Admin sees everyone's
    if user["role"] != "Admin":
        entries = [e for e in entries if e["user"] == user["username"]]

    if not entries:

        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-title">No questions yet</div>
                Your questions will appear here after you ask them.
            </div>
            """,
            unsafe_allow_html=True
        )

    else:

        search = st.text_input("Search history", placeholder="Search by keyword")

        if search:
            entries = [
                e for e in entries
                if search.lower() in e["question"].lower()
            ]

        entries = list(reversed(entries))

        show_user = user["role"] == "Admin"

        head = "<tr><th>Time</th>"
        head += "<th>User</th>" if show_user else ""
        head += "<th>Question</th><th>Dataset</th><th>Type</th><th>Status</th></tr>"

        body = ""

        for e in entries[:200]:

            status = e.get("status", "")

            pill = (
                "ok" if status == "success"
                else "warn" if status == "no results"
                else "bad"
            )

            body += (
                f"<tr><td>{esc(e['time'])}</td>"
                + (f"<td>{esc(e['user'])}</td>" if show_user else "")
                + f"<td>{esc(e['question'])}</td>"
                f"<td>{esc(e.get('dataset') or '')}</td>"
                f"<td>{esc(e.get('operation') or '')}</td>"
                f"<td><span class='pill {pill}'>{esc(status)}</span></td></tr>"
            )

        st.markdown(
            f"""
            <div class="history-wrap">
                <table class="history">{head}{body}</table>
            </div>
            """,
            unsafe_allow_html=True
        )

        section("Ask a previous question again")

        recent = [e["question"] for e in entries[:30]]

        if recent:

            choice = st.selectbox(
                "Choose a question",
                recent,
                label_visibility="collapsed"
            )

            col_a, col_b, _ = st.columns([1, 1, 4])

            with col_a:
                st.button(
                    "Ask again",
                    on_click=go_to_chat_with,
                    args=(choice,)
                )

            with col_b:
                if st.button("Clear my history"):

                    remaining = [
                        e for e in load_history()
                        if e["user"] != user["username"]
                    ]

                    save_history(remaining)
                    st.rerun()
