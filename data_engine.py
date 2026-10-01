import pandas as pd


# Load dataset
DATA_FILE = "data/synthetic_transactions_1000.xlsx"

df = pd.read_excel(DATA_FILE)

# Clean column names
df.columns = df.columns.str.strip()

# Convert date column
df["Transaction Date"] = pd.to_datetime(
    df["Transaction Date"],
    errors="coerce"
)


def get_data():
    """
    Returns the complete dataset.
    """
    return df.copy()


def search_transactions(
    transaction_id=None,
    department=None,
    category=None,
    transaction_type=None,
    payment_status=None,
    vendor=None,
    currency=None
):
    """
    Filters transactions based on the supplied conditions.
    """

    result = df.copy()

    if transaction_id:
        result = result[
            result["Transaction ID"].astype(str).str.lower()
            == transaction_id.lower()
        ]

    if department:
        result = result[
            result["Department"].astype(str).str.lower()
            == department.lower()
        ]

    if category:
        result = result[
            result["Category"].astype(str).str.lower()
            == category.lower()
        ]

    if transaction_type:
        result = result[
            result["Transaction Type"].astype(str).str.lower()
            == transaction_type.lower()
        ]

    if payment_status:
        result = result[
            result["Payment Status"].astype(str).str.lower()
            == payment_status.lower()
        ]

    if vendor:
        result = result[
            result["Vendor / Customer"].astype(str).str.lower()
            .str.contains(vendor.lower(), na=False)
        ]

    if currency:
        result = result[
            result["Currency"].astype(str).str.upper()
            == currency.upper()
        ]

    return result


def calculate_total(data):
    return float(data["Amount"].sum())


def calculate_average(data):
    return float(data["Amount"].mean())


def count_transactions(data):
    return int(len(data))


def budget_vs_actual(data):
    actual = float(data["Amount"].sum())
    budget = float(data["Budget"].sum())

    variance = budget - actual

    return {
        "budget": budget,
        "actual": actual,
        "variance": variance
    }


def get_top_expenses(data, n=5):
    return data.sort_values(
        by="Amount",
        ascending=False
    ).head(n)