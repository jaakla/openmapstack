# 0008 — Bind external owners without a second analytical DAG

- Status: Accepted
- Date: 2026-10-07
- Related: [#80](https://github.com/jaakla/openmapstack-skills/issues/80)

Keep loader state, operational asset dependencies, SQL model definitions/tests
and delivery builds with their existing tools. The project entrypoint invokes
those owners; project provenance remains an independent analytical obligation.
An optional versioned declaration binds owner definitions, derived processing
summaries and build resources so static verification can detect drift without
installing or running every integration. Tool execution requires separate native
results; a receipt alone is not proof of execution.

This avoids maintaining another general orchestration framework or embedding
every tool in core dependencies. The standalone example renderer is deliberately
bounded to its fixture SQL; production dependency management remains with dbt.

Owners: [integration.py](../../../openmapstack/integration.py),
[contract guidance](../../../skills/open-map-stack/references/established-stack.md),
[focused/composed example](../../../examples/established-stack/README.md).
