"""Single SQL definition set: real dbt build or bounded standalone renderer.

The standalone examples render these same model/test files, discovering ref()
dependencies; they never copy the spatial formulas or store a second SQL DAG.
Use dbt for production SQL dependency management, tests and incremental models.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import re


def load_spatial(con) -> None:
    try:
        con.execute('LOAD spatial')
    except Exception:
        con.execute('INSTALL spatial; LOAD spatial')


def model_plan(root: Path) -> list[tuple[str, list[str]]]:
    dependencies = {}
    for path in sorted((root / 'dbt/models').glob('*.sql')):
        # Bounded fixture syntax: literal ref('name') calls only. Real dbt
        # owns richer macro/dynamic dependencies in the dbt/composed runtime.
        refs = re.findall(r"ref\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\)", path.read_text())
        dependencies[path.stem] = sorted(set(refs))
    ordered, pending = [], dict(dependencies)
    while pending:
        ready = [name for name, refs in pending.items() if all(r in {n for n, _ in ordered} for r in refs)]
        if not ready:
            raise ValueError('unresolved/cyclic model references')
        for name in ready:
            ordered.append((name, pending.pop(name)))
    return ordered


def transform(root: Path, database: Path, owner: str) -> dict:
    params = json.loads((root / 'parameters.json').read_text())
    if owner == 'dbt':
        env = {**os.environ, 'OMS_EXAMPLE_DATABASE': str(database), 'DO_NOT_TRACK': '1', 'DBT_SEND_ANONYMOUS_USAGE_STATS': 'false'}
        target = root / 'work/dbt-target'
        command = [str(Path(sys.executable).with_name('dbt')), 'build', '--project-dir', str(root / 'dbt'),
            '--profiles-dir', str(root / 'dbt'), '--target-path', str(target), '--log-path', str(root / 'work/dbt-logs'),
            '--vars', json.dumps(params)]
        completed = subprocess.run(command, env=env, text=True, capture_output=True)
        (root / 'work/dbt-console.txt').write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError('dbt build/tests failed; inspect work/dbt-console.txt')
        manifest = json.loads((target / 'manifest.json').read_text())
        results = json.loads((target / 'run_results.json').read_text())
        return {'owner': 'dbt', 'model_dependencies': {key: value['depends_on']['nodes'] for key, value in manifest['nodes'].items()},
                'results': [{'id': r['unique_id'], 'status': r['status']} for r in results['results']],
                'command': ['dbt', 'build', '--vars', params]}
    import duckdb
    from jinja2 import Environment, StrictUndefined
    env = Environment(undefined=StrictUndefined)
    with duckdb.connect(str(database)) as con:
        load_spatial(con)
        con.execute('CREATE SCHEMA IF NOT EXISTS analysis')
        values = {'ref': lambda name: 'analysis.' + name, 'source': lambda schema, name: 'raw.' + name,
                  'var': lambda name: params[name]}
        for name, dependencies in model_plan(root):
            sql = env.from_string((root / 'dbt/models' / (name + '.sql')).read_text()).render(**values)
            con.execute('CREATE OR REPLACE TABLE analysis.' + name + ' AS ' + sql)
        for path in sorted((root / 'dbt/tests').glob('*.sql')):
            sql = env.from_string(path.read_text()).render(**values)
            if con.execute(sql).fetchall():
                raise ValueError('spatial correctness test failed: ' + path.name)
        if con.execute('select count(*) != count(distinct id) or count(*) != count(id) from analysis.measured').fetchone()[0]:
            raise ValueError('duplicate/null feature identifier')
    return {'owner': 'sql', 'model_dependencies': dict(model_plan(root)), 'tests': ['spatial_correctness', 'unique_not_null_id']}
