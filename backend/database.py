"""SQLite access for the allowlisted financial datasets and documents."""

import json
import logging
import math
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MAX_QUERY_ROWS = 5000
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
MAX_DATE_RANGE_DAYS = 3660
TABLES = {
    "transactions": "transactions",
    "procurement": "procurement_orders",
    "payroll": "payroll",
    "sales": "sales_orders",
}
DATASET_COLUMNS = {
    "transactions": [
        "Transaction ID", "Transaction Date", "Department", "Category",
        "Transaction Type", "Amount", "Budget", "Vendor / Customer",
        "Payment Status", "Currency",
    ],
    "procurement": [
        "PO Number", "PO Date", "Supplier", "Item Category", "Item Name",
        "Warehouse", "Qty Ordered", "Qty Received", "Unit Cost",
        "Total Cost", "Budgeted Cost", "Expected Delivery", "Delay Days",
        "Delivery Status", "Currency",
    ],
    "payroll": [
        "Payroll ID", "Employee ID", "Employee Name", "Department",
        "Designation", "Location", "Employment Type", "Pay Date",
        "Basic Salary", "Allowances", "Bonus", "Gross Pay", "Deductions",
        "Net Pay", "Budgeted Cost", "Payment Status", "Currency",
    ],
    "sales": [
        "Order ID", "Order Date", "Region", "Sales Rep", "Customer",
        "Product Category", "Product", "Quantity", "Unit Price",
        "Discount %", "Order Value", "Target Value", "Order Status",
        "Payment Method", "Currency",
    ],
}
DEPARTMENT_COLUMNS = {
    "transactions": "Department",
    "procurement": None,
    "payroll": "Department",
    "sales": None,
}


class DatabaseConfigurationError(RuntimeError):
    """Database setup is missing or incomplete."""


class DatabaseUnavailableError(RuntimeError):
    """SQLite database could not be opened or queried."""


class QueryLimitError(RuntimeError):
    """A query exceeded the supported result bound."""


class _CursorContext:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self._cursor

    def __exit__(self, exc_type, exc_value, traceback):
        self._cursor.close()


class _ConnectionContext:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return _CursorContext(self._connection.cursor())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.set_progress_handler(None, 0)
            self._connection.close()


def _database_path():
    configured = os.getenv("SQLITE_DATABASE_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "Data" / "financial_chatbot.sqlite3"


def _cosine_similarity(left, right):
    try:
        first = json.loads(left)
        second = json.loads(right)
        if len(first) != len(second) or not first:
            return 0.0
        dot = sum(a * b for a, b in zip(first, second))
        norm_first = math.sqrt(sum(value * value for value in first))
        norm_second = math.sqrt(sum(value * value for value in second))
        if not norm_first or not norm_second:
            return 0.0
        return dot / (norm_first * norm_second)
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0.0


def connect(read_only=True, connection_url=None, statement_timeout_ms=5000):
    """Open a short-lived local SQLite connection.

    `connection_url` remains accepted for call-site compatibility but SQLite
    uses SQLITE_DATABASE_PATH instead of a network connection URL.
    """

    del connection_url
    if not 1000 <= int(statement_timeout_ms) <= 60000:
        raise ValueError("Statement timeout must be between 1 and 60 seconds.")

    path = _database_path()
    try:
        if read_only:
            if not path.is_file():
                raise DatabaseConfigurationError(
                    "The SQLite database is not initialized. Run "
                    "'python -m backend.initialize_database' first."
                )
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro",
                uri=True,
                timeout=5,
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=5)
        connection.row_factory = None
        connection.execute("PRAGMA foreign_keys = ON")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        # SQLite's busy_timeout only controls lock waits, not long-running SQL.
        # A progress handler interrupts expensive statements at the deadline.
        deadline = time.monotonic() + int(statement_timeout_ms) / 1000
        connection.set_progress_handler(
            lambda: 1 if time.monotonic() >= deadline else 0, 1000
        )
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.create_function(
            "cosine_similarity", 2, _cosine_similarity, deterministic=True
        )
        return _ConnectionContext(connection)
    except DatabaseConfigurationError:
        raise
    except sqlite3.Error as exc:
        logger.error("Could not open SQLite database (%s)", type(exc).__name__)
        raise DatabaseUnavailableError(
            "The SQLite database is unavailable. Check the database path and "
            "file permissions."
        ) from exc


def _quote_identifier(identifier):
    return '"' + str(identifier).replace('"', '""') + '"'


def _approved_column(dataset, column):
    if dataset not in DATASET_COLUMNS:
        raise ValueError("Unknown dataset.")
    if column not in DATASET_COLUMNS[dataset]:
        raise ValueError("The requested column is not available.")
    return _quote_identifier(column)


def _in_clause(values):
    values = list(values)
    if not values:
        return "(SELECT NULL WHERE 0)", []
    return "(" + ", ".join("?" for _ in values) + ")", values


def build_select(
    dataset,
    filters=None,
    allowed_departments=None,
    sort_by=None,
    sort_order=None,
):
    """Build an allowlisted SELECT with bound values and department scope."""

    if dataset not in TABLES:
        raise ValueError("Unknown dataset.")

    filters = filters or {}
    clauses = []
    parameters = []

    department_column = DEPARTMENT_COLUMNS[dataset]
    if allowed_departments is not None:
        if department_column is None:
            return None, []
        values = [str(value).lower() for value in allowed_departments]
        in_sql, in_parameters = _in_clause(values)
        clauses.append(
            "lower(" + _approved_column(dataset, department_column) +
            ") IN " + in_sql
        )
        parameters.extend(in_parameters)

    record_id = filters.get("record_id")
    if record_id:
        id_column = {
            "transactions": "Transaction ID",
            "procurement": "PO Number",
            "payroll": "Payroll ID",
            "sales": "Order ID",
        }[dataset]
        clauses.append(
            "lower(" + _approved_column(dataset, id_column) + ") = ?"
        )
        parameters.append(str(record_id).lower())

    for column, values in (filters.get("equals") or {}).items():
        quoted = _approved_column(dataset, column)
        values = values if isinstance(values, (list, tuple)) else [values]
        in_sql, in_parameters = _in_clause(
            str(value).lower() for value in values
        )
        clauses.append("lower(" + quoted + ") IN " + in_sql)
        parameters.extend(in_parameters)

    for column, values in (filters.get("exclude") or {}).items():
        quoted = _approved_column(dataset, column)
        values = values if isinstance(values, (list, tuple)) else [values]
        in_sql, in_parameters = _in_clause(
            str(value).lower() for value in values
        )
        clauses.append("lower(" + quoted + ") NOT IN " + in_sql)
        parameters.extend(in_parameters)

    for column, text in (filters.get("contains") or {}).items():
        quoted = _approved_column(dataset, column)
        clauses.append(
            "instr(lower(coalesce(" + quoted + ", '')), lower(?)) > 0"
        )
        parameters.append(str(text))

    date_column = {
        "transactions": "Transaction Date",
        "procurement": "PO Date",
        "payroll": "Pay Date",
        "sales": "Order Date",
    }[dataset]
    quoted_date = _approved_column(dataset, date_column)

    start_date = None
    end_date = None
    if filters.get("start_date"):
        start_date = date.fromisoformat(str(filters["start_date"])[:10])
        clauses.append(quoted_date + " >= ?")
        parameters.append(start_date.isoformat())
    if filters.get("end_date"):
        end_date = date.fromisoformat(str(filters["end_date"])[:10])
        clauses.append(quoted_date + " < ?")
        parameters.append((end_date + timedelta(days=1)).isoformat())
    if (
        start_date is not None
        and end_date is not None
        and (end_date - start_date).days > MAX_DATE_RANGE_DAYS
    ):
        raise ValueError("Date ranges cannot exceed ten years.")

    amount_column = {
        "transactions": "Amount",
        "procurement": "Total Cost",
        "payroll": "Gross Pay",
        "sales": "Order Value",
    }[dataset]
    quoted_amount = _approved_column(dataset, amount_column)

    if filters.get("min_amount") is not None:
        clauses.append(quoted_amount + " >= ?")
        parameters.append(str(filters["min_amount"]))
    if filters.get("max_amount") is not None:
        clauses.append(quoted_amount + " <= ?")
        parameters.append(str(filters["max_amount"]))

    columns = ", ".join(
        _approved_column(dataset, column) for column in DATASET_COLUMNS[dataset]
    )
    query = "SELECT " + columns + " FROM " + _quote_identifier(TABLES[dataset])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    if sort_by:
        direction = (sort_order or "asc").lower()
        if direction not in ("asc", "desc"):
            raise ValueError("Invalid sort order.")
        query += (
            " ORDER BY " + _approved_column(dataset, sort_by) +
            " " + direction.upper()
        )

    return query, parameters


def fetch_records(
    dataset,
    filters=None,
    allowed_departments=None,
    sort_by=None,
    sort_order=None,
    limit=None,
):
    query, parameters = build_select(
        dataset, filters, allowed_departments, sort_by, sort_order
    )
    if query is None:
        return []

    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query + " LIMIT ?", parameters + [MAX_QUERY_ROWS + 1])
                rows = cursor.fetchall()
                if len(rows) > MAX_QUERY_ROWS:
                    raise QueryLimitError(
                        "This query matches more than 5,000 records. Narrow "
                        "the date range or add a filter."
                    )
                columns = [description[0] for description in cursor.description]
    except QueryLimitError:
        raise
    except (DatabaseConfigurationError, DatabaseUnavailableError):
        raise
    except sqlite3.Error as exc:
        logger.error("Financial query failed (%s)", type(exc).__name__)
        raise DatabaseUnavailableError(
            "The financial database query failed. Check that its schema is "
            "initialized and available."
        ) from exc

    records = [dict(zip(columns, row)) for row in rows]
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise ValueError("Result limit must be a non-negative integer.")
        return records[:limit]
    return records



def fetch_page(
    dataset,
    filters=None,
    allowed_departments=None,
    sort_by=None,
    sort_order=None,
    page=1,
    page_size=DEFAULT_PAGE_SIZE,
):
    """Return one bounded page of authorized SQL records.

    This API is additive: legacy fetch_records keeps its existing 5,000-row
    safety limit. The total is counted in SQLite without loading all records.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("Page must be a positive integer.")
    if (isinstance(page_size, bool) or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_PAGE_SIZE):
        raise ValueError(f"Page size must be between 1 and {MAX_PAGE_SIZE}.")
    if page > 100000:
        raise ValueError("Page number is too large.")

    sql, params = build_select(
        dataset, filters, allowed_departments, sort_by, sort_order
    )
    if sql is None:
        return {"rows": [], "page": page, "page_size": page_size,
                "total": 0, "has_next": False}

    # Stable ordering is required to avoid records moving between pages.
    if not sort_by:
        id_column = {
            "transactions": "Transaction ID", "procurement": "PO Number",
            "payroll": "Payroll ID", "sales": "Order ID",
        }[dataset]
        sql += " ORDER BY " + _approved_column(dataset, id_column) + " ASC"

    offset = (page - 1) * page_size
    try:
        with connect(statement_timeout_ms=15000) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM (" + sql + ")", params)
                total = int(cursor.fetchone()[0])
                cursor.execute(sql + " LIMIT ? OFFSET ?", params + [page_size, offset])
                columns = [item[0] for item in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    except (DatabaseConfigurationError, DatabaseUnavailableError):
        raise
    except sqlite3.Error as exc:
        logger.error("Paginated query failed (%s)", type(exc).__name__)
        raise DatabaseUnavailableError(
            "The database could not complete the paginated query."
        ) from exc
    return {"rows": rows, "page": page, "page_size": page_size,
            "total": total, "has_next": offset + len(rows) < total}


def fetch_unique_values(dataset, column, allowed_departments=None):
    quoted_column = _approved_column(dataset, column)
    department_column = DEPARTMENT_COLUMNS[dataset]
    where = ""
    parameters = []

    if allowed_departments is not None:
        if department_column is None:
            return []
        values = [str(value).lower() for value in allowed_departments]
        in_sql, in_parameters = _in_clause(values)
        where = (
            " WHERE lower(" + _approved_column(dataset, department_column) +
            ") IN " + in_sql
        )
        parameters.extend(in_parameters)

    query = (
        "SELECT DISTINCT " + quoted_column + " FROM " +
        _quote_identifier(TABLES[dataset]) + where +
        " ORDER BY " + quoted_column + " LIMIT ?"
    )
    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters + [MAX_QUERY_ROWS + 1])
                return [
                    str(row[0]).strip()
                    for row in cursor.fetchall()
                    if row[0] not in (None, "")
                ]
    except (DatabaseConfigurationError, DatabaseUnavailableError):
        raise
    except sqlite3.Error as exc:
        logger.error(
            "Could not read financial filter values (%s)", type(exc).__name__
        )
        raise DatabaseUnavailableError(
            "The financial database query failed. Check that its schema is "
            "initialized and available."
        ) from exc


def count_records(dataset, allowed_departments=None):
    if dataset not in TABLES:
        raise ValueError("Unknown dataset.")

    query = "SELECT COUNT(*) FROM " + _quote_identifier(TABLES[dataset])
    parameters = []
    department_column = DEPARTMENT_COLUMNS[dataset]

    if allowed_departments is not None:
        if department_column is None:
            return 0
        values = [str(value).lower() for value in allowed_departments]
        in_sql, parameters = _in_clause(values)
        query += (
            " WHERE lower(" +
            _approved_column(dataset, department_column) + ") IN " + in_sql
        )

    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                return cursor.fetchone()[0]
    except (DatabaseConfigurationError, DatabaseUnavailableError):
        raise
    except sqlite3.Error as exc:
        logger.error(
            "Could not count financial records (%s)", type(exc).__name__
        )
        raise DatabaseUnavailableError(
            "The financial database query failed. Check that its schema is "
                       "initialized and available."
        ) from exc


def validate_schema():
    """Return safe setup errors for missing SQLite tables/columns."""

    try:
        with connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name IN (?, ?, ?, ?)",
                    list(TABLES.values()),
                )
                existing_tables = {row[0] for row in cursor.fetchall()}
                actual = {}
                for table in existing_tables:
                    cursor.execute("PRAGMA table_info(" + _quote_identifier(table) + ")")
                    actual[table] = {row[1] for row in cursor.fetchall()}
    except (DatabaseConfigurationError, DatabaseUnavailableError) as exc:
        return {"database": str(exc)}
    except sqlite3.Error as exc:
        logger.error(
            "Could not inspect SQLite database schema (%s)", type(exc).__name__
        )
        return {
            "database": (
                "Could not inspect the SQLite schema. Check database "
                "availability and permissions."
            )
        }

    errors = {}
    for dataset, table in TABLES.items():
        missing = set(DATASET_COLUMNS[dataset]) - actual.get(table, set())
        if missing:
            errors[dataset] = (
                f"SQLite table '{table}' is missing required columns: "
                + ", ".join(sorted(missing))
                + ". Initialize the database with "
                "'python -m backend.initialize_database'."
            )
    return errors
