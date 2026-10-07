# Synthetic spatial analysis

DuckDB Spatial measures polygon area in **EPSG:3301**, including holes. These are synthetic features, not cadastral evidence. Scenario choices display precomputed validated results; they do not change the accepted run.

```js
import {FileAttachment} from "observablehq:stdlib";
const data = await FileAttachment("data.json").json();
const choices = document.createElement("select");
choices.setAttribute("aria-label", "Scenario");
choices.id = "scenario";
for (const item of data.scenarios) {
  const option = document.createElement("option");
  option.value = item.scenario;
  option.textContent = `${item.scenario}: minimum ${item.minimum_area_m2.toLocaleString()} m²`;
  choices.append(option);
}
const scenario = Generators.input(choices);
const reset = document.createElement("button");
reset.textContent = "Reset to canonical";
reset.onclick = () => { choices.value = "canonical"; choices.dispatchEvent(new Event("input", {bubbles:true})); };
const controls = document.createElement("div");
controls.append(choices, reset);
display(controls);
```

```js
const summary = data.scenarios.find(d => d.scenario === scenario);
const status = document.createElement("p");
status.id = "scenario-status";
status.textContent = `${scenario === "canonical" ? "Accepted canonical result" : "Exploratory scenario — accepted run unchanged"}: ${summary.feature_count} features; ${summary.area_m2.toLocaleString("en-US")} m²`;
display(status);
const table = document.createElement("table");
table.innerHTML = "<thead><tr><th>Feature</th><th>Area (m²)</th></tr></thead>";
const tbody = document.createElement("tbody");
for (const feature of data.features.filter(d => d.area_m2 >= summary.minimum_area_m2)) {
  const row = document.createElement("tr");
  for (const value of [feature.id, feature.area_m2.toLocaleString("en-US")]) {
    const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
  }
  tbody.append(row);
}
table.append(tbody); display(table);
```

<a href="areas.csv" download>Download accepted areas CSV</a>

```js
const provenance = document.createElement("section");
provenance.setAttribute("data-openmapstack-provenance", "");
const title = document.createElement("h2"); title.textContent = "Provenance and limitations"; provenance.append(title);
for (const text of data.provenance) { const p = document.createElement("p"); p.textContent = text; provenance.append(p); }
display(provenance);
```
