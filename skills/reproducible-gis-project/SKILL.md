---
name: reproducible-gis-project
description: "Compile, validate or maintain a reproducible GIS project from an existing analysis or chosen workflow: project.yaml, canonical pipeline, pinned sources, executable corrections, validation, reruns and QGIS/web presentation. Use when delivery and reproducibility are the task. Route unresolved source/compute/storage architecture to open-map-stack; a bounded SQL review or dataset lookup does not need a project."
metadata:
  version: "0.4.0"
---

# Reproducible GIS project

Turn the analysis into an inspectable project another analyst can execute
without the conversation. Retain the user's chosen algorithm and stack unless
they cannot meet a stated requirement; explain consequential changes before
making them. Do not silently substitute a convenient proxy for the requested
measurement.

## Compile or update the project

Read `references/project-workflow.md` for mandatory workflow and delivery
requirements and `references/project-spec.md` for the project contract. Use
the local `templates/` and `examples/tartu-development/` as structural examples,
not as evidence about the user's data. `references/installation.md` explains
CLI setup and the worked example's runtime requirements.

1. Establish the analysis objective, authoritative inputs, semantic filters,
   metric CRS, algorithm, outputs and consequential unresolved assumptions.
   Inspect an existing project before changing its structure. If architecture
   is still undecided, use an installed `open-map-stack` or resolve those
   decisions with the user before presenting the project as complete.
2. Pin immutable sources and represent corrections/scenarios as explicit data
   with provenance. Verify each correction's target and prior value before
   applying it. Never fabricate missing authoritative geometry or overwrite a
   source to make validation pass.
3. Declare one canonical executable pipeline and ordered, resolvable steps.
   Record `delivery.schema: openmapstack-delivery/v1` for new projects; use the
   built-in dashboard by default, or the user's explicit external-only/combined
   selection. QGIS and Observable are optional targets. Existing manifests
   without `delivery` retain legacy v1 behavior until explicitly migrated.
   Derive selected views from that pipeline and manifest;
   avoid independent presentation logic that silently changes the analysis.
4. Execute relevant checks, retain machine-readable results and run evidence,
   and perform a clean rerun. Check whether the `openmapstack` CLI is installed
   (`openmapstack --version` or `python3 -m openmapstack --version`, then use
   whichever form works for every command); if it is, run
   `openmapstack validate`, then `openmapstack verify project.yaml --rerun`
   for the clean rerun. Make that `verify --rerun` your last step and do not
   deliver until it passes; a passing `validate` alone is not enough. The
   canonical pipeline must produce every file under `data/derived`,
   every selected deliverable, the run record, and the manifest's `runs.latest` and
   `project.status`, never a one-off script or a hand edit: a rerun writes a
   new run record, so a hand-patched pointer breaks it.
   Missing tools or unknown data semantics remain visible limitations. A stated
   intention to validate is not validation.
5. Deliver the project, source/override policy, runtime instructions and
   substantive method limitations together. Preserve the full presentation
   obligations for the selected targets in the workflow reference; a view alone is not
   the project.

## Focused supporting guidance

- CRS, formats and metric correctness: `references/formats-and-crs.md`.
- User warehouses and reproducible snapshots: `references/user-data-sources.md`.
- Authoritative public inputs and catalog completeness: `references/data-sources.md`.
- Validation and operational evidence: `references/validation-and-ops.md`.
- QGIS delivery and runtime behavior: `references/qgis.md`.

An installed `geospatial-data-discovery` or `spatial-sql` may help with a bounded
source or query question. Their absence does not prevent this skill from using
its own references. Keep ownership of the compiled project and cross-stage
correctness; do not require or automatically install sibling skills.

For optional product expertise, read `references/companion-skills.md`.
Keep GIS correctness and the task boundaries above when using a companion.
