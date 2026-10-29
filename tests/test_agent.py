import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend import agent, chatbot
from backend.main import app, current_user


USER = {
    "username": "analyst",
    "name": "Analyst",
    "role": "Analyst",
    "datasets": ["transactions", "sales"],
    "departments": None,
    "hidden_columns": ["Vendor / Customer", "Customer"],
}


class AgentRoutingTests(unittest.TestCase):
    def test_rule_router_selects_sql_document_and_both(self):
        self.assertEqual(
            agent._rule_route("What was our total sales revenue?")["route"],
            "sql",
        )
        self.assertEqual(
            agent._rule_route("What does the travel policy say about expenses?")[
                "route"
            ],
            "documents",
        )
        self.assertEqual(
            agent._rule_route(
                "Compare our total travel spending with the travel policy."
            )["route"],
            "both",
        )

    def test_rule_router_clarifies_ambiguous_or_contextless_follow_up(self):
        self.assertEqual(agent._rule_route("Show it")["route"], "clarify")
        self.assertEqual(agent._rule_route("Tell me more")["route"], "clarify")

    def test_rule_router_uses_only_user_history_for_follow_up(self):
        route = agent._rule_route(
            "What about last month?",
            history=[
                {"role": "user", "content": "What was total sales revenue?"},
                {
                    "role": "assistant",
                    "content": "Do not use assistant output as routing evidence.",
                },
            ],
        )
        self.assertEqual(route["route"], "sql")

    def test_llm_router_only_receives_authorized_dataset_names_and_user_history(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"route":"sql","clarification":""}'
                    )
                )
            ]
        )

        result = agent.classify_question(
            "What was our sales revenue?",
            USER,
            history=[
                {"role": "assistant", "content": "Do not transmit this result."},
                {"role": "user", "content": "How did sales perform?"},
            ],
            client=client,
        )

        user_message = client.chat.completions.create.call_args.kwargs[
            "messages"
        ][1]["content"]
        payload = json.loads(user_message)
        self.assertEqual(result["route"], "sql")
        self.assertEqual(
            payload["authorized_financial_datasets"],
            ["Transactions", "Sales Orders"],
        )
        self.assertEqual(
            payload["recent_user_questions"], ["How did sales perform?"]
        )
        self.assertNotIn("Do not transmit this result", user_message)

    def test_invalid_llm_route_falls_back_and_surfaces_warning(self):
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"route":"execute_sql","clarification":""}'
                    )
                )
            ]
        )

        result = agent.classify_question(
            "What was total sales revenue?", USER, client=client
        )

        self.assertEqual(result["route"], "sql")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["method"], "rules")

    def test_question_length_is_validated(self):
        with self.assertRaises(agent.AgentError):
            agent.classify_question(" " * (agent.MAX_QUESTION_CHARACTERS + 1), USER)


class AgentExecutionTests(unittest.TestCase):
    def test_unified_api_requires_authentication(self):
        from fastapi.testclient import TestClient

        response = TestClient(app).post(
            "/agent/chat",
            json={"question": "What was total sales revenue?"},
        )

        self.assertEqual(response.status_code, 401)

    def test_clarification_returns_before_either_tool_runs(self):
        with (
            patch.object(
                agent,
                "classify_question",
                return_value={
                    "route": "clarify",
                    "clarification": "Which dataset should I use?",
                    "warnings": [],
                },
            ),
            patch.object(agent.chatbot, "execute_query") as sql_tool,
            patch.object(agent.documents, "answer_document_question") as doc_tool,
        ):
            response = agent.answer_question("Compare the results.", USER)

        self.assertEqual(response["route"], "clarify")
        self.assertEqual(response["answer"], "Which dataset should I use?")
        sql_tool.assert_not_called()
        doc_tool.assert_not_called()

    def test_sql_route_uses_existing_sql_workflow(self):
        query = chatbot.make_query("total", dataset="sales")
        result = chatbot._result(
            "total",
            message="The total sales revenue is 100.",
            dataset="sales",
            dataset_label="Sales Orders",
            metrics={"total": 100},
        )
        with (
            patch.object(
                agent,
                "classify_question",
                return_value={"route": "sql", "warnings": []},
            ),
            patch.object(agent.chatbot, "understand_question", return_value=query),
            patch.object(agent.chatbot, "execute_query", return_value=result),
            patch.object(agent.chatbot, "explain_result", return_value=""),
            patch.object(agent.chatbot, "suggest_followups", return_value=[]),
        ):
            response = agent.answer_question("What was total sales revenue?", USER)

        self.assertEqual(response["route"], "sql")
        self.assertIs(response["result"], result)
        self.assertEqual(response["answer"], result["message"])

    def test_document_route_uses_document_tool(self):
        document_result = {
            "answer": "Manager approval is required [S1].",
            "reliability_status": "Partially verified",
            "sources": [
                {
                    "source": "S1",
                    "document_id": "doc-1",
                    "name": "travel.pdf",
                    "page": 3,
                    "section": None,
                    "text": "Manager approval is required.",
                }
            ],
            "warnings": [],
        }
        with (
            patch.object(
                agent,
                "classify_question",
                return_value={"route": "documents", "warnings": []},
            ),
            patch.object(
                agent.documents,
                "answer_document_question",
                return_value=document_result,
            ) as document_tool,
        ):
            response = agent.answer_question(
                "What does the travel policy say?", USER
            )

        document_tool.assert_called_once()
        self.assertEqual(response["route"], "documents")
        self.assertEqual(response["result"]["sources"], document_result["sources"])

    def test_mixed_route_calls_both_tools_and_returns_both_evidence_types(self):
        query = chatbot.make_query("total", dataset="sales")
        sql_result = chatbot._result(
            "total",
            message="Total travel spending is 500.",
            dataset="sales",
            dataset_label="Sales Orders",
            metrics={"total": 500},
        )
        passages = [
            {
                "document_id": "doc-1",
                "name": "travel-policy.pdf",
                "page": 5,
                "section": None,
                "text": "Travel spending requires manager approval.",
            }
        ]
        combined = {
            "answer": "Spending is 500 [SQL]; approval is required [D1].",
            "reliability_status": "Partially verified",
            "warnings": [],
        }
        with (
            patch.object(
                agent,
                "classify_question",
                return_value={"route": "both", "warnings": []},
            ),
            patch.object(agent.chatbot, "understand_question", return_value=query),
            patch.object(agent.chatbot, "execute_query", return_value=sql_result),
            patch.object(
                agent.documents,
                "retrieve_document_passages",
                return_value={"passages": passages, "warning": None},
            ) as doc_tool,
            patch.object(agent, "_combined_evidence", return_value=combined),
            patch.object(agent.chatbot, "suggest_followups", return_value=[]),
        ):
            response = agent.answer_question(
                "Compare travel spending with the travel policy.", USER
            )

        doc_tool.assert_called_once()
        self.assertEqual(response["route"], "both")
        self.assertEqual(response["result"]["sources"][0]["source"], "D1")
        self.assertEqual(response["result"]["message"], combined["answer"])

    def test_combined_prompt_excludes_raw_sql_rows_and_requires_citations(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Total is 500 [SQL]; approval is required [D1]."
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"supported": true}')
                    )
                ]
            ),
        ]
        sql_result = {
            "dataset_label": "Transactions",
            "dataset": "transactions",
            "operation": "total",
            "message": "The total is 500 for Secret Vendor.",
            "metrics": {
                "total": 500,
                "measure": "Amount",
                "department": "Sensitive Department",
            },
            "data": [{"Vendor / Customer": "Secret Vendor"}],
        }
        passages = [
            {
                "name": "policy.pdf",
                "page": 2,
                "section": None,
                "text": "Approval is required.",
            }
        ]

        result = agent._combined_evidence(
            "Question?", sql_result, passages, client, "test-model"
        )

        synthesis_payload = json.loads(
            client.chat.completions.create.call_args_list[0].kwargs[
                "messages"
            ][1]["content"]
        )
        encoded = json.dumps(synthesis_payload)
        self.assertNotIn("Secret Vendor", encoded)
        self.assertNotIn("Sensitive Department", encoded)
        self.assertEqual(
            synthesis_payload["sql_result"]["verified_aggregate_metrics"],
            {"total": 500},
        )
        self.assertEqual(
            synthesis_payload["sql_result"]["descriptors"],
            {"measure": "Amount"},
        )
        self.assertEqual(result["reliability_status"], "Partially verified")

    def test_combined_without_llm_shows_separate_cited_evidence(self):
        result = agent._combined_evidence(
            "Compare total with the policy.",
            {
                "dataset_label": "Transactions",
                "dataset": "transactions",
                "operation": "total",
                "message": "The total is 500 for Secret Vendor.",
                "metrics": {"total": 500, "measure": "Amount"},
            },
            [
                {
                    "name": "policy.pdf",
                    "page": 4,
                    "section": None,
                    "text": "Approval is required.",
                }
            ],
            None,
            "test-model",
        )

        self.assertIn("[SQL]", result["answer"])
        self.assertIn("[D1]", result["answer"])
        self.assertNotIn("Secret Vendor", result["answer"])
        self.assertEqual(result["reliability_status"], "Partially verified")

    def test_missing_document_evidence_returns_sql_result_with_warning(self):
        query = chatbot.make_query("total", dataset="sales")
        sql_result = chatbot._result(
            "total",
            message="The total sales revenue is 100.",
            dataset="sales",
            dataset_label="Sales Orders",
            metrics={"total": 100},
        )
        with (
            patch.object(
                agent,
                "classify_question",
                return_value={"route": "both", "warnings": []},
            ),
            patch.object(agent.chatbot, "understand_question", return_value=query),
            patch.object(agent.chatbot, "execute_query", return_value=sql_result),
            patch.object(
                agent.documents,
                "retrieve_document_passages",
                return_value={"passages": [], "warning": None},
            ),
            patch.object(agent.chatbot, "explain_result", return_value=""),
            patch.object(agent.chatbot, "suggest_followups", return_value=[]),
        ):
            response = agent.answer_question(
                "Compare total sales revenue with the policy.", USER
            )

        self.assertEqual(response["route"], "both")
        self.assertEqual(response["answer"], sql_result["message"])
        self.assertEqual(response["result"]["reliability_status"], "Partially verified")
        self.assertTrue(response["result"]["warnings"])

    def test_unified_api_route_calls_agent_and_returns_route_shape(self):
        from fastapi.testclient import TestClient

        original_overrides = app.dependency_overrides.copy()
        app.dependency_overrides[current_user] = lambda: USER
        try:
            with patch.object(
                agent,
                "answer_question",
                return_value={
                    "route": "clarify",
                    "answer": "Which source should I use?",
                    "query": None,
                    "result": {
                        "message": "Which source should I use?",
                        "error": "clarify",
                    },
                    "explanation": "",
                    "suggestions": [],
                },
            ) as answer_question:
                response = TestClient(app).post(
                    "/agent/chat",
                    json={"question": "Compare those results"},
                )
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(original_overrides)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"], "clarify")
        answer_question.assert_called_once()


if __name__ == "__main__":
    unittest.main()
