# 0007 — One vendored, descriptor-driven dashboard renderer

- Status: Proposed
- Date: 2026-10-06
- Related: [#6](https://github.com/jaakla/openmapstack-skills/issues/6), [#69](https://github.com/jaakla/openmapstack-skills/issues/69); `examples/tartu-development/pipeline.py` (`DASHBOARD_TEMPLATE`); `examples/nyc-private-mobility/dashboard-template.html`; `evals/fixtures/reference_pipeline/gen.py`; `openmapstack/checks/visual.py`; [0003](0003-shared-clean-rerun.md); [0005](0005-standalone-skill-distribution.md)

## Context

`presentation` declares what a view shows, but no shared code renders it. The repository has three independent renderers, and the browser check's DOM conventions match only the eval stand-in:

| Renderer | Layer toggles | Filters and scenarios | Tabs |
|---|---|---|---|
| Tartu, ~2,000 lines inside `pipeline.py` | `data-layer` | `render_dashboard` requires four filter ids by name; `data-scenario` uses camelCase state keys (`scenarioRoad`) | DOM `data`, manifest `provenance` |
| NYC `dashboard-template.html` | `id="layer-*"` | renders search, borough, scope and score filters; the manifest declares no `controls` | — |
| Eval reference generator | `data-layer-group` | `data-scenario` with manifest ids; no filters | none |

`visual.dashboard_loads_in_browser` looks up `data-layer-group` and `data-scenario` by manifest id. From the code, neither committed example would pass its toggle assertions. #69 found the same class of mismatch in a live-agent dashboard: controls in hidden tabs, an undocumented DOM shape, and an Edit tab and legend that the manifest declared but the agent never built.

Two constraints shape any shared renderer:

- A produced project must rebuild from its own declared files ([0003](0003-shared-clean-rerun.md)). Importing from a skill's `templates/` or from a repository path escapes the project root. That breaks both the clean rerun and the installed skill copy ([0005](0005-standalone-skill-distribution.md)).
- The manifest lacks semantics a generic renderer needs. Tartu's JavaScript also encodes these:
  - the comparison direction of each range filter;
  - a `choice` control that sets a classification threshold instead of filtering;
  - a scenario effect computed as `min(official, scenario)`, although the run already exports `dist_main_road_m` next to `dist_official_road_m`;
  - the tier rules;
  - the MapLibre styling of each layer.

  Its stylesheet and shell are already domain-neutral: tabs, accordions, switches, sliders, provenance and credits, validation, the basemap switcher and reset.

## Decision (proposed)

1. **Distribution.** Keep one canonical renderer under `templates/`: a static HTML/CSS/JS asset plus a small Python function that validates the view descriptor and substitutes it. Each project vendors an unmodified copy and lists it in `runtime.implementation.dependencies`. Tests prove the worked examples' copies are identical to the canonical files. At run time, a produced project imports nothing from the `openmapstack` package or a skill path.
2. **Declarative view rules within the spec's browser rules.** The renderer re-applies only what project-spec §3 already lets a browser do:
   - compare fields against control values;
   - apply ordered class rules built from those comparisons;
   - substitute scenario fields, switching between the effective and baseline fields.

   These semantics become new optional keys in `presentation.controls`, documented in project-spec §3, and `controls_match_pipeline` checks them. Example keys: a range filter's comparison direction, whether a choice control filters or classifies, and a scenario's effective-to-baseline field map. A view that needs anything else is a bespoke renderer and must still meet point 3.
3. **Documented DOM hooks are the check interface.** Every renderer emits stable hooks keyed by manifest id, bespoke renderers included:
   - `data-layer-group`, `data-scenario` and `data-filter` on controls;
   - `data-tab` and `data-panel`, using the manifest's tab ids;
   - `data-testid` for the legend, provenance, warnings, off-canonical label and canonical reset.

   Project-spec §3 lists these hooks. `visual.dashboard_loads_in_browser` is extended to use them, and it activates a control's tab before operating the control. No second verifier is added.
4. **Every default capability is implemented.** The renderer implements every capability that `templates/presentation.yaml` enables by default, including the edit tab and the semantic legend. A manifest copied from the template then cannot claim more than the view delivers.
5. **The real renderer is what CI exercises.** The eval reference generator and the Tartu example both render through the shared renderer, so fixture and visual CI test the real renderer and not a stand-in.

## Consequences

- Both project-owning skills ship the renderer asset ([0005](0005-standalone-skill-distribution.md) payload).
- Renderer fixes reach new projects only. An existing project keeps its vendored copy until it re-vendors; this is the cost of self-contained projects.
- A renderer change requires re-rendering the committed example dashboards. Tartu's view can be re-rendered offline from the data embedded in its committed `dashboard.html`. On 2026-10-06 that re-render was byte-identical to the committed file, so it is the regression baseline for a pure extraction. Once the HTML changes on purpose, a visual comparison replaces byte identity.
- `SKILL.md` must tell agents to vendor the renderer instead of writing one. That is a shipped behavior change and needs live-eval evidence, which requires authorizing the model, trial count and spending cap.
- The new optional manifest keys extend `openmapstack-project/v1` compatibly, since the presentation schema is open. Validators, templates and examples change together.

## Alternatives considered

### Render from the `openmapstack` package

An `openmapstack render` command would avoid vendoring and let fixes reach existing projects. It would also turn a QA tool into a runtime dependency of every produced project and tie the clean rerun to the installed package version. It would change the established boundary, under which `openmapstack/` validates artifacts and does not produce them.

### Shared shell with a per-project JavaScript hook

A shared shell would call an `evaluate(feature, state)` function written for each project. This is flexible, but agents would again write JavaScript for the part the checks cannot see. That hook is the most likely place for browser-side re-measurement to return.

### Keep prose guidance and strengthen only the checks

#69 shows that checks catch drift only after an agent has reinvented the view. They do not prevent it.

## Open questions for review

- NYC could move to the shared renderer, which would need descriptor support for its ranked list, search, CSV export and URL state, plus declared controls. Alternatively it stays a deliberately bespoke example that still meets point 3. Either way, its manifest must declare the filters it renders.
- Should edit mode be in the first shared release, or follow in a later change?
- What does the asset look like? This draft assumes `templates/dashboard-template.html` plus a Python render helper, following the NYC precedent. The project output must not also be called `dashboard.html`.
