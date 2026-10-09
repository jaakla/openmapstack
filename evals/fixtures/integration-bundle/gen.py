"""Synthetic complete-bundle contract controls; no Framework or agent runtime."""

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from openmapstack.integration import SCHEMA, definition_hash, steps_hash
from openmapstack.validation import validate_project

spec = importlib.util.spec_from_file_location("bundle_create", ROOT / "examples/delivery-profiles/create.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def generate(root: Path, mutation: str | None) -> None:
    path = example.create(root, ["observable"])
    project = yaml.safe_load(path.read_text())
    project["outputs"]["observable"]["path"] = "views/observable/index.html"
    project["outputs"]["integration_receipt"] = {
        "path": "delivery/integration.json", "kind": "document", "format": "JSON", "generated_by": "render"}
    script = root / "pipeline.py"
    # The canonical synthetic pipeline creates the entire bundle and its receipts.
    source = script.read_text().replace('href="data/derived/areas.csv"', 'href="../../data/derived/areas.csv"')
    source = source.replace('</body></html>', '<script src="app.js"></script></body></html>')
    source = source.replace('    write_evidence(ROOT, project)', '''    bundle = ROOT / "views/observable"
    (bundle / "app.js").write_text("window.openmapstackBundleFixture = true;")
    (bundle / "data.json").write_text(json.dumps(chosen, sort_keys=True))
    write_evidence(ROOT, project)
    from openmapstack.integration import write_evidence as integration_evidence
    integration_evidence(ROOT, project)''')
    script.write_text(source)
    steps = [step["id"] for step in project["processing"]["steps"]]
    for step in project["processing"]["steps"]:
        step["integration"] = "pipeline"
    project["integrations"] = {
        "schema": SCHEMA, "evidence": "integration_receipt",
        "bundles": [{"target": "observable", "path": "views/observable"}],
        "bindings": [{"id": "pipeline", "owner": "python", "role": "orchestration",
                      "definition": "pipeline.py", "sha256": definition_hash(root, "pipeline.py"),
                      "steps": steps, "steps_sha256": steps_hash(project, steps)}]}
    path.write_text(yaml.safe_dump(project, sort_keys=False))
    subprocess.run([sys.executable, str(script)], check=True)
    result = validate_project(path)
    if result.status == "failed":
        raise RuntimeError(f"unhealthy bundle control: {result.to_dict()}")
    project = yaml.safe_load(path.read_text())
    if mutation == "missing":
        project["integrations"].pop("bundles")
    elif mutation == "receipt":
        project["outputs"]["integration_receipt"]["path"] = "views/observable/integration.json"
    path.write_text(yaml.safe_dump(project, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--break", dest="mutation", choices=["missing", "receipt"])
    args = parser.parse_args()
    generate(args.destination, args.mutation)
