import unittest

from src.harness.rag import (
    CallableEvidenceAnswerer,
    CrossEncoderRanker,
    GeneratedAnswer,
    LinearSVMRanker,
    RetrievalDocument,
    RetrievalMode,
    SVMRankWeights,
    ThreeStageRAGPipeline,
)


class FakeRetriever:
    def __init__(self, documents):
        self.documents = documents
        self.requested_limit = None

    def retrieve(self, query, limit):
        self.requested_limit = limit
        return self.documents[:limit]


class RecordingRanker:
    def __init__(self, score_offset=0):
        self.calls = []
        self.score_offset = score_offset

    def rank(self, query, documents, limit):
        self.calls.append((query, len(documents), limit))
        return [
            item.model_copy(update={"score": self.score_offset + index})
            for index, item in enumerate(reversed(documents[:limit]), start=1)
        ]


class FakeCrossEncoder:
    def __init__(self, scores):
        self.scores = scores
        self.pairs = None

    def predict(self, pairs):
        self.pairs = pairs
        return self.scores


def documents(count):
    return [
        RetrievalDocument(
            chunk_id=f"chunk-{index}",
            source="runbook.md",
            content=f"E102 evidence {index}",
            score=float(count - index),
        )
        for index in range(count)
    ]


class ThreeStageRAGPipelineTest(unittest.TestCase):
    def test_pipeline_applies_100_20_3_limits_and_returns_sources(self):
        retriever = FakeRetriever(documents(120))
        svm = RecordingRanker()
        cross_encoder = RecordingRanker(score_offset=10)
        pipeline = ThreeStageRAGPipeline(
            retriever=retriever,
            svm_ranker=svm,
            cross_encoder_ranker=cross_encoder,
            answerer=CallableEvidenceAnswerer(
                lambda query, evidence: f"answer [{evidence[0].chunk_id}]"
            ),
        )

        result = pipeline.search("E102")

        self.assertEqual(retriever.requested_limit, 100)
        self.assertEqual(svm.calls, [("E102", 100, 20)])
        self.assertEqual(cross_encoder.calls, [("E102", 20, 3)])
        self.assertEqual(len(result.sources), 3)
        self.assertIn(f"[{result.sources[0].chunk_id}]", result.answer)
        self.assertEqual(
            [item.stage for item in result.retrieval_trace],
            ["faiss", "svm_rank", "cross_encoder", "answer_generation"],
        )
        self.assertEqual(
            [item.candidate_count for item in result.retrieval_trace],
            [100, 20, 3, 3],
        )

    def test_no_relevant_evidence_refuses_without_calling_answerer(self):
        answer_calls = []
        pipeline = ThreeStageRAGPipeline(
            retriever=FakeRetriever(documents(2)),
            svm_ranker=RecordingRanker(),
            cross_encoder_ranker=RecordingRanker(score_offset=-10),
            answerer=CallableEvidenceAnswerer(
                lambda query, evidence: answer_calls.append(query) or "unsafe"
            ),
            relevance_threshold=0.5,
        )

        result = pipeline.search("unknown")

        self.assertTrue(result.refused)
        self.assertEqual(result.sources, [])
        self.assertEqual(answer_calls, [])

    def test_search_can_bypass_optional_reranking_stages(self):
        pipeline = ThreeStageRAGPipeline(
            retriever=FakeRetriever(documents(5)),
            svm_ranker=RecordingRanker(),
            cross_encoder_ranker=RecordingRanker(score_offset=1),
            answerer=CallableEvidenceAnswerer(lambda query, evidence: "answer"),
            search_mode=RetrievalMode.FAISS,
        )

        result = pipeline.search("E102")

        self.assertEqual(
            [item.stage for item in result.retrieval_trace],
            ["faiss", "answer_generation"],
        )

    def test_linear_svm_ranker_uses_dense_and_lexical_features(self):
        ranker = LinearSVMRanker(
            SVMRankWeights(
                dense_score=0,
                token_overlap=2,
                exact_phrase=3,
                length_penalty=0,
            )
        )
        candidates = [
            RetrievalDocument(
                chunk_id="unrelated",
                source="a",
                content="calendar instructions",
                score=10,
            ),
            RetrievalDocument(
                chunk_id="relevant",
                source="b",
                content="fix E102 now",
                score=1,
            ),
        ]

        ranked = ranker.rank("E102", candidates, 1)

        self.assertEqual(ranked[0].chunk_id, "relevant")

    def test_cross_encoder_scores_all_pairs_in_one_batch(self):
        model = FakeCrossEncoder([0.1, 0.9, 0.2])
        ranker = CrossEncoderRanker(model)

        ranked = ranker.rank("E102", documents(3), 2)

        self.assertEqual(len(model.pairs), 3)
        self.assertEqual(ranked[0].chunk_id, "chunk-1")
        self.assertEqual(len(ranked), 2)

    def test_ablation_modes_only_run_selected_stages(self):
        expected_stages = {
            RetrievalMode.FAISS: ["faiss"],
            RetrievalMode.FAISS_SVM: ["faiss", "svm_rank"],
            RetrievalMode.FAISS_CROSS_ENCODER: ["faiss", "cross_encoder"],
            RetrievalMode.FULL: ["faiss", "svm_rank", "cross_encoder"],
        }
        for mode, stages in expected_stages.items():
            with self.subTest(mode=mode):
                pipeline = ThreeStageRAGPipeline(
                    retriever=FakeRetriever(documents(30)),
                    svm_ranker=RecordingRanker(),
                    cross_encoder_ranker=RecordingRanker(score_offset=10),
                    answerer=CallableEvidenceAnswerer(
                        lambda query, evidence: "unused"
                    ),
                )

                selected, trace = pipeline.retrieve("E102", mode=mode)

                self.assertEqual(len(selected), 3)
                self.assertEqual([item.stage for item in trace], stages)

    def test_retrieval_can_override_candidate_window_for_evaluation(self):
        retriever = FakeRetriever(documents(30))
        pipeline = ThreeStageRAGPipeline(
            retriever=retriever,
            svm_ranker=RecordingRanker(),
            cross_encoder_ranker=RecordingRanker(score_offset=1),
            answerer=CallableEvidenceAnswerer(lambda query, evidence: "unused"),
        )

        pipeline.retrieve(
            "E102",
            mode=RetrievalMode.FAISS_CROSS_ENCODER,
            recall_limit=10,
        )

        self.assertEqual(retriever.requested_limit, 10)

    def test_generation_metadata_is_returned_for_observability(self):
        pipeline = ThreeStageRAGPipeline(
            retriever=FakeRetriever(documents(1)),
            svm_ranker=RecordingRanker(),
            cross_encoder_ranker=RecordingRanker(score_offset=1),
            answerer=CallableEvidenceAnswerer(
                lambda query, evidence: GeneratedAnswer(
                    text="answer [chunk-0]",
                    model="test-model",
                    token_usage={"input_tokens": 10, "output_tokens": 3},
                )
            ),
        )

        result = pipeline.search("E102")

        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.token_usage["input_tokens"], 10)


if __name__ == "__main__":
    unittest.main()
