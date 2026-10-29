
"""
Pricing Copilot - Dynamic Visualization Tool

Builds safe Chart.js specifications from
authorized SQL query results.

This module:
- Never queries the database directly
- Never generates financial figures
- Never calls Groq
- Never exposes raw SQL rows
- Returns None when a chart is not appropriate

IMPORTANT:
Call this only AFTER SQL authorization and
query execution have succeeded.
"""

from decimal import Decimal, InvalidOperation
from datetime import date, datetime
import math

from backend import data_engine as de


MAX_CHART_POINTS = 24


def _number(value):
    """Convert supported numeric values safely."""

    if value is None or isinstance(value, bool):
        return None

    try:
        number = Decimal(str(value))

        if not number.is_finite():
            return None

        converted = float(number)

        if not math.isfinite(converted):
            return None

        return converted

    except (ValueError, TypeError, InvalidOperation):
        return None


def _label(value):
    """Convert a grouping value into a label."""

    if isinstance(value, (date, datetime)):
        return value.isoformat()

    return str(value)[:100]


def _chart(
    chart_type,
    title,
    labels,
    datasets,
    currency=True,
):
    """Create a frontend-compatible chart specification."""

    if not labels or not datasets:
        return None

    return {
        "type": chart_type,
        "title": title,
        "labels": labels,
        "datasets": datasets,
        "currency": currency,
        "source": "sql",
        "generated_from": "authorized_sql_result",
    }


def _dataset(label, values):
    """Create one Chart.js dataset."""

    return {
        "label": label,
        "data": values,
    }


def _group_chart(query, result, cfg):
    """Generate charts for grouped SQL results."""

    rows = result.get("table") or []

    if not rows:
        return None

    group = (
        query.get("group_by")
        or result.get("metrics", {}).get("group_by")
        or cfg["default_group"]
    )

    if group not in rows[0]:
        return None

    # Use the actual column returned by group_summary.
    if cfg["type_column"]:
        measure = de.primary_column(
            query["dataset"]
        )
    else:
        measure = cfg["amount_label"]

    if measure not in rows[0]:
        return None

    points = []

    for row in rows:
        value = _number(row.get(measure))

        if value is None:
            continue

        points.append((
            _label(row.get(group, "Unknown")),
            value,
        ))

    if not points:
        return None

    is_month = group == "Month"

    if is_month:
        points.sort(key=lambda x: x[0])
    else:
        points.sort(
            key=lambda x: x[1],
            reverse=True,
        )

    points = points[:MAX_CHART_POINTS]

    labels = [p[0] for p in points]
    values = [p[1] for p in points]

    chart_type = "line" if is_month else "bar"

    return _chart(
        chart_type,
        f"{cfg['label']} by {group}",
        labels,
        [_dataset(measure, values)],
        currency=measure in cfg["money_columns"],
    )


def _budget_chart(query, result, cfg):
    """Compare budget or target against actual."""

    metrics = result.get("metrics") or {}

    budget = _number(metrics.get("budget"))
    actual = _number(metrics.get("actual"))

    if budget is None or actual is None:
        return None

    return _chart(
        "bar",
        f"{cfg['label']}: Budget vs Actual",
        [
            cfg["budget_label"],
            cfg["amount_label"],
        ],
        [
            _dataset(
                "Amount",
                [budget, actual],
            )
        ],
    )


def _variance_chart(query, result, cfg):
    """Visualize grouped budget variances."""

    rows = result.get("table") or []

    if not rows:
        return None

    group = (
        query.get("group_by")
        or result.get("metrics", {}).get("group_by")
        or cfg["default_group"]
    )

    points = []

    for row in rows:
        budget = _number(row.get("Budget"))
        actual = _number(row.get("Actual"))

        if budget is None or actual is None:
            continue

        points.append((
            _label(row.get(group, "Unknown")),
            budget,
            actual,
        ))

    if not points:
        return None

    points = points[:MAX_CHART_POINTS]

    return _chart(
        "bar",
        f"{cfg['label']} Variance by {group}",
        [p[0] for p in points],
        [
            _dataset(
                "Budget",
                [p[1] for p in points],
            ),
            _dataset(
                "Actual",
                [p[2] for p in points],
            ),
        ],
    )


def _comparison_chart(query, result, cfg):
    """Visualize two comparable financial values."""

    metrics = result.get("metrics") or {}

    a = metrics.get("a") or {}
    b = metrics.get("b") or {}

    label_a = metrics.get("label_a", "A")
    label_b = metrics.get("label_b", "B")

    metric = (
        "expense"
        if cfg["type_column"]
        else "primary_actual"
    )

    value_a = _number(a.get(metric))
    value_b = _number(b.get(metric))

    if value_a is None or value_b is None:
        return None

    return _chart(
        "bar",
        f"{cfg['label']} Comparison",
        [label_a, label_b],
        [
            _dataset(
                cfg["amount_label"],
                [value_a, value_b],
            )
        ],
    )


def _period_chart(query, result, cfg):
    """
    Visualize MoM, DoD, YoY and QoQ comparisons.

    Supports metrics containing previous_total
    and current_total.
    """

    metrics = result.get("metrics") or {}

    previous = _number(
        metrics.get("previous_total")
    )

    current = _number(
        metrics.get("current_total")
    )

    if previous is None or current is None:
        return None

    period_type = (
        query.get("period_type")
        or query.get("comparison_type")
        or "Period"
    )

    return _chart(
        "bar",
        f"{cfg['label']} {str(period_type).upper()} Comparison",
        ["Previous Period", "Current Period"],
        [
            _dataset(
                cfg["amount_label"],
                [previous, current],
            )
        ],
    )


def build_visualization(query, result, user=None):
    """
    Main visualization entry point.

    Returns:
        Chart.js specification or None.

    Security:
    - Requires an authenticated Admin user.
    - Checks dataset permissions.
    - Uses only already-authorized SQL results.
    - Does not access the database independently.
    """

    if not isinstance(query, dict):
        return None

    if not isinstance(result, dict):
        return None

    if result.get("error") or result.get("empty"):
        return None

    key = query.get("dataset")

    if key not in de.DATASETS:
        return None

    if de.dataset_access_problem(user, key):
        return None

    # Require the SQL result to match the requested dataset.
    if result.get("dataset") != key:
        return None

    if result.get("verification_method") != (
        "deterministic_sql_execution"
    ):
        return None

    cfg = de.DATASETS[key]
    operation = result.get("operation")

    if operation == "group_summary":
        return _group_chart(query, result, cfg)

    if operation == "budget_vs_actual":
        return _budget_chart(query, result, cfg)

    if operation == "variance":
        return _variance_chart(query, result, cfg)

    if operation == "compare":
        return _comparison_chart(query, result, cfg)

    if operation == "period_comparison":
        return _period_chart(query, result, cfg)

    # A single total or count usually does not
    # require a chart.
    return None
