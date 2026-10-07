# Selectable delivery contract example

This offline example uses two explicitly synthetic rectangles, not authoritative
parcel data. Their areas are 10,000 and 20,000 m² in EPSG:3301; the 15,000 m²
threshold selects only `large`. One canonical pipeline creates the analytical
table/geodata and only the selected views, then records evidence and run hashes.

With OpenMapStack installed, create a writable copy (default: built-in dashboard):

```bash
python examples/delivery-profiles/create.py /tmp/oms-dashboard
openmapstack run /tmp/oms-dashboard
openmapstack inspect /tmp/oms-dashboard --json
openmapstack verify /tmp/oms-dashboard --rerun
```

Other selections replace the default, rather than adding to it:

```bash
python examples/delivery-profiles/create.py /tmp/oms-qgis --targets qgis
python examples/delivery-profiles/create.py /tmp/oms-observable --targets observable
python examples/delivery-profiles/create.py /tmp/oms-combined --targets dashboard,qgis,observable
```

Run/verify each copy as above. The QGIS XML and Observable HTML export exercise
their delivery contracts; they are not turnkey external-tool adapters. Those
integrations are tracked in issue #80. QGIS static checks inspect the actual
archive; native load/render is `not_testable` without PyQGIS. Web smoke checks
are `not_testable` without Playwright/Chromium. A generated archive/export alone
does not establish runtime success or analytical truth.

`--targets observable --hosted` exercises an explicitly fictional hosted-export
receipt with a local build/configuration output and retrieval identity. It makes
no network calls and performs no publication. A real hosted integration must
retain the actual export/configuration and retrieval evidence. A link alone is
insufficient.

Do not run the pipeline in this source directory: use `create.py` so regenerable
outputs and mutated run pointers stay outside the repository.
