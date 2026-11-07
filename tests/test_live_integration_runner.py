import unittest

from scripts.check_live_integrations import SkipProbe, safe_probe


class LiveIntegrationRunnerTest(unittest.TestCase):
    def test_skip_is_recorded_without_becoming_pass(self):
        def skipped():
            raise SkipProbe("missing sandbox")

        result = safe_probe("service", "boundary", skipped)

        self.assertEqual(result["status"], "SKIP")
        self.assertEqual(result["evidence"]["reason"], "missing sandbox")

    def test_failure_records_only_exception_type(self):
        def failed():
            raise RuntimeError("secret backend detail")

        result = safe_probe("service", "boundary", failed)

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["evidence"], {"error_type": "RuntimeError"})
        self.assertNotIn("secret", str(result))

    def test_pass_preserves_non_secret_evidence(self):
        result = safe_probe(
            "service",
            "boundary",
            lambda: {"verified": True},
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["evidence"], {"verified": True})


if __name__ == "__main__":
    unittest.main()
