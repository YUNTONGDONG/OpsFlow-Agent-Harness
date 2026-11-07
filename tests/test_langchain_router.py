import unittest

from src.harness import LangChainRequestRouter, RouteDecision


class FakeStructuredModel:
    def __init__(self, result):
        self.result = result
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.result


class SequencedStructuredModel:
    def __init__(self, results):
        self.results = iter(results)

    def invoke(self, messages):
        return next(self.results)


class FakeChatModel:
    def __init__(self, result):
        self.structured_model = FakeStructuredModel(result)
        self.schema = None
        self.method = None

    def with_structured_output(self, schema, *, method=None):
        self.schema = schema
        self.method = method
        return self.structured_model


class LangChainRequestRouterTest(unittest.TestCase):
    def test_router_uses_route_schema_and_available_skill_metadata(self) -> None:
        model = FakeChatModel(
            {
                "steps": [
                    {
                        "step_id": "knowledge",
                        "skill_name": "knowledge_search",
                        "arguments": {"query": "E102"},
                    }
                ]
            }
        )
        router = LangChainRequestRouter(model)

        decision = router.route(
            "How do I handle E102?",
            [
                {
                    "name": "knowledge_search",
                    "description": "Search internal knowledge.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                    "output_schema": {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                    },
                }
            ],
            [{"role": "user", "content": "Payment service failed."}],
        )

        self.assertIs(model.schema, RouteDecision)
        self.assertEqual(model.method, "function_calling")
        self.assertEqual(decision.steps[0].skill_name, "knowledge_search")
        system_message = model.structured_model.messages[0][1]
        self.assertIn("knowledge_search", system_message)
        self.assertIn("Search internal knowledge", system_message)
        self.assertIn("Output schema", system_message)
        self.assertIn("$from", system_message)
        self.assertEqual(
            model.structured_model.messages[-2],
            ("user", "Payment service failed."),
        )

    def test_invalid_model_output_is_rejected(self) -> None:
        router = LangChainRequestRouter(FakeChatModel({"steps": []}))

        with self.assertRaises(Exception):
            router.route("test", [])

    def test_router_can_request_missing_business_parameters(self) -> None:
        router = LangChainRequestRouter(
            FakeChatModel(
                {
                    "steps": [],
                    "clarification": {
                        "question": "会议安排在什么时间？",
                        "missing_fields": ["start", "end"],
                    },
                }
            )
        )

        decision = router.route("帮我创建一个项目会议", [])

        self.assertEqual(decision.steps, [])
        self.assertEqual(
            decision.clarification.missing_fields,
            ["start", "end"],
        )

    def test_router_retries_one_invalid_structured_result(self) -> None:
        model = FakeChatModel({})
        model.structured_model = SequencedStructuredModel(
            [
                {"steps": []},
                {
                    "steps": [],
                    "clarification": {
                        "question": "你想查询什么内容？",
                        "missing_fields": ["query"],
                    },
                },
            ]
        )
        router = LangChainRequestRouter(model)

        decision = router.route("从知识库查一下", [])

        self.assertEqual(decision.clarification.missing_fields, ["query"])


if __name__ == "__main__":
    unittest.main()
