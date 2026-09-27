# 0005 — Standalone skill assets and a separately pinned CLI

- Status: Accepted (payload scope amended 2026-09-27)
- Date: 2026-09-14
- Related: [#34](https://github.com/jaakla/openmapstack-skills/issues/34), [#31](https://github.com/jaakla/openmapstack-skills/issues/31)

## Context and assessment

Moving from the repository-root skill to `skills/<name>/` makes each directory
the install boundary. The [skills CLI](https://github.com/vercel-labs/skills)
supports selecting individual skills; [Claude plugins](https://code.claude.com/docs/en/plugins-reference)
discover a collection under `skills/`. Mandatory assets cannot live in an absent
sibling or depend on a checkout. The coordinated release version is 0.4.0.

| Option | Benefit | Cost / limitation |
|---|---|---|
| Version-pinned Python package + bundled skill assets | Small installs; normal Python dependency management; assets available offline after installation | CLI and skill must be explicitly installed at matching versions |
| Pinned Git source/release installation | Works before publication; inspectable exact revision | Git/build tooling and network on first install; larger source transfer |
| Bundle CLI source in every skill | No separate source download | Repeats executable code and packaging metadata; Python dependencies still need installation; upgrades become harder to audit |
| Fetch assets on demand | Smallest initial payload | Network/authentication becomes a prerequisite for reading the example; requires download, cache and integrity machinery |

The existing [0.3.0 PyPI release](https://pypi.org/project/openmapstack/0.3.0/)
demonstrates an available distribution route, but does not itself settle asset
delivery. Current package data includes schemas, not templates/examples.
The canonical Tartu pipeline is approximately 196 KiB; its generated dashboard
adds approximately 1.3 MiB without being necessary to regenerate the project.

## Decision

Use a pinned Python package for the CLI and checked, generated local asset
copies in the project-owning `open-map-stack` and `reproducible-gis-project`
skills. Keep canonical templates in `templates/` and the canonical worked
project in `examples/tartu-development/`. Export its README, manifest, pipeline,
convenience runner and override geometry into those two skills; omit generated
dashboards, QGIS archives, run logs, validation output and source data. Include
a local notice explaining those omissions. The real-data pipeline requires
network access and optional GIS dependencies; a copied example is not
advertised as an offline completed analysis.

The bounded `geospatial-data-discovery` and `spatial-sql` skills ship task
guidance and only the references needed for their tasks. Standalone installation
means they can answer those tasks without a sibling skill or CLI; it does not
require them to carry project templates, a schema or a worked project. When the
user asks for a full project, route to the generalist or project skill.

After publication, the proposed executable install command is
`python -m pip install 'openmapstack[geo]==0.4.0'`. Before publication, build and
install the wheel from the same checkout/revision. A pinned Git URL remains a
documented fallback; neither route installs Python automatically or triggers
downloads from skill instructions without the user's task requiring them.

Use `npx skills@1.5.26 add jaakla/openmapstack-skills --skill <name>` for the
installer verification. Test release pinning through a full GitHub tree URL
at the release tag or commit; do not confuse the CLI's `repo@skill` shorthand
with a Git revision. Release commands must be marked unavailable until the
0.4.0 tag and Python artifacts exist.

## Consequences and verification

Each skill ships reachable local references; only the two project-owning skills
ship templates, examples, schema and CLI setup instructions. Shared references
are copied from one maintained source with a drift check, rather than linked
across installed sibling directories. Snapshot v2 inventories the complete
selected payload; legacy v1 inspection remains available without changing
historical hashes or meaning.

Verify copied single-skill installs, subset/collection snapshots, missing or
tampered assets, package/version agreement and isolated fresh/update/removal
paths. Validate the trimmed example's preflight separately from executing its
network-dependent analysis. Final-state live behavior remains #39's release
gate, not a before/after equivalence requirement.
