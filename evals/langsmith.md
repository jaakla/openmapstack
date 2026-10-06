# LangSmith human review setup

This optional integration imports retained eval evidence into LangSmith. It does
not run an agent, rerun an agent-generated pipeline, or change grading verdicts.
The runner and check API remain authoritative. Generic benchmark orchestration
remains owned by [OpenMapBench](../docs/openmapbench-interop.md).

The renderer presents the retained prompt, expected acceptance criteria, actual
dashboard/screenshots/tables, check differences, and assessment together. Human
feedback is saved through LangSmith's own review controls.

## EU account setup

1. Open [your EU workspace](https://eu.smith.langchain.com/).
2. In Settings → API Keys, create a workspace-scoped service key. Store it as the
   repository Actions secret `LANGSMITH_API_KEY` in
   [repository secret settings](https://github.com/jaakla/openmapstack-skills/settings/secrets/actions).
   Do not put the key in chat, a committed file, or a command argument.
3. For a key scoped to multiple workspaces, also set the Actions variable
   `LANGSMITH_WORKSPACE_ID` to the target workspace ID.
4. Once this change is deployed by the Project homepage workflow, open
   `https://jaakla.github.io/openmapstack-skills/langsmith/`. It should show
   **Connect this renderer to LangSmith**. This public page contains only renderer
   code; it includes no results, API key, or saved prompts.
5. Manually run **Import saved evals into LangSmith** in GitHub Actions. Defaults
   import case `001-basic-spatial-analysis`, trial 2, arm `oms`, from run
   `37291508703`, into the EU workspace. No model calls occur. The optional
   reference step regenerates the deterministic fixture control from the exact
   recorded commit, rather than using today's fixture as historical truth.
6. In LangSmith → Datasets & Experiments, open dataset
   `openmapstack-fb0d8fcb-live-review`. Dataset menu → Custom Output Rendering →
   Enable → enter `https://jaakla.github.io/openmapstack-skills/langsmith/` → Save.
7. Open an imported result. The view should show the prompt, reference dashboard,
   actual dashboard, expected/actual checks, and failure reasons. Add it to an
   annotation queue when you want to save human feedback. If the queue overrides
   dataset rendering, configure the same URL there.

Official references: [API keys](https://docs.langchain.com/langsmith/create-account-api-key),
[custom rendering](https://docs.langchain.com/langsmith/custom-output-rendering),
[external imports](https://docs.langchain.com/langsmith/upload-existing-experiments),
[annotation queues](https://docs.langchain.com/langsmith/annotation-queues).

## Local preparation and first import

Use the eval development environment (`evals/requirements.txt`); no LangChain or
LangSmith SDK is required. Extract the existing CI evidence artifact first.

```bash
python3 evals/integrations/langsmith.py /path/to/eval-benchmark-results-claude_code.json \
  --artifact-root /path/to/extracted-artifact \
  --case 001-basic-spatial-analysis --trial 2 --arm oms \
  --dataset-name openmapstack-fb0d8fcb-live-review \
  --experiment-name claude-saved-run-pilot \
  --output evals/results/langsmith-pilot
```

Open `evals/results/langsmith-pilot/index.html` for local review. `upload.json` is
the exact prepared request. Neither command nor preview contacts LangSmith.
Generated results stay in ignored `evals/results/`.

To authenticate locally without putting the token in shell history:

```bash
read -rs -p 'LangSmith EU API key: ' LANGSMITH_API_KEY
export LANGSMITH_API_KEY
export LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
# Only needed for a key scoped to multiple workspaces:
# export LANGSMITH_WORKSPACE_ID=your-workspace-id
```

Repeat preparation with a fresh `--output` directory and add `--upload`.
`receipt.json` records the service response. Use a new experiment name for a
repeated import; uploads are not automatically retried because a timeout may
occur after the service accepted the request.

For a local renderer during development:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory site/langsmith
```

Set the renderer URL to `http://localhost:8765/`. The official
[sample renderer](https://github.com/langchain-samples/custom-output-rendering)
also documents local development. Browser policy may restrict local frames;
the HTTPS GitHub Pages URL is the portable option.

## Evidence and comparison rules

- Choose a dataset name/version for one frozen task set. Import arms/models as
  separate experiments into that dataset. Repetitions have distinct rows;
  matching repetitions align across arms/models. Import one mode, score type,
  and arm at a time. Unsupported/skipped cases are excluded.
- Expected outputs contain expected criteria and optional independent reference
  artifacts. Actual check results are never copied into the reference as truth.
- Optional `--references DIR` reads `DIR/<case-id>/reference.json` plus
  `dashboard.html`, `data/derived/*.geojson`, and `visual/*.png`. The manifest
  must contain `task_commit` matching recorded `run_config.skill_commit` and a
  `provenance` explanation. A fixture dashboard illustrates a valid presentation;
  it does not require the agent to reproduce that layout.
- The importer reads `dashboard.html`, PNGs, and GeoJSON property tables from the
  trial bundle. Local JS/CSS dependencies are embedded without executing them.
  Interactive HTML runs in a sandboxed browser frame. Remote map resources may
  need network access; relative runtime fetches are not packaged.
- Table previews show at most 40 features per file and 12 GeoJSON files. They
  are labeled when truncated. File/image size limits fail explicitly rather
  than silently removing a selected artifact.
- The diff compares recorded criteria, statuses, codes, and evidence. It does
  **not** compute spatial or screenshot differences. References/images must
  exist for those comparisons; appearance alone does not prove GIS correctness.
- Setup failures and `not_testable` checks have no numeric success score.
  Mutations expecting a defect can correctly receive a matched-expectation score.
- Historical summaries lack wall-clock trial timestamps. Required LangSmith
  timestamps describe **import time**, labeled in metadata and description.
  LangSmith latency does not represent historical execution; original duration
  appears separately in the custom view.
- Uploads contain selected prompts, final responses, check evidence, and artifact
  content, inheriting the saved run's redaction. Raw provider event streams and
  entire project archives are not uploaded.

The first CI pilot imports only one trial. After checking the view, set `case`
and `trial` to `all` in the manual workflow to import all attempted trials for the selected
arm. To import only the remaining trials, set `exclude-import-run` to the prior
successful import workflow run ID. It reads that run's `upload.json` and skips
matching trial identities from the same dataset/source run/model. Locally, use
`--exclude-imported /path/to/prior/upload.json`. The previous pilot stays in its
original experiment; the remaining trials appear in a new experiment.
Source outcomes and score-type denominators remain in experiment metadata;
filtering a pilot does not alter the original run's score.

To verify an existing import without uploading again, set `verify-import-run` to
its workflow run ID. The read-only job checks workspace, dataset, example, and
experiment access using the configured region and secret.
