"""
chatbot.py
==========
Pipeline:
    question (+ previous query + chosen dataset)
        ->  understand_question()  ->  structured JSON (incl. which dataset)
    JSON + user
        ->  execute_query()        ->  verified result
    result
        ->  explain_result()       ->  plain-English explanation

Four datasets are supported: transactions, procurement, payroll and sales.

The optional LLM converts text into a JSON query. SQL calculations and factual answers are produced locally. With Groq enabled,
selected query metadata and minimized aggregate results may be sent to Groq
for interpretation and optional advisory text. Raw SQL rows are not sent by
the advisory function.

Product principles followed here:
    accuracy over fluency, transparency by default, conversational follow-ups,
    security and access control, simplicity, consistency across users.
"""

import os
import re
import json
import copy
import logging
import calendar
from datetime import datetime
from decimal import Decimal, InvalidOperation
from backend import business_explanations as be
from backend import financial_language as fl

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                      # .env support is optional
    pass

try:
    from groq import Groq
except ImportError:                      # the app still starts without it
    Groq = None

from backend import data_engine as de
from backend import database

logger = logging.getLogger(__name__)


# ============================================================
# GROQ CLIENT
# ============================================================

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Opt-in advisory sends a bounded subset of aggregate metrics to Groq.
GROQ_ADVISORY_ENABLED = os.getenv("GROQ_ADVISORY_ENABLED", "true").lower() in {"1", "true", "yes"}

_api_key = os.getenv("GROQ_API_KEY")

client = Groq(api_key=_api_key) if (_api_key and Groq) else None


# ============================================================
# CONSTANTS
# ============================================================

SUPPORTED_OPERATIONS = [
    "search",
    "dataset_summary",
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
    "period_comparison",
    "period_total",
    "unsupported",
]

SCALAR_FILTER_KEYS = [
    "record_id",
    "start_date",
    "end_date",
    "min_amount",
    "max_amount",
]

DATASET_ALIASES = {
    "transaction": "transactions",
    "finance": "transactions",
    "procurement_orders": "procurement",
    "purchase": "procurement",
    "purchase_orders": "procurement",
    "hr": "payroll",
    "hr_payroll": "payroll",
    "sales_orders": "sales",
}


# ============================================================
# SYSTEM PROMPT
# ============================================================

PROMPT_TEMPLATE = """
You are the query understanding assistant for AI Pricing Copilot.
Convert the user's question into ONE structured JSON query.

Return JSON only. No explanations. No markdown.
Never calculate or invent numbers. Only choose the dataset, the operation
and the filters.

TODAY'S DATE: __TODAY__

------------------------------------------------------------
DATASETS (choose exactly one)
------------------------------------------------------------
__DATASETS__

Pick the dataset from the topic of the question:
  purchase orders, suppliers, items, warehouses, delivery, delays => procurement
  employees, salary, payroll, bonus, deductions, net pay          => payroll
  sales orders, customers, regions, sales reps, products, discounts => sales
  general income / expenses, departments, categories, vendors,
  payment status, budgets                                         => transactions
If the dataset cannot be worked out from the question or the previous
query, set "dataset" to null. Never guess. A question that needs two
datasets at once (for example "compare sales and payroll") is "unsupported".

Use the exact column names and exact spellings listed above. If the user
names something that is clearly one of the listed values, use the exact
spelling. If it is not in the lists, output it exactly as the user wrote it
(the system will report it as unknown).

------------------------------------------------------------
OPERATIONS
------------------------------------------------------------
dataset_summary         overview / summarize an entire dataset
search                  show / list / find / filter records
total                   total of a column (default: the main amount)
average                 average of a column
count                   how many records
budget_vs_actual        budget (or target) compared with actual
top_expenses            highest / largest / biggest records (for sales: the
                        highest orders; for payroll: highest paid)
bottom_expenses         lowest / smallest records
income_expense_summary  income and expense together (transactions); for the
                        other datasets it gives the overall total
compare                 compare two things (two departments, suppliers,
                        regions, months, periods, ...)
variance                variance detection: where is spending over budget
                        (or sales below target), deviations, anomalies
group_summary           breakdown / split / "by ..." / month-wise / trend
unsupported             anything else

------------------------------------------------------------
FILTERS
------------------------------------------------------------
"filters" has this shape (use only what the question needs):
{
  "equals":   {"<Filter column>": "value" or ["value1", "value2"]},
  "contains": {"<Text column>": "partial text"},
  "exclude":  {"<Filter column>": "value" or ["value1", ...]},
  "record_id": null, "start_date": null, "end_date": null,
  "min_amount": null, "max_amount": null
}
- equals    : exact value of a "Filter column" (a list means "either of").
- contains  : partial text of a "Text column" (names, vendors, designations).
- exclude   : leave rows out ("excluding cancelled orders" =>
              exclude {"Order Status": ["Cancelled"]}).
- record_id : one specific ID (transaction, PO, payroll or order ID).
- min_amount / max_amount apply to the dataset's MAIN AMOUNT column.
  "above / over / more than X" => min_amount,
  "below / under / less than X" => max_amount.
- Transactions only: "spending", "spent", "expenses", "costs" =>
  equals {"Transaction Type": "Expense"}; "income", "revenue", "earnings" =>
  equals {"Transaction Type": "Income"}; both together => no type filter.

DATE RULES (always YYYY-MM-DD, applied to the dataset's date column)
"May 2026"            => start 2026-05-01, end 2026-05-31
"after May 2026"      => start 2026-06-01
"before May 2026"     => end 2026-04-30
"Q1 2026"             => 2026-01-01 to 2026-03-31
"this year" / "last month" / "this month" => work it out from TODAY'S DATE.

------------------------------------------------------------
EXTRA FIELDS
------------------------------------------------------------
measure      a numeric column from the dataset's "Measures" list to total,
             average or rank, for example "Net Pay", "Qty Ordered",
             "Delay Days", "Quantity". Null = the main amount.
top_n        number of rows requested ("top 5", "latest 10"), else null.
sort_by      "amount", "date", "budget" or any column name (or null).
sort_order   "asc" or "desc" (or null).  "highest/latest" => desc,
             "lowest/oldest" => asc.
group_by     a column from the dataset's "Group-by columns" list, or "Month"
             (or null). Used by group_summary, variance and budget_vs_actual.
threshold_pct  variance tolerance in percent (default null = 10).
compare      only for operation "compare":
             {
               "label_a": "short name for side A",
               "filters_a": { only the filters that differ for side A },
               "label_b": "short name for side B",
               "filters_b": { only the filters that differ for side B }
             }
             Filters shared by both sides go in the normal "filters" object.
             "Compare IT and HR spending" (transactions) =>
               filters.equals {"Transaction Type": "Expense"},
               filters_a.equals {"Department": "IT"},
               filters_b.equals {"Department": "HR"}.

------------------------------------------------------------
FOLLOW-UP QUESTIONS
------------------------------------------------------------
You receive the PREVIOUS QUERY. If the new question depends on it
("what about HR?", "and for June?", "only the top 3", "sort by date",
"now show it by category", "why is that?"), return the FULL merged query:
copy the previous dataset, operation and filters, then apply only what
changed. If the new question is independent, ignore the previous query.
Set "follow_up" to true when you used the previous query.
If a SELECTED DATASET is given, always use it.

------------------------------------------------------------
OUTPUT FORMAT
------------------------------------------------------------
{
  "dataset": "transactions",
  "operation": "search",
  "filters": {
    "equals": {}, "contains": {}, "exclude": {},
    "record_id": null, "start_date": null, "end_date": null,
    "min_amount": null, "max_amount": null
  },
  "measure": null,
  "top_n": null,
  "sort_by": null,
  "sort_order": null,
  "group_by": null,
  "threshold_pct": null,
  "compare": null,
  "follow_up": false
}

If the question cannot be handled, return:
{"dataset": null, "operation": "unsupported", "filters": {}}
"""


def _dataset_block(key, user):

    cfg = de.DATASETS[key]
    hidden = de.hidden_columns(user)

    def visible(columns):
        return [c for c in columns if de._norm(c) not in hidden]

    lines = [
        f'DATASET "{key}" - {cfg["label"]}',
        f'  Holds: {cfg["description"]}',
        f'  One row = {cfg["row_meaning"]}.',
        f'  Main amount: {cfg["amount"]}   '
        f'Budget column: {cfg["budget"]}   Date column: {cfg["date"]}',
    ]

    lines.append("  Filter columns: " + ", ".join(visible(cfg["filter_columns"])))

    text_columns = visible(cfg["text_columns"])
    lines.append(
        "  Text columns: " + (", ".join(text_columns) if text_columns else "none")
    )
    lines.append(
        "  Group-by columns: "
        + ", ".join(visible(cfg["group_columns"]) + ["Month"])
    )
    lines.append("  Measures: " + ", ".join(visible(cfg["measures"])))

    return "\n".join(lines)


def _build_system_prompt(user=None):

    keys = de.allowed_datasets(user)

    return (
        PROMPT_TEMPLATE
        .replace("__TODAY__", datetime.now().strftime("%Y-%m-%d"))
        .replace("__DATASETS__", "\n\n".join(_dataset_block(k, user) for k in keys))
    )


# ============================================================
# QUERY NORMALISATION
# ============================================================

def _empty_filters():

    return {
        "equals": {},
        "contains": {},
        "exclude": {},
        "record_id": None,
        "start_date": None,
        "end_date": None,
        "min_amount": None,
        "max_amount": None,
    }


def _clean_value(value):

    if isinstance(value, str) and value.strip().lower() in (
        "", "null", "none", "all", "n/a"
    ):
        return None

    return value


def _clean_list(value):
    """A value or list of values -> list of clean strings."""

    items = value if isinstance(value, (list, tuple)) else [value]

    out = []

    for item in items:
        item = _clean_value(item)
        if item is not None and str(item).strip():
            out.append(str(item).strip())

    return out


def _clean_filters(raw):

    filters = _empty_filters()

    raw = raw if isinstance(raw, dict) else {}

    for group in ("equals", "contains", "exclude"):

        source = raw.get(group)

        if not isinstance(source, dict):
            continue

        for column, value in source.items():

            values = _clean_list(value)

            if not values:
                continue

            filters[group][str(column).strip()] = (
                values[0] if group == "contains" else values
            )

    for key in SCALAR_FILTER_KEYS:
        filters[key] = _clean_value(raw.get(key))

    return filters


def _compact_filters(filters):
    """Only the parts of a filter dict that are actually set."""

    return {
        k: v for k, v in filters.items()
        if v not in (None, {}, [])
    }


def _merge_filters(base, override):
    """Applies 'override' (side A / side B filters) on top of 'base'."""

    merged = copy.deepcopy(base)

    for group in ("equals", "contains", "exclude"):
        merged[group].update(override.get(group) or {})

    for key in SCALAR_FILTER_KEYS:
        if override.get(key) is not None:
            merged[key] = override[key]

    return merged


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


def _clean_dataset(value):

    value = _clean_value(value)

    if not isinstance(value, str):
        return None

    key = value.strip().lower().replace(" ", "_")
    key = DATASET_ALIASES.get(key, key)

    return key if key in de.DATASETS else None


def normalize_query(raw):
    """Makes any LLM output a safe, complete query dict."""

    base = {
        "dataset": None,
        "operation": "unsupported",
        "filters": _empty_filters(),
        "measure": None,
        "top_n": None,
        "sort_by": None,
        "sort_order": None,
        "group_by": None,
        "threshold_pct": None,
        "compare": None,
        "follow_up": False,
        "error_message": None,
        "period_type": None,
        "current_period": None,
        "previous_period": None,
        "period": None,
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

    key = _clean_dataset(raw.get("dataset"))
    base["dataset"] = key

    base["filters"] = _clean_filters(raw.get("filters"))
    base["top_n"] = _to_int(raw.get("top_n"))

    # dataset-dependent fields can only be checked once the dataset is known
    if key:

        sort_by = _clean_value(raw.get("sort_by"))
        if isinstance(sort_by, str):
            base["sort_by"] = de.resolve_sort_column(key, sort_by)

        group_by = _clean_value(raw.get("group_by"))
        if isinstance(group_by, str):
            base["group_by"] = de.resolve_group_column(key, group_by)

        measure = _clean_value(raw.get("measure"))
        if isinstance(measure, str):
            base["measure"] = de.resolve_measure(key, measure)

    sort_order = _clean_value(raw.get("sort_order"))
    if isinstance(sort_order, str) and sort_order.strip().lower() in ("asc", "desc"):
        base["sort_order"] = sort_order.strip().lower()

    threshold = _to_float_or_none(raw.get("threshold_pct"))
    if threshold is not None and threshold >= 0:
        base["threshold_pct"] = threshold

    compare = raw.get("compare")
    if isinstance(compare, dict):
        base["compare"] = {
            "label_a": _clean_value(compare.get("label_a")),
            "label_b": _clean_value(compare.get("label_b")),
            "filters_a": _compact_filters(_clean_filters(compare.get("filters_a"))),
            "filters_b": _compact_filters(_clean_filters(compare.get("filters_b"))),
        }

    # Period ranges are resolved by financial_language, never by an LLM.
    # Preserve them through normalization for execute_query().
    if base["operation"] in {"period_comparison", "period_total"}:
        valid_types = {"mom", "dod", "yoy", "qoq", "ytd", "mtd", "qtd"}
        period_type = raw.get("period_type")
        if period_type in valid_types:
            base["period_type"] = period_type
        for field in ("current_period", "previous_period", "period"):
            value = raw.get(field)
            if isinstance(value, dict):
                base[field] = {
                    "start_date": value.get("start_date"),
                    "end_date": value.get("end_date"),
                }

    base["follow_up"] = bool(raw.get("follow_up"))

    return base


def make_query(operation, dataset=None, **kwargs):
    """Builds a query without the LLM (used by the dashboard)."""

    return normalize_query({"operation": operation, "dataset": dataset, **kwargs})


def _compact_query(query):
    """Shorter version of a query to send as follow-up context."""

    if not query:
        return None

    return {
        "dataset": query.get("dataset"),
        "operation": query.get("operation"),
        "filters": _compact_filters(query.get("filters") or {}),
        "measure": query.get("measure"),
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


def _resolve_dataset(parsed, last_query, forced, user):
    """Decides the dataset: sidebar choice > LLM > previous query > only option."""

    if forced:
        parsed["dataset"] = forced
        return

    if _clean_dataset(parsed.get("dataset")):
        return

    usable = de.allowed_datasets(user)

    if parsed.get("follow_up") and last_query and last_query.get("dataset"):
        parsed["dataset"] = last_query["dataset"]
    elif len(usable) == 1:
        parsed["dataset"] = usable[0]


def _fallback_understand_question(question, last_query=None, dataset=None, user=None):
    """Handle common questions deterministically when the LLM is unavailable."""

    text = question.lower()
    usable = de.allowed_datasets(user)
    key = _clean_dataset(dataset)

    if key is None:
        dataset_terms = {
            "transactions": ("transaction", "expense", "spending", "income",
                             "finance", "budget", "category", "vendor"),
            "procurement": ("procurement", "purchase order", "supplier",
                            "warehouse", "delivery", "purchase"),
            "payroll": ("payroll", "salary", "employee", "net pay", "gross pay"),
            "sales": ("sales order", "sales", "customer", "sales rep", "product"),
        }
        for candidate, terms in dataset_terms.items():
            if candidate in usable and any(term in text for term in terms):
                key = candidate
                break

    is_follow_up = bool(
        re.search(r"\b(it|that|those|same|previous|last answer)\b", text)
    )
    if key is None and is_follow_up and last_query:
        previous_dataset = _clean_dataset(last_query.get("dataset"))
        if previous_dataset in usable:
            key = previous_dataset
    if key is None and len(usable) == 1:
        key = usable[0]
    if key not in usable:
        return normalize_query({"operation": "unsupported"})

    cfg = de.DATASETS[key]
    filters = _empty_filters()

    if key == "transactions":
        if re.search(r"\b(expenses?|spending|spent|costs?)\b", text):
            filters["equals"][cfg["type_column"]] = ["Expense"]
        elif re.search(r"\b(income|revenue|earnings)\b", text):
            filters["equals"][cfg["type_column"]] = ["Income"]

    now = datetime.now()
    if "last month" in text:
        month = now.month - 1 or 12
        year = now.year - (1 if now.month == 1 else 0)
        filters["start_date"] = f"{year:04d}-{month:02d}-01"
        filters["end_date"] = (
            f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        )
    elif "this month" in text:
        filters["start_date"] = now.replace(day=1).strftime("%Y-%m-%d")
        filters["end_date"] = now.strftime("%Y-%m-%d")
    elif "this year" in text:
        filters["start_date"] = f"{now.year}-01-01"
        filters["end_date"] = now.strftime("%Y-%m-%d")
    else:
        month_match = re.search(
            r"\b(january|february|march|april|may|june|july|august|"
            r"september|october|november|december)\s+(\d{4})\b",
            text,
        )
        if month_match:
            month = datetime.strptime(month_match.group(1), "%B").month
            year = int(month_match.group(2))
            filters["start_date"] = f"{year:04d}-{month:02d}-01"
            filters["end_date"] = (
                f"{year:04d}-{month:02d}-"
                f"{calendar.monthrange(year, month)[1]:02d}"
            )

    if "budget" in text and any(
        phrase in text for phrase in ("actual", "variance", "vs", "versus")
    ):
        operation = "budget_vs_actual"
    elif "income and expense" in text or "income vs expense" in text:
        operation = "income_expense_summary"
    elif re.search(r"\b(summarize|summarise|summary|overview)\b", text):
        operation = "dataset_summary"
    elif re.search(r"\b(average|avg)\b", text):
        operation = "average"
    elif re.search(r"\b(count|how many|number of)\b", text):
        operation = "count"
    elif re.search(r"\b(top|highest|largest|biggest)\b", text):
        operation = "top_expenses"
    elif re.search(r"\b(bottom|lowest|smallest)\b", text):
        operation = "bottom_expenses"
    elif re.search(r"\b(compare|comparison)\b", text):
        return normalize_query({"operation": "unsupported"})
    elif re.search(r"\b(breakdown|group|by|trend|month-wise)\b", text):
        operation = "group_summary"
    elif re.search(r"\b(total|sum|spent|spending|expenses?|income|revenue)\b", text):
        operation = "total"
    elif re.search(r"\b(show|list|find|search|which|what|records?)\b", text):
        operation = "search"
    else:
        return normalize_query({"operation": "unsupported"})

    group_by = None
    for column in cfg["group_columns"]:
        if column.lower() in text:
            group_by = column
            break
    if group_by is None and "month" in text:
        group_by = "Month"

    top_match = re.search(r"\b(?:top|bottom|highest|lowest)\s+(\d+)\b", text)
    return normalize_query({
        "dataset": key,
        "operation": operation,
        "filters": filters,
        "group_by": group_by,
        "top_n": top_match.group(1) if top_match else None,
        "follow_up": is_follow_up,
    })


def understand_question(question, last_query=None, dataset=None, user=None):
    """
    Converts a natural-language question into a structured query.

    last_query : previous query, enables follow-up questions.
    dataset    : optional - forces one dataset (the Streamlit app passes None
                 so the question decides), or None to let the question decide.
    user       : the signed-in user, so the prompt only lists the datasets
                 and values that user is allowed to see.
    """

    question = (question or "").strip()
    # Detect time-period questions BEFORE asking the LLM to select
    # an ordinary total/compare operation.
    period_spec = fl.resolve_financial_period(question)
    question = fl.normalize_financial_terms(question)

    if period_spec:
        forced = _clean_dataset(dataset)
        if forced is None:
            # Match explicit dataset mentions before using previous context.
            hints = {
                "sales": r"\b(sales|revenue|orders?|customers?)\b",
                "procurement": r"\b(procurement|purchase orders?|suppliers?)\b",
                "payroll": r"\b(payroll|salaries|salary|gross pay|net pay)\b",
                "transactions": r"\b(transactions?|general expenses?)\b",
            }
            matches = [k for k, pattern in hints.items() if re.search(pattern, question, re.I)]
            if len(matches) == 1:
                forced = matches[0]
            elif len(matches) > 1:
                return normalize_query({"operation": "unsupported"})
            elif last_query:
                forced = _clean_dataset(last_query.get("dataset"))
            elif len(de.allowed_datasets(user)) == 1:
                forced = de.allowed_datasets(user)[0]
        if forced is None:
            return normalize_query({"operation": "unsupported"})
        if forced not in de.allowed_datasets(user):
            return normalize_query({"operation": "error", "error_message": "You do not have access to this SQL dataset."})
        # Only carry forward non-date filters for explicit follow-up language.
        followup = bool(re.search(r"\b(same|those|that|again|now|and for)\b", question, re.I))
        prior_filters = copy.deepcopy((last_query or {}).get("filters") or {}) if followup else {}
        if isinstance(prior_filters, dict):
            prior_filters.pop("start_date", None)
            prior_filters.pop("end_date", None)
        raw = {"dataset": forced, "filters": prior_filters, **period_spec}
        # Keep the previous financial measure only on explicit follow-ups.
        if followup and last_query:
            raw["measure"] = last_query.get("measure")
        return normalize_query(raw)

    if not question:
        return normalize_query({"operation": "unsupported"})

    if client is None:
        return _fallback_understand_question(
            question, last_query=last_query, dataset=dataset, user=user
        )

    forced = _clean_dataset(dataset)

    previous = (
        json.dumps(_compact_query(last_query), default=str)
        if last_query else "none"
    )

    user_message = (
        f"PREVIOUS QUERY: {previous}\n\n"
        + (f"SELECTED DATASET: {forced}\n\n" if forced else "")
        + f"QUESTION: {question}"
    )

    last_error = None

    for _ in range(2):                      # one retry

        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": _build_system_prompt(user)},
                    {"role": "user", "content": user_message},
                ],
                temperature=0
            )

            parsed = _extract_json(response.choices[0].message.content)

            if isinstance(parsed, dict):
                if parsed.get("operation") not in (None, "unsupported", "error"):
                    _resolve_dataset(parsed, last_query, forced, user)
                return normalize_query(parsed)

        except Exception as exc:
            last_error = exc

    if last_error is not None:
        fallback = _fallback_understand_question(
            question, last_query=last_query, dataset=dataset, user=user
        )
        if fallback["operation"] != "unsupported":
            return fallback

    return normalize_query({"operation": "unsupported"})


# ============================================================
# RESULT HELPERS
# ============================================================

def format_amount(value):

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    return f"-₹{abs(number):,.2f}" if number < 0 else f"₹{number:,.2f}"


def format_measure(key, measure, value):
    """Money columns get the rupee sign; counts and days stay plain numbers."""

    cfg = de.DATASETS[key]

    if measure in cfg["money_columns"]:
        return format_amount(value)

    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)

    return f"{number:,.0f}" if number == int(number) else f"{number:,.2f}"


def _fmt_pct(value):

    return "n/a" if value is None else f"{value:,.1f}%"


def _plural(n, word):

    return f"{n} {word}" + ("" if n == 1 else "s")


def describe_filters(key, filters, skip_type=False, split=False):
    """
    Readable description of the filters that were applied.
    skip_type : leave out the Income/Expense type filter (it is implied).
    split     : return (included_text, excluded_text) instead of one string.
    """

    cfg = de.DATASETS[key]
    f = filters or {}
    parts, left_out = [], []

    if f.get("record_id"):
        parts.append(f"{cfg['id']} {f['record_id']}")

    for column, values in (f.get("equals") or {}).items():
        values = values if isinstance(values, list) else [values]
        if column == cfg["type_column"]:
            if not skip_type:
                parts.append(
                    f"{' or '.join(v.lower() for v in values)} transactions"
                )
        else:
            parts.append(f"{column} {' or '.join(values)}")

    for column, text in (f.get("contains") or {}).items():
        parts.append(f"{column} matching '{text}'")

    for column, values in (f.get("exclude") or {}).items():
        values = values if isinstance(values, list) else [values]
        left_out.append(f"{' or '.join(values)} {column}")

    if f.get("start_date") and f.get("end_date"):
        parts.append(f"dates between {f['start_date']} and {f['end_date']}")
    elif f.get("start_date"):
        parts.append(f"dates from {f['start_date']}")
    elif f.get("end_date"):
        parts.append(f"dates up to {f['end_date']}")

    if f.get("min_amount") is not None:
        parts.append(
            f"{cfg['amount']} of at least {format_amount(f['min_amount'])}"
        )
    if f.get("max_amount") is not None:
        parts.append(
            f"{cfg['amount']} of at most {format_amount(f['max_amount'])}"
        )

    included = ", ".join(parts)
    excluded = ", ".join(left_out)

    if split:
        return included, excluded

    if excluded:
        included = (included + ", " if included else "") + f"excluding {excluded}"

    return included


def _scope_text(key, filters, skip_type=False):
    """' for X (excluding Y)' - ready to put inside a sentence."""

    included, excluded = describe_filters(
        key, filters, skip_type=skip_type, split=True
    )

    text = f" for {included}" if included else ""

    if excluded:
        text += f" (excluding {excluded})"

    return text


def _measure_phrase(measure):
    """'total' + 'Total Cost' -> 'Total Cost', 'total' + 'Net Pay' -> 'total Net Pay'."""

    return measure if measure.lower().startswith("total") else f"total {measure}"


def _result(operation, **kwargs):

    base = {
        "operation": operation,
        "dataset": None,
        "dataset_label": None,
        "message": "",
        "data": [],
        "table": [],
        "metrics": {},
        "warnings": [],
        "filters": {},
        "chart": None,
        "empty": False,
        "error": None,
        "reliability_status": "Not verified",
        "verification_badge": "source_referenced",
        "verification_label": "Source Referenced",
        "verification_method": "none",
    }

    base.update(kwargs)

    return base


def _error(kind, message, operation="error", **kwargs):

    return _result(operation, error=kind, message=message, **kwargs)


# ============================================================
# ACCESS CHECKS
# ============================================================

def _filter_access_problem(key, filters, user):
    """Department and hidden-column checks on the raw (unvalidated) filters."""

    cfg = de.DATASETS[key]
    allowed = de.allowed_departments(user)
    hidden = de.hidden_columns(user)

    f = filters or {}

    for group in ("equals", "contains", "exclude"):

        for column, values in (f.get(group) or {}).items():

            if de._norm(column) in hidden:
                return f"You do not have permission to view '{column}' information."

            if (
                allowed is not None
                and cfg["department"]
                and de._norm(column) == de._norm(cfg["department"])
                and group != "contains"
            ):
                values = values if isinstance(values, list) else [values]
                for value in values:
                    if str(value).lower() not in [d.lower() for d in allowed]:
                        return (
                            f"You do not have permission to view {value} data. "
                            f"Your access is limited to: {', '.join(allowed)}."
                        )

    return None


def _column_access_problem(columns, user):

    hidden = de.hidden_columns(user)

    for column in columns:
        if column and de._norm(column) in hidden:
            return f"You do not have permission to view '{column}' information."

    return None


# ============================================================
# EXECUTE QUERY
# ============================================================

def _fetch(key, filters, allowed, sort_by=None, sort_order=None, limit=None):

    return de.search_records(
        key,
        filters,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        allowed_departments=allowed
    )


def _primary_filters(key, filters):
    """
    Filters for questions that only make sense on 'primary' rows.
    For transactions that means Expense rows only.
    """

    cfg = de.DATASETS[key]
    scoped = copy.deepcopy(filters)

    if cfg["type_column"]:
        scoped["equals"][cfg["type_column"]] = [cfg["primary_kind"].title()]
        scoped["exclude"].pop(cfg["type_column"], None)

    return scoped


def _extra_warnings(key, rows, filters, measures=(), include_missing=True):
    """Notes about currencies and about cancelled / returned records."""

    cfg = de.DATASETS[key]
    warnings = []

    if include_missing:
        for column in dict.fromkeys(measures):
            missing = sum(1 for row in rows if row.get(column) in (None, ""))
            if missing:
                warnings.append(
                    f"Missing values: {missing} of {len(rows)} matching "
                    f"records have no '{column}' value. Missing values are "
                    "unavailable, not zero, and are omitted from sums and "
                    "averages."
                )

    currencies = {r.get("Currency") for r in rows if r.get("Currency")}

    if len(currencies) > 1 and "Currency" not in (filters.get("equals") or {}):
        warnings.append(
            "The data contains several currencies "
            f"({', '.join(sorted(currencies))}). Amounts are added as they "
            "are, without currency conversion."
        )

    for column, values in cfg["warn_values"].items():

        handled = {
            de._norm(c)
            for group in ("equals", "exclude")
            for c in (filters.get(group) or {})
        }

        if de._norm(column) in handled:
            continue

        found = {
            v: sum(1 for r in rows if str(r.get(column)) == v)
            for v in values
        }
        found = {v: n for v, n in found.items() if n}

        if found:
            listed = " and ".join(f"{n} {v.lower()}" for v, n in found.items())
            warnings.append(
                f"These figures include {listed} {cfg['noun_plural']}. "
                f"Ask to exclude them (for example 'excluding "
                f"{' and '.join(v.lower() for v in found)}') to leave them out."
            )

    return warnings


def _numeric_data_problem(rows, columns):
    for column in columns:
        if rows and not any(row.get(column) not in (None, "") for row in rows):
            return (
                f"No '{column}' values are available for these records, "
                "so I cannot calculate a financial result."
            )
    return None


def execute_query(query, user=None):
    """
    Executes the structured query. Never raises: every problem is returned
    as a result with result["error"] set and a friendly result["message"].
    """

    query = normalize_query(query)

    try:
        result = _execute(query, user)
    except database.QueryLimitError as exc:
        result = _error("query_limit", str(exc), query["operation"])
    except (
        database.DatabaseConfigurationError,
        database.DatabaseUnavailableError,
    ) as exc:
        result = _error("database_unavailable", str(exc), query["operation"])
    except ValueError as exc:
        result = _error("validation", str(exc), query["operation"])
    except PermissionError as exc:
        result = _error("access_denied", str(exc), query["operation"])
    except Exception as exc:
        logger.exception("Unexpected error while executing a financial query")
        result = _error(
            "internal",
            "Something went wrong while processing your question. "
            "Please try again or contact the administrator.",
            query["operation"]
        )

    key = query.get("dataset")

    if key in de.DATASETS:
        result["dataset"] = key
        result["dataset_label"] = de.DATASETS[key]["label"]

    if not result.get("error"):
        result["message"] = build_answer(query, result)
        # A successful SQL query is source-grounded, but warnings and empty
        # selections do not justify a full verification claim.
        warning_text = " ".join(str(w).lower() for w in result.get("warnings", []))
        blocking = any(token in warning_text for token in (
            "missing values:", "several currencies", "multiple currencies",
            "without currency conversion", "invalid", "interpreted",
        ))
        fully_verified = not result.get("empty") and not blocking
        if query["operation"] in ("period_total", "period_comparison"):
            fully_verified = fully_verified and not result.get("warnings")
        result["reliability_status"] = (
            "Verified" if fully_verified else "Partially verified"
        )
        result["verification_badge"] = (
            "source_verified" if fully_verified else "source_referenced"
        )
        result["verification_label"] = (
            "Source Verified" if fully_verified else "Source Referenced"
        )
        result["verification_method"] = "deterministic_sql_execution"
    else:
        result["reliability_status"] = "No answer"
        result["verification_badge"] = "not_verified"
        result["verification_label"] = "Not Verified"
        result["verification_method"] = "none"

    return result


def _execute(query, user):

    operation = query["operation"]

    if operation == "error":
        return _error(
            "llm_error",
            query.get("error_message")
            or "The question could not be processed.",
        )

    if operation in ("period_comparison", "period_total"):
        key = query.get("dataset")
        if not key or key not in de.DATASETS:
            return _error("clarify", "Please specify a financial dataset for this period analysis.", operation)
        access_problem = de.dataset_access_problem(user, key)
        if access_problem:
            return _error("access_denied", access_problem, operation)
        measure = query.get("measure")
        filters = query.get("filters") or {}
        if operation == "period_comparison":
            if query.get("period_type") not in ("mom", "dod", "yoy", "qoq"):
                return _error("validation", "Invalid comparison period type.", operation)
            analysis = de.calculate_period_comparison(
                key, user, query.get("current_period"), query.get("previous_period"),
                filters=filters, measure=measure,
            )
            return _result(
                operation, metrics=analysis, filters=filters,
                warnings=analysis.get("warnings", []),
                empty=not analysis.get("has_comparable_data"),
            )
        if query.get("period_type") not in ("ytd", "mtd", "qtd"):
            return _error("validation", "Invalid cumulative period type.", operation)
        analysis = de.calculate_period_total(
            key, user, query.get("period"), filters=filters, measure=measure,
        )
        return _result(
            operation, metrics=analysis, filters=filters,
            warnings=analysis.get("warnings", []),
            empty=not analysis.get("has_data"),
        )

    if operation == "unsupported":
        return _error(
            "unsupported",
            "Sorry, I cannot handle this type of question yet. "
            "Try asking about records, totals, top or lowest items, "
            "budget vs actual, comparisons or breakdowns for one dataset "
            "at a time.",
            "unsupported"
        )

    usable = de.allowed_datasets(user)
    key = query["dataset"]

    # ---------- which dataset? ----------
    if key is None:
        names = ", ".join(de.DATASETS[k]["label"] for k in usable)
        return _error(
            "clarify",
            "Which dataset do you mean? I can answer from: "
            f"{names or 'none (you have no dataset access)'}. "
            "Please mention it in your question, for example 'sales', "
            "'payroll' or 'purchase orders'.",
            operation
        )

    problem = de.dataset_access_problem(user, key)

    if problem:
        return _error("access_denied", problem, operation)

    cfg = de.DATASETS[key]
    allowed = de.allowed_departments(user)

    # ---------- access checks (before anything is looked up) ----------
    problem = (
        _filter_access_problem(key, query["filters"], user)
        or _column_access_problem(
            [query["group_by"], query["sort_by"], query["measure"]], user
        )
    )

    if problem:
        return _error("access_denied", problem, operation)

    # ---------- validate filters against real data ----------
    filters, warnings, errors = de.validate_filters(
        key, query["filters"], allowed
    )

    if errors:
        return _error("validation", " ".join(errors), operation)

    group_by = query["group_by"]
    measure = query["measure"] or cfg["amount"]
    top_n = query["top_n"]
    sort_by = query["sort_by"]
    sort_order = query["sort_order"]
    threshold = query["threshold_pct"] or 10.0

    clean = lambda rows: de.sanitize_rows(rows, user)

    # =========================================================
    # DATASET SUMMARY: deterministic evidence, no raw rows to Groq
    # =========================================================
    
    if operation == "dataset_summary":

        # Fetch only authorized and filtered SQL records
        rows = _fetch(key, filters, allowed)

        if not rows:
            return _result(
                operation,
                metrics={"count": 0},
                filters=filters,
                empty=True
            )

        visible = de.hidden_columns(user)

        main = cfg["amount"]
        budget = cfg["budget"]

        # Group-level analysis
        groups = []

        if de._norm(cfg["default_group"]) not in visible:
            groups = de.group_summary(
                key,
                rows,
                cfg["default_group"]
            )[:8]

        # Validate financial data
        warnings += _extra_warnings(
            key,
            rows,
            filters,
            measures=[main, budget]
        )

        # Calculate complete financial summary
        summary = de.summarize(key, rows)

        # Calculate actual vs budget / target
        budget_analysis = de.budget_vs_actual(
            key,
            rows
        )

        # Combine all metrics into one result
        summary_metrics = {
            **summary,

            "count": len(rows),
            "measure": main,

            "total": de.calculate_total(
                rows,
                main
            ),

            "average": de.calculate_average(
                rows,
                main
            ),

            "primary_actual": budget_analysis["actual"],
            "primary_budget": budget_analysis["budget"],

            "variance": budget_analysis["variance"],
            "variance_pct": budget_analysis["variance_pct"],
            "budget_status": budget_analysis["status"],

            # Keep this for existing agent compatibility
            "budget_comparison": budget_analysis,

            "group_by": cfg["default_group"],
        }

        return _result(
            operation,
            metrics=summary_metrics,
            table=groups,
            filters=filters,
            warnings=warnings
        )

    # =========================================================
    # SEARCH
    # =========================================================
    if operation == "search":

        matched = _fetch(key, filters, allowed, sort_by, sort_order)
        shown = matched[:top_n] if top_n else matched

        warnings += _extra_warnings(key, matched, filters)

        return _result(
            operation,
            data=clean(shown),
            metrics={"matched": len(matched), "shown": len(shown)},
            filters=filters,
            warnings=warnings,
            empty=not matched
        )

    # =========================================================
    # TOTAL / AVERAGE / COUNT
    # =========================================================
    if operation in ("total", "average", "count"):

        rows = _fetch(key, filters, allowed, sort_by, sort_order)
        if operation != "count":
            problem = _numeric_data_problem(rows, [measure])
            if problem:
                return _error("missing_data", problem, operation)

        types = {de.row_kind(key, r) for r in rows}

        if (
            cfg["type_column"]
            and not (filters["equals"].get(cfg["type_column"]))
            and {"income", "expense"} <= types
            and operation != "count"
            and measure == cfg["amount"]
        ):
            warnings.append(
                "This includes both income and expense transactions. "
                "Ask for 'total spending' or 'total income' to separate them."
            )

        warnings += _extra_warnings(
            key,
            rows,
            filters,
            measures=[measure] if operation != "count" else (),
            include_missing=operation != "count",
        )

        metrics = {"count": len(rows), "measure": measure}

        if operation == "total":
            metrics["total"] = de.calculate_total(rows, measure)
        elif operation == "average":
            metrics["average"] = de.calculate_average(rows, measure)
            metrics["measure_count"] = sum(
                1 for row in rows if row.get(measure) not in (None, "")
            )

        return _result(
            operation,
            data=clean(rows) if operation != "count" else [],
            metrics=metrics,
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # TOP / BOTTOM
    # =========================================================
    if operation in ("top_expenses", "bottom_expenses"):

        n = top_n or 5

        scoped = _primary_filters(key, filters)
        rows = _fetch(key, scoped, allowed)
        problem = _numeric_data_problem(
            de._primary_rows(key, rows), [measure]
        )
        if problem:
            return _error("missing_data", problem, operation)

        picked = (
            de.get_top_expenses(key, rows, n, measure)
            if operation == "top_expenses"
            else de.get_bottom_expenses(key, rows, n, measure)
        )

        all_total = de.calculate_total(rows, measure)
        picked_total = de.calculate_total(picked, measure)

        warnings += _extra_warnings(key, rows, scoped, measures=[measure])

        return _result(
            operation,
            data=clean(picked),
            metrics={
                "n": len(picked),
                "measure": measure,
                "picked_total": picked_total,
                "all_total": all_total,
                "share_pct": (picked_total / all_total * 100) if all_total else None,
                "record_count": len(rows),
            },
            filters=scoped,
            warnings=warnings,
            empty=not picked
        )

    # =========================================================
    # INCOME & EXPENSE SUMMARY
    # =========================================================
    if operation == "income_expense_summary":

        rows = _fetch(key, filters, allowed)
        problem = _numeric_data_problem(rows, [cfg["amount"]])
        if problem:
            return _error("missing_data", problem, operation)
        summary = de.income_expense_summary(key, rows)

        warnings += _extra_warnings(
            key, rows, filters, measures=[cfg["amount"]]
        )

        return _result(
            operation,
            data=clean(rows),
            metrics=summary | {"count": len(rows)},
            filters=filters,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # BUDGET VS ACTUAL
    # =========================================================
    if operation == "budget_vs_actual":

        scoped = _primary_filters(key, filters)
        rows = _fetch(key, scoped, allowed)
        problem = _numeric_data_problem(
            rows, [cfg["amount"], cfg["budget"]]
        )
        if problem:
            return _error("missing_data", problem, operation)

        summary = de.budget_vs_actual(key, rows, threshold)
        summary["count"] = len(rows)

        table, chart = [], None

        if group_by:
            table = de.detect_variances(key, rows, group_by, threshold)
            chart = {"x": group_by, "y": ["Budget", "Actual"]}

        warnings += _extra_warnings(
            key, rows, scoped, measures=[cfg["amount"], cfg["budget"]]
        )

        return _result(
            operation,
            data=clean(rows),
            table=table,
            chart=chart,
            metrics=summary,
            filters=scoped,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # VARIANCE DETECTION
    # =========================================================
    if operation == "variance":

        group_by = group_by or cfg["default_group"]

        if (problem := _column_access_problem([group_by], user)):
            return _error("access_denied", problem, operation)

        scoped = _primary_filters(key, filters)
        rows = _fetch(key, scoped, allowed)
        problem = _numeric_data_problem(
            rows, [cfg["amount"], cfg["budget"]]
        )
        if problem:
            return _error("missing_data", problem, operation)

        table = de.detect_variances(key, rows, group_by, threshold)

        bad, good = de.status_labels(key)

        warnings += _extra_warnings(
            key, rows, scoped, measures=[cfg["amount"], cfg["budget"]]
        )

        return _result(
            operation,
            table=table,
            chart={"x": group_by, "y": ["Budget", "Actual"]},
            metrics={
                "group_by": group_by,
                "threshold_pct": threshold,
                "groups": len(table),
                "bad": [r for r in table if r["Status"] == bad],
                "good": [r for r in table if r["Status"] == good],
                "overall": de.budget_vs_actual(key, rows, threshold),
            },
            filters=scoped,
            warnings=warnings,
            empty=not rows
        )

    # =========================================================
    # GROUP SUMMARY
    # =========================================================
    if operation == "group_summary":

        group_by = group_by or cfg["default_group"]

        if (problem := _column_access_problem([group_by], user)):
            return _error("access_denied", problem, operation)

        rows = _fetch(key, filters, allowed)
        problem = _numeric_data_problem(
            rows, [cfg["amount"], cfg["budget"]]
        )
        if problem:
            return _error("missing_data", problem, operation)
        table = de.group_summary(key, rows, group_by)

        main = de.primary_column(key)

        chart_y = (
            ["Income", "Expense"] if cfg["type_column"]
            else [cfg["amount_label"], cfg["budget_label"]]
        )

        warnings += _extra_warnings(
            key, rows, filters, measures=[cfg["amount"], cfg["budget"]]
        )

        return _result(
            operation,
            table=table,
            chart={"x": group_by, "y": chart_y},
            metrics={
                "group_by": group_by,
                "groups": len(table),
                "count": len(rows),
                "main_column": main,
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

        for side in ("a", "b"):

            merged = _merge_filters(
                query["filters"],
                _clean_filters(compare[f"filters_{side}"])
            )

            problem = _filter_access_problem(key, merged, user)

            if problem:
                return _error("access_denied", problem, operation)

            side_clean, side_warnings, side_errors = de.validate_filters(
                key, merged, allowed
            )

            if side_errors:
                return _error("validation", " ".join(side_errors), operation)

            warnings += side_warnings

            rows = _fetch(key, side_clean, allowed)

            label = (
                compare.get(f"label_{side}")
                or describe_filters(key, compare[f"filters_{side}"])
                or side.upper()
            )

            sides.append((label, side_clean, rows))

        (label_a, filters_a, rows_a), (label_b, filters_b, rows_b) = sides
        problem = _numeric_data_problem(
            rows_a + rows_b, [cfg["amount"], cfg["budget"]]
        )
        if problem:
            return _error("missing_data", problem, operation)

        if label_a == label_b:
            label_a, label_b = f"{label_a} (A)", f"{label_b} (B)"

        comparison = de.compare_datasets(key, rows_a, rows_b, label_a, label_b)

        warnings += _extra_warnings(
            key,
            rows_a + rows_b,
            {},
            measures=[cfg["amount"], cfg["budget"]],
        )

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

    key = query["dataset"]
    cfg = de.DATASETS[key]

    operation = result["operation"]
    m = result.get("metrics", {})
    implied = operation in (
        "top_expenses", "bottom_expenses", "budget_vs_actual", "variance"
    )
    scope_text = _scope_text(key, result.get("filters"), skip_type=implied)

    noun, nouns = cfg["noun"], cfg["noun_plural"]
    
    # Period calculations are deterministic, not model-generated.
    if operation == "period_total":
        period = m.get("period") or {}
        if not m.get("has_data"):
            return (f"No usable {m.get('measure', cfg['amount'])} values were found "
                    f"between {period.get('start_date')} and {period.get('end_date')}. "
                    "This is missing evidence, not a confirmed zero.")
        total = format_measure(key, m["measure"], m["total"])
        label = str(query.get("period_type", "period")).upper()
        answer = (f"### {label} {cfg['label']}\n\n"
                  f"From **{period.get('start_date')}** through **{period.get('end_date')}**, "
                  f"the recorded {m['measure']} totals **{total}** across "
                  f"**{m['record_count']:,} records**. "
                  "This describes recorded activity in the selected period, "
                  "not necessarily recognized revenue or settled spending.")
        if m.get("warnings"):
            answer += "\n\n**Data notes:** " + " ".join(m["warnings"])
        return answer

    if operation == "period_comparison":
        current = m.get("current") or {}
        previous = m.get("previous") or {}
        cp = current.get("period") or {}
        pp = previous.get("period") or {}
        label = str(query.get("period_type", "period")).upper()
        if not m.get("has_comparable_data"):
            return (f"### {label} {cfg['label']} comparison\n\n"
                    f"I checked **{cp.get('start_date')} to {cp.get('end_date')}** "
                    f"against **{pp.get('start_date')} to {pp.get('end_date')}**, "
                    "but cannot calculate a reliable change from the available records. "
                    + " ".join(m.get("warnings") or []))
        measure = m["measure"]
        now_value = format_measure(key, measure, current["total"])
        old_value = format_measure(key, measure, previous["total"])
        difference = m["difference"]
        direction = "increased" if difference > 0 else "decreased" if difference < 0 else "was unchanged"
        answer = (f"### {label} {cfg['label']} comparison\n\n"
                  f"Recorded {measure} **{direction}** from **{old_value}** "
                  f"({pp.get('start_date')} to {pp.get('end_date')}) to "
                  f"**{now_value}** ({cp.get('start_date')} to {cp.get('end_date')}). "
                  f"The absolute change is **{format_measure(key, measure, abs(difference))}**.")
        if m.get("growth_pct") is None:
            answer += " Percentage growth is undefined because the previous-period total was zero."
        else:
            answer += f" This represents a **{m['growth_pct']:+,.2f}%** change."
        answer += ("\n\nThis compares recorded values for the two periods; "
                   "it does not by itself establish the reason for the change.")
        if m.get("warnings"):
            answer += "\n\n**Data notes:** " + " ".join(m["warnings"])
        return answer

    # Meaningful business explanations

    
    # ==================================================
    # MEANINGFUL BUSINESS EXPLANATIONS
    # ==================================================

    if operation == "dataset_summary":

        summary_metrics = dict(m)

        # Support alternative summary metric names.
        if "primary_actual" not in summary_metrics:
            summary_metrics["primary_actual"] = (
                m.get("actual")
                or m.get("total")
                or 0
            )

        if "primary_budget" not in summary_metrics:
            summary_metrics["primary_budget"] = (
                m.get("budget")
                if m.get("budget") is not None
                else m.get("target")
            )

        if summary_metrics["primary_budget"] is None:
            # Missing evidence is not a zero target.
            return (
                "The dataset summary contains recorded "
                "sales values, but the target or budget "
                "was not included in the SQL result. "
                "I cannot calculate target performance "
                "reliably from this response."
            )

        if "variance" not in summary_metrics:
            actual = de._to_decimal(
                summary_metrics["primary_actual"]
            )
            budget = de._to_decimal(
                summary_metrics["primary_budget"]
            )

            summary_metrics["variance"] = de._variance(
                cfg,
                budget,
                actual
            )

        return be.explain_dataset_summary(
            key,
            summary_metrics
        )

    if operation == "group_summary":

        grouped_rows = (
            result.get("data")
            or result.get("table")
            or result.get("groups")
            or []
        )

        return be.explain_group_summary(
            key,
            query.get("group_by")
            or cfg["default_group"],
            grouped_rows
        )

    if operation == "variance":

        variance_rows = (
            result.get("data")
            or result.get("table")
            or []
        )

        if variance_rows:
            return be.explain_variances(
                key,
                query.get("group_by")
                or cfg["default_group"],
                variance_rows
            )

    if operation == "compare":

        comparison_data = (
            result.get("data")
            or result
        )

        return be.explain_comparison(
            key,
            comparison_data
        )


    if result.get("empty"):
        return (
            f"I could not find any {nouns}{scope_text}. "
            "Try widening the filters or the date range."
        )

    if operation == "dataset_summary":
        comparison = m["budget_comparison"]
        total = format_measure(key, m["measure"], m["total"])
        average = format_measure(key, m["measure"], m["average"])
        text = (f"The {cfg['label']} dataset contains {m['count']:,} {nouns}, "
                f"with {total} in {m['measure']} and an average of {average} per record. "
                f"The recorded {cfg['budget_word']} is {format_amount(comparison['budget'])}, "
                f"compared with actual {cfg['actual_word']} of {format_amount(comparison['actual'])}. "
                f"The difference is {format_amount(abs(comparison['variance']))} "
                f"({'favourable' if comparison['variance'] >= 0 else 'unfavourable'}).")
        if result.get("table"):
            group = m['group_by']
            top = result['table'][0]
            measure_name = de.primary_column(key)
            text += (f" The largest {group.lower()} group is {top[group]}, "
                     f"with {format_amount(top[measure_name])} in {measure_name}.")
        return text

    if operation == "search":

        text = f"I found {_plural(m['matched'], 'matching ' + noun)}"

        if m["shown"] < m["matched"]:
            text += f" and I am showing {m['shown']} of them"

        return text + "."

    if operation == "total":
        measure = m["measure"]
        return (
            f"The {_measure_phrase(measure)}{scope_text} is "
            f"{format_measure(key, measure, m['total'])} "
            f"across {_plural(m['count'], noun)}."
        )

    if operation == "average":
        measure = m["measure"]
        count = m.get("measure_count", m["count"])
        count_text = _plural(count, noun)
        if count < m["count"]:
            count_text += f" with available {measure} values"
        return (
            f"The average {measure}{scope_text} is "
            f"{format_measure(key, measure, m['average'])} "
            f"across {count_text}."
        )

    if operation == "count":
        return (
            f"There {'is' if m['count'] == 1 else 'are'} "
            f"{_plural(m['count'], noun)}{scope_text}."
        )

    
    
    if operation in ("top_expenses", "bottom_expenses"):

        word = (
            "highest"
            if operation == "top_expenses"
            else "lowest"
        )

        measure = m["measure"]
        records = result.get("data") or []

        total = m.get("picked_total", 0)
        share = m.get("share_pct")

        lines = [
            f"## Top {len(records)} {cfg['top_label']}"
            if operation == "top_expenses"
            else f"## Lowest {len(records)} {cfg['top_label']}",
            "",
        ]

        lines.append(
            f"The {len(records)} {word}-value "
            f"{cfg['top_label']} have a combined "
            f"{measure} of "
            f"**{format_measure(key, measure, total)}**."
        )

        if share is not None:
            lines.append(
                f"Together, they represent "
                f"**{share:.2f}%** of the total "
                f"{measure} across "
                f"{m.get('record_count', 0):,} "
                f"matching records."
            )

        lines.extend(["", "### Individual records", ""])

        preferred_columns = [
            cfg["id"],
            cfg["date"],
            "Customer",
            "Region",
            "Sales Rep",
            "Product",
            "Supplier",
            "Department",
            "Order Status",
            "Delivery Status",
            measure,
        ]

        for index, row in enumerate(records, start=1):

            identifier = row.get(
                cfg["id"],
                f"Record {index}"
            )

            lines.append(
                f"**{index}. {cfg['id']}: {identifier}**"
            )

            for column in preferred_columns:

                if column == cfg["id"]:
                    continue

                if column not in row:
                    continue

                value = row.get(column)

                if value is None or value == "":
                    continue

                if column == measure:
                    value = format_measure(
                        key,
                        measure,
                        value
                    )

                lines.append(
                    f"- {column}: {value}"
                )

            lines.append("")

        # Evidence-grounded status analysis.
        if key == "sales":

            statuses = {}

            for row in records:
                status = str(
                    row.get("Order Status") or "Unknown"
                ).strip()

                statuses[status] = (
                    statuses.get(status, 0) + 1
                )

            cancelled = [
                row for row in records
                if str(
                    row.get("Order Status", "")
                ).strip().lower() == "cancelled"
            ]

            returned = [
                row for row in records
                if str(
                    row.get("Order Status", "")
                ).strip().lower() == "returned"
            ]

            lines.append("### What this means")
            lines.append("")

            if cancelled or returned:

                excluded = cancelled + returned

                excluded_value = de.calculate_total(
                    excluded,
                    measure
                )

                lines.append(
                    f"**Important:** {len(excluded)} of "
                    f"these orders are marked Cancelled "
                    f"or Returned, representing "
                    f"**{format_measure(key, measure, excluded_value)}** "
                    f"of the combined order value."
                )

                lines.append(
                    "These amounts are included in the "
                    "recorded order-value ranking. "
                    "They should not automatically be "
                    "treated as realized revenue."
                )

            else:
                lines.append(
                    "None of these selected orders is "
                    "marked Cancelled or Returned."
                )

            lines.append(
                "The ranking identifies the largest "
                "individual sales orders. It does not "
                "by itself establish profitability, "
                "customer concentration, or revenue "
                "recognition."
            )

        elif key == "procurement":

            lines.extend([
                "### What this means",
                "",
                "These are the largest individual "
                "procurement amounts in the selected "
                "records. Their financial significance "
                "should be assessed against approved "
                "budgets, order status, and applicable "
                "procurement policies."
            ])

        elif key == "payroll":

            lines.extend([
                "### What this means",
                "",
                "These records have the highest "
                "payroll amounts in the selected data. "
                "They represent employee compensation "
                "costs, not sales revenue."
            ])

        return "\n".join(lines)


    if operation == "income_expense_summary":

        if cfg["type_column"]:
            return (
                f"Total income{scope_text} is {format_amount(m['income'])} and "
                f"total expense is {format_amount(m['expense'])}. "
                f"Net: {format_amount(m['net'])}."
            )

        total = m["income"] if cfg["primary_kind"] == "income" else m["expense"]

        return (
            f"Total {cfg['actual_word']}{scope_text} is {format_amount(total)} "
            f"across {_plural(m['count'], noun)}. This dataset only holds "
            f"{cfg['primary_kind']} records, so there is no income and "
            "expense split."
        )

    if operation == "budget_vs_actual":

        budget, actual = m["budget"], m["actual"]
        b_word, a_word = cfg["budget_word"], cfg["actual_word"]

        if actual > budget:
            gap = f"{format_amount(actual - budget)} higher than"
        elif actual < budget:
            gap = f"{format_amount(budget - actual)} lower than"
        else:
            gap = "equal to"

        return (
            f"The {b_word}{scope_text} is {format_amount(budget)} and actual "
            f"{a_word} is {format_amount(actual)}, which is {gap} the "
            f"{b_word} (status: {m['status']})."
        )

    if operation == "variance":

        bad, good = m["bad"], m["good"]
        bad_label, good_label = de.status_labels(key)
        group = m["group_by"]

        text = (
            f"I checked {m['groups']} {group.lower()} groups against a "
            f"±{m['threshold_pct']:g}% tolerance: {len(bad)} "
            f"{bad_label.lower()}, {len(good)} {good_label.lower()}."
        )

        if bad:
            worst = bad[0]
            what = "overspend" if cfg["primary_kind"] == "expense" else "shortfall"
            text += (
                f" The largest {what} is {worst[group]} "
                f"({_fmt_pct(worst['Variance %'])})."
            )

        if bad:
            text += " Unfavourable groups: " + "; ".join(
                f"{row[group]} ({_fmt_pct(row['Variance %'])}, "
                f"difference {format_amount(abs(row['Variance']))})"
                for row in bad[:10]
            ) + "."
        return text

    if operation == "group_summary":

        group = m["group_by"]
        main = m["main_column"]
        if not result.get("table"):
            return f"No groups were available for the requested {group.lower()} breakdown."
        top = max(result["table"], key=lambda r: r[main])

        detail = "; ".join(
            f"{row[group]}: {format_amount(row[main])}"
            for row in result["table"][:10]
        )
        return (f"Across {m['groups']} {group.lower()} groups, the largest "
                f"{main.lower()} is {top[group]} at {format_amount(top[main])}. "
                f"Breakdown: {detail}.")

    if operation == "compare":

        a, b = m["a"], m["b"]
        la, lb = m["label_a"], m["label_b"]

        if cfg["type_column"]:
            a_val, b_val, what = a["expense"], b["expense"], "expense"
            head = (
                f"{la}: income {format_amount(a['income'])}, "
                f"expense {format_amount(a['expense'])}. "
                f"{lb}: income {format_amount(b['income'])}, "
                f"expense {format_amount(b['expense'])}. "
            )
        else:
            a_val, b_val = a["primary_actual"], b["primary_actual"]
            what = cfg["amount_label"]
            head = (
                f"{la}: {what} {format_amount(a_val)}. "
                f"{lb}: {what} {format_amount(b_val)}. "
            )

        diff = b_val - a_val

        if diff > 0:
            verdict = f"{lb} is {format_amount(diff)} higher than {la}."
        elif diff < 0:
            verdict = f"{lb} is {format_amount(abs(diff))} lower than {la}."
        else:
            verdict = f"{la} and {lb} have the same {what.lower()}."

        return head + verdict

    return "I found the requested information."


# ============================================================
# RESULT EXPLANATION
# ============================================================

def _rule_based_explanation(query, result):

    if result.get("error"):
        return ""

    key = query["dataset"]
    cfg = de.DATASETS[key]

    operation = result["operation"]
    m = result.get("metrics", {})
    scope = describe_filters(key, result.get("filters"))

    how = (
        f"I used the {cfg['label']} data and filtered it by {scope}"
        if scope else
        f"I used all the {cfg['label']} records you have access to"
    )

    favourable = (
        "a negative variance means overspending"
        if cfg["primary_kind"] == "expense"
        else "a negative variance means sales below target"
    )

    calc = (
        "budget minus actual" if cfg["primary_kind"] == "expense"
        else "actual minus target"
    )

    if result.get("empty"):
        return (
            f"{how}, but no records matched. Check the spelling of the "
            "names or try a wider date range."
        )

    if operation == "search":
        return f"{how}, which left {m['matched']} records. See the table below."

    if operation == "total":
        return (
            f"{how} and added up the {m['measure']} column of the "
            f"{m['count']} matching records."
        )

    if operation == "average":
        return (
            f"{how} and divided the total {m['measure']} by the "
            f"{m['count']} matching records."
        )

    if operation == "count":
        return f"{how} and counted the matching records."

    if operation in ("top_expenses", "bottom_expenses"):
        share = m.get("share_pct")
        extra = (
            f" Together they are {share:.1f}% of the total {m['measure']} "
            f"of all {m['record_count']} records in this selection."
            if share is not None else ""
        )
        return f"{how} and sorted the records by {m['measure']}.{extra}"

    if operation == "income_expense_summary":

        if not cfg["type_column"]:
            return f"{how} and added up the {cfg['amount']} column."

        if m["net"] > 0:
            tail = f"Income is higher than expense by {format_amount(m['net'])}."
        elif m["net"] < 0:
            tail = (
                f"Expense is higher than income by "
                f"{format_amount(abs(m['net']))}."
            )
        else:
            tail = "Income and expense are equal."

        return f"{how} and added income and expense separately. {tail}"

    if operation == "budget_vs_actual":
        return (
            f"{how}, then compared the {cfg['budget']} column with the "
            f"{cfg['amount']} column. Variance is {calc} "
            f"({format_amount(m['variance'])}, "
            f"{_fmt_pct(m.get('variance_pct'))} of the "
            f"{cfg['budget_word']}); {favourable}."
        )

    if operation == "variance":
        return (
            f"For each {m['group_by'].lower()}, I compared "
            f"{cfg['budget_word']} with actual and flagged those that differ "
            f"by more than {m['threshold_pct']:g}%. Variance is {calc}; "
            f"{favourable}."
        )

    if operation == "group_summary":
        return (
            f"{how} and grouped the records by {m['group_by'].lower()}, "
            f"adding up the {cfg['amount']} inside each group."
        )

    if operation == "compare":
        label = "Expense" if cfg["type_column"] else cfg["amount_label"]
        pct = next(
            (r["% Change"] for r in result["table"] if r["Metric"] == label),
            None
        )
        return (
            f"I calculated the same figures for {m['label_a']} and "
            f"{m['label_b']}. Difference is {m['label_b']} minus "
            f"{m['label_a']}; the {label.lower()} changed by {_fmt_pct(pct)}."
        )

    return "The result is based on the verified data."


def explain_result(question, query, result, use_llm=True):
    """Explain results locally; legacy arguments remain for call compatibility."""

    if result.get("error"):
        return ""

    return _rule_based_explanation(query, result)


# ============================================================
# FOLLOW-UP QUESTION SUGGESTIONS
# ============================================================
# Built from the verified result (real group names, real columns, the user's
# permissions), never invented by the LLM. Every suggestion is a question this
# chatbot can answer, and it is written as a short follow-up so the previous
# query (dataset + filters) carries over.

MAX_GROUP_VALUES = 25      # skip "by ..." suggestions for very long lists


def _group_choices(key, filters, current, user):
    """Columns worth suggesting for 'break this down by ...'."""

    cfg = de.DATASETS[key]
    allowed = de.allowed_departments(user)
    hidden = de.hidden_columns(user)
    used = {de._norm(c) for c in (filters.get("equals") or {})}

    choices = []

    for column in cfg["group_columns"]:

        if (
            column == current
            or column == "Currency"
            or de._norm(column) in hidden
            or de._norm(column) in used
        ):
            continue

        count = len(de.get_unique_values(key, column, allowed))

        if 2 <= count <= MAX_GROUP_VALUES:
            choices.append(column)

    return choices


def _excludable_statuses(key, filters, user):
    """Statuses (e.g. Cancelled) still included in the numbers, if any."""

    cfg = de.DATASETS[key]
    allowed = de.allowed_departments(user)

    handled = {
        de._norm(c)
        for group in ("equals", "exclude")
        for c in (filters.get(group) or {})
    }

    rows = None
    found = []

    for column, values in cfg["warn_values"].items():

        if de._norm(column) in handled:
            continue

        if rows is None:
            rows = de.search_records(key, filters, allowed_departments=allowed)

        found += [v for v in values if any(r.get(column) == v for r in rows)]

    return found


def suggest_followups(query, result, user=None, limit=3):
    """Up to `limit` follow-up questions the user can ask next."""

    try:
        return _suggest_followups(query, result, user, limit)
    except Exception:                       # suggestions must never break an answer
        return []


def _suggest_followups(query, result, user, limit):

    # ---- vague question: point at what can be asked ----
    if result.get("error") == "clarify":
        return [
            f"What is the {_measure_phrase(de.DATASETS[k]['amount'])} for "
            f"{de.DATASETS[k]['label']}?"
            for k in de.allowed_datasets(user)
        ][:limit]

    if result.get("error") or result.get("empty"):
        return []
    if query.get("operation") in ("period_total", "period_comparison"):
        return []

    key = query["dataset"]
    cfg = de.DATASETS[key]
    operation = result["operation"]
    filters = result.get("filters") or {}
    metrics = result.get("metrics") or {}

    current = metrics.get("group_by") or query.get("group_by")
    groups = _group_choices(key, filters, current, user)
    by_group = f"Break this down by {groups[0]}" if groups else None
    by_group_2 = f"Break this down by {groups[1]}" if len(groups) > 1 else None

    no_dates = not (filters.get("start_date") or filters.get("end_date"))
    by_month = (
        "Show this month by month"
        if no_dates and current != "Month" else None
    )

    measure = metrics.get("measure") or cfg["amount"]
    top = f"Show the top 5 {cfg['top_label']}"
    low = f"Show the lowest 5 {cfg['top_label']}"
    total = f"What is the {_measure_phrase(measure)}?"
    average = f"What is the average {measure}?"
    bva = f"Show {cfg['budget_word']} vs actual"

    bad_label = de.status_labels(key)[0].lower()
    where_bad = lambda col: f"Show where we are {bad_label} by {col}"

    exclude = None
    leftovers = _excludable_statuses(key, filters, user)
    if leftovers:
        exclude = (
            f"Exclude {' and '.join(v.lower() for v in leftovers)} "
            f"{cfg['noun_plural']}"
        )

    # ---- compare the two biggest groups of a breakdown ----
    compare = None
    if operation == "group_summary" and current:
        names = [
            str(r[current]) for r in result["table"]
            if str(r[current]) != "Unknown"
        ][:2]
        if len(names) == 2:
            compare = (
                f"Compare {names[0]} with {names[1]}"
                if current == "Month"
                else f"Compare {current} {names[0]} with {current} {names[1]}"
            )

    if operation == "search":
        order = [exclude, total, by_group, top, by_month]

    elif operation in ("total", "average", "count"):
        order = [exclude, by_group, top, bva, by_month]
        if operation == "total":
            order.insert(2, average)

    elif operation == "top_expenses":
        order = [exclude, low, total, bva, by_group]

    elif operation == "bottom_expenses":
        order = [exclude, top, total, bva, by_group]

    elif operation == "income_expense_summary":
        order = [exclude, by_group, top, bva, by_month]

    elif operation == "budget_vs_actual":
        order = [
            exclude,
            where_bad(groups[0]) if groups else None,
            top, by_month, by_group_2,
        ]

    elif operation == "variance":
        order = [
            exclude, bva, top,
            where_bad(groups[0]) if groups else None,
            by_month,
        ]

    elif operation == "group_summary":
        order = [
            exclude, compare,
            where_bad(current) if current and current != "Month" else None,
            top, by_group,
        ]

    elif operation == "compare":
        order = [exclude, by_group, top, bva, by_month]

    else:
        order = []

    suggestions = []

    for text in order:
        if text and text not in suggestions:
            suggestions.append(text)

    return suggestions[:limit]


# ============================================================
# GROQ ADVISORY LAYER (MINIMIZED AGGREGATE DATA ONLY)
# ============================================================

ADVISORY_SYSTEM_PROMPT = """You are an advisory assistant for a financial SQL chatbot.
Return a JSON object with keys insights, recommendations, followups; each is
an array of at most three concise strings. Base observations ONLY on supplied
aggregate metrics. Clearly distinguish suggestions from facts. Never invent
numbers, source records, or claim that recommendations are verified findings.
Do not request personal data. Follow-up questions must be answerable from a
single SQL dataset, not depend on new data sources. Return JSON only."""


def _safe_advisory_metrics(result):
    """Extract a bounded, JSON-safe summary; never include data/table/filters.

    Nested structures, labels, names, raw rows and free-form strings are
    intentionally excluded. This does not anonymize aggregate numbers: users
    must authorize sharing those metrics with Groq.
    """
    metrics = result.get("metrics") or {}
    safe = {}
    allowed = {
        "count", "matched", "shown", "total", "average", "measure_count",
        "picked_total", "all_total", "share_pct", "record_count",
        "income", "expense", "net", "budget", "actual", "variance",
        "variance_pct", "groups", "threshold_pct", "n",
    }
    for key in sorted(allowed):
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            continue
        if isinstance(value, float) and not __import__("math").isfinite(value):
            continue
        if isinstance(value, Decimal) and not value.is_finite():
            continue
        safe[key] = float(value) if isinstance(value, Decimal) else value
    return safe


def generate_advisory(query, result, user=None, llm_client=None, model=None):
    """Return separate, AI-labeled advice without altering verified SQL answers.

    Sends only operation and a small allowlist of aggregate metrics, NOT user
    questions, SQL rows, names, document passages, schema or filter values.
    No network call is made if the query failed or Groq is disabled.
    """
    output = {"insights": [], "recommendations": [], "followups": [],
              "label": "AI Generated", "verification_badge": "ai_generated"}
    active = client if llm_client is None else llm_client
    if not GROQ_ADVISORY_ENABLED or active is None or result.get("error") or result.get("empty"):
        return output
    operation = query.get("operation")
    if operation not in SUPPORTED_OPERATIONS or operation == "unsupported":
        return output
    cfg = de.DATASETS.get(query.get("dataset"), {})
    payload = {"dataset": cfg.get("label"), "business_type": cfg.get("nature") or "mixed",
               "measure": cfg.get("amount"), "currency": "Use source currency; never assume USD",
               "operation": operation, "aggregate_metrics": _safe_advisory_metrics(result)}
    try:
        response = active.chat.completions.create(
            model=model or MODEL,
            messages=[
                {"role": "system", "content": ADVISORY_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        if not isinstance(parsed, dict):
            return output
        for key in ("insights", "recommendations", "followups"):
            values = parsed.get(key)
            if isinstance(values, list):
                output[key] = [item.strip()[:240] for item in values[:3]
                               if isinstance(item, str) and item.strip()]
    except Exception as exc:
        logger.warning("Groq advisory unavailable (%s)", type(exc).__name__)
    return output
