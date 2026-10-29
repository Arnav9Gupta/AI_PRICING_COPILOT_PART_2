
"""
PRICING COPILOT
Financial Language Engine - Version 2

Features:
- MoM: Month over Month
- DoD: Day over Day
- YoY: Year over Year
- QoQ: Quarter over Quarter
- YTD: Year to Date
- MTD: Month to Date
- QTD: Quarter to Date

Also supports:
- Full and abbreviated month names
- ISO dates
- ISO timestamps
- Natural-language dates
- Quarters such as Q1 2026
- Relative periods
- Previous-period calculation
- Year and quarter boundaries

This module ONLY calculates date ranges.
Financial values are calculated by the SQL engine.
"""

import re
import calendar

from datetime import (
    datetime,
    date,
    timedelta,
    timezone,
)


# ============================================================
# 1. FINANCIAL ABBREVIATIONS
# ============================================================

ABBREVIATIONS = {
    "mom": "month over month",
    "m/m": "month over month",
    "dod": "day over day",
    "d/d": "day over day",
    "yoy": "year over year",
    "y/y": "year over year",
    "qoq": "quarter over quarter",
    "q/q": "quarter over quarter",
    "ytd": "year to date",
    "mtd": "month to date",
    "qtd": "quarter to date",
}


COMPARISON_TYPES = {
    "month over month": "mom",
    "day over day": "dod",
    "year over year": "yoy",
    "quarter over quarter": "qoq",
}


CUMULATIVE_TYPES = {
    "year to date": "ytd",
    "month to date": "mtd",
    "quarter to date": "qtd",
}


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


# ============================================================
# 2. NORMALIZE FINANCIAL TERMS
# ============================================================

def normalize_financial_terms(question):
    """
    Expand financial abbreviations.

    Examples:
        MoM sales -> month over month sales
        YoY revenue -> year over year revenue
        YTD payroll -> year to date payroll
    """

    result = str(question or "")

    # Longest abbreviations first.
    for abbreviation in sorted(
        ABBREVIATIONS,
        key=len,
        reverse=True,
    ):
        expansion = ABBREVIATIONS[abbreviation]

        pattern = (
            r"(?<!\w)"
            + re.escape(abbreviation)
            + r"(?!\w)"
        )

        result = re.sub(
            pattern,
            expansion,
            result,
            flags=re.IGNORECASE,
        )

    # Normalize hyphenated phrases.
    replacements = {
        "month-over-month": "month over month",
        "day-over-day": "day over day",
        "year-over-year": "year over year",
        "quarter-over-quarter": "quarter over quarter",
        "year-to-date": "year to date",
        "month-to-date": "month to date",
        "quarter-to-date": "quarter to date",
    }

    for old, new in replacements.items():
        result = re.sub(
            re.escape(old),
            new,
            result,
            flags=re.IGNORECASE,
        )

    return result


# ============================================================
# 3. BASIC DATE HELPERS
# ============================================================

def month_range(year, month):
    """
    Return the complete date range of a month.
    """

    year = int(year)
    month = int(month)

    last_day = calendar.monthrange(
        year,
        month,
    )[1]

    return {
        "start_date": date(
            year,
            month,
            1,
        ).isoformat(),
        "end_date": date(
            year,
            month,
            last_day,
        ).isoformat(),
    }


def previous_month(year, month):
    """
    Return the previous year and month.
    """

    if month == 1:
        return year - 1, 12

    return year, month - 1


def next_month(year, month):
    """
    Return the next year and month.
    """

    if month == 12:
        return year + 1, 1

    return year, month + 1


def quarter_number(month):
    """
    Return quarter number from month.
    """

    return ((month - 1) // 3) + 1


def quarter_range(year, quarter):
    """
    Return the complete quarter date range.

    Example:
        Q2 2026
        2026-04-01 through 2026-06-30
    """

    year = int(year)
    quarter = int(quarter)

    if quarter not in (1, 2, 3, 4):
        raise ValueError(
            "Quarter must be between 1 and 4."
        )

    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2

    return {
        "start_date": date(
            year,
            start_month,
            1,
        ).isoformat(),
        "end_date": date(
            year,
            end_month,
            calendar.monthrange(
                year,
                end_month,
            )[1],
        ).isoformat(),
    }


def previous_quarter(year, quarter):
    """
    Return previous year and quarter.
    """

    if quarter == 1:
        return year - 1, 4

    return year, quarter - 1


def year_range(year):
    """
    Return complete calendar-year range.
    """

    return {
        "start_date": date(
            year,
            1,
            1,
        ).isoformat(),
        "end_date": date(
            year,
            12,
            31,
        ).isoformat(),
    }


def _shift_year(day, years=-1):
    """
    Shift a date by a number of years.

    Handles leap-day comparisons by using
    February 28 when the target year
    does not contain February 29.
    """

    target_year = day.year + years

    last_day = calendar.monthrange(
        target_year,
        day.month,
    )[1]

    return date(
        target_year,
        day.month,
        min(day.day, last_day),
    )


def _to_date(value):
    """
    Convert date/datetime/ISO string to date.
    """

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    if isinstance(value, str):
        try:
            return date.fromisoformat(
                value.strip()[:10]
            )
        except ValueError:
            pass

    raise ValueError(
        "Invalid date. Expected YYYY-MM-DD."
    )


# ============================================================
# 4. DATE EXPRESSION PARSING
# ============================================================

def parse_date_expression(question, today=None):
    """
    Parse a single date or date range.

    Returns:
        {
            "start_date": "YYYY-MM-DD",
            "end_date": "YYYY-MM-DD"
        }

    Returns None for unresolved expressions.

    For multiple dates in a question,
    use a dedicated comparison parser.
    """

    today = _to_date(today) if today else date.today()

    text = str(
        question or ""
    ).lower().strip()

    # --------------------------------------------------------
    # EXPLICIT ISO TIMESTAMP
    # --------------------------------------------------------

    timestamp_match = re.search(
        r"\b(\d{4}-\d{2}-\d{2})"
        r"[T ]\d{2}:\d{2}"
        r"(?::\d{2}(?:\.\d+)?)?"
        r"(?:Z|[+-]\d{2}:\d{2})?",
        text,
        re.IGNORECASE,
    )

    if timestamp_match:
        try:
            day = date.fromisoformat(
                timestamp_match.group(1)
            )

            return {
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            }

        except ValueError:
            return None

    # --------------------------------------------------------
    # RELATIVE DATES
    # --------------------------------------------------------

    if re.search(r"\byesterday\b", text):
        day = today - timedelta(days=1)

        return {
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
        }

    if re.search(r"\btoday\b", text):
        return {
            "start_date": today.isoformat(),
            "end_date": today.isoformat(),
        }

    if re.search(
        r"\b(last|previous) month\b",
        text,
    ):
        year, month = previous_month(
            today.year,
            today.month,
        )

        return month_range(year, month)

    if re.search(r"\bthis month\b", text):
        return {
            "start_date": date(
                today.year,
                today.month,
                1,
            ).isoformat(),
            "end_date": today.isoformat(),
        }

    if re.search(
        r"\b(last|previous) quarter\b",
        text,
    ):
        year, quarter = previous_quarter(
            today.year,
            quarter_number(today.month),
        )

        return quarter_range(
            year,
            quarter,
        )

    if re.search(r"\bthis quarter\b", text):
        quarter = quarter_number(
            today.month
        )

        start_month = (
            (quarter - 1) * 3 + 1
        )

        return {
            "start_date": date(
                today.year,
                start_month,
                1,
            ).isoformat(),
            "end_date": today.isoformat(),
        }

    if re.search(r"\bthis year\b", text):
        return {
            "start_date": date(
                today.year,
                1,
                1,
            ).isoformat(),
            "end_date": today.isoformat(),
        }

    if re.search(
        r"\b(last|previous) year\b",
        text,
    ):
        return year_range(
            today.year - 1
        )

    # --------------------------------------------------------
    # EXPLICIT ISO DATE
    # --------------------------------------------------------

    iso_match = re.search(
        r"\b(\d{4}-\d{2}-\d{2})\b",
        text,
    )

    if iso_match:
        try:
            day = date.fromisoformat(
                iso_match.group(1)
            )

            return {
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            }

        except ValueError:
            return None

    # --------------------------------------------------------
    # QUARTER: Q2 2026
    # --------------------------------------------------------

    quarter_match = re.search(
        r"\bq([1-4])\s*[-/]?\s*(\d{4})\b",
        text,
        re.IGNORECASE,
    )

    if quarter_match:
        return quarter_range(
            int(quarter_match.group(2)),
            int(quarter_match.group(1)),
        )

    # --------------------------------------------------------
    # MONTH: October 2026 / Oct 2026
    # --------------------------------------------------------

    month_names = "|".join(
        sorted(
            MONTHS,
            key=len,
            reverse=True,
        )
    )

    month_match = re.search(
        rf"\b({month_names})\s+(\d{{4}})\b",
        text,
        re.IGNORECASE,
    )

    if month_match:
        return month_range(
            int(month_match.group(2)),
            MONTHS[
                month_match.group(1).lower()
            ],
        )

    # --------------------------------------------------------
    # MONTH AND DAY: 15 October 2026
    # --------------------------------------------------------

    day_month_match = re.search(
        rf"\b(\d{{1,2}})\s+({month_names})\s+(\d{{4}})\b",
        text,
        re.IGNORECASE,
    )

    if day_month_match:
        try:
            day = date(
                int(day_month_match.group(3)),
                MONTHS[
                    day_month_match.group(2).lower()
                ],
                int(day_month_match.group(1)),
            )

            return {
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            }

        except ValueError:
            return None

    return None


# ============================================================
# 5. DETECT FINANCIAL PERIOD TYPE
# ============================================================

def detect_period_comparison(question):
    """
    Detect MoM, DoD, YoY, QoQ.

    Returns:
        mom / dod / yoy / qoq / None
    """

    normalized = normalize_financial_terms(
        question
    ).lower()

    for phrase, code in COMPARISON_TYPES.items():
        if phrase in normalized:
            return code

    return None


def detect_cumulative_period(question):
    """
    Detect YTD, MTD, QTD.

    Returns:
        ytd / mtd / qtd / None
    """

    normalized = normalize_financial_terms(
        question
    ).lower()

    for phrase, code in CUMULATIVE_TYPES.items():
        if phrase in normalized:
            return code

    return None


def detect_period_type(question):
    """
    Return any supported period abbreviation.
    """

    comparison = detect_period_comparison(
        question
    )

    if comparison:
        return comparison

    return detect_cumulative_period(
        question
    )


# ============================================================
# 6. RESOLVE PERIOD COMPARISONS
# ============================================================

def resolve_period_comparison(
    question,
    today=None,
):
    """
    Resolve comparison periods.

    Examples:
        MoM June 2026
        DoD 15 October 2026
        YoY June 2026
        QoQ Q2 2026

    Returns:
        {
            "comparison_type": "mom",
            "current_period": {...},
            "previous_period": {...}
        }

    Returns None if no comparison is requested
    or the date cannot be resolved.
    """

    comparison_type = (
        detect_period_comparison(question)
    )

    if not comparison_type:
        return None

    today = _to_date(today) if today else date.today()

    parsed = parse_date_expression(
        question,
        today=today,
    )

    if parsed:
        start = _to_date(
            parsed["start_date"]
        )

        end = _to_date(
            parsed["end_date"]
        )

    else:
        # Default to current calendar period.
        if comparison_type == "mom":
            parsed = month_range(
                today.year,
                today.month,
            )

        elif comparison_type == "dod":
            parsed = {
                "start_date": today.isoformat(),
                "end_date": today.isoformat(),
            }

        elif comparison_type == "qoq":
            parsed = quarter_range(
                today.year,
                quarter_number(today.month),
            )

        else:
            parsed = year_range(
                today.year
            )

        start = _to_date(
            parsed["start_date"]
        )

        end = _to_date(
            parsed["end_date"]
        )

    # --------------------------------------------------------
    # MONTH OVER MONTH
    # --------------------------------------------------------

    if comparison_type == "mom":
        current = month_range(
            start.year,
            start.month,
        )

        year, month = previous_month(
            start.year,
            start.month,
        )

        previous = month_range(
            year,
            month,
        )

    # --------------------------------------------------------
    # DAY OVER DAY
    # --------------------------------------------------------

    elif comparison_type == "dod":
        current = {
            "start_date": start.isoformat(),
            "end_date": start.isoformat(),
        }

        previous_day = (
            start - timedelta(days=1)
        )

        previous = {
            "start_date": previous_day.isoformat(),
            "end_date": previous_day.isoformat(),
        }

    # --------------------------------------------------------
    # YEAR OVER YEAR
    # --------------------------------------------------------

    elif comparison_type == "yoy":
        current = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }

        previous = {
            "start_date": _shift_year(
                start
            ).isoformat(),
            "end_date": _shift_year(
                end
            ).isoformat(),
        }

    # --------------------------------------------------------
    # QUARTER OVER QUARTER
    # --------------------------------------------------------

    elif comparison_type == "qoq":
        quarter = quarter_number(
            start.month
        )

        current = quarter_range(
            start.year,
            quarter,
        )

        year, prev_quarter = previous_quarter(
            start.year,
            quarter,
        )

        previous = quarter_range(
            year,
            prev_quarter,
        )

    else:
        return None

    return {
        "comparison_type": comparison_type,
        "current_period": current,
        "previous_period": previous,
    }


# ============================================================
# 7. RESOLVE CUMULATIVE PERIODS
# ============================================================

def resolve_cumulative_period(
    question,
    today=None,
):
    """
    Resolve YTD, MTD, QTD date ranges.

    Examples:
        YTD sales as of 26 October 2026
        MTD sales for October 2026
        QTD procurement spending
    """

    period_type = detect_cumulative_period(
        question
    )

    if not period_type:
        return None

    today = _to_date(today) if today else date.today()

    parsed = parse_date_expression(
        question,
        today=today,
    )

    if parsed:
        end = _to_date(
            parsed["end_date"]
        )
    else:
        end = today

    if period_type == "ytd":
        start = date(
            end.year,
            1,
            1,
        )

    elif period_type == "mtd":
        start = date(
            end.year,
            end.month,
            1,
        )

    elif period_type == "qtd":
        quarter = quarter_number(
            end.month
        )

        start_month = (
            (quarter - 1) * 3 + 1
        )

        start = date(
            end.year,
            start_month,
            1,
        )

    else:
        return None

    return {
        "period_type": period_type,
        "period": {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
    }


# ============================================================
# 8. MAIN PERIOD RESOLVER
# ============================================================

def resolve_financial_period(
    question,
    today=None,
):
    """
    Main entry point for financial date analysis.

    Returns one of:

    Comparison:
        {
            "operation": "period_comparison",
            "comparison_type": "mom",
            "current_period": {...},
            "previous_period": {...}
        }

    Cumulative:
        {
            "operation": "period_total",
            "period_type": "ytd",
            "period": {...}
        }

    None:
        No financial period operation detected.
    """

    comparison = resolve_period_comparison(
        question,
        today=today,
    )

    if comparison:
        return {
            "operation": "period_comparison",
            **comparison,
        }

    cumulative = resolve_cumulative_period(
        question,
        today=today,
    )

    if cumulative:
        return {
            "operation": "period_total",
            **cumulative,
        }

    return None
