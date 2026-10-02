"""
data_engine.py
==============
All data logic for AI Pricing Copilot. No LLM calls live here: every number
the chatbot shows is computed in this file (this is what prevents hallucination).

Covers: transaction search, category/department/date filters, sorting,
top/bottom expenses, income-expense summary, budget vs actual, variance
detection, comparison, grouping, input validation and access control.
"""

import os
import hmac
import hashlib
import difflib
from collections import defaultdict
from datetime import datetime, date

from openpyxl import load_workbook


# ============================================================
# DATASET LOCATION
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

DATA_FILE = os.path.join(
    BASE_DIR,
    "Data",
    "synthetic_transactions_1000.xlsx"
)

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
    "%d-%b-%Y"
]


# ============================================================
# SMALL HELPERS
# ============================================================

def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_cell_date(value):
    """Converts an Excel cell value into a datetime (or None)."""

    if isinstance(value, datetime):
        return value

    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())

    if value:
        text = str(value).strip()

        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

    return None


def parse_date_input(value, end_of_day=False):
    """
    Converts a YYYY-MM-DD string (or datetime) into a datetime.
    Raises ValueError with a friendly message when invalid.
    """

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.strptime(
                str(value).strip()[:10],
                "%Y-%m-%d"
            )
        except ValueError:
            raise ValueError(
                f"'{value}' is not a valid date. Please use YYYY-MM-DD."
            )

    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)

    return parsed


# ============================================================
# LOAD DATASET
# ============================================================

def _load_dataset():

    if not os.path.exists(DATA_FILE):
        raise FileNotFoundError(
            f"Dataset not found at: {DATA_FILE}"
        )

    workbook = load_workbook(DATA_FILE, data_only=True)
    sheet = workbook.active

    headers = [
        str(cell.value).strip()
        if cell.value is not None
        else ""
        for cell in sheet[1]
    ]

    rows = []

    for raw in sheet.iter_rows(min_row=2, values_only=True):

        # skip completely empty rows
        if not any(
            v is not None and str(v).strip() != ""
            for v in raw
        ):
            continue

        record = dict(zip(headers, raw))

        if "Transaction Date" in record:
            record["Transaction Date"] = _parse_cell_date(
                record["Transaction Date"]
            )

        for column in ("Amount", "Budget"):
            if column in record:
                record[column] = _to_float(record[column])

        for key, value in record.items():
            if isinstance(value, str):
                record[key] = value.strip()

        rows.append(record)

    return rows


data = _load_dataset()


def get_data():
    """Returns the complete dataset."""
    return data.copy()


# ============================================================
# COLUMN METADATA
# ============================================================

# filter key -> dataset column (columns with a fixed set of values)
FILTER_COLUMNS = {
    "department": "Department",
    "category": "Category",
    "transaction_type": "Transaction Type",
    "payment_status": "Payment Status",
    "currency": "Currency",
}

SORT_COLUMNS = {
    "amount": "Amount",
    "date": "Transaction Date",
    "transaction date": "Transaction Date",
    "transaction_date": "Transaction Date",
    "department": "Department",
    "category": "Category",
    "budget": "Budget",
    "vendor": "Vendor / Customer",
    "vendor / customer": "Vendor / Customer",
    "transaction id": "Transaction ID",
    "transaction_id": "Transaction ID",
    "id": "Transaction ID",
    "payment status": "Payment Status",
    "status": "Payment Status",
}

GROUP_COLUMNS = [
    "Department",
    "Category",
    "Vendor / Customer",
    "Payment Status",
    "Transaction Type",
    "Currency",
    "Month",
]


def get_unique_values(column):
    """Sorted unique non-empty values of a dataset column."""

    return sorted(
        {
            str(row.get(column)).strip()
            for row in data
            if row.get(column) not in (None, "")
        }
    )


# ============================================================
# VALIDATION (no-hallucination layer)
# ============================================================

def match_value(column, value):
    """
    Maps a user/LLM supplied value to a REAL value in the dataset.
    Returns (canonical_value or None, was_fuzzy_match).
    """

    options = get_unique_values(column)
    lookup = {option.lower(): option for option in options}

    key = str(value).strip().lower()

    if key in lookup:
        return lookup[key], False

    close = difflib.get_close_matches(
        key,
        list(lookup),
        n=1,
        cutoff=0.75
    )

    if close:
        return lookup[close[0]], True

    return None, False


def validate_filters(filters):
    """
    Checks every filter against the real data.

    Returns (clean_filters, warnings, errors).
    """

    clean = {}
    warnings = []
    errors = []

    filters = filters or {}

    # ---- categorical columns ----
    for key, column in FILTER_COLUMNS.items():

        value = filters.get(key)

        if value in (None, ""):
            continue

        matched, fuzzy = match_value(column, value)

        if matched is None:
            options = get_unique_values(column)
            shown = ", ".join(options[:15])
            errors.append(
                f"'{value}' is not a valid {column.lower()}. "
                f"Available values: {shown}."
            )
        else:
            if fuzzy:
                warnings.append(
                    f"I interpreted {column.lower()} '{value}' as '{matched}'."
                )
            clean[key] = matched

    # ---- free text ----
    for key in ("transaction_id", "vendor"):
        value = filters.get(key)
        if value not in (None, ""):
            clean[key] = str(value).strip()

    # ---- dates ----
    for key in ("start_date", "end_date"):
        value = filters.get(key)
        if value in (None, ""):
            continue
        try:
            clean[key] = parse_date_input(value).strftime("%Y-%m-%d")
        except ValueError as exc:
            errors.append(str(exc))

    if (
        "start_date" in clean
        and "end_date" in clean
        and clean["start_date"] > clean["end_date"]
    ):
        clean["start_date"], clean["end_date"] = (
            clean["end_date"],
            clean["start_date"]
        )
        warnings.append(
            "The start date was after the end date, so I swapped them."
        )

    # ---- amounts ----
    for key in ("min_amount", "max_amount"):
        value = filters.get(key)
        if value in (None, ""):
            continue
        try:
            clean[key] = float(value)
        except (TypeError, ValueError):
            errors.append(f"'{value}' is not a valid amount.")

    if (
        "min_amount" in clean
        and "max_amount" in clean
        and clean["min_amount"] > clean["max_amount"]
    ):
        clean["min_amount"], clean["max_amount"] = (
            clean["max_amount"],
            clean["min_amount"]
        )
        warnings.append(
            "The minimum amount was above the maximum, so I swapped them."
        )

    return clean, warnings, errors


# ============================================================
# SORTING
# ============================================================

def _sort_key(column):

    if column in ("Amount", "Budget"):
        return lambda row: _to_float(row.get(column))

    if column == "Transaction Date":
        return lambda row: row.get(column) or datetime.min

    return lambda row: str(row.get(column, "")).lower()


def sort_rows(rows, sort_by=None, sort_order=None):
    """Sorts rows by a friendly column name."""

    column = SORT_COLUMNS.get(str(sort_by).lower()) if sort_by else None

    if not column:
        return rows

    default_desc = column in ("Amount", "Budget", "Transaction Date")
    order = (sort_order or ("desc" if default_desc else "asc")).lower()

    return sorted(
        rows,
        key=_sort_key(column),
        reverse=(order == "desc")
    )


# ============================================================
# SEARCH / FILTER TRANSACTIONS
# ============================================================

def _equals(rows, column, value):

    if not value:
        return rows

    target = str(value).strip().lower()

    return [
        row for row in rows
        if str(row.get(column, "")).strip().lower() == target
    ]


def search_transactions(
    transaction_id=None,
    department=None,
    category=None,
    transaction_type=None,
    payment_status=None,
    vendor=None,
    currency=None,
    start_date=None,
    end_date=None,
    min_amount=None,
    max_amount=None,
    sort_by=None,
    sort_order=None,
    limit=None,
    allowed_departments=None
):
    """
    Filters, sorts and (optionally) limits transactions.

    allowed_departments : list of departments the current user may see
                          (None = no restriction).
    """

    result = list(data)

    # ---- access control ----
    if allowed_departments is not None:
        allowed = {d.lower() for d in allowed_departments}
        result = [
            row for row in result
            if str(row.get("Department", "")).lower() in allowed
        ]

    # ---- exact-match filters ----
    result = _equals(result, "Transaction ID", transaction_id)
    result = _equals(result, "Department", department)
    result = _equals(result, "Category", category)
    result = _equals(result, "Transaction Type", transaction_type)
    result = _equals(result, "Payment Status", payment_status)
    result = _equals(result, "Currency", currency)

    # ---- vendor (partial match) ----
    if vendor:
        needle = str(vendor).lower()
        result = [
            row for row in result
            if needle in str(row.get("Vendor / Customer", "")).lower()
        ]

    # ---- dates ----
    start = parse_date_input(start_date)
    end = parse_date_input(end_date, end_of_day=True)

    if start:
        result = [
            row for row in result
            if row.get("Transaction Date")
            and row["Transaction Date"] >= start
        ]

    if end:
        result = [
            row for row in result
            if row.get("Transaction Date")
            and row["Transaction Date"] <= end
        ]

    # ---- amount range ----
    if min_amount is not None:
        result = [
            row for row in result
            if _to_float(row.get("Amount")) >= float(min_amount)
        ]

    if max_amount is not None:
        result = [
            row for row in result
            if _to_float(row.get("Amount")) <= float(max_amount)
        ]

    # ---- sorting + limit ----
    result = sort_rows(result, sort_by, sort_order)

    if limit:
        result = result[:int(limit)]

    return result


# ============================================================
# BASIC CALCULATIONS
# ============================================================

def calculate_total(rows):

    return sum(_to_float(row.get("Amount")) for row in rows)


def calculate_average(rows):

    if not rows:
        return 0.0

    return calculate_total(rows) / len(rows)


def count_transactions(rows):

    return len(rows)


def income_expense_summary(rows):
    """Total income and total expense."""

    total_income = 0.0
    total_expense = 0.0

    for row in rows:

        amount = _to_float(row.get("Amount"))
        kind = str(row.get("Transaction Type", "")).strip().lower()

        if kind == "income":
            total_income += amount
        elif kind == "expense":
            total_expense += amount

    return {
        "income": total_income,
        "expense": total_expense,
        "net": total_income - total_expense
    }


def summarize(rows):
    """Income, expense, net, count and average for a set of rows."""

    base = income_expense_summary(rows)

    base["count"] = len(rows)
    base["average"] = calculate_average(rows)
    base["total"] = calculate_total(rows)

    return base


# ============================================================
# TOP / BOTTOM EXPENSES
# ============================================================

def _expense_rows(rows):

    return [
        row for row in rows
        if str(row.get("Transaction Type", "")).strip().lower() == "expense"
    ]


def get_top_expenses(rows, n=5):
    """Highest expense transactions."""

    return sorted(
        _expense_rows(rows),
        key=lambda row: _to_float(row.get("Amount")),
        reverse=True
    )[:n]


def get_bottom_expenses(rows, n=5):
    """Lowest expense transactions."""

    return sorted(
        _expense_rows(rows),
        key=lambda row: _to_float(row.get("Amount"))
    )[:n]


# ============================================================
# BUDGET VS ACTUAL  +  VARIANCE DETECTION
# ============================================================
# Convention: variance = budget - actual
#   positive -> under budget (good), negative -> over budget.
# Budget vs actual is calculated on EXPENSE rows only.

def _variance_status(budget, actual, threshold_pct):

    if budget <= 0:
        return "No budget set" if actual > 0 else "No activity"

    variance_pct = (budget - actual) / budget * 100

    if variance_pct < -threshold_pct:
        return "Over budget"

    if variance_pct > threshold_pct:
        return "Under budget"

    return "Within tolerance"


def budget_vs_actual(rows, threshold_pct=10.0):

    expenses = _expense_rows(rows)

    actual = sum(_to_float(row.get("Amount")) for row in expenses)
    budget = sum(_to_float(row.get("Budget")) for row in expenses)

    variance = budget - actual

    variance_pct = (variance / budget * 100) if budget else None

    return {
        "budget": budget,
        "actual": actual,
        "variance": variance,
        "variance_pct": variance_pct,
        "status": _variance_status(budget, actual, threshold_pct)
    }


def group_summary(rows, group_by="Category"):
    """
    Groups rows and returns one summary row per group:
    Count, Income, Expense, Net, Budget, Variance.
    """

    if group_by not in GROUP_COLUMNS:
        group_by = "Category"

    groups = defaultdict(
        lambda: {"Count": 0, "Income": 0.0, "Expense": 0.0, "Budget": 0.0}
    )

    for row in rows:

        if group_by == "Month":
            when = row.get("Transaction Date")
            key = when.strftime("%Y-%m") if when else "Unknown"
        else:
            key = str(row.get(group_by) or "Unknown")

        amount = _to_float(row.get("Amount"))
        kind = str(row.get("Transaction Type", "")).strip().lower()

        entry = groups[key]
        entry["Count"] += 1

        if kind == "income":
            entry["Income"] += amount
        elif kind == "expense":
            entry["Expense"] += amount
            entry["Budget"] += _to_float(row.get("Budget"))

    table = []

    for key, entry in groups.items():
        table.append(
            {
                group_by: key,
                "Count": entry["Count"],
                "Income": round(entry["Income"], 2),
                "Expense": round(entry["Expense"], 2),
                "Net": round(entry["Income"] - entry["Expense"], 2),
                "Budget": round(entry["Budget"], 2),
                "Variance": round(entry["Budget"] - entry["Expense"], 2),
            }
        )

    if group_by == "Month":
        table.sort(key=lambda r: (r[group_by] == "Unknown", r[group_by]))
    else:
        table.sort(key=lambda r: r["Expense"], reverse=True)

    return table


def detect_variances(rows, group_by="Category", threshold_pct=10.0):
    """
    Compares budget and actual for each group and flags groups whose
    variance is larger than threshold_pct. Worst overspend comes first.
    """

    table = []

    for entry in group_summary(_expense_rows(rows), group_by):

        budget = entry["Budget"]
        actual = entry["Expense"]
        variance = budget - actual

        variance_pct = (
            round(variance / budget * 100, 2) if budget > 0 else None
        )

        table.append(
            {
                group_by: entry[group_by],
                "Budget": budget,
                "Actual": actual,
                "Variance": round(variance, 2),
                "Variance %": variance_pct,
                "Status": _variance_status(budget, actual, threshold_pct)
            }
        )

    table.sort(
        key=lambda r: (
            r["Variance %"] is None,
            r["Variance %"] if r["Variance %"] is not None else 0
        )
    )

    return table


# ============================================================
# COMPARISON
# ============================================================

def compare_datasets(rows_a, rows_b, label_a, label_b):
    """
    Compares two sets of transactions (two departments, two months, ...).
    Difference = B - A.
    """

    summary_a = summarize(rows_a)
    summary_b = summarize(rows_b)

    metrics = [
        ("Income", "income"),
        ("Expense", "expense"),
        ("Net", "net"),
        ("Transactions", "count"),
        ("Average Amount", "average"),
    ]

    table = []

    for name, key in metrics:

        a_value = summary_a[key]
        b_value = summary_b[key]
        difference = b_value - a_value

        percent = (difference / abs(a_value) * 100) if a_value else None

        table.append(
            {
                "Metric": name,
                label_a: round(a_value, 2),
                label_b: round(b_value, 2),
                "Difference": round(difference, 2),
                "% Change": round(percent, 2) if percent is not None else None,
            }
        )

    return {"table": table, "a": summary_a, "b": summary_b}


# ============================================================
# ACCESS CONTROL
# ============================================================
# Passwords come from environment variables (.env). The defaults below
# are for DEMO ONLY - change them before sharing the app.
# Edit the "departments" lists to match the department names in your data.

_SALT = os.getenv("AUTH_SALT", "ai-pricing-copilot").encode()


def hash_password(password):

    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode(),
        _SALT,
        100_000
    ).hex()


USERS = {
    "admin": {
        "name": "Admin",
        "role": "Admin",
        "departments": None,            # None = every department
        "hidden_columns": [],
        "password_hash": hash_password(os.getenv("ADMIN_PASSWORD", "admin123")),
    },
    "finance": {
        "name": "Finance Manager",
        "role": "Finance Manager",
        "departments": None,
        "hidden_columns": [],
        "password_hash": hash_password(os.getenv("FINANCE_PASSWORD", "finance123")),
    },
    "it_head": {
        "name": "IT Department Head",
        "role": "Department Head",
        "departments": ["IT"],          # only sees IT data
        "hidden_columns": [],
        "password_hash": hash_password(os.getenv("ITHEAD_PASSWORD", "ithead123")),
    },
    "analyst": {
        "name": "Analyst",
        "role": "Analyst",
        "departments": None,
        "hidden_columns": ["Vendor / Customer"],   # cannot see vendor names
        "password_hash": hash_password(os.getenv("ANALYST_PASSWORD", "analyst123")),
    },
}


def authenticate(username, password):
    """Returns the user profile (without the hash) or None."""

    record = USERS.get(str(username or "").strip().lower())

    # always hash, so timing does not reveal whether the user exists
    candidate = hash_password(password or "")
    expected = record["password_hash"] if record else hash_password("x")

    if record and hmac.compare_digest(candidate, expected):
        return {
            "username": str(username).strip().lower(),
            "name": record["name"],
            "role": record["role"],
            "departments": record["departments"],
            "hidden_columns": list(record["hidden_columns"]),
        }

    return None


def allowed_departments(user):
    """None = unrestricted, otherwise the list of permitted departments."""

    if not user:
        return None

    return user.get("departments")


def sanitize_rows(rows, user):
    """Removes columns the user is not allowed to see."""

    hidden = (user or {}).get("hidden_columns") or []

    if not hidden:
        return rows

    return [
        {k: v for k, v in row.items() if k not in hidden}
        for row in rows
    ]
