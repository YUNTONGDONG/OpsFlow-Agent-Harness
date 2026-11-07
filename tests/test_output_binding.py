import unittest

from src.harness import OutputBindingError, resolve_output_references


class OutputBindingTest(unittest.TestCase):
    def test_resolves_references_inside_nested_arguments(self) -> None:
        arguments = {
            "subject": {"$from": "diagnose.output.issue_title"},
            "labels": [
                "incident",
                {"$from": "diagnose.output.matched_error_codes"},
            ],
        }

        resolved = resolve_output_references(
            arguments,
            {
                "diagnose": {
                    "issue_title": "Investigate E102",
                    "matched_error_codes": ["E102"],
                }
            },
        )

        self.assertEqual(resolved["subject"], "Investigate E102")
        self.assertEqual(resolved["labels"], ["incident", ["E102"]])

    def test_missing_field_fails_without_guessing(self) -> None:
        with self.assertRaisesRegex(OutputBindingError, "not available"):
            resolve_output_references(
                {"subject": {"$from": "diagnose.output.issue_title"}},
                {"diagnose": {"summary": "No title"}},
            )

    def test_reference_object_cannot_mix_literal_fields(self) -> None:
        with self.assertRaisesRegex(OutputBindingError, "only"):
            resolve_output_references(
                {
                    "subject": {
                        "$from": "diagnose.output.issue_title",
                        "fallback": "guessed title",
                    }
                },
                {"diagnose": {"issue_title": "Investigate E102"}},
            )


if __name__ == "__main__":
    unittest.main()
