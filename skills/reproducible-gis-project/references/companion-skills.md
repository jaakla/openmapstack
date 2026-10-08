# Optional product companions

For selected existing tools and runnable dlt/Dagster/dbt/Observable/QGIS examples,
see `established-stack.md`. Keep one owner per transformation/dependency;
optional tool runtimes are required only for selected integration work.

Use focused product expertise when it helps the requested work. Check the
installed skills first; do not assume this table means a companion is installed.
The entries below were checked on **2026-09-14**. Recheck the source and actual
skill before a new installation; names, maintenance and capabilities can change.

| Capability and when useful | Verified source and exact skill / optional discovery query | Authority and responsibility |
|---|---|---|
| Configure MapLibre sources, tiles and sprites after choosing the delivery architecture | [maplibre/maplibre-agent-skills](https://github.com/maplibre/maplibre-agent-skills), [`maplibre-tile-sources`](https://github.com/maplibre/maplibre-agent-skills/blob/main/skills/maplibre-tile-sources/SKILL.md) | Community-maintained collection in the MapLibre organization. Rendering integration expertise; not authority for analytical source fitness, metric CRS or architecture. |
| Read a Portolan catalog and its cloud-native assets | [portolan-sdi/portolan-skills](https://github.com/portolan-sdi/portolan-skills), `reading-portolan` | Portolan project-maintained guidance. Catalog access and conventions; OMS still checks completeness, semantic filters, provenance and pins. |
| BigQuery-specific setup or geography function behavior in an already chosen engine | Optional discovery query: `BigQuery GIS spatial SQL`; use [Google's geospatial documentation](https://docs.cloud.google.com/bigquery/docs/geospatial-data) directly when needed | No dedicated vendor skill was verified in this review. Google documentation is the product authority; a discovered community skill must not be described as official. |

## Composition rules

1. Prefer a relevant installed companion and read its stated scope. Ask it for
   the bounded product task, retaining the user's constraints and chosen stack.
2. If absent, use authoritative product documentation. A specific capability
   recommendation or optional skill discovery can help if the user wants it;
   absence of a discovery tool does not block ordinary work with local guidance
   and documentation. State any remaining uncertainty.
3. Acquire a third-party skill only within the user's existing authorization
   and the host agent's policy. Never bulk-install this table, automatically
   download dependencies, or require a sibling to finish a bounded task.
4. Evaluate advice against the analysis requirements. A renderer's convenient
   projection, sampled catalog, mutable URL or simplified geometry does not
   override metric correctness, completeness, immutable source pins or the
   canonical pipeline. Explain and resolve material conflicts. Product-specific
   API details can change; verify them against the product's current docs.

These are optional companions, not portable package dependencies. OpenMapStack
owns source meaning, GIS methodology, coupled architecture decisions, provenance,
reproducibility and verification of the final result. A successful product demo
is not evidence that the underlying analysis is correct.
