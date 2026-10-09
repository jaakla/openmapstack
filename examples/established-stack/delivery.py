"""Presentation adapters consume validated outputs; no spatial transformations."""
from __future__ import annotations
import csv
import html
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from openmapstack.delivery import metadata_json, targets


def provenance(project: dict) -> list[str]:
    return [project['interpretation']['objective'],
        'Generated with AI using the free OpenMapStack toolkit: ' + project['project']['generated_with']['url'],
        'Author: ' + project['project']['author']['name'] + ' <' + project['project']['author']['email'] + '>',
        *[f"{s['provider']}; {s['dataset']}; {s['version']['identifier']}; {s['license']['name']}; {s['schema']['crs']}" for s in project['sources'].values()],
        *[a['statement'] + ' ' + a['rationale'] for a in project['interpretation']['assumptions']],
        *[w['statement'] for w in project.get('warnings', [])]]


def render(root: Path, project: dict) -> list[dict]:
    # Views consume declared, already-validated artifacts, never operational DB
    # state. The bounded exploratory threshold is a subset of accepted features.
    with (root / project['outputs']['areas']['path']).open(newline='') as stream:
        features = [{'id': row['id'], 'area_m2': float(row['area_m2'])} for row in csv.DictReader(stream)]
    with (root / project['outputs']['scenarios']['path']).open(newline='') as stream:
        scenarios = [{'scenario': row['scenario'], 'minimum_area_m2': float(row['minimum_area_m2']),
                      'feature_count': int(row['feature_count']), 'area_m2': float(row['area_m2'])}
                     for row in csv.DictReader(stream)]
    payload = {'features': features, 'scenarios': scenarios, 'provenance': provenance(project)}
    results = []
    for target in targets(project):
        path = root / project['outputs'][target['output']]['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        if target['kind'] == 'dashboard':
            # Small focused example, not a new generic renderer (#6).
            rows = ''.join(f'<tr><td>{html.escape(f["id"])}</td><td>{f["area_m2"]}</td></tr>' for f in features if f['area_m2'] >= scenarios[0]['minimum_area_m2'])
            notes = ''.join('<p>' + html.escape(s) + '</p>' for s in payload['provenance'])
            path.write_text(f'<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic spatial analysis</title>'
                f'<body><h1>Synthetic spatial analysis</h1><p>Accepted canonical result: {scenarios[0]["feature_count"]} features; {scenarios[0]["area_m2"]:,.0f} m²</p>'
                f'<table><thead><tr><th>Feature</th><th>Area (m²)</th></tr></thead><tbody>{rows}</tbody></table>'
                f'<a href="data/derived/areas.csv" download>Download accepted areas CSV</a>'
                f'<section data-openmapstack-provenance><h2>Provenance and limitations</h2>{notes}</section>'
                f'<script id="openmapstack-view" type="application/json">{metadata_json(root, project, target)}</script></body></html>')
            results.append({'owner': 'dashboard', 'output': target['output']})
        elif target['kind'] == 'observable':
            stage = root / 'work/observable-app'
            shutil.copytree(root / 'observable', stage)
            (stage / 'metadata.json').write_text(metadata_json(root, project, target))
            (stage / 'src/data.json').write_text(json.dumps(payload, sort_keys=True))
            shutil.copyfile(root / 'data/derived/areas.csv', stage / 'src/areas.csv')
            env = {**os.environ, 'OBSERVABLE_TELEMETRY_DISABLE': '1'}
            for command in [['npm', 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], ['npm', 'run', 'build']]:
                completed = subprocess.run(command, cwd=stage, env=env, text=True, capture_output=True)
                with (root / 'work/observable-console.txt').open('a') as log:
                    log.write(completed.stdout + completed.stderr)
                if completed.returncode:
                    raise RuntimeError('Observable build failed; inspect work/observable-console.txt')
            shutil.copytree(stage / 'dist', path.parent, dirs_exist_ok=True)
            # Keep a stable declared download link in addition to Framework's hashed resources.
            shutil.copyfile(root / 'data/derived/areas.csv', path.parent / 'areas.csv')
            results.append({'owner': 'observable', 'surface': 'Framework static build', 'framework': '1.13.4',
                            'node': subprocess.check_output(['node', '--version'], text=True).strip(), 'published': False})
        elif target['kind'] == 'qgis':
            executable = os.environ.get('OMS_QGIS_PYTHON', sys.executable)
            completed = subprocess.run([executable, '-m', 'openmapstack.integrations.qgis', str(root / 'project.yaml'), '--target', target['id']],
                env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, text=True, capture_output=True)
            (root / 'work/qgis-console.txt').write_text(completed.stdout + completed.stderr)
            if completed.returncode:
                raise RuntimeError('native QGIS generation failed; select a PyQGIS interpreter; inspect work/qgis-console.txt')
            results.append({'owner': 'qgis', 'native_write_reload': 'passed', 'interpreter_reference': 'env:OMS_QGIS_PYTHON'})
    return results
