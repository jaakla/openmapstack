# Read a Portolan catalog

A Portolan catalog is published under `./catalog/` in this workspace. Its root
is `catalog/catalog.json`.

Compile a reproducible OpenMapStack project that answers: **how many parcels in
that catalog are zoned `ARIMAA`?** Write the qualifying parcels to
the candidate output declared under `outputs.candidate_parcels` in `project.yaml`.

Treat the catalog as the authoritative source: pin it in `project.yaml` with
the provider, license, and version identity it publishes, and carry its
attribution requirement into the project's warnings or license fields. Do not
copy the catalog's files into `data/source/` and re-describe them as your own
extract — cite the collection.

Choose a suitable spatial format and filename for the declared candidate output; retain source parcel identifiers and actual CRS metadata.
