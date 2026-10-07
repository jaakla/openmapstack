# Design language for project views — `openmapstack-views/0.1`

A project's dashboard or report is written for its own analysis; no template fits
every question. This language keeps those views recognisable and honest anyway. It
fixes the principles, the page archetypes, how a component is chosen, the names of
the design tokens and a small state protocol the checks use. It leaves layout detail,
colour values and new components to the project.

A manifest opts in with `presentation.design_language: openmapstack-views/0.1`
(project-spec.md s. 2.7, Pages and the design language). The checks then hold its
pages to section 5. A view that does not declare a version is checked as before.

## 1. Principles

- **The answer comes first.** Open on the result the question asked for: a ranked
  shortlist, the recommendation, the map of the accepted run. Method and provenance
  follow.
- **The run's state is visible and restorable.** The view opens at the canonical run.
  Leaving it is labelled as exploratory and one action returns to it, unless every
  state the control offers is a published result (`effect: published`).
- **Kinds of data stay distinguishable.** Source, derived result, human correction and
  hypothetical or scenario geometry never look alike. Use the semantic roles of
  project-spec.md s. 3.
- **A colour means one thing everywhere.** The colour for a scenario, site or class
  is the same on the map, in charts, in tables and in prose highlights.
- **Re-apply published rules; never re-measure.** The browser switches between values
  the pipeline computed. It does not compute distances, areas or buffers.
- **Provenance is one step away.** Every page has the credits block and a way from a
  number or feature to its source, run and assumptions.
- **It works on a phone and in both themes.** The map keeps a usable share of a
  390-pixel-wide screen, and every token has a light and a dark value.
- **The reader's language.** Labels, dates and numbers follow the language and locale
  of the view, including decimal separators and thousands grouping.

## 2. Archetypes

| Archetype | Use it for | Baseline |
|---|---|---|
| `workspace` | Exploring a result on a map: shortlists, screening, what-if controls | A map beside a sidebar organised as ARIA tabs of collapsible sections. Analysis first, then map controls, then provenance. On a phone the map comes first and the tabs follow below it; the sidebar never covers the map. |
| `report` | Explaining a decision: comparison of options, recommendation, cost–benefit | One reading column broken by wide figures (map, charts, schematics). The recommendation or verdict opens the page. A section navigation stays in reach. Numbers in the prose come from the pipeline's outputs, not from hand-typed text. |

A result that needs both, such as a citywide screening app and a report on one
district, is several pages: declare them in top-level `views:` with their scope. Link
the pages to each other. Each page says which scope its numbers describe.

## 3. Components, chosen by state

Pick the component from the kind of state it controls, not from habit:

| State | Component | State exposed as |
|---|---|---|
| Independent on/off (a layer group, one scenario override) | switch: `<input type="checkbox">` styled as a switch | `checked` |
| A few named, mutually exclusive states (travel mode, year, scenario) | segmented control: a `role="group"` of buttons | `aria-pressed` on each button |
| Many mutually exclusive states, or long labels | `<select>` | `value` |
| An ordered value (a threshold the run materialised) | `<input type="range">` snapping to the computed options | `value` |
| Set membership (land-use classes, categories) | chips: a `role="group"` of toggle buttons | `aria-pressed` on each button |
| Comparison with a reference | a difference view with a reference selector; the legend switches to a diverging ramp | as for its segmented control and select |
| A selected feature or row | a details card with a close button | — |
| Pages within a page | ARIA tabs: `role="tab"` with `aria-selected` and `aria-controls` pointing at a `role="tabpanel"` | `aria-selected` |

Interaction rules:

- Hovering previews, clicking pins, and Esc or the close button clears. This holds
  for map features, table rows and list entries alike.
- A list, table or chart entry that has a place on the map links to it, and the map
  selection highlights the entry.
- When one control appears in several places (a travel-mode switch in three panels),
  every copy shows the same state and carries the same manifest id.
- Tables follow project-spec.md s. 3, Tabular results.

## 4. Tokens: names fixed, values free

Declare colours, type and spacing as CSS custom properties under these names. Choose
the values for the project, within contrast and theme rules: body text at least 4.5:1
against its background, and every token defined for light and dark.

| Group | Names |
|---|---|
| Neutrals | `--oms-bg`, `--oms-surface`, `--oms-surface-2`, `--oms-border`, `--oms-text`, `--oms-text-muted` |
| Status | `--oms-accent`, `--oms-ok`, `--oms-warn`, `--oms-error`, `--oms-exploratory` |
| Semantic roles | `--oms-role-<role>` for each role of project-spec.md s. 3, with `-` for `_`: `--oms-role-primary-result`, `--oms-role-user-override`, … |
| Entity identity | `--oms-entity-<id>`, one per scenario, site or class that keeps its colour across the page |
| Ramps | `--oms-ramp-seq-<n>` and `--oms-ramp-div-<n>`, numbered from low to high |
| Type | `--oms-font-body`, `--oms-font-data` (tabular figures), `--oms-font-display` |
| Space and shape | `--oms-space-<n>` from small to large, `--oms-radius` |

Set light values on `:root` and dark values under
`@media (prefers-color-scheme: dark)`. A page with a theme switch also honours
`<html data-theme="light|dark">`. Add project-specific tokens freely, but use these
names for these roles.

## 5. State protocol

These hooks are how the checks operate any view, whatever its widgets look like. They
add no visible markup.

- **Controls.** Each control declared in the page's `presentation.controls`
  (`filters`, `scenarios`, `variants`) carries `data-oms-control="<manifest id>"` on
  exactly one of these elements:
  - an `<input type="checkbox">`, `<input type="range">` or `<select>`, whose state is
    `checked` or `value`;
  - a `role="group"` or `role="radiogroup"` element whose option buttons carry
    `data-oms-value="<option>"` and `aria-pressed="true|false"`.

  Copies of a repeated control carry the same id; the checks operate the first one that
  is visible.
- **Layer groups.** Each group in `presentation.map.layer_groups` has one toggle with
  `data-oms-layer-group="<group id>"`: a checkbox, or a button with `aria-pressed`.
- **Page state.** `<html data-oms-state="canonical">` while every exploratory control
  is at its canonical position. Switch to `data-oms-state="exploratory"` as soon as any
  one is not. Published controls do not change the state.
- **Exploratory label.** An element with `data-oms-exploratory` is visible while the
  state is exploratory and hidden otherwise. It says in words that the view no longer
  shows the published result.
- **Reset.** A button with `data-oms-reset` restores every control to its canonical
  position. It is reachable whenever the state is exploratory, typically inside the
  label.
- **Panels.** The legend, provenance and warnings panels carry
  `data-testid="legend"`, `data-testid="provenance"` and `data-testid="warnings"`.
- **Tabs.** Use ARIA tabs (section 3). A control may sit on any tab; the checks open
  its tab first.

With the version declared, `visual.dashboard_loads_in_browser` opens every page in
`views:` and, for each declared control, checks that:

- the control exists, on whichever tab it sits;
- changing it changes the page;
- an exploratory control shows the label and sets the page state;
- the reset restores every control and the canonical state.

Layer-group toggles must change the rendered map.

## 6. Adding a component

When section 3 has nothing that fits, invent the component:

- Build it from the tokens of section 4 and the interaction rules of section 3.
- Name it in the page's design header, a comment at the top of the stylesheet, for
  example `/* openmapstack-views/0.1 · report · new: river-profile strip chart */`.
- If it can move the view away from the run's state, declare it as a control under the
  kind that matches its meaning: `filters` re-apply a rule, `scenarios` switch an
  override, `variants` choose between precomputed results. Give it the hooks of
  section 5.
- A purely presentational addition, such as a chart or a schematic, needs no
  declaration. It still uses the role and entity colours, has a legend or direct
  labels, and an accessible name.

## 7. How the language changes

Patterns enter the language from real projects. A component that recurs
independently, or that the maintainers judge broadly useful, is added here with an
example and, if it carries a claim, a check. Each change that adds or tightens a rule
gets a new version. A project keeps the version it declares until it moves to a newer
one, so a published view is never failed by a rule written after it.

### Changelog

- **0.1** — first version, drawn from the Tartu and NYC worked examples and an
  independent bridge-comparison project with a screening page and a report.
