# 0007 — Dashboards follow a versioned design language, not a shared renderer

- Status: Proposed
- Date: 2026-10-07
- Related: [#6](https://github.com/jaakla/openmapstack-skills/issues/6), [#69](https://github.com/jaakla/openmapstack-skills/issues/69); `examples/tartu-development/`; `examples/nyc-private-mobility/`; [jaakla/tartu-sillad](https://github.com/jaakla/tartu-sillad); `openmapstack/checks/visual.py`; [0003](0003-shared-clean-rerun.md); [0005](0005-standalone-skill-distribution.md)

## Context

#6 asked for one reusable renderer driven by `presentation`. A real medium-sized project shows why a fixed renderer cannot keep up. In [tartu-sillad](https://github.com/jaakla/tartu-sillad), a comparison of two planned Emajõgi footbridges, the views needed patterns that neither the Tartu example's renderer nor the manifest could express:

- **Report archetype.** The view is one reading column broken by wide figures, opening with the recommendation. It is not a sidebar workspace.
- **Combined scenario states.** Two declared scenario toggles appear as one four-way choice: none, either bridge, or both.
- **Precomputed views.** Travel-mode and year switches drive the map, KPIs and charts together.
- **Encodings beyond map layers.** Sequential and diverging ramps with tick legends, scenario identity colours shared by map, charts and tables, dumbbell and stacked charts, a river schematic, and numbers computed by the pipeline set inside the prose.
- **Evidence sections.** Validation against observed bike-share trips, cost-benefit with ranges, sensitivity, and switchable sortable tables with downloads.
- **More on its companion page.** That page compares five sites and adds a difference-from-reference view, a river-profile strip chart, dismissible linked selection and cross-links from lists to the map.

The contract kept growing to follow such projects. Precomputed views, table outputs and the credits block were added to `openmapstack-project/v1` on the same day as tartu-sillad's runs. They still do not cover its scenario combinations or charts. Each new non-trivial analysis can be expected to need controls, view types or tabs that nobody declared in advance.

Some things are already consistent without a template:

- Each tartu-sillad page opens with a comment naming its layout archetype and its visual identity.
- All three real dashboards use the same build seam: a template file, one embedded JSON payload, and Python substitution.
- They share the light/dark token mechanism and the credits block.

What does not hold is the link between claims and delivery:

- The browser check expects checkbox inputs with `data-layer-group` or `data-scenario`. tartu-sillad uses buttons with `aria-pressed` and short state codes, and neither repository example matches either.
- The tartu-sillad manifest declares a canonical reset and a required off-canonical label. Its report renders neither, and `verify` does not run the browser check, so this went unnoticed. #69 records the same class of gap in a live-agent dashboard.
- In a report that compares scenarios side by side, "canonical versus exploratory" fits poorly, because every scenario shown is a published result.

The constraints recorded in [0003](0003-shared-clean-rerun.md) and [0005](0005-standalone-skill-distribution.md) still apply. A project rebuilds from its own declared files and imports nothing from a skill path or the `openmapstack` package at run time.

## Decision (proposed)

1. **Ship a design language, not a renderer.** The skills carry a versioned reference that defines:
   - **Principles.** The answer comes first. The state the analysis ran in is visible and restorable. Source, result, override and hypothetical data stay distinguishable. A colour means one thing on the map, in charts and in tables. The view re-applies published rules and never re-measures. Provenance is one step away. The view works on a phone and in both themes.
   - **Archetypes and their baseline.** Version 1 has two: the analytical workspace (tabs beside a map) and the report (a reading column broken by wide figures).
   - **Component grammar chosen by state semantics.** Independent on/off uses a switch. A few named, mutually exclusive states use a segmented control. An ordered value uses a range. Set membership uses chips. Comparison with a reference uses an explicit difference view. A selection opens a dismissible details card.
   - **Token roles.** Neutral, semantic-role, entity-identity and ramp tokens. Each project picks its own values within contrast and theme rules.
   - **The build seam.** A template file, one JSON payload, and substitution in the pipeline.
2. **Ad-hoc additions are expected.** A project may invent a component when the grammar has none. It composes the component from the token roles and interaction rules and names it in the page's design header. A new component that moves the view away from the state the analysis ran in must be declared in `presentation.controls`, using an existing kind or a generic custom kind with `id`, `canonical` and states, so the checks can operate it. A purely presentational addition, such as a chart, needs no declaration.
3. **Checks verify claims through a state protocol, not DOM shape.**
   - Each declared control carries `data-oms-control="<manifest id>"`.
   - It exposes state through native semantics: `checked`, `aria-pressed`, `value` or `aria-selected`.
   - Tabs follow ARIA tab semantics, so the checker can find and activate the tab that owns a control.

   `visual.dashboard_loads_in_browser` is generalised to this protocol. It checks that every declared control exists and has an effect, that reset restores the declared state, and that off-canonical labelling appears wherever the archetype requires it. No second verifier is added.
4. **The language evolves by promotion.** New patterns come from real projects, in this repository or outside it. A pattern that recurs independently, or that the maintainer judges broadly useful, joins the language with a specimen and, if it carries a claim, a check. The language version then increases, and checks apply the rules of the version a project follows.
5. **Specimens, not templates.** The worked examples demonstrate the archetypes. A small optional kit with tokens and base component CSS may be extracted once two specimens share the same code. It stays a convenience and never becomes a required dependency.

## Consequences

- Agents keep writing each project's HTML and JavaScript. Consistency comes from the language, the specimens and the checks, not from identical code. Look and feel may drift within limits; a mismatch between claims and delivery fails.
- The manifest keeps the semantics and the claims, and stops trying to enumerate the user interface. For example, two declared scenario toggles may legitimately appear as a four-way segmented control.
- Automatic checks can only judge claims and measurable rules: controls, reset, theme, contrast and mobile layout. Conformance to the language beyond that needs a human or visual review rubric.
- `SKILL.md` must route agents to the language reference. That is a shipped behavior change and needs live-eval evidence, which requires authorizing the model, trial count and spending cap.
- #6 changes scope from "extract a renderer" to "publish the language and generalise the checker". #69's checker items become point 3.

## Alternatives considered

### One descriptor-driven shared renderer

This was the first draft of this ADR. Each of tartu-sillad's needs would have required new manifest semantics before the renderer could show it. The contract already gained three features on the day those runs happened and still did not cover them.

### Prose guidance without a check interface

Claims stay unverifiable. tartu-sillad's missing reset and #69's missing controls both shipped this way.

### A mandatory component library

This reproduces the template problem at a smaller scale. Point 5 keeps a kit possible later, as an optional convenience.

## Open questions for review

- Should a manifest record the language version it follows, for example `presentation.design_language`?
- Should `verify` run the browser check when Playwright is available?
- What does "canonical" mean in the report archetype? One option: the initial selection, with no off-canonical label required when every scenario shown is a published result.
- What should the report specimen be? A trimmed public copy of tartu-sillad needs its unresolved source licence (its `source_license_resolved` warning) settled first.
