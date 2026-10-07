import fs from "node:fs";
const metadata = JSON.parse(fs.readFileSync(new URL("./metadata.json", import.meta.url)));
export default {
  title: "Synthetic spatial analysis",
  root: "src",
  output: "dist",
  globalStylesheets: [],
  head: `<script id="openmapstack-view" type="application/json">${JSON.stringify(metadata).replaceAll("<", "\\u003c")}</script>`,
  footer: "Built locally with Observable Framework; synthetic data, no hosted account."
};
