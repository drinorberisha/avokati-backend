PYTHON := venv/bin/python
PIP    := venv/bin/pip

# The scraper lives in a sibling directory and uses absolute imports that
# only resolve when run from inside it. We invoke via `cd ../Scraping &&`
# rather than copying the script tree. The scraper has its own venv but
# the deps overlap heavily with backend/, so we reuse backend/venv when
# the scraper's own venv isn't present (set SCRAPER_PYTHON to override).
SCRAPING_DIR    := ../Scraping
SCRAPER_PYTHON ?= $(abspath venv/bin/python)

.PHONY: help install eval-baseline eval-hybrid eval-pipeline eval-pipeline-llm \
        audit golden tests demo build-v2 v2-status \
        backend-import-check scrape abolishment ingest-fresh scraper-status

help:
	@echo "AvokAI Make targets"
	@echo ""
	@echo "Tests:"
	@echo "  make tests                      Run all Phase 2/3 unit tests"
	@echo "  make backend-import-check       Verify backend modules import cleanly"
	@echo ""
	@echo "Evals (talk to live Pinecone + OpenAI; require backend/.env):"
	@echo "  make eval-baseline              Pure dense retrieval baseline"
	@echo "  make eval-hybrid                Citation + abolishment + BM25 (no LLM)"
	@echo "  make eval-pipeline              End-to-end pipeline against default_v2"
	@echo "  make eval-pipeline-llm          Same, but with DeepSeek-V4-Pro answers"
	@echo "                                  (requires DEEPSEEK_API_KEY)"
	@echo "  make eval-pipeline-v1           Pipeline against v1 namespace (default)"
	@echo ""
	@echo "Index ops:"
	@echo "  make audit                      Sample 50 vectors, count corruption"
	@echo "  make golden                     Regenerate the 40-query golden set"
	@echo "  make build-v2                   Embed/upsert PDFs already on disk → Pinecone"
	@echo "  make v2-status                  Print v2 index build progress"
	@echo ""
	@echo "Adding new laws (full pipeline; see docs/AVOKAI_ARCHITECTURE.md §4):"
	@echo "  make scrape                     Stage 1: fetch new PDFs from gzk.rks-gov.net"
	@echo "  make abolishment                Stage 2: rebuild abolishment_relations.json"
	@echo "  make build-v2                   Stage 3: embed + upsert to Pinecone"
	@echo "  make ingest-fresh               All three stages in order (cron-safe)"
	@echo "  make scraper-status             Print scraper progress"
	@echo ""
	@echo "Demo:"
	@echo "  make demo                       3 representative queries through full LLM pipeline"

install:
	@if [ ! -d venv ]; then python3.11 -m venv venv; fi
	$(PIP) install -q -r requirements.txt 2>/dev/null || true
	$(PIP) install -q openai pinecone python-dotenv langchain langchain-openai \
	                  langchain-pinecone langchain-community pinecone-text \
	                  rank_bm25 pymupdf pydantic pydantic-settings supabase

tests:
	$(PYTHON) tests/eval/test_phase23_modules.py
	$(PYTHON) tests/eval/test_citation_parser.py
	$(PYTHON) tests/eval/test_pipeline.py

backend-import-check:
	$(PYTHON) -c "import sys; sys.path.insert(0,'.'); \
	from dotenv import load_dotenv; load_dotenv('.env'); \
	from app.core.config import settings; \
	from app.ai.retrieval.langchain_service import langchain_service; \
	from app.ai.pipeline import answer; \
	print('Backend imports OK')"

eval-baseline:
	$(PYTHON) tests/eval/run_eval.py

eval-hybrid:
	$(PYTHON) tests/eval/run_eval_hybrid.py

eval-pipeline:
	$(PYTHON) tests/eval/run_eval_pipeline.py

eval-pipeline-llm:
	$(PYTHON) tests/eval/run_eval_pipeline.py --use-llm

eval-pipeline-v1:
	$(PYTHON) tests/eval/run_eval_pipeline.py --namespace default

audit:
	$(PYTHON) tests/eval/audit_index.py

golden:
	$(PYTHON) tests/eval/generate_golden_set.py

build-v2:
	$(PYTHON) scripts/build_v2_index.py

v2-status:
	@$(PYTHON) -c "import json; \
	d = json.load(open('tests/eval/results/v2_index_state.json')); \
	n = len(d['completed_laws']); total = 1003; \
	print(f'completed: {n}/{total} ({n/total*100:.1f}%)'); \
	print(f'chunks: {d.get(\"total_chunks_upserted\", 0)}'); \
	print(f'skipped: {len(d.get(\"skipped_laws\", {}))}')"

demo:
	$(PYTHON) tests/eval/demo_llm_pipeline.py

# ---- Scraping pipeline (lives in ../Scraping, runs there) -----------------
# These targets all `cd $(SCRAPING_DIR)` so the scraper's relative imports
# (e.g. `from downloaders.law_list_page import ...`) resolve. The scraper
# has its own state file at Scraping/data/law_scraper_state.json — running
# without --no-resume skips already-completed laws.

scrape:
	@echo "[stage 1] Scraping gzk.rks-gov.net for new laws (resumable)..."
	cd $(SCRAPING_DIR) && $(SCRAPER_PYTHON) law_scraper.py --pages all

abolishment:
	@echo "[stage 2] Rebuilding abolishment_relations.json..."
	cd $(SCRAPING_DIR) && $(SCRAPER_PYTHON) abolishment_extractor.py

# Full ingest. Idempotent — safe to cron. Order matters:
#   scrape MUST come before build-v2 (build-v2 reads PDFs from disk)
#   abolishment SHOULD come before build-v2 so the abolishment registry
#   reflects any newly-published abolishing law before we surface it
ingest-fresh: scrape abolishment build-v2
	@echo "[ingest-fresh] All three stages complete."

scraper-status:
	@$(PYTHON) -c "import json, os; \
	p = '$(SCRAPING_DIR)/data/law_scraper_state.json'; \
	d = json.load(open(p)) if os.path.exists(p) else {}; \
	print(f'scraper state: {p}'); \
	print(f'  completed documents: {len(d.get(\"completed_documents\", []))}'); \
	print(f'  failed documents: {len(d.get(\"failed_documents\", []))}'); \
	print(f'  last_updated: {d.get(\"last_updated\", \"never\")}')"
