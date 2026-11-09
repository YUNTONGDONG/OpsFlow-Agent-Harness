import unittest

from scripts.evaluate_rag import diagnose_drops, metrics


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

    def test_diagnosis_identifies_the_stage_that_drops_relevant_chunks(self):
        result = diagnose_drops(
            {"gold-faiss", "gold-svm", "never-found"},
            {
                "faiss": ["gold-faiss", "gold-svm", "other"],
                "faiss_svm": ["gold-svm", "other", "another"],
                "full": ["other", "another", "third"],
            },
        )

        self.assertEqual(result["missing_from_faiss_top_3"], ["never-found"])
        self.assertEqual(result["dropped_by_svmrank"], ["gold-faiss"])
        self.assertEqual(result["dropped_by_cross_encoder"], ["gold-svm"])


if __name__ == "__main__":
    unittest.main()
