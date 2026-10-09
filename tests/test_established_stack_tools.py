"""Opt-in real tool checks: OMS_STACK_TOOLS=dashboard,dlt,dbt,dagster,observable,qgis,composed.

Install only the selected requirements. No accounts or live agent calls are used.
Native QGIS can use env:OMS_QGIS_PYTHON; Playwright is needed for Observable UI QA.
"""
from __future__ import annotations
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import yaml
from openmapstack.integrity import sha256_file
from openmapstack.rerun import perform_clean_rerun
from openmapstack.validation import validate_project
from openmapstack.checks.delivery import _serve_bundle, web_view_loads

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/established-stack'
SELECTED = set(filter(None, os.environ.get('OMS_STACK_TOOLS', '').split(',')))


@unittest.skipUnless(SELECTED, 'optional tool QA requires explicit OMS_STACK_TOOLS selection')
class EstablishedStackToolTests(unittest.TestCase):
    def test_selected_tools_execute_and_rebuild_from_pins(self):
        accepted = None
        for tool in sorted(SELECTED):
            with self.subTest(tool=tool), tempfile.TemporaryDirectory(prefix='oms-stack-tools-') as temp:
                root = Path(temp) / 'project'
                command = [sys.executable, str(EXAMPLE / 'create.py'), str(root), '--tool', tool]
                if tool == 'composed' and os.environ.get('OMS_QGIS_PYTHON'):
                    command.append('--qgis')
                completed = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                completed = subprocess.run([sys.executable, str(root / 'pipeline.py')], capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                result = validate_project(root / 'project.yaml')
                self.assertEqual(result.status, 'passed', result.to_dict())
                digest = sha256_file(root / 'data/derived/areas.csv')
                if accepted is None:
                    accepted = digest
                self.assertEqual(digest, accepted, 'integration changed the analytical result')
                rerun = perform_clean_rerun(root, Path(temp) / 'clean', 300)
                self.assertEqual(rerun['status'], 'passed', rerun)
                self.assertEqual(sha256_file(Path(temp) / 'clean/data/derived/areas.csv'), digest)
                project = yaml.safe_load((root / 'project.yaml').read_text())
                receipt = json.loads((root / 'delivery/tool-run.json').read_text())
                if tool == 'dbt' or tool == 'composed':
                    self.assertTrue(all(r['status'] in {'pass', 'success'} for r in receipt['stages']['transform']['results']))
                if tool == 'dagster' or tool == 'composed':
                    self.assertTrue(receipt['orchestration']['success'])
                    self.assertTrue(any(e['type'] == 'ASSET_CHECK_EVALUATION' for e in receipt['orchestration']['events']))
                if tool == 'observable' or tool == 'composed':
                    self.assertEqual(web_view_loads(root, 'observable').status, 'passed')
                    self.check_observable(root)
                if tool == 'qgis' or (tool == 'composed' and os.environ.get('OMS_QGIS_PYTHON')):
                    self.check_qgis(root)

    def check_observable(self, root):
        from playwright.sync_api import sync_playwright
        with _serve_bundle(root / 'views/observable/index.html') as url, sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={'width':1280,'height':900})
            page.goto(url, wait_until='networkidle')
            page.wait_for_selector('#scenario-status')
            self.assertIn('2 features; 50,000 m²', page.locator('#scenario-status').inner_text())
            page.get_by_label('Scenario').select_option('exploratory')
            page.wait_for_function("document.querySelector('#scenario-status').textContent.includes('1 features; 30,000 m²')")
            self.assertEqual(page.locator('tbody tr').count(), 1)
            page.get_by_role('button', name='Reset to canonical').click()
            page.wait_for_function("document.querySelector('#scenario-status').textContent.includes('2 features; 50,000 m²')")
            with page.expect_download() as download:
                page.get_by_role('link', name='Download accepted areas CSV').click()
            self.assertEqual(Path(download.value.path()).read_bytes(), (root / 'data/derived/areas.csv').read_bytes())
            page.set_viewport_size({'width':390,'height':844})
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            browser.close()

    def check_qgis(self, root):
        executable = os.environ.get('OMS_QGIS_PYTHON', sys.executable)
        script = '''from pathlib import Path
from openmapstack.checks import qgis
root=Path(__import__('sys').argv[1])
for name in ['runtime_load','layers_match_manifest','every_declared_layer_renders']:
 result=getattr(qgis,name)(root,path='project.qgz')
 assert result.status=='passed', (name,result.to_dict())
'''
        result = subprocess.run([executable, '-c', script, str(root)], env={**os.environ,'QT_QPA_PLATFORM':'offscreen'}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless('dlt' in SELECTED or 'composed' in SELECTED, 'dlt not selected')
    def test_incremental_state_is_separate_and_second_load_skips_seen_rows(self):
        spec = importlib.util.spec_from_file_location('stack_ingest', EXAMPLE / 'ingest.py')
        ingest = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ingest
        spec.loader.exec_module(ingest)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = ingest.load(EXAMPLE / 'data/source/parcels.csv', root / 'upstream.duckdb', root / 'state', 'dlt')
            second = ingest.load(EXAMPLE / 'data/source/parcels.csv', root / 'upstream.duckdb', root / 'state', 'dlt')
            self.assertEqual(first['loaded_packages'], 1)
            self.assertEqual(second['loaded_packages'], 0)
            import duckdb
            with duckdb.connect(str(root / 'upstream.duckdb')) as con:
                self.assertEqual(con.execute('select count(*) from raw.parcels').fetchone()[0], 3)
            self.assertEqual(first['snapshot_sha256'], second['snapshot_sha256'])

    @unittest.skipUnless('dagster' in SELECTED or 'composed' in SELECTED, 'Dagster not selected')
    def test_dagster_failed_transform_stops_downstream_delivery(self):
        spec = importlib.util.spec_from_file_location('stack_orchestration', EXAMPLE / 'orchestration.py')
        orchestration = importlib.util.module_from_spec(spec); spec.loader.exec_module(orchestration)
        calls = []
        def fail():
            calls.append('transform'); raise ValueError('injected transform failure')
        stages = {'load':lambda: {'snapshot_sha256':'sha256:'+'0'*64,'owner':'file'}, 'transform':fail,
            'export':lambda: calls.append('export'), 'render':lambda: calls.append('render')}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'work').mkdir()
            with self.assertRaisesRegex(RuntimeError,'Dagster run failed'):
                orchestration.execute(stages, root)
            result=json.loads((root/'work/dagster-run.json').read_text())
            self.assertFalse(result['success'])
            self.assertEqual(calls, ['transform'])
            self.assertTrue(any(e['type']=='STEP_FAILURE' for e in result['events']))

    @unittest.skipUnless('dbt' in SELECTED or 'composed' in SELECTED, 'dbt not selected')
    def test_dbt_spatial_oracle_rejects_a_changed_hole(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'project'
            subprocess.run([sys.executable, str(EXAMPLE / 'create.py'), str(root), '--tool', 'dbt'], check=True, capture_output=True)
            subprocess.run([sys.executable, str(root / 'pipeline.py')], check=True, capture_output=True)
            import duckdb
            database = root / 'work/spatial.duckdb'
            with duckdb.connect(str(database)) as con:
                # Replacing the donut by its outer ring must fail the independent net-area test.
                con.execute("update raw.parcels set wkt='POLYGON ((650500 6470000,650700 6470000,650700 6470200,650500 6470200,650500 6470000))' where id='donut'")
            spec = importlib.util.spec_from_file_location('stack_analysis', EXAMPLE / 'analysis.py')
            analysis = importlib.util.module_from_spec(spec); spec.loader.exec_module(analysis)
            with self.assertRaisesRegex(RuntimeError, 'dbt build/tests failed'):
                analysis.transform(root, database, 'dbt')
            self.assertIn('spatial_correctness', (root / 'work/dbt-console.txt').read_text())
