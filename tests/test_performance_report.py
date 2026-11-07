import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_performance import build_report, percentile
from src.harness import JsonlTraceSink, TraceEvent, TraceEventType


class PerformanceReportTest(unittest.TestCase):
    def test_percentile_interpolates_sorted_values(self):
        self.assertEqual(percentile([40, 10, 20, 30], 0.5), 25)

    def test_report_records_scope_and_request_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_path = root / "trace.jsonl"
            dataset_path = root / "cases.jsonl"
            dataset_path.write_text('{"input":"E102"}\n', encoding="utf-8")
            sink = JsonlTraceSink(trace_path)
            for request_id, duration, status in (
                ("one", 100, "completed"),
                ("two", 200, "failed"),
            ):
                sink.emit(
                    TraceEvent(
                        request_id=request_id,
                        event_type=TraceEventType.REQUEST_COMPLETED,
                        attributes={"duration_ms": duration, "status": status},
                    )
                )

            report = build_report(
                trace_path=trace_path,
                dataset_path=dataset_path,
                concurrency=2,
                warm_state="prewarmed",
                model="test-model",
                hardware="test-hardware",
            )

        self.assertEqual(report["request_count"], 2)
        self.assertEqual(report["latency_ms"]["p50"], 150)
        self.assertEqual(report["failure_rate"], 0.5)
        self.assertEqual(report["concurrency"], 2)
        self.assertEqual(report["warm_state"], "prewarmed")
        self.assertEqual(report["hardware"], "test-hardware")
        self.assertEqual(len(report["dataset_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
