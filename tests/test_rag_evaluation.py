import unittest

from scripts.evaluate_rag import metrics


class RAGEvaluationTest(unittest.TestCase):
    def test_metrics_use_ranked_chunk_ids(self):
        result = metrics(["wrong", "gold-1", "gold-2"], {"gold-1", "gold-2"})

        self.assertEqual(result["recall"], 1.0)
        self.assertEqual(result["mrr"], 0.5)
        self.assertGreater(result["ndcg"], 0)
        self.assertLess(result["ndcg"], 1)

    def test_metrics_handle_no_relevant_documents(self):
        self.assertEqual(
            metrics(["chunk"], set()),
            {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0},
        )


if __name__ == "__main__":
    unittest.main()
