
"""
Business explanations for Pricing Copilot.

Converts authorized SQL results into meaningful,
evidence-grounded natural-language explanations.

Does not call Groq or query the database.
"""

from decimal import Decimal, InvalidOperation


def number(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def money(value, currency="INR"):
    amount = number(value)

    symbols = {
        "INR": "₹",
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
    }

    symbol = symbols.get(currency, currency + " ")

    return f"{symbol}{amount:,.2f}"


def percentage(value):
    if value is None:
        return "Not available"

    return f"{number(value):,.2f}%"


def describe_variance(dataset, variance):
    value = number(variance)

    if dataset == "sales":
        if value < 0:
            return "below the recorded sales target"
        if value > 0:
            return "above the recorded sales target"
        return "equal to the recorded sales target"

    if value < 0:
        return "over the recorded budget"
    if value > 0:
        return "under the recorded budget"

    return "equal to the recorded budget"


def explain_dataset_summary(dataset, metrics):
    """
    Explain a dataset-wide financial summary.
    """

    count = int(number(metrics.get("count", 0)))
    total = number(metrics.get("total", 0))
    average = number(metrics.get("average", 0))

    actual = number(
        metrics.get("primary_actual", total)
    )

    budget = number(
        metrics.get("primary_budget", 0)
    )

    variance = number(
        metrics.get("variance", 0)
    )

    names = {
        "sales": "Sales Orders",
        "procurement": "Procurement Orders",
        "payroll": "HR Payroll",
        "transactions": "Transactions",
    }

    label = names.get(dataset, dataset)

    lines = [
        f"## {label} overview",
        "",
        f"The dataset contains **{count:,} records**.",
        "",
    ]

    if dataset == "sales":
        lines.append(
            f"The combined recorded order value is "
            f"**{money(total)}**, with an average "
            f"order value of **{money(average)}**."
        )

        lines.append(
            f"The recorded sales target is "
            f"**{money(budget)}**, compared with "
            f"actual recorded order value of "
            f"**{money(actual)}**."
        )

    elif dataset == "procurement":
        lines.append(
            f"The purchase orders represent "
            f"**{money(total)}** in recorded "
            f"procurement costs, averaging "
            f"**{money(average)}** per order."
        )

    elif dataset == "payroll":
        lines.append(
            f"The total recorded gross payroll "
            f"cost is **{money(total)}**, with "
            f"an average of **{money(average)}** "
            f"per payroll record."
        )

    else:
        lines.append(
            f"The selected transactions total "
            f"**{money(total)}**, averaging "
            f"**{money(average)}** per record."
        )

    lines.extend([
        "",
        "### Financial interpretation",
        "",
    ])

    if budget > 0:
        difference = abs(variance)
        variance_pct = difference / budget * 100

        lines.append(
            f"Performance is "
            f"**{describe_variance(dataset, variance)}** "
            f"by **{money(difference)}**, equivalent "
            f"to approximately "
            f"**{percentage(variance_pct)}** "
            f"of the recorded target or budget."
        )

    if dataset == "sales":
        lines.append(
            "These figures describe recorded "
            "sales order value. Cancelled or "
            "returned orders may still be "
            "included, so this total should "
            "not automatically be treated "
            "as recognized revenue."
        )

    elif dataset == "procurement":
        lines.append(
            "The total procurement value "
            "shows purchasing activity, but "
            "it does not independently "
            "establish whether purchases "
            "were approved or compliant."
        )

    elif dataset == "payroll":
        lines.append(
            "Payroll records represent "
            "compensation expenditure. "
            "The average is per payroll "
            "record, not necessarily "
            "per unique employee."
        )
    if dataset == "sales" and budget > 0:
        lines.extend([
            "",
            explain_sales_performance(metrics)
        ])
    return "\n\n".join(lines)


def explain_group_summary(dataset, group_by, rows):
    """
    Explain grouped SQL results using actual group values.
    """

    if not rows:
        return "No matching grouped records were found."

    amount_columns = {
        "sales": "Order Value",
        "procurement": "Total Cost",
        "payroll": "Gross Pay",
        "transactions": "Expense",
    }

    amount_column = amount_columns.get(
        dataset,
        "Amount",
    )

    ordered = sorted(
        rows,
        key=lambda row: number(
            row.get(amount_column, 0)
        ),
        reverse=True,
    )

    lines = [
        f"## Breakdown by {group_by}",
        "",
        f"The analysis covers "
        f"**{len(ordered)} groups**.",
        "",
    ]

    for index, row in enumerate(ordered, 1):
        name = row.get(group_by, "Unknown")
        amount = number(
            row.get(amount_column, 0)
        )

        lines.append(
            f"{index}. **{name}** — "
            f"{money(amount)}"
        )

    total = sum(
        (
            number(row.get(amount_column, 0))
            for row in ordered
        ),
        Decimal("0"),
    )

    if total > 0:
        largest = ordered[0]
        largest_value = number(
            largest.get(amount_column, 0)
        )

        share = largest_value / total * 100

        lines.extend([
            "",
            "### What this means",
            "",
            f"**{largest.get(group_by, 'Unknown')}** "
            f"has the highest recorded "
            f"{amount_column.lower()}, contributing "
            f"approximately **{percentage(share)}** "
            f"of the total across these groups.",
        ])

    return "\n".join(lines)


def explain_variances(dataset, group_by, rows):
    """
    Explain group-level target or budget variances.
    """

    if not rows:
        return "No variance results were available."

    unfavourable = (
        "Below target"
        if dataset == "sales"
        else "Over budget"
    )

    flagged = [
        row for row in rows
        if row.get("Status") == unfavourable
    ]

    lines = [
        f"## {group_by} performance analysis",
        "",
        f"The analysis evaluated "
        f"**{len(rows)} groups**.",
        "",
    ]

    if not flagged:
        lines.append(
            f"No groups were classified as "
            f"**{unfavourable.lower()}** "
            f"under the configured tolerance."
        )
        return "\n".join(lines)

    lines.append(
        f"**{len(flagged)} groups** were "
        f"classified as {unfavourable.lower()}."
    )

    lines.extend([
        "",
        "### Groups requiring attention",
        "",
    ])

    flagged.sort(
        key=lambda row: number(
            row.get("Variance", 0)
        )
    )

    for index, row in enumerate(flagged, 1):
        name = row.get(group_by, "Unknown")

        actual = number(
            row.get("Actual", 0)
        )

        budget = number(
            row.get("Budget", 0)
        )

        variance = number(
            row.get("Variance", 0)
        )

        lines.extend([
            f"**{index}. {name}**",
            f"- Actual: {money(actual)}",
            f"- Target/Budget: {money(budget)}",
            f"- Unfavourable difference: "
            f"{money(abs(variance))}",
            f"- Variance: "
            f"{percentage(row.get('Variance %'))}",
            "",
        ])

    lines.extend([
        "### Business interpretation",
        "",
        "These groups have unfavourable "
        "performance relative to their "
        "recorded targets or budgets. "
        "The figures identify where gaps "
        "exist, but do not independently "
        "establish the causes.",
    ])

    return "\n".join(lines)



def explain_comparison(dataset, result):
    """
    Explain both sides of a SQL comparison,
    including actual values and differences.
    """

    table = result.get("table") or []

    if not table:
        return (
            "The comparison could not be completed "
            "because no comparison records were returned."
        )

    lines = [
        "## Comparison results",
        "",
    ]

    for row in table:

        metric = row.get("Metric", "Metric")

        reserved = {
            "Metric",
            "Difference",
            "% Change",
        }

        group_columns = [
            key for key in row
            if key not in reserved
        ]

        if len(group_columns) < 2:
            continue

        first = group_columns[0]
        second = group_columns[1]

        first_value = number(row.get(first))
        second_value = number(row.get(second))
        difference = number(row.get("Difference"))
        change = row.get("% Change")

        is_count = metric.lower() in {
            "records",
            "transactions",
            "count",
        }

        def display(value):
            if is_count:
                return f"{int(value):,}"
            return money(value)

        lines.extend([
            f"### {metric}",
            "",
            f"- **{first}:** {display(first_value)}",
            f"- **{second}:** {display(second_value)}",
            f"- **Difference ({second} − {first}):** "
            f"{display(difference)}",
        ])

        if change is not None:
            lines.append(
                f"- **Percentage change:** "
                f"{percentage(change)}"
            )

        lines.append("")

    lines.extend([
        "### Business interpretation",
        "",
        "The comparison shows the financial "
        "differences between the selected groups. "
        "For sales, a higher order value indicates "
        "more recorded sales activity, but does "
        "not necessarily mean better profitability. "
        "Target performance should be evaluated "
        "using each group's actual and target values.",
    ])

    return "\n".join(lines)

def explain_sales_performance(metrics):
    """
    Explain sales performance in business language
    using deterministic SQL metrics.
    """

    actual = number(
        metrics.get("primary_actual")
    )

    target = number(
        metrics.get("primary_budget")
    )

    if target <= 0:
        return (
            "A valid sales target is not available, "
            "so target achievement cannot be calculated."
        )

    shortfall = target - actual

    achievement_pct = (
        actual / target * 100
    )

    gap_pct = (
        abs(shortfall) / target * 100
    )

    lines = [
        "### What this means for the business",
        "",
        f"The business has achieved approximately "
        f"**{achievement_pct:.2f}%** of its recorded "
        f"sales target.",
    ]

    if shortfall > 0:
        lines.extend([
            "",
            f"Recorded order value is "
            f"**{money(shortfall)} below target**, "
            f"representing a gap of "
            f"**{gap_pct:.2f}%**.",
            "",
            "This indicates that recorded sales "
            "activity is below the target set "
            "for the selected records.",
        ])

    elif shortfall < 0:
        lines.extend([
            "",
            f"Recorded order value exceeds "
            f"the target by "
            f"**{money(abs(shortfall))}**.",
        ])

    else:
        lines.append(
            "Recorded order value exactly "
            "matches the target."
        )

    lines.extend([
        "",
        "The overall result does not establish "
        "which regions, products, or sales "
        "representatives caused the difference. "
        "Those factors require a more detailed "
        "breakdown using the SQL records.",
        "",
        "Cancelled and returned orders may be "
        "included in recorded order value, so "
        "this should not automatically be "
        "interpreted as recognized revenue.",
    ])

    return "\n".join(lines)

    