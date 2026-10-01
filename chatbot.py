import json
import os

from groq import Groq
from dotenv import load_dotenv

from data_engine import (
    search_transactions,
    calculate_total,
    calculate_average,
    count_transactions,
    budget_vs_actual,
    get_top_expenses
)

# Load environment variables
load_dotenv()

api_key = os.getenv("GROQ_API_KEY")

print("API key found:", api_key is not None)

if not api_key:
    raise ValueError("GROQ_API_KEY was not found in the .env file")

# Create Groq client
client = Groq(api_key=api_key)


SYSTEM_PROMPT = """
You are the AI Pricing Copilot.

You answer questions using ONLY the structured financial dataset
provided by the application.

You must NOT invent financial values.

Convert the user's question into one of these operations:

1. search
2. total
3. average
4. count
5. budget_vs_actual
6. top_expenses

Available fields:

Transaction ID
Transaction Date
Department
Category
Transaction Type
Amount
Budget
Vendor / Customer
Payment Status
Currency

Return JSON only.

Example:

{
    "operation": "total",
    "filters": {
        "department": "IT"
    }
}

Another example:

{
    "operation": "search",
    "filters": {
        "department": "Sales",
        "payment_status": "Pending"
    }
}

If the question cannot be answered from these fields,
return:

{
    "operation": "unsupported",
    "filters": {}
}
"""


def understand_question(question):

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": question
            }
        ],
        temperature=0
    )

    text = response.choices[0].message.content

    return json.loads(text)


def execute_query(query):

    operation = query["operation"]
    filters = query.get("filters", {})

    data = search_transactions(
        transaction_id=filters.get("transaction_id"),
        department=filters.get("department"),
        category=filters.get("category"),
        transaction_type=filters.get("transaction_type"),
        payment_status=filters.get("payment_status"),
        vendor=filters.get("vendor"),
        currency=filters.get("currency")
    )

    if len(data) == 0:
        return {
            "message": "No matching financial data was found.",
            "data": []
        }

    if operation == "search":
        return {
            "message": f"{len(data)} matching transactions found.",
            "data": data.to_dict(orient="records")
        }

    if operation == "total":
        return {
            "total": calculate_total(data)
        }

    if operation == "average":
        return {
            "average": calculate_average(data)
        }

    if operation == "count":
        return {
            "count": count_transactions(data)
        }

    if operation == "budget_vs_actual":
        return budget_vs_actual(data)

    if operation == "top_expenses":
        result = get_top_expenses(data)

        return {
            "data": result.to_dict(orient="records")
        }

    return {
        "message": "I could not answer this question using the available dataset."
    }