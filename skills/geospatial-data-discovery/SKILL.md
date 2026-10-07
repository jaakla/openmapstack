---
name: geospatial-data-discovery
description: "Find and assess authoritative geospatial datasets or catalogs against known requirements: coverage, feature meaning, access, license, completeness, versions and reproducible pinning. Use for public sources, STAC/Portolan catalogs and read-only discovery of user data. Route joint source/compute/storage/delivery architecture to open-map-stack; do not turn a bounded lookup into a full analysis project."
metadata:
  version: "0.4.0"
---

# Geospatial data discovery

Find data that answers the user's question, and show what is known about its
fitness. Dataset names, convenient APIs and plausible-looking geometries are
not evidence that the features have the intended meaning.

## Discover and assess

1. Establish the area, time/version, feature semantics, necessary attributes,
   resolution, completeness and intended use. Ask only for consequential
   missing requirements; record assumptions that are safe to proceed with.
2. Read `references/data-sources.md` for public sources and STAC/Portolan
   catalogs. For the user's warehouse/database, read
   `references/user-data-sources.md`; use credentials by reference and
   read-only inspection. Snapshot materialization follows the existing
   authorization boundary. Before presenting current availability, coverage or
   licensing as verified, open the provider's actual metadata/download/license
   pages during this task. Reference notes are discovery leads, not evidence
   of a fresh lookup. Cite the pages checked and the check date; if access is
   unavailable, label the recommendation unverified and state what is missing.
3. Prefer the authoritative provider for the requested meaning. Verify fields
   and code lists against current provider metadata (report unavailable access):
   ownership, active status, eligibility and missing values
   must not be inferred from a generic category. Preserve unknowns as unknown.
   Official status alone does not prove that every alternative derives from
   that source or is less current; support comparative claims with evidence.
4. Check coverage and completeness using provider counts, pagination, catalog
   structure, partitions and spatial/temporal filters as applicable. Distinguish
   a sample from a complete extract and a partial catalog from absent data.
5. Identify a reproducible version or immutable snapshot, access method,
   retrieval time and license/attribution requirements. A URL containing
   `latest`, a filename or a retrieval date alone is not an immutable pin;
   retain snapshot bytes and their content hash when the source is mutable. Report unresolved license or retention
   constraints without inventing terms or promising a future rerun.

Return a concise source assessment: provider/dataset, authoritative link,
coverage/time, relevant fields and predicates, access/pin strategy, completeness
evidence and material limitations. Compare alternatives when they change the
answer; do not download an entire dataset just to establish its existence.

When the user has dlt, Dagster, dbt, QGIS or Observable in their stack, read
`references/established-stack.md` for ownership and integration boundaries.
Preserve their chosen tools; retain source pins, GIS correctness and reproducible
evidence. Do not duplicate their SQL, ingestion or scheduler definitions.

## Boundaries and resources

Use `references/formats-and-crs.md` when inspecting coordinate systems, formats
or spatial extents. A bounded discovery answer does not require `project.yaml`,
a canonical pipeline or presentation artifacts. If the user asks for the
actual multi-stage analysis, use an installed `open-map-stack` or
`reproducible-gis-project`, or explain the needed next step; do not label a
source assessment as a completed material analysis.

When source choice depends on unresolved compute placement, continental scale,
team concurrency or delivery architecture, keep those coupled decisions with
the generalist. Do not choose an engine merely because a dataset is easy to
query with it.

No project template, worked example or OpenMapStack CLI is needed for a source
assessment. If the task expands into a project, hand off the source assessment
to an installed `open-map-stack` or `reproducible-gis-project`. No sibling skill
or third-party installation is required for bounded discovery.

For optional product expertise, read `references/companion-skills.md`.
Keep GIS correctness and the task boundaries above when using a companion.
