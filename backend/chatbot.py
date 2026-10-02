"""
chatbot.py
==========
Pipeline:
    question (+ previous query)  ->  understand_question()  ->  structured JSON
    JSON + user                  ->  execute_query()        ->  verified result
    result                       ->  explain_result()       ->  plain-English explanation

The LLM only (a) converts text into a JSON query and (b) optionally rewords
an explanation. All numbers come from data_engine.py, and any LLM explanation
containing a number that is not in the verified result is thrown away.
"""

import os
import re
import json
from datetime import datetime

from dotenv import load_dotenv
from groq import Groq

from backend import data_engine as de


# ============================================================
# GROQ CLIENT
# ============================================================

load_dotenv()

MODEL = "openai/gpt-oss-120b"

_api_key = os.getenv("GROQ_API_KEY")

client = Groq(api_key=_api_key) if _api_key else None


# ============================================================
# CONSTANTS
# ============================================================

SUPPORTED_OPERATIONS = [
    "search",
    "total",
    "average",
    "count",
    "budget_vs_actual",
    "top_expenses",
    "bottom_expenses",
    "income_expense_summary",
    "compare",
    "variance",
    "group_summary",
    "unsupported",
]

FILTER_KEYS = [
    "transaction_id",
    "department",
    "category",
    "transaction_type",
    "payment_status",
    "vendor",
    "currency",
    "start_date",
    "end_date",
    "min_amount",
    "max_amount",
]

GROUP_ALIASES = {
    "department": "Department",
    "category": "Category",
    "month": "Month",
    "vendor": "Vendor / Customer",
    "vendor / customer": "Vendor / Customer",
    "customer": "Vendor / Customer",
    "payment status": "Payment Status",
    "payment_status": "Payment Status",
    "transaction type": "Transaction Type",
    "transaction_type": "Transaction Type",
    "currency": "Currency",
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

PROMPT_TEMPLATE = """
You are the query understanding assistant for AI Pricing Copilot.
Convert the user's question into ONE structured JSON query.

Return JSON only. No explanations. No markdown.
Never calculate or invent numbers. Only choose the operation and the filters.

TODAY'S DATE: __TODAY__

VALID VALUES IN THE DATASET
__VALID_VALUES__
Use these exact spellings. If the user names something that is clearly one of
them, use the exact spelling. If it is not in the lists, output it exactly as
the user wrote it (the system will report it as unknown).

------------------------------------------------------------
OPERATIONS
------------------------------------------------------------
search                  show / list / find / filter transactions
total                   total amount
average                 average transaction amount
count                   how many transactions
budget_vs_actual        budget compared with actual spending
top_expenses            highest / largest / biggest expenses
bottom_expenses         lowest / smallest expenses
income_expense_summary  income and/or expense together, income vs expense
compare                 compare two things (two departments, categories,
                        months, periods, ...)
variance                variance detection: where is spending over/under
                        budget, overspending, budget deviations, anomalies
group_summary           breakdown / split / by department / by category /
                        month-wise / monthly trend
unsupported             anything else

------------------------------------------------------------
FILTERS
------------------------------------------------------------
transaction_id, department, category, transaction_type ("Expense" or
"Income"), payment_status, vendor, currency, start_date, end_date,
min_amount, max_amount.

- "spending", "spent", "expenses", "costs"  => transaction_type "Expense"
- "income", "revenue", "earnings"           => transaction_type "Income"
- "income and expense" together             => transaction_type null
- "above / over / more than X"              => min_amount
- "below / under / less than X"             => max_amount

DATE RULES (always YYYY-MM-DD)
"May 2026"            => start 2026-05-01, end 2026-05-31
"after May 2026"      => start 2026-06-01
"before May 2026"     => end 2026-04-30
"Q1 2026"             => 2026-01-01 to 2026-03-31
"this year" / "last month" / "this month" => work it out from TODAY'S DATE.

------------------------------------------------------------
EXTRA FIELDS
------------------------------------------------------------
top_n        number of rows requested ("top 5", "latest 10"), else null.
sort_by      one of: amount, date, department, category, budget, vendor,
             transaction_id, payment status  (or null)
sort_order   "asc" or "desc" (or null).  "highest/latest" => desc,
             "lowest/oldest" => asc.
group_by     one of: Department, Category, Month, Vendor / Customer,
             Payment Status, Transaction Type, Currency  (or null).
             Used by group_summary, variance and budget_vs_actual.
threshold_pct  variance tolerance in percent (default null = 10).
compare      only for operation "compare":
             {
               "label_a": "short name for side A",
               "filters_a": { only the filters that differ for side A },
               "label_b": "short name for side B",
               "filters_b": { only the filters that differ for side B }
             }
             Filters shared by both sides go in the normal "filters" object.
             "Compare IT and HR spending" => filters.transaction_type
             "Expense", filters_a.department "IT", filters_b.department "HR".

------------------------------------------------------------
FOLLOW-UP QUESTIONS
------------------------------------------------------------
You receive the PREVIOUS QUERY. If the new question depends on it
("what about HR?", "and for June?", "only the top 3", "sort by date",
"now show it by category", "why is that?"), return the FULL merged query:
copy the previous operation and filters, then apply only what changed.
If the new question is independent, ignore the previous query.
Set "follow_up" to true when you used the previous query.

------------------------------------------------------------
OUTPUT FORMAT
------------------------------------------------------------
{
  "operation": "search",
  "filters": {
    "transaction_id": null, "department": null, "category": null,
    "transaction_type": null, "payment_status": null, "vendor": null,
    "currency": null, "start_date": null, "end_date": null,
    "min_amount": null, "max_amount": null
  },
  "top_n": null,
  "sort_by": null,
  "sort_order": null,
  "group_by": null,
  "threshold_pct": null,
  "compare": null,
  "follow_up": false
}

If the question cannot be handled, return:
{"operation": "unsupported", "filters": {}}
"""


def _build_system_prompt():

    valid = []

    for label, column in [
        ("Departments", "Department"),
        ("Categories", "Category"),
        ("Transaction types", "Transaction Type"),
        ("Payment statuses", "Payment Status"),
        ("Currencies", "Currency"),
    ]:
        values = de.get_unique_values(column)
        valid.append(f"{label}: {', '.join(values) if values else '-'}")

    return (
        PROMPT_TEMPLATE
        .replace("__TODAY__", datetime.now().strftime("%Y-%m-%d"))
        .replace("__VALID_VALUES__", "\n".join(valid))
    )


# ============================================================
# QUERY NORMALISATION
# ============================================================

def _empty_filters():

    return {key: None for key in FILTER_KEYS}


def _clean_value(value):

    if isinstance(value, str) and value.strip().lower() in (
        "", "null", "none", "all", "n/a"
    ):
        return None

    return value


def _clean_filters(raw):

    filters = _empty_filters()

    for key, value in (raw or {}).items():
        if key in filters:
            filters[key] = _clean_value(value)

    return filters


def _to_int(value, low=1, high=100):

    try:
        return max(low, min(high, int(float(value))))
    except (TypeError, ValueError):
        return None


def _to_float_or_none(value):

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_query(raw):
    """Makes any LLM output a safe, complete query dict."""

    base = {
        "operation": "unsupported",
        "filters": _empty_filters(),
        "top_n": None,
        "sort_by": None,
        "sort_order": None,
        "group_by": None,
        "threshold_pct": None,
        "compare": None,
        "follow_up": False,
        "error_message": None,
    }

    if not isinstance(raw, dict):
        return base

    if raw.get("operation") == "error":
        base["operation"] = "error"
        base["error_message"] = raw.get("error_message")
        return base

    operation = raw.get("operation")
    base["operation"] = (
        operation if operation in SUPPORTED_OPERATIONS else "unsupported"
    )

    base["filters"] = _clean_filters(raw.get("filters"))
    base["top_n"] = _to_int(raw.get("top_n"))

    sort_by = _clean_value(raw.get("sort_by"))
    if isinstance(sort_by, str) and sort_by.strip().lower() in de.SORT_COLUMNS:
        base["sort_by"] = sort_by.strip().lower()

    sort_order = _clean_value(raw.get("sort_order"))
    if isinstance(sort_order, str) and sort_order.strip().lower() in ("asc", "desc"):
        base["sort_order"] = sort_order.strip().lower()

    group_by = _clean_value(raw.get("group_by"))
    if isinstance(group_by, str):
        base["group_by"] = GROUP_ALIASES.get(group_by.strip().lower())

    threshold = _to_float_or_none(raw.get("threshold_pct"))
    if threshold is not None and threshold >= 0:
        base["threshold_pct"] = threshold

    compare = raw.get("compare")
    if isinstance(compare, dict):
        base["compare"] = {
            "label_a": _clean_value(compare.get("label_a")),
            "label_b": _clean_value(compare.get("label_b")),
            "filters_a": {
                k: v for k, v in _clean_filters(compare.get("filters_a")).items()
                if v is not None
            },
            "filters_b": {
                k: v for k, v in _clean_filters(compare.get("filters_b")).items()
                if v is not None
            },
        }

    base["follow_up"] = bool(raw.get("follow_up"))

    return base


def make_query(operation, **kwargs):
    """Builds a query without the LLM (used by the dashboard)."""

    return normalize_query({"operation": operation, **kwargs})


def _compact_query(query):
    """Shorter version of a query to send as follow-up context."""

    if not query:
        return None

    return {
        "operation": query.get("operation"),
        "filters": {
            k: v for k, v in (query.get("filters") or {}).items()
            if v is not None
        },
        "top_n": query.get("top_n"),
        "sort_by": query.get("sort_by"),
        "sort_order": query.get("sort_order"),
        "group_by": query.get("group_by"),
        "threshold_pct": query.get("threshold_pct"),
        "compare": query.get("compare"),
    }


# ============================================================
# UNDERSTAND USER QUESTION
# ============================================================

def _extract_json(text):

    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")

    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    return None


def understand_question(question, last_query=None):
    """
    Converts a natural-language question into a structured query.
    last_query enables follow-up questions.
    """

    question = (question or "").strip()

    if not question:
        return normalize_query({"operation": "unsupported"})

    if client is None:
        return normalize_query(
            {
                "operation": "error",
                "error_message": (
                    "GROQ_API_KEY is not configured. "
                    "Add it to your .env file and restart the app."
                ),
            }
        )

    previous = (
        json.dumps(_compact_query(last_query), default=str)
        if last_query else "none"
    )

    user_message = (
        f"PREVIOUS QUERY: {previous}\n\n"
        f"QUESTION: {question}"
    )

    last_error = None

    for _ in range(2):                      # one retry

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": _build_system_prompt()},
                    {"role": "user", "content": user_message},
                ],
                temperature=0
            )

            parsed = _extract_json(response.choices[0].message.content)

            if parsed is not None:
                return normalize_query(parsed)

        except Exception as exc:
            last_error = exc

    if last_error is not None:
        return normalize_query(
            {
                "operation": "error",
                "error_message": (
                    "I could not reach the language model "
                    f"({type(last_error).__name__}). Please try again."
                ),
            }
        )

    return normalize_query({"operation": "unsupported"})


# ============================================================
# RESULT HELPERS
# ============================================================

def format_amount(value):

    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value):

    return "n/a" if value is None else f"{value:,.1f}%"


def describe_filters(filters):
    """Readable description of the filters that were applied."""

    f = filters or {}
    parts = []

    if f.get("transaction_id"):
        parts.append(f"transaction ID {f['transaction_id']}")
    if f.get("department"):
        parts.append(f"the {f['department']} department")
    if f.get("category"):
        parts.append(f"the {f['category']} category")
    if f.get("transaction_type"):
        parts.append(f"{f['transaction_type'].lower()} transactions")
    if f.get("payment_status"):
        parts.append(f"payment status {f['payment_status']}")
    if f.get("vendor"):
        parts.append(f"vendor/customer matching '{f['vendor']}'")
    if f.get("currency"):
        parts.append(f"currency {f['currency']}")

    if f.get("start_date") and f.get("end_date"):
        parts.append(f"dates between {f['start_date']} and {f['end_date']}")
    elif f.get("start_date"):
        parts.append(f"dates from {f['start_date']}")
    elif f.get("end_date"):
        parts.append(f"dates up to {f['end_date']}")

    if f.get("min_amount") is not None:
        parts.append(f"amount of at least {format_amount(f['min_amount'])}")
    if f.get("max_amount") is not None:
        parts.append(f"amount of at most {format_amount(f['max_amount'])}")

    return ", ".join(parts)


def _result(operation, **kwargs):

    base = {
        "operation": operation,
        "message": "",
        "data": [],
        "table": [],
        "metrics": {},
        "warnings": [],
        "filters": {},
        "chart": None,
        "empty": False,
        "error": None,
    }

    base.update(kwargs)

    return base


def _error(kind, message, operation="error", **kwargs):

    return _result(operation, error=kind, message=message, **kwargs)


# ============================================================
# EXECUTE QUERY
# ============================================================

def _fetch(filters, allowed, transaction_type=None,
           sort_by=None, sort_order=None, limit=None):

    params = dict(filters)

    if transaction_type:
        params["transaction_type"] = transaction_type

    return de.search_transactions(
        **params,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        allowed_departments=allowed
    )


def _access_problem(filters, user, group_by=None):
    """Returns (allowed_departments, error_message_or_None)."""

    allowed = de.allowed_departments(user)

    department = filters.get("department")

    if allowed is not None and department:
        if department.lower() not in [d.lower() for d in allowed]:
            return allowed, (
                f"You do not have permission to view {department} data. "
                f"Your access is limited to: {', '.join(allowed)}."
            )

    hidden = (user or {}).get("hidden_columns") or []

    if group_by and group_by in hidden:
        return allowed, (
            f"You do not have permission to view '{group_by}' information."
        )

    return allowed, None


def _extra_warnings(rows, filters):

    warnings = []

    currencies = {
        r.get("Currency") for r in rows if r.get("Currency")
    }

    if len(currencies) > 1 and not filters.get("currency"):
        warnings.append(
            "The data contains several currencies "
            f"({', '.join(sorted(currencies))}). Amounts are added as they "
            "are, without currency conversion."
        )

    return warnings


def execute_query(query, user=None):
    """
    Executes the structured query. Never raises: every problem is returned
    as a result with result["error"] set and a friendly result["message"].
    """

    query = normalize_query(query)

    try:
        result = _execute(query, user)
    except ValueError as exc:
        result = _error("validation", str(exc), query["operation"])
    except Exception as exc:
        result = _error(
            "internal",
            "Something went wrong while processing your question. "
            "Please try rephrasing it.",
            query["operation"],
            detail=f"{type(exc).__name__}: {exc}"
        )

    if not result.get("error"):
        result["message"] = build_answer(query, result)

    return result


def _execute(query, user):

    operation = query["operation"]

    if operation == "error":
        return _error(
            "llm_error",
            query.get("error_message")
            or "The question could not be processed.",
        )

    if operation == "unsupported":
        return _error(
            "unsupported",
            "Sorry, I cannot handle this type of question yet. "
            "Try asking about transactions, totals, top expenses, "
            "budget vs actual, comparisons or breakdowns.",
            "unsupported"
        )

    # ---------- validate filters against real data ----------
    filters, warnings, errors = de.validate_filters(query["filters"])

    if errors:
        return _error("validation", " ".join(errors), operation)

    group_by = query["group_by"]

    allowed, denied = _access_problem(filters, user, group_by)

    if denied:
        return _error("access_denied", denied, operation)

    hidden_check = lambda rows: de.sanitize_rows(rows, user)

    top_n = query["top_n"]
    sort_by = query["sort_by"]
    sort_order = query["sort_order"]
    threshold = query["threshold_pct"] or 10.0

    # =========================================================
    # SEARCH
    # =========================================================
    if operation == "search":

        matched = _fetch(filters, allowed, sort_by=sort_by,
                         sort_order=sort_order)
        shown = matched[:top_n] if top_n else matched

        warnings += _extra_warnings(matched, filters)

        return _result(
            operation,
            data=hidden_check(shown),
            metrics={"matched": len(matched), "shown": len(shown)},
            filters=filters,
            warnings=warnings,
            empty=not matched
        )

    # =========================================================
    # TOTAL / AVERAGE / COUNT
    # =========================================================
    if operation in ("total", "average", "count"):

        rows = _fetch(filters, allowed, sort_by=sort_by,
                      sort_order=sort_order)

        types = {str(r.get("Transaction Type", "")).lower() for r in rows}

        if (
            not filters.get("transaction_type")
            and {"income", "expense"} <= types
            and operation != "count"
        ):
            warnings.append(
                "This includes both income and expense transactions. "
                "Ask for 'total spending' or 'total income' to separate them."
            )

        warnings += _extra_warnings(rows, filters)

        metrics = {"count": len(rows)}

        if operation == "total":
            metrics["total"] = de.calculate_total(rows)
        elif operation == "average":
            metrics["average"] = de.calculate_average(rows)

        return _result(
            operation,
            data=hidden_check(rows) if operation != "count" else [],
            metrics=metrics,
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # TOP / BOTTOM EXPENSES
    # =========================================================
    if operation in ("top_expenses", "bottom_expenses"):

        n = top_n or 5

        rows = _fetch(filters, allowed, transaction_type="Expense")
        filters["transaction_type"] = "Expense"

        picked = (
            de.get_top_expenses(rows, n)
            if operation == "top_expenses"
            else de.get_bottom_expenses(rows, n)
        )

        all_total = de.calculate_total(rows)
        picked_total = de.calculate_total(picked)

        warnings += _extra_warnings(rows, filters)

        return _result(
            operation,
            data=hidden_check(picked),
            metrics={
                "n": len(picked),
                "picked_total": picked_total,
                "all_expense_total": all_total,
                "share_pct": (picked_total / all_total * 100) if all_total else None,
                "expense_count": len(rows),
            },
            filters=filters,
            warnings=warnings,
            empty=not picked
        )

    # =========================================================
    # INCOME & EXPENSE SUMMARY
    # =========================================================
    if operation == "income_expense_summary":

        rows = _fetch(filters, allowed)
        summary = de.income_expense_summary(rows)

        warnings += _extra_warnings(rows, filters)

        return _result(
            operation,
            data=hidden_check(rows),
            metrics=summary | {"count": len(rows)},
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # BUDGET VS ACTUAL
    # =========================================================
    if operation == "budget_vs_actual":

        rows = _fetch(filters, allowed, transaction_type="Expense")
        filters["transaction_type"] = "Expense"

        summary = de.budget_vs_actual(rows, threshold)
        summary["count"] = len(rows)

        table = []
        chart = None

        if group_by:
            table = de.detect_variances(rows, group_by, threshold)
            chart = {"x": group_by, "y": ["Budget", "Actual"]}

        warnings += _extra_warnings(rows, filters)

        return _result(
            operation,
            data=hidden_check(rows),
            table=table,
            chart=chart,
            metrics=summary,
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # VARIANCE DETECTION
    # =========================================================
    if operation == "variance":

        group_by = group_by or "Category"

        rows = _fetch(filters, allowed, transaction_type="Expense")
        filters["transaction_type"] = "Expense"

        table = de.detect_variances(rows, group_by, threshold)

        over = [r for r in table if r["Status"] == "Over budget"]
        under = [r for r in table if r["Status"] == "Under budget"]

        warnings += _extra_warnings(rows, filters)

        return _result(
            operation,
            table=table,
            chart={"x": group_by, "y": ["Budget", "Actual"]},
            metrics={
                "group_by": group_by,
                "threshold_pct": threshold,
                "groups": len(table),
                "over": over,
                "under": under,
                "overall": de.budget_vs_actual(rows, threshold),
            },
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # GROUP SUMMARY
    # =========================================================
    if operation == "group_summary":

        group_by = group_by or "Category"

        rows = _fetch(filters, allowed)
        table = de.group_summary(rows, group_by)

        warnings += _extra_warnings(rows, filters)

        return _result(
            operation,
            table=table,
            chart={"x": group_by, "y": ["Income", "Expense"]},
            metrics={
                "group_by": group_by,
                "groups": len(table),
                "count": len(rows),
            },
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # COMPARE
    # =========================================================
    if operation == "compare":

        compare = query["compare"]

        if (
            not compare
            or not compare["filters_a"]
            or not compare["filters_b"]
        ):
            return _error(
                "validation",
                "Please tell me what to compare, for example "
                "'Compare IT and HR spending' or "
                "'Compare May 2026 and June 2026'.",
                operation
            )

        sides = []

        for key in ("a", "b"):

            merged = {**query["filters"], **compare[f"filters_{key}"]}

            clean, side_warnings, side_errors = de.validate_filters(merged)

            if side_errors:
                return _error("validation", " ".join(side_errors), operation)

            side_allowed, denied = _access_problem(clean, user)

            if denied:
                return _error("access_denied", denied, operation)

            warnings += side_warnings

            rows = _fetch(clean, side_allowed)

            label = (
                compare.get(f"label_{key}")
                or describe_filters(compare[f"filters_{key}"])
                or key.upper()
            )

            sides.append((label, clean, rows))

        (label_a, filters_a, rows_a), (label_b, filters_b, rows_b) = sides

        if label_a == label_b:
            label_a, label_b = f"{label_a} (A)", f"{label_b} (B)"

        comparison = de.compare_datasets(rows_a, rows_b, label_a, label_b)

        warnings += _extra_warnings(rows_a + rows_b, {})

        return _result(
            operation,
            table=comparison["table"],
            metrics={
                "label_a": label_a,
                "label_b": label_b,
                "a": comparison["a"],
                "b": comparison["b"],
                "filters_a": filters_a,
                "filters_b": filters_b,
            },
            filters=filters,
            warnings=warnings,
            empty=not (rows_a or rows_b)
        )

    return _error("unsupported", "Unsupported operation.", operation)


# ============================================================
# ANSWER (deterministic, built only from verified numbers)
# ============================================================

def build_answer(query, result):

    if result.get("error"):
        return result.get("message", "")

    operation = result["operation"]
    m = result.get("metrics", {})
    scope = describe_filters(result.get("filters"))
    scope_text = f" for {scope}" if scope else ""

    if result.get("empty"):
        return (
            f"I could not find any transactions{scope_text}. "
            "Try widening the filters or the date range."
        )

    if operation == "search":

        text = (
            f"I found {m['matched']} matching "
            f"transaction{'s' if m['matched'] != 1 else ''}"
        )

        if m["shown"] < m["matched"]:
            text += f" and I am showing {m['shown']} of them"

        return text + "."

    if operation == "total":
        return (
            f"The total amount{scope_text} is {format_amount(m['total'])} "
            f"across {m['count']} transactions."
        )

    if operation == "average":
        return (
            f"The average transaction amount{scope_text} is "
            f"{format_amount(m['average'])} across {m['count']} transactions."
        )

    if operation == "count":
        return (
            f"There {'is' if m['count'] == 1 else 'are'} {m['count']} "
            f"transaction{'s' if m['count'] != 1 else ''}{scope_text}."
        )

    if operation in ("top_expenses", "bottom_expenses"):
        word = "highest" if operation == "top_expenses" else "lowest"
        return (
            f"Here are the {m['n']} {word} expenses{scope_text}, "
            f"totalling {format_amount(m['picked_total'])}."
        )

    if operation == "income_expense_summary":
        return (
            f"Total income{scope_text} is {format_amount(m['income'])} and "
            f"total expense is {format_amount(m['expense'])}. "
            f"Net: {format_amount(m['net'])}."
        )

    if operation == "budget_vs_actual":

        budget, actual = m["budget"], m["actual"]

        if actual > budget:
            gap = f"{format_amount(actual - budget)} higher than"
        elif actual < budget:
            gap = f"{format_amount(budget - actual)} lower than"
        else:
            gap = "equal to"

        return (
            f"The budget{scope_text} is {format_amount(budget)} and actual "
            f"spending is {format_amount(actual)}, which is {gap} the budget "
            f"(status: {m['status']})."
        )

    if operation == "variance":

        over, under = m["over"], m["under"]
        group = m["group_by"]

        text = (
            f"I checked {m['groups']} {group.lower()} groups against a "
            f"±{m['threshold_pct']:g}% tolerance: {len(over)} over budget, "
            f"{len(under)} under budget."
        )

        if over:
            worst = over[0]
            text += (
                f" The largest overspend is {worst[group]} "
                f"({_fmt_pct(worst['Variance %'])})."
            )

        return text

    if operation == "group_summary":

        group = m["group_by"]
        table = result["table"]
        top = max(table, key=lambda r: r["Expense"])

        return (
            f"Here is the breakdown by {group.lower()}{scope_text} "
            f"({m['groups']} groups). Highest expense: {top[group]} "
            f"at {format_amount(top['Expense'])}."
        )

    if operation == "compare":

        a, b = m["a"], m["b"]
        la, lb = m["label_a"], m["label_b"]

        diff = b["expense"] - a["expense"]

        if diff > 0:
            verdict = f"{lb} spent {format_amount(diff)} more than {la}."
        elif diff < 0:
            verdict = f"{lb} spent {format_amount(abs(diff))} less than {la}."
        else:
            verdict = f"{la} and {lb} have the same expense."

        return (
            f"{la}: income {format_amount(a['income'])}, "
            f"expense {format_amount(a['expense'])}. "
            f"{lb}: income {format_amount(b['income'])}, "
            f"expense {format_amount(b['expense'])}. {verdict}"
        )

    return "I found the requested information."


# ============================================================
# RESULT EXPLANATION
# ============================================================

def _rule_based_explanation(query, result):

    if result.get("error"):
        return ""

    operation = result["operation"]
    m = result.get("metrics", {})
    scope = describe_filters(result.get("filters"))

    how = (
        f"I filtered the transaction data using {scope}"
        if scope else
        "I used all the transactions you have access to"
    )

    if result.get("empty"):
        return (
            f"{how}, but no records matched. Check the spelling of the "
            "names or try a wider date range."
        )

    if operation == "search":
        return f"{how}, which left {m['matched']} transactions. See the table below."

    if operation == "total":
        return (
            f"{how} and added up the Amount column of the "
            f"{m['count']} matching transactions."
        )

    if operation == "average":
        return (
            f"{how} and divided the total amount by the "
            f"{m['count']} matching transactions."
        )

    if operation == "count":
        return f"{how} and counted the matching transactions."

    if operation in ("top_expenses", "bottom_expenses"):
        share = m.get("share_pct")
        extra = (
            f" Together they are {share:.1f}% of all "
            f"{m['expense_count']} expenses in this selection."
            if share is not None else ""
        )
        return (
            f"{how}, kept only expense transactions and sorted them by "
            f"amount.{extra}"
        )

    if operation == "income_expense_summary":
        if m["net"] > 0:
            tail = (
                f"Income is higher than expense by {format_amount(m['net'])}."
            )
        elif m["net"] < 0:
            tail = (
                f"Expense is higher than income by "
                f"{format_amount(abs(m['net']))}."
            )
        else:
            tail = "Income and expense are equal."
        return f"{how} and added income and expense separately. {tail}"

    if operation == "budget_vs_actual":
        pct = _fmt_pct(m.get("variance_pct"))
        return (
            f"{how}, then compared the Budget column with the Amount column "
            f"of expense transactions. Variance is budget minus actual "
            f"({format_amount(m['variance'])}, {pct} of budget); "
            f"a negative value means overspending."
        )

    if operation == "variance":
        return (
            f"For each {m['group_by'].lower()}, I compared budget with actual "
            f"spending and flagged those that differ by more than "
            f"{m['threshold_pct']:g}%. Negative variance means overspending."
        )

    if operation == "group_summary":
        return (
            f"{how} and grouped the transactions by {m['group_by'].lower()}, "
            "adding income and expense inside each group."
        )

    if operation == "compare":
        pct = next(
            (r["% Change"] for r in result["table"] if r["Metric"] == "Expense"),
            None
        )
        return (
            f"I calculated the same figures for {m['label_a']} and "
            f"{m['label_b']}. Difference is {m['label_b']} minus "
            f"{m['label_a']}; the expense changed by {_fmt_pct(pct)}."
        )

    return "The result is based on the verified transaction data."


# ---- optional LLM wording, protected by a number check ----

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text):

    values = []

    for token in _NUMBER.findall(text or ""):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue

    return values


def _numbers_are_grounded(text, facts):

    known = _numbers(facts)

    for number in _numbers(text):

        if number <= 12:                    # months, small counts
            continue

        if not any(
            abs(number - k) <= max(0.011, abs(k) * 0.0005)
            for k in known
        ):
            return False

    return True


def _facts_text(query, result):

    facts = {
        "operation": result["operation"],
        "filters": {k: v for k, v in result.get("filters", {}).items() if v},
        "answer": result.get("message"),
        "metrics": result.get("metrics"),
        "table": result.get("table", [])[:15],
        "rows": [
            {
                k: v for k, v in row.items()
                if k in (
                    "Transaction ID", "Department", "Category",
                    "Amount", "Budget", "Transaction Date"
                )
            }
            for row in result.get("data", [])[:10]
        ],
    }

    return json.dumps(facts, default=str)[:6000]


def _llm_explanation(question, query, result):

    if client is None:
        return None

    facts = _facts_text(query, result)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You explain financial query results in simple English "
                    "in 2-3 short sentences. Use ONLY the facts provided. "
                    "Do not add, estimate or round any number, and do not "
                    "mention anything that is not in the facts. Point out "
                    "what stands out (largest item, over/under budget, "
                    "higher/lower side). No markdown."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nFacts: {facts}",
            },
        ],
        temperature=0
    )

    text = (response.choices[0].message.content or "").strip()

    if text and _numbers_are_grounded(text, facts):
        return text

    return None


def explain_result(question, query, result, use_llm=True):
    """Plain-English explanation of how the answer was produced."""

    if result.get("error"):
        return ""

    if use_llm and not result.get("empty"):
        try:
            text = _llm_explanation(question, query, result)
            if text:
                return text
        except Exception:
            pass                            # fall back silently

    return _rule_based_explanation(query, result)
