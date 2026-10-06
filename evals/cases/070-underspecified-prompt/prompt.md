# Vague request (deliberately underspecified)

"Make me a useful map of the Tartu test data."

The data layers in `data/source/` are supplied. No thresholds, no output
format, no selection rule are given. Do not silently invent decision
parameters: whatever you choose must be declared in `project.yaml`
(interpretation.assumptions, with rationale) and reflected in
`presentation.controls` so the reader can change it. Deliver the standard
project artifact with the candidate set declared under
`outputs.candidate_parcels` in `project.yaml` (column `parcel_id`).

Python 3, DuckDB Spatial and the openmapstack package are installed; use
`openmapstack` or `python3 -m openmapstack` for the CLI. Do not assume other
geospatial Python packages, and do not install packages.

Choose a suitable spatial format and filename for the declared candidate output; retain source parcel identifiers and actual CRS metadata.
