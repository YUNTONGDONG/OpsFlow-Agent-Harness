import unittest

from scripts.train_svmrank import train


class SVMRankTrainingTest(unittest.TestCase):
    def test_pairwise_training_learns_positive_dense_direction(self):
        rows = [
            {
                "query_id": "q1",
                "relevance": 2,
                "dense_score": 0.9,
                "token_overlap": 1.0,
                "exact_phrase": 1.0,
                "length_penalty": 1.0,
            },
            {
                "query_id": "q1",
                "relevance": 0,
                "dense_score": 0.1,
                "token_overlap": 0.0,
                "exact_phrase": 0.0,
                "length_penalty": 1.0,
            },
        ]

        weights = train(
            rows,
            epochs=10,
            learning_rate=0.1,
            regularization=0.001,
        )

        self.assertGreater(weights["dense_score"], 0)
        self.assertGreater(weights["token_overlap"], 0)
        self.assertEqual(weights["length_penalty"], 0)
        self.assertIn("feature_means", weights)
        self.assertIn("feature_stds", weights)

    def test_training_requires_ordered_pairs(self):
        row = {
            "query_id": "q1",
            "relevance": 1,
            "dense_score": 0.5,
            "token_overlap": 0.5,
            "exact_phrase": 0.0,
            "length_penalty": 1.0,
        }

        with self.assertRaisesRegex(ValueError, "ordered relevance pairs"):
            train([row], epochs=1, learning_rate=0.1, regularization=0.1)


if __name__ == "__main__":
    unittest.main()
