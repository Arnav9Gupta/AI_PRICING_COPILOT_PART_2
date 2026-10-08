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

The LLM only (a) converts text into a JSON query and (b) optionally rewords
an explanation. All numbers come from data_engine.py, and any LLM explanation
containing a number that is not in the verified result is thrown away.

Product principles followed here:
    accuracy over fluency, transparency by default, conversational follow-ups,
    security and access control, simplicity, consistency across users.
"""

import os
import re
import json
import copy
from datetime import datetime

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


# ============================================================
# GROQ CLIENT
# ============================================================

MODEL = "openai/gpt-oss-120b"

_api_key = os.getenv("GROQ_API_KEY")

client = Groq(api_key=_api_key) if (_api_key and Groq) else None


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
    allowed = de.allowed_departments(user)
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

    filter_parts = []

    for column in visible(cfg["filter_columns"]):

        values = de.get_unique_values(key, column, allowed)

        if column in cfg["exact_columns"] or len(values) > 40:
            filter_parts.append(f"{column} (specific value, {len(values)} possible)")
        else:
            filter_parts.append(f"{column}: [{', '.join(values) if values else '-'}]")

    lines.append("  Filter columns: " + "; ".join(filter_parts))

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

    if not question:
        return normalize_query({"operation": "unsupported"})

    if client is None:
        return normalize_query(
            {
                "operation": "error",
                "error_message": (
                    "The language model is not available. Make sure the "
                    "'groq' package is installed and GROQ_API_KEY is set in "
                    "your .env file, then restart the app."
                ),
            }
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
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    return f"-₹{abs(number):,.2f}" if number < 0 else f"₹{number:,.2f}"


def format_measure(key, measure, value):
    """Money columns get the rupee sign; counts and days stay plain numbers."""

    cfg = de.DATASETS[key]

    if measure in cfg["money_columns"]:
        return format_amount(value)

    try:
        number = float(value)
    except (TypeError, ValueError):
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


def _extra_warnings(key, rows, filters):
    """Notes about currencies and about cancelled / returned records."""

    cfg = de.DATASETS[key]
    warnings = []

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

    key = query.get("dataset")

    if key in de.DATASETS:
        result["dataset"] = key
        result["dataset_label"] = de.DATASETS[key]["label"]

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

        warnings += _extra_warnings(key, rows, filters)

        metrics = {"count": len(rows), "measure": measure}

        if operation == "total":
            metrics["total"] = de.calculate_total(rows, measure)
        elif operation == "average":
            metrics["average"] = de.calculate_average(rows, measure)

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

        picked = (
            de.get_top_expenses(key, rows, n, measure)
            if operation == "top_expenses"
            else de.get_bottom_expenses(key, rows, n, measure)
        )

        all_total = de.calculate_total(rows, measure)
        picked_total = de.calculate_total(picked, measure)

        warnings += _extra_warnings(key, rows, scoped)

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
        summary = de.income_expense_summary(key, rows)

        warnings += _extra_warnings(key, rows, filters)

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

        summary = de.budget_vs_actual(key, rows, threshold)
        summary["count"] = len(rows)

        table, chart = [], None

        if group_by:
            table = de.detect_variances(key, rows, group_by, threshold)
            chart = {"x": group_by, "y": ["Budget", "Actual"]}

        warnings += _extra_warnings(key, rows, scoped)

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

        table = de.detect_variances(key, rows, group_by, threshold)

        bad, good = de.status_labels(key)

        warnings += _extra_warnings(key, rows, scoped)

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
        table = de.group_summary(key, rows, group_by)

        main = de.primary_column(key)

        chart_y = (
            ["Income", "Expense"] if cfg["type_column"]
            else [cfg["amount_label"], cfg["budget_label"]]
        )

        warnings += _extra_warnings(key, rows, filters)

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

        if label_a == label_b:
            label_a, label_b = f"{label_a} (A)", f"{label_b} (B)"

        comparison = de.compare_datasets(key, rows_a, rows_b, label_a, label_b)

        warnings += _extra_warnings(key, rows_a + rows_b, {})

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

    if result.get("empty"):
        return (
            f"I could not find any {nouns}{scope_text}. "
            "Try widening the filters or the date range."
        )

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
        return (
            f"The average {measure}{scope_text} is "
            f"{format_measure(key, measure, m['average'])} "
            f"across {_plural(m['count'], noun)}."
        )

    if operation == "count":
        return (
            f"There {'is' if m['count'] == 1 else 'are'} "
            f"{_plural(m['count'], noun)}{scope_text}."
        )

    if operation in ("top_expenses", "bottom_expenses"):
        word = "highest" if operation == "top_expenses" else "lowest"
        measure = m["measure"]
        return (
            f"Here are the {m['n']} {word} {cfg['top_label']} by {measure}"
            f"{scope_text}, totalling "
            f"{format_measure(key, measure, m['picked_total'])}."
        )

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

        return text

    if operation == "group_summary":

        group = m["group_by"]
        main = m["main_column"]
        top = max(result["table"], key=lambda r: r[main])

        return (
            f"Here is the breakdown by {group.lower()}{scope_text} "
            f"({m['groups']} groups). Highest {main.lower()}: {top[group]} "
            f"at {format_amount(top[main])}."
        )

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
        "dataset": result.get("dataset_label"),
        "operation": result["operation"],
        "filters": _compact_filters(result.get("filters", {})),
        "answer": result.get("message"),
        "metrics": result.get("metrics"),
        "table": result.get("table", [])[:15],
        "rows": result.get("data", [])[:10],
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
                    "You explain business data query results in simple "
                    "English in 2-3 short sentences. Use ONLY the facts "
                    "provided. Do not add, estimate or round any number, and "
                    "do not mention anything that is not in the facts. Point "
                    "out what stands out (largest item, over/under budget or "
                    "above/below target, higher/lower side). No markdown."
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
