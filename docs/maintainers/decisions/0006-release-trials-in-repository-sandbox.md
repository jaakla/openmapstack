# 0006 — Release trials run in this repository, with the agent sandboxed

- Status: Accepted
- Date: 2026-09-23
- Related: [ADR 0004](0004-narrow-benchmark-interface.md); `evals/adapters/isolation.py`; `evals/adapters/claude_code.py`; `evals/final-state-acceptance.md`; [OpenMapBench #2](https://github.com/jaakla/OpenMapBench/issues/2)

## Context

The 0.4.0 acceptance moved live-trial execution into OpenMapBench. That work was useful, but it
made the release wait on another project's harness, task packs and review cycle. Meanwhile the
in-repository Claude Code adapter ran the CLI directly in a temporary directory. The agent could
read this checkout (expected answers, tests, unshipped docs), the maintainer's home directory and
installed skills. A trial that can see the repository is not evidence about the shipped payload.

## Decision

- The OpenMapStack release gate uses this repository's runner: `evals/run.py --mode live` for
  executed projects and `evals/run.py routing` for native skill selection.
- The live Claude Code adapter runs the agent in a rootless Bubblewrap allowlist. The agent can
  see system `/usr`, the Python runtime, the shipped `openmapstack` package, DuckDB extensions,
  the CLI and the trial directory; only the trial directory is writable. The adapter fails closed
  when the sandbox is unavailable, and requires a per-trial `--max-budget-usd` and one explicit
  credential. The isolation allowlist is recorded with each trial.
- In live mode the clean rerun of the pipeline the agent delivered runs in the same sandbox, with
  the delivered project read-only, so the host runner never executes agent-written code.
- ADR 0004 still holds. OpenMapBench keeps generic, repeated and multi-model benchmarking through
  the versioned check API. Its 2026-09-23 trials remain supporting evidence for 0.4.0.

## Consequences

- A release no longer waits for OpenMapBench parity
  ([OpenMapBench #2](https://github.com/jaakla/OpenMapBench/issues/2)). Evidence for the release
  comes from the same repository that ships the payload.
- Live Claude Code project trials need Linux with unprivileged user namespaces. CI installs
  Bubblewrap and lifts the Ubuntu AppArmor restriction; macOS maintainers cannot run this leg.
- Routing trials still need Docker access to a digest-pinned image; the maintainer running them
  must be able to use the Docker daemon.
- Project trials inject guidance through the prompt. Native activation on an executed project task
  is therefore not measured here; routing cases measure selection separately.
- The Codex adapter relies on Codex's own `workspace-write` sandbox, which still allows reads
  outside the workspace. Isolate it the same way before using it as release evidence.

## Alternatives considered

### Keep the release gate on OpenMapBench

Rejected for the release: it couples publishing to a separate project's roadmap. The harness remains
the right home for comparisons across agents and models.

### Run project trials in Docker like routing

Rejected for now: using the Docker daemon is root-equivalent and unavailable in some maintainer
environments. Bubblewrap needs no daemon and runs unprivileged.

### A multi-model harness such as pi for the release gate

Deferred. It measures whether guidance works across models, not whether the skill activates in the
products users install it into. It belongs with OpenMapBench-style benchmarking.
