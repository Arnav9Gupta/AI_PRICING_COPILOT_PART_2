import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from backend import chatbot, data_engine, database, documents
from backend import initialize_database


class SQLiteDatabaseTests(unittest.TestCase):
    def test_initialized_sqlite_supports_financial_and_fts_document_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = os.path.join(directory, "copilot.sqlite3")
            with patch.dict(os.environ, {"SQLITE_DATABASE_PATH": db_path}):
                initialize_database.initialize()
                self.assertEqual(database.validate_schema(), {})

                with database.connect(read_only=False) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO transactions
                                ("Transaction ID", "Transaction Date",
                                 "Department", "Category", "Amount")
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            ("TX-1", "2026-01-10", "Finance", "Travel", 25.5),
                        )
                        cursor.execute(
                            """
                            INSERT INTO documents
                                (id, original_name, storage_name, content_type,
                                 size_bytes, owner, status)
                            VALUES (?, ?, ?, ?, ?, ?, 'ready')
                            """,
                            (
                                "doc-1", "policy.pdf", "doc-1.pdf",
                                "application/pdf", 100, "analyst",
                            ),
                        )
                        cursor.execute(
                            """
                            INSERT INTO document_chunks
                                (document_id, chunk_number, page_number,
                                 chunk_text, embedding)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                "doc-1", 1, 2,
                                "Travel budget approvals require a manager.",
                                json.dumps([1.0] + [0.0] * 383),
                            ),
                        )

                records = database.fetch_records(
                    "transactions",
                    filters={"equals": {"Department": ["Finance"]}},
                    allowed_departments=["Finance"],
                )
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["Transaction ID"], "TX-1")

                passages = documents._retrieval_context(
                    {"username": "analyst", "role": "Analyst"},
                    "travel budget approvals",
                    [1.0] + [0.0] * 383,
                )
                self.assertEqual(len(passages), 1)
                self.assertEqual(passages[0]["name"], "policy.pdf")
                self.assertEqual(passages[0]["page"], 2)


class DatabaseQueryTests(unittest.TestCase):
    def test_user_values_are_bound_and_not_interpolated(self):
        injected_filter = "'; DROP TABLE transactions; --"
        query, parameters = database.build_select(
            "transactions",
            filters={
                "equals": {"Category": [injected_filter]},
                "contains": {"Vendor / Customer": "A%_"},
                "start_date": "2026-01-01",
                "end_date": "2026-01-31",
                "min_amount": Decimal("12.34"),
            },
            allowed_departments=["Finance"],
        )

        self.assertTrue(query.startswith("SELECT "))
        self.assertNotIn(injected_filter, query)
        self.assertNotIn("A%_", query)
        self.assertNotIn("DROP TABLE", query.upper())
        self.assertIn(injected_filter.lower(), parameters)
        self.assertIn("A%_", parameters)
        self.assertIn("finance", parameters)
        self.assertIn("12.34", parameters)
        self.assertNotIn(";", query)

    def test_only_allowlisted_columns_and_tables_are_accepted(self):
        with self.assertRaises(ValueError):
            database.build_select(
                "transactions",
                filters={"equals": {"Amount; DROP TABLE x": ["1"]}},
            )

        with self.assertRaises(ValueError):
            database.build_select("unapproved_table")

    def test_date_ranges_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "ten years"):
            database.build_select(
                "transactions",
                filters={
                    "start_date": "2000-01-01",
                    "end_date": "2020-01-01",
                },
            )

    def test_department_scope_is_in_sql_or_rejects_unscoped_dataset(self):
        payroll_query, payroll_parameters = database.build_select(
            "payroll",
            filters={"equals": {"Department": ["IT"]}},
            allowed_departments=["Engineering"],
        )
        self.assertIn('lower("Department") IN (?)', payroll_query)
        self.assertIn("engineering", payroll_parameters)
        self.assertIn("it", payroll_parameters)

        query, parameters = database.build_select(
            "sales", allowed_departments=["Engineering"]
        )
        self.assertIsNone(query)
        self.assertEqual(parameters, [])


class FinancialCalculationTests(unittest.TestCase):
    def test_totals_use_decimal_arithmetic(self):
        rows = [{"Amount": Decimal("0.10")}, {"Amount": Decimal("0.20")}]
        self.assertEqual(
            data_engine.calculate_total(rows, "Amount"), Decimal("0.30")
        )

    def test_average_excludes_missing_values(self):
        rows = [
            {"Amount": Decimal("0.10")},
            {"Amount": None},
            {"Amount": Decimal("0.20")},
        ]
        self.assertEqual(
            data_engine.calculate_average(rows, "Amount"), Decimal("0.15")
        )

    def test_missing_llm_supports_basic_sql_question_deterministically(self):
        query = chatbot._fallback_understand_question(
            "What were total expenses last month?"
        )

        self.assertEqual(query["dataset"], "transactions")
        self.assertEqual(query["operation"], "total")
        self.assertEqual(
            query["filters"]["equals"]["Transaction Type"], ["Expense"]
        )
        start = datetime.strptime(query["filters"]["start_date"], "%Y-%m-%d")
        end = datetime.strptime(query["filters"]["end_date"], "%Y-%m-%d")
        self.assertLessEqual(start, end)

    def test_result_explanation_is_local(self):
        result = {
            "operation": "total",
            "message": "Total is ₹10.00.",
            "metrics": {"total": Decimal("10.00")},
            "data": [{"Vendor / Customer": "Sensitive Vendor"}],
        }
        with patch.object(chatbot, "client") as llm, patch.object(
            chatbot, "_rule_based_explanation", return_value="Local explanation"
        ):
            explanation = chatbot.explain_result("question", {}, result)

        self.assertEqual(explanation, "Local explanation")
        llm.chat.completions.create.assert_not_called()

    def test_schema_prompt_does_not_fetch_or_include_database_values(self):
        with patch.object(
            data_engine, "get_unique_values",
            side_effect=AssertionError("The LLM prompt must not fetch values"),
        ):
            prompt = chatbot._dataset_block("transactions", user=None)

        self.assertIn("Category", prompt)
        self.assertNotIn("Sensitive Vendor", prompt)

    def test_database_configuration_error_is_safe_and_unverified(self):
        query = chatbot.make_query("total", dataset="transactions")
        with patch.object(
            database,
            "connect",
            side_effect=database.DatabaseConfigurationError(
                "The SQLite database is not initialized."
            ),
        ):
            result = chatbot.execute_query(query)

        self.assertEqual(result["error"], "database_unavailable")
        self.assertEqual(result["reliability_status"], "No answer")
        self.assertNotIn("Traceback", result["message"])

    def test_missing_metric_values_mark_results_partially_verified(self):
        query = chatbot.make_query("total", dataset="transactions")
        result = chatbot._result(
            "total",
            metrics={
                "measure": "Amount",
                "total": Decimal("10.00"),
                "count": 2,
            },
            warnings=["Missing values: 1 of 2 records have no Amount value."],
        )
        with patch.object(chatbot, "_execute", return_value=result):
            answer = chatbot.execute_query(query)

        self.assertEqual(answer["reliability_status"], "Partially verified")


if __name__ == "__main__":
    unittest.main()
