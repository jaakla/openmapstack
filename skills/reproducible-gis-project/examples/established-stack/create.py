"""Prepare an independent selected-tool project; never install tools here."""
from __future__ import annotations
import argparse
import json
import shutil
import sys
from pathlib import Path
import yaml
from openmapstack.integration import definition_hash, steps_hash
from openmapstack.integrity import sha256_file

HERE = Path(__file__).resolve().parent
TOOLS = {'dashboard', 'dlt', 'dbt', 'dagster', 'observable', 'qgis', 'composed'}


def create(destination: Path, tool: str = 'dashboard', *, qgis: bool = False) -> Path:
    if tool not in TOOLS:
        raise ValueError('unknown integration')
    destination = destination.resolve()
    if destination == HERE or destination.is_relative_to(HERE):
        raise ValueError('use a separate writable project directory')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('destination must be empty; existing analysis is never overwritten')
    destination.mkdir(parents=True, exist_ok=True)
    for source in HERE.rglob('*'):
        relative = source.relative_to(HERE)
        if not source.is_file() or any(p in {'node_modules', '__pycache__', '.observablehq', 'target', 'logs'} for p in relative.parts):
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    project = yaml.safe_load((HERE / 'project.yaml').read_text())
    project['project'].update(id='established-stack-' + tool, title='Established-stack synthetic spatial example',
                             question='Which synthetic polygons have net area at least 15000 square metres?')
    project['interpretation']['objective'] = 'Run one spatial method through selected external tools with reproducible delivery.'
    project['interpretation']['assumptions'] = [{'id': 'synthetic', 'statement': 'Synthetic polygons in EPSG:3301 include a hole.',
        'rationale': 'A hand-derived net-area oracle verifies the spatial method; this is not a factual parcel study.'}]
    source = project['sources']['parcels']
    source['dataset'] = 'Three synthetic WKT polygons'
    source['pin'].update(path='data/source/parcels.csv', sha256=sha256_file(destination / 'data/source/parcels.csv'))
    source['access']['file'] = {'name': 'parcels.csv', 'format': 'CSV with WKT', 'row_count': 3, 'column_count': 3}
    source['selection']['filter'] = 'All three synthetic polygons; no API pagination'
    source['schema']['columns'] = ['id', 'wkt', 'updated_at']
    source['version']['identifier'] = 'synthetic-spatial-v1'
    project['warnings'] = [{'id': 'synthetic', 'severity': 'info', 'issue': 'Synthetic data', 'statement': 'Synthetic example only; no real cadastral or planning conclusions.', 'mitigation': 'Use authoritative pinned data for real studies.'}]
    params = json.loads((destination / 'parameters.json').read_text())
    # The example SQL renderer discovers model dependencies from ref() calls.
    sys.path.insert(0, str(destination))
    from analysis import model_plan
    plan = model_plan(destination)
    model_steps = []
    for name, dependencies in plan:
        model_steps.append({'id': name, 'operation': 'area' if name == 'measured' else 'sql_model', 'inputs': dependencies or ['raw'], 'output': name + '_result',
                            'integration': 'analysis', **(params if name == 'candidates' else {})})
        # Symbol names are the dbt model names, mapped to manifest outputs.
        model_steps[-1]['inputs'] = [d + '_result' for d in dependencies] or ['raw']
    project['processing']['steps'] = [
        {'id': 'load', 'operation': 'read', 'source': 'parcels', 'output': 'raw', 'integration': 'ingestion'},
        *model_steps,
        {'id': 'export', 'operation': 'export', 'inputs': ['candidates_result', 'scenarios_result'], 'output': 'analytical_outputs'},
        {'id': 'render', 'operation': 'render', 'input': 'analytical_outputs', 'output': 'views'}]
    project['outputs'] = {k: v for k, v in project['outputs'].items() if v.get('kind') != 'document'}
    project['outputs']['scenarios'] = {'path': 'data/derived/scenarios.csv', 'format': 'CSV', 'kind': 'table', 'generated_by': 'export',
        'role': 'exploratory_companion', 'note': 'Precomputed canonical and exploratory thresholds; canonical remains the accepted result.',
        'table': {'key': ['scenario'], 'columns': [{'name': 'scenario', 'type': 'string'}, {'name': 'minimum_area_m2', 'type': 'number', 'unit': 'm²'},
                  {'name': 'feature_count', 'type': 'integer', 'unit': 'features'}, {'name': 'area_m2', 'type': 'number', 'unit': 'm²'}]}}
    project['delivery']['targets'] = []
    kinds = ['observable'] if tool in {'observable', 'composed'} else ['qgis'] if tool == 'qgis' else ['dashboard']
    if qgis and 'qgis' not in kinds:
        kinds.append('qgis')
    for kind in kinds:
        path = {'qgis': 'project.qgz', 'observable': 'views/observable/index.html', 'dashboard': 'dashboard.html'}[kind]
        project['outputs'][kind] = {'path': path, 'format': 'QGZ' if kind == 'qgis' else 'HTML', 'kind': 'document', 'generated_by': 'render'}
        project['outputs'][kind + '_evidence'] = {'path': 'delivery/' + kind + '-evidence.json', 'format': 'JSON', 'kind': 'document', 'generated_by': 'render'}
        project['delivery']['targets'].append({'id': kind, 'kind': kind, 'mode': 'local', 'output': kind,
                                            'inputs': ['candidates', 'areas', 'scenarios'], 'evidence': kind + '_evidence'})
    if 'dashboard' not in kinds:
        project['presentation'].pop('layout', None)
        project['presentation'].pop('provenance_ui', None)
    if 'qgis' in kinds:
        project['presentation']['map'] = {'layer_groups': [{'id': 'analysis', 'title': 'Analysis'}],
            'layers': [{'source': 'candidates', 'group': 'analysis', 'semantic_role': 'primary_result', 'geometry': 'polygon'}]}
    for key, filename in [('integration_evidence', 'delivery/integration-evidence.json'), ('tool_run', 'delivery/tool-run.json')]:
        project['outputs'][key] = {'path': filename, 'format': 'JSON', 'kind': 'document', 'generated_by': 'render'}
    definition_paths = ['analysis.py', 'ingest.py', 'delivery.py', 'orchestration.py', 'parameters.json', 'dbt', 'observable',
                        'requirements-base.txt', 'requirements-dlt.txt', 'requirements-dbt.txt', 'requirements-dagster.txt', 'requirements-composed.txt']
    project['runtime']['implementation'] = {'preferred_engine': 'duckdb-spatial', 'pipeline': 'pipeline.py', 'dependencies': definition_paths}
    project['runtime']['environment'] = {'python': '3.12', 'duckdb': '1.5.6'}
    bindings = [
        {'id': 'ingestion', 'owner': 'dlt' if tool in {'dlt', 'composed'} else 'file', 'role': 'ingestion', 'definition': 'ingest.py', 'steps': ['load']},
        {'id': 'analysis', 'owner': 'dbt' if tool in {'dbt', 'composed'} else 'sql', 'role': 'transformation', 'definition': 'dbt', 'steps': [n for n, _ in plan]},
        {'id': 'orchestration', 'owner': 'dagster' if tool in {'dagster', 'composed'} else 'python', 'role': 'orchestration', 'definition': 'orchestration.py', 'steps': []},
    ]
    for kind in kinds:
        bindings.append({'id': kind, 'owner': kind, 'role': 'presentation', 'definition': 'observable' if kind == 'observable' else 'delivery.py', 'steps': []})
    for binding in bindings:
        binding['sha256'] = definition_hash(destination, binding['definition'])
        binding['steps_sha256'] = steps_hash(project, binding['steps'])
    project['integrations'] = {'schema': 'openmapstack-integrations/v1', 'bindings': bindings, 'evidence': 'integration_evidence', 'run_evidence': 'tool_run',
        'bundles': [{'target': 'observable', 'path': 'views/observable'}] if 'observable' in kinds else []}
    path = destination / 'project.yaml'
    path.write_text(yaml.safe_dump(project, sort_keys=False))
    return path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--tool', choices=sorted(TOOLS), default='dashboard')
    parser.add_argument('--qgis', action='store_true')
    args = parser.parse_args()
    print(create(args.destination, args.tool, qgis=args.qgis))
