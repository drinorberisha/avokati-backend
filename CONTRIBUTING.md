# Contributing — Backend (avokati-backend)

Working agreement for changes in this repo. Keep it boring; the goal is
to avoid surprises in prod.

## What counts as small vs bigger

| Size   | Examples                                                                                          | Workflow            |
|--------|---------------------------------------------------------------------------------------------------|---------------------|
| Small  | typo fix · log line tweak · env-var rename · single-file bug fix · doc edit · cloudbuild knob     | Direct to `main`    |
| Bigger | new endpoint · refactor across 2+ files · new dependency · DB migration · prompt rewrite · IAM changes | Feature branch      |

**When in doubt, branch.** Cost of a branch is ~10 seconds; cost of
breaking prod on `main` is several minutes of debugging plus a rollback.

## Workflow — small change

```bash
# Test locally before pushing.
venv/bin/uvicorn main:app --reload --port 8000

# Hit the new code path with curl or the local frontend, confirm it works.
make backend-import-check   # smoke test the import graph

git add <files>
git commit -m "Short imperative subject (what + why in body if non-obvious)"
git push origin main
```

`git push origin main` triggers Cloud Build, which builds the image,
pushes to Artifact Registry, and rolls a new Cloud Run revision. End to
end ~5 minutes. Watch at:
https://console.cloud.google.com/cloud-build/builds?project=berix-systems-llc-admin

## Workflow — bigger change

```bash
# Branch off main with a name that says what + scope.
git checkout main && git pull
git checkout -b feat/streaming-cancel-button       # or fix/, refactor/, chore/

# Work, commit as much as you want.
git add <files> && git commit -m "..."

# Push the branch (does NOT auto-deploy — main branch only is wired to the trigger).
git push -u origin feat/streaming-cancel-button

# When you're confident it works locally, merge to main:
git checkout main
git merge --no-ff feat/streaming-cancel-button     # --no-ff preserves the branch in history
git push origin main                                # this is what kicks off the deploy

# Clean up.
git branch -d feat/streaming-cancel-button
git push origin --delete feat/streaming-cancel-button
```

`--no-ff` is a small detail but worth it — the merge commit makes it
obvious in `git log` what was one feature vs one bugfix.

## Testing locally

Three smoke checks before any push to main:

```bash
# 1. App imports cleanly (catches missing deps, syntax errors).
make backend-import-check

# 2. The pipeline still produces events (skip if change is unrelated to AI).
venv/bin/python -c "
import asyncio, os
from dotenv import load_dotenv
load_dotenv('.env')
from app.ai.pipeline import answer_stream
async def main():
    events = []
    async for n, _ in answer_stream('Çfarë thotë Neni 5 i Ligjit 02/L-10?', use_llm=False):
        events.append(n)
    print('events:', events)
asyncio.run(main())
"

# 3. Run uvicorn against the local frontend and click through the feature.
venv/bin/uvicorn main:app --reload --port 8000
```

## If a deploy breaks prod

```bash
# Pin Cloud Run back to the previous revision (instant rollback).
gcloud run services update-traffic avokai-backend \
  --to-revisions=<previous-revision-name>=100 \
  --region=europe-west1 \
  --project=berix-systems-llc-admin

# Then fix forward in a branch, test, merge, deploy.
```

Previous revision names: `gcloud run revisions list --service=avokai-backend --region=europe-west1`.

## Commit messages

Short imperative subject, optional body explaining *why* (the code shows
the *what*). Co-Authored-By trailers for pair work are fine.

Bad: `update file`, `fixes`, `wip`
Good: `Stream: shrink heartbeat 512B->64B, cut reranker max_length 512->256`

## Secrets

Never commit `.env` or anything from it. Secret Manager holds the prod
secrets; the deploy step in `cloudbuild.yaml` references them.
