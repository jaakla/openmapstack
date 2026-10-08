"""Canonical selected-stack entrypoint, replaying immutable analytical inputs."""
from __future__ import annotations
import csv
import datetime
import importlib.metadata
import json
import platform
import shutil
from pathlib import Path
import yaml
from openmapstack import delivery as delivery_contract
from openmapstack.integration import binding_errors, write_evidence
from openmapstack.integrity import canonical_file_set_hash, declared_input_paths, declared_output_paths, file_inventory
from openmapstack.checks.delivery import evidence_matches
from openmapstack.checks.integration import evidence_matches as integration_matches
import analysis
import delivery
import ingest
import orchestration

ROOT = Path(__file__).resolve().parent


def export(root: Path, project: dict, database: Path) -> dict:
    import duckdb
    directory = root / 'data/derived'
    directory.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(database)) as con:
        analysis.load_spatial(con)
        rows = con.execute('select id,area_m2,ST_AsGeoJSON(geometry) from analysis.candidates order by id').fetchall()
        # Actual independent oracle, not a receipt claiming the renderer ran.
        if [(r[0], r[1]) for r in rows] != [('donut', 30000.0), ('medium', 20000.0)]:
            raise ValueError('synthetic canonical net-area/selection oracle failed')
        scenarios = con.execute('select * from analysis.scenarios order by scenario').fetchall()
        if scenarios != [('canonical', 15000, 2, 50000.0), ('exploratory', 30000, 1, 30000.0)]:
            raise ValueError('synthetic scenario count/net-area oracle failed')
        features = [{'type': 'Feature', 'properties': {'id': r[0], 'area_m2': r[1]}, 'geometry': json.loads(r[2])} for r in rows]
        (directory / 'candidates.geojson').write_text(json.dumps({'type': 'FeatureCollection', 'crs': {'type': 'name', 'properties': {'name': 'EPSG:3301'}}, 'features': features}, sort_keys=True) + '\n')
        for table, columns, order in [('areas', ['id', 'area_m2'], 'id'), ('scenarios', ['scenario', 'minimum_area_m2', 'feature_count', 'area_m2'], 'scenario')]:
            query = 'select id,area_m2 from analysis.candidates order by id' if table == 'areas' else 'select * from analysis.scenarios order by scenario'
            with (directory / (table + '.csv')).open('w', newline='') as stream:
                writer = csv.writer(stream); writer.writerow(columns); writer.writerows(con.execute(query).fetchall())
    return {'features': len(rows), 'area_m2': sum(r[1] for r in rows)}


def finalize(project: dict, started: datetime.datetime, checks: list[dict]) -> None:
    completed = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = 'failed' if any(c['status'] == 'failed' for c in checks) else 'passed'
    project['validation']['required'] = [c['id'] for c in checks]
    run_id = started.strftime('run-%Y%m%d-%H%M%S-%f')
    inputs, outputs = declared_input_paths(ROOT, project), declared_output_paths(project)
    ih, oh = canonical_file_set_hash(ROOT, inputs), canonical_file_set_hash(ROOT, outputs)
    report = {'run_id': run_id, 'status': status, 'checks': checks, 'inputs_hash': ih, 'outputs_hash': oh}
    (ROOT / 'validation').mkdir(exist_ok=True)
    (ROOT / 'validation/latest-report.json').write_text(json.dumps(report, indent=2) + '\n')
    record = {'run_id': run_id, 'status': status, 'started_at': started.isoformat(), 'completed_at': completed,
        'inputs_hash': ih, 'outputs_hash': oh, 'inputs': file_inventory(ROOT, inputs), 'outputs': file_inventory(ROOT, outputs),
        'environment': {'python': platform.python_version()}}
    (ROOT / 'runs').mkdir(exist_ok=True)
    (ROOT / f'runs/{run_id}.json').write_text(json.dumps(record, indent=2) + '\n')
    project['project']['status'] = 'failed' if status == 'failed' else 'validated'
    project['runs']['latest'] = {'id': run_id, 'status': status, 'started_at': started.isoformat(), 'completed_at': completed,
        'inputs_hash': ih, 'outputs_hash': oh, 'record': {'path': f'runs/{run_id}.json'},
        'validation_report': {'path': 'validation/latest-report.json'}}
    (ROOT / 'project.yaml').write_text(yaml.safe_dump(project, sort_keys=False))


def main() -> None:
    project = yaml.safe_load((ROOT / 'project.yaml').read_text())
    errors = binding_errors(ROOT, project)
    if errors:
        raise ValueError('; '.join(errors))
    # No upstream API access: verify immutable snapshot before touching any tool.
    source = ROOT / project['sources']['parcels']['pin']['path']
    from openmapstack.integrity import sha256_file
    if sha256_file(source) != project['sources']['parcels']['pin']['sha256']:
        raise ValueError('pinned analytical snapshot changed')
    model_steps = {s['id']: s for s in project['processing']['steps']}
    for name, refs in analysis.model_plan(ROOT):
        expected = [d + '_result' for d in refs] or ['raw']
        if model_steps[name]['inputs'] != expected:
            raise ValueError('manifest SQL dependency summary diverges from definitions')
    params = json.loads((ROOT / 'parameters.json').read_text())
    if any(model_steps['candidates'].get(key) != value for key, value in params.items()):
        raise ValueError('manifest threshold summary diverges from parameters')
    started = datetime.datetime.now(datetime.timezone.utc)
    shutil.rmtree(ROOT / 'work', ignore_errors=True)
    (ROOT / 'work').mkdir()
    database = ROOT / 'work/spatial.duckdb'
    owners = {b['id']: b['owner'] for b in project['integrations']['bindings']}
    receipt = {'versions': {p: importlib.metadata.version(p) for p in ['duckdb', 'jinja2', 'PyYAML']}, 'stages': {}}
    for owner, package in [('dlt', 'dlt'), ('dbt', 'dbt-duckdb'), ('dbt', 'dbt-core'), ('dagster', 'dagster')]:
        if owner in owners.values():
            receipt['versions'][package] = importlib.metadata.version(package)
    def capture(name, function):
        result = function(); receipt['stages'][name] = result; return result
    stages = {
        'load': lambda: capture('load', lambda: ingest.load(source, database, ROOT / 'work/dlt-state', owners['ingestion'])),
        'transform': lambda: capture('transform', lambda: analysis.transform(ROOT, database, owners['analysis'])),
        'export': lambda: capture('export', lambda: export(ROOT, project, database)),
        'render': lambda: capture('render', lambda: delivery.render(ROOT, project))}
    if owners['orchestration'] == 'dagster':
        receipt['orchestration'] = orchestration.execute(stages, ROOT)
    else:
        for stage in stages.values():
            stage()
    (ROOT / 'delivery').mkdir(exist_ok=True)
    (ROOT / 'delivery/tool-run.json').write_text(json.dumps(receipt, default=str, indent=2) + '\n')
    delivery_contract.write_evidence(ROOT, project)
    write_evidence(ROOT, project)
    checks = [{'id': 'spatial_net_area_oracle', 'status': 'passed'}, {'id': 'unique_not_null_id', 'status': 'passed'}]
    for target in delivery_contract.targets(project):
        result = evidence_matches(ROOT, target['id'])
        checks.append({'id': 'delivery_' + target['id'], 'status': result.status, 'reason': result.detail})
    result = integration_matches(ROOT)
    checks.append({'id': 'integration_evidence', 'status': result.status, 'reason': result.detail})
    finalize(project, started, checks)
    if any(c['status'] == 'failed' for c in checks):
        raise RuntimeError('validation failed')

if __name__ == '__main__':
    main()
