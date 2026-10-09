# 0007 — Versioned, opt-in delivery selection

- Status: Accepted
- Date: 2026-10-07
- Related: [#79](https://github.com/jaakla/openmapstack-skills/issues/79)

## Decision

Add `delivery.schema: openmapstack-delivery/v1` to the existing project v1
contract. New templates explicitly select the built-in dashboard. Absence of
`delivery` retains legacy presentation/QGIS behavior; it never means an implicit
migration. Reject malformed, unknown, or unsupported explicit selections.

Selection binds each target to declared outputs, analytical inputs and delivery
evidence. Core analytical checks remain independent of target selection.
Target-specific dependencies and checks follow declarations, not incidental files.

## Consequences

Existing projects and check API consumers retain their v1 interpretation. Users
must explicitly migrate their manifest, pipeline and outputs together to opt in.
Older tools that do not understand the delivery declaration cannot certify a
migrated project; migration documentation must state this boundary.

The alternative was a second complete project schema. A separately versioned
delivery declaration preserves the analytical contract without maintaining two
copies of it. Future incompatible delivery changes require a new delivery major.

Owning semantics: [project-spec.md](../../../skills/open-map-stack/references/project-spec.md).
Implementation: [delivery.py](../../../openmapstack/delivery.py).
