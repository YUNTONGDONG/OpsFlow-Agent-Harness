.PHONY: install install-rag test test-integration test-live-read test-live-write eval rag-eval rag-window-eval perf-report demo demo-scenarios cli slack knowledge-chunks rag-index rag-training-data rag-train

PYTHON ?= .venv/bin/python

install:
	poetry install

install-rag:
	poetry install -E rag

test:
	$(PYTHON) -m unittest discover -s tests -v

test-integration:
	$(PYTHON) -m unittest discover -s tests/integration -v

test-live-read:
	$(PYTHON) scripts/check_live_integrations.py --output logs/live_integrations.json

test-live-write:
	$(PYTHON) scripts/check_live_integrations.py --allow-writes --output logs/live_integrations_write.json

eval:
	$(PYTHON) scripts/evaluate_router.py

rag-eval:
	$(PYTHON) scripts/evaluate_rag.py --dataset "$(DATASET)"

rag-window-eval:
	$(PYTHON) scripts/evaluate_rerank_windows.py \
		--dataset "$(DATASET)" \
		$(if $(MODEL),--model "$(MODEL)") \
		--output logs/rag_rerank_windows.json

perf-report:
	$(PYTHON) scripts/summarize_performance.py --trace "$(TRACE)" --dataset "$(DATASET)" --concurrency "$(CONCURRENCY)" --warm-state "$(WARM_STATE)" --model "$(MODEL)" --output "$(OUTPUT)"

demo:
	$(PYTHON) -m uvicorn src.api:app --host 0.0.0.0 --port 8000

demo-scenarios:
	$(PYTHON) scripts/run_demo_scenarios.py --output logs/demo_scenarios.json

cli:
	$(PYTHON) -m src.cli "$(MESSAGE)"

slack:
	$(PYTHON) -m src.SlackWebHook

knowledge-chunks:
	$(PYTHON) scripts/build_knowledge_chunks.py \
		--output data/knowledge_chunks.jsonl \
		README.md HARNESS_MIGRATION.md docs/DEMO.md \
		docs/LIVE_INTEGRATION.md docs/FINAL_STATE_ACCEPTANCE.md

rag-index:
	$(PYTHON) scripts/build_faiss_index.py --input "$(INPUT)" --index "$(FAISS_INDEX_PATH)" --documents "$(RAG_DOCUMENTS_PATH)"

rag-training-data:
	$(PYTHON) scripts/build_svmrank_training.py \
		--queries data/rag_training_queries.jsonl \
		--index rag_data/knowledge.faiss \
		--documents rag_data/documents.jsonl \
		--output data/svmrank_training.jsonl

rag-train:
	$(PYTHON) scripts/train_svmrank.py \
		--input data/svmrank_training.jsonl \
		--output rag_data/svmrank_weights.json
