"""Owner drift/bundle evidence is checkable without installing optional tools."""
from __future__ import annotations
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from openmapstack import delivery, integration
from openmapstack.checks import integration as checks
from openmapstack.checks import delivery as delivery_checks
from openmapstack.integrity import sha256_file
from openmapstack.validation import validate_project
from openmapstack.verify import verify_project
from openmapstack.api import run_check

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('integration_fixture_create', ROOT / 'examples/delivery-profiles/create.py')
fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='oms-integration-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture.create(self.root, ['observable'])
        result = subprocess.run([sys.executable, str(self.root / 'pipeline.py')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.project = yaml.safe_load((self.root / 'project.yaml').read_text())
        (self.root / 'definitions').mkdir()
        (self.root / 'definitions/model.sql').write_text('select 1')
        (self.root / 'project.yaml').write_text(yaml.safe_dump(self.project))
        binding = {'id': 'models', 'owner': 'dbt', 'role': 'transformation', 'definition': 'definitions',
            'sha256': integration.definition_hash(self.root, 'definitions'), 'steps': ['select']}
        self.project['processing']['steps'][1]['integration'] = 'models'
        binding['steps_sha256'] = integration.steps_hash(self.project, ['select'])
        self.project['outputs']['integration_receipt'] = {'path': 'delivery/integrations.json', 'format': 'JSON', 'kind': 'document', 'generated_by': 'render'}
        self.project['integrations'] = {'schema': integration.SCHEMA, 'bindings': [binding], 'evidence': 'integration_receipt'}
        self.save()
        integration.write_evidence(self.root, self.project)

    def save(self):
        (self.root / 'project.yaml').write_text(yaml.safe_dump(self.project, sort_keys=False))

    def test_healthy_binding_is_in_api_validator_and_verify(self):
        self.assertEqual(checks.bindings_valid(self.root).status, 'passed')
        self.assertEqual(checks.evidence_matches(self.root).status, 'passed')
        self.assertEqual(run_check('integration.bindings_valid', self.root)['status'], 'passed')
        # Run/report remain from fixture; inspect only integration results.
        checks_in_validator = [c for c in validate_project(self.root / 'project.yaml').checks if c.id.startswith('integration.')]
        self.assertEqual([c.status for c in checks_in_validator], ['passed', 'passed'])
        with patch('openmapstack.checks.visual._playwright', side_effect=ImportError):
            plan = verify_project(self.root / 'project.yaml')
        self.assertEqual([c.result.status for c in plan.checks if c.name.startswith('integration.')], ['passed', 'passed'])

    def test_model_and_step_summary_drift_fail(self):
        (self.root / 'definitions/model.sql').write_text('select 2')
        self.assertEqual(checks.bindings_valid(self.root).data['code'], 'integration_binding_invalid')
        (self.root / 'definitions/model.sql').write_text('select 1')
        self.project['processing']['steps'][1]['minimum_area_m2'] = 999
        self.save()
        self.assertEqual(checks.bindings_valid(self.root).status, 'failed')

    def test_duplicate_owners_unknown_step_and_unbound_summary_fail(self):
        healthy = copy.deepcopy(self.project)
        mutations = [lambda p: p['integrations']['bindings'].append(copy.deepcopy(p['integrations']['bindings'][0])),
            lambda p: p['integrations']['bindings'][0].update(steps=['absent']),
            lambda p: p['processing']['steps'][0].update(integration='absent'),
            lambda p: p['integrations']['bindings'][0].update(owner='observable')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.project = copy.deepcopy(healthy); mutate(self.project); self.save()
                self.assertEqual(checks.bindings_valid(self.root).status, 'failed')

    def test_malformed_declarations_return_failure_without_runtime(self):
        healthy = copy.deepcopy(self.project)
        for value in [None, {}, [], {'schema': integration.SCHEMA, 'bindings': [{'role': [], 'owner': []}]}]:
            self.project = copy.deepcopy(healthy); self.project['integrations'] = value; self.save()
            self.assertEqual(checks.bindings_valid(self.root).status, 'failed')

    def test_bundle_changes_detected_beyond_index(self):
        bundle = self.root / 'views'; bundle.mkdir()
        (bundle / 'index.html').write_bytes((self.root / 'observable.html').read_bytes())
        (bundle / 'data.json').write_text('{}')
        self.project['outputs']['observable']['path'] = 'views/index.html'
        self.project['integrations']['bundles'] = [{'target': 'observable', 'path': 'views'}]
        self.save(); integration.write_evidence(self.root, self.project)
        self.assertEqual(checks.evidence_matches(self.root).status, 'passed')
        (bundle / 'data.json').write_text('{"stale":true}')
        self.assertEqual(checks.evidence_matches(self.root).data['code'], 'integration_evidence_mismatch')
        integration.write_evidence(self.root, self.project)
        (bundle / 'data.json').unlink()
        self.assertEqual(checks.evidence_matches(self.root).status, 'failed')

    def test_unsafe_bundle_and_definitions_rejected(self):
        self.project['integrations']['bundles'] = [{'target': 'observable', 'path': '.'}]
        self.save(); self.assertEqual(checks.bindings_valid(self.root).status, 'failed')
        self.project['integrations']['bundles'] = []
        (self.root / 'link').symlink_to(self.root / 'definitions', target_is_directory=True)
        self.project['integrations']['bindings'][0]['definition'] = 'link'
        self.save(); self.assertEqual(checks.bindings_valid(self.root).status, 'failed')

    def test_missing_receipts_and_analytical_outputs_fail(self):
        receipt = self.root / 'delivery/integrations.json'
        receipt.unlink()
        self.assertEqual(checks.evidence_matches(self.root).data['code'], 'integration_artifact_missing')
        integration.write_evidence(self.root, self.project)
        (self.root / 'data/derived/areas.csv').unlink()
        self.assertEqual(checks.evidence_matches(self.root).status, 'failed')

    def test_stale_immutable_input_detected(self):
        with (self.root / 'data/source/parcels.geojson').open('a') as stream: stream.write(' ')
        self.assertEqual(checks.evidence_matches(self.root).data['code'], 'integration_evidence_mismatch')

    def test_versioned_run_evidence_is_bound(self):
        (self.root / 'delivery/tool-run.json').write_text('{"owner":"dbt"}')
        self.project['outputs']['tool_run'] = {'path': 'delivery/tool-run.json', 'format': 'JSON', 'kind': 'document', 'generated_by': 'render'}
        self.project['integrations']['run_evidence'] = 'tool_run'
        self.save(); integration.write_evidence(self.root, self.project)
        (self.root / 'delivery/tool-run.json').write_text('{}')
        self.assertEqual(checks.evidence_matches(self.root).status, 'failed')

    def test_absent_extension_and_missing_manifest(self):
        del self.project['integrations']; self.save()
        self.assertEqual(checks.bindings_valid(self.root).status, 'passed')
        self.assertEqual(checks.evidence_matches(self.root).status, 'passed')
        (self.root / 'project.yaml').unlink()
        self.assertEqual(checks.bindings_valid(self.root).data['code'], 'manifest_missing')
        self.assertEqual(checks.evidence_matches(self.root).status, 'failed')

    def test_sha256_view_fields_have_one_prefix(self):
        target = delivery.targets(self.project)[0]
        payload = delivery.evidence_payload(self.root, self.project, target)
        self.assertEqual(payload['view_sha256'], sha256_file(self.root / 'observable.html'))
        for digest in payload['metadata']['analytical_outputs'].values():
            self.assertRegex(digest, r'^sha256:[0-9a-f]{64}$')


class ExampleDefinitionTests(unittest.TestCase):
    def test_eval_source_matches_canonical_spatial_fixture(self):
        self.assertEqual((ROOT / 'examples/established-stack/data/source/parcels.csv').read_bytes(),
                         (ROOT / 'evals/fixtures/established-stack/parcels.csv').read_bytes())

    def test_focused_projects_prepare_without_any_optional_runtime(self):
        example = ROOT / 'examples/established-stack/create.py'
        for tool in ['dashboard', 'dlt', 'dbt', 'dagster', 'observable', 'qgis', 'composed']:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as temp:
                result = subprocess.run([sys.executable, str(example), temp, '--tool', tool], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                root = Path(temp); project = yaml.safe_load((root / 'project.yaml').read_text())
                self.assertEqual(integration.binding_errors(root, project), [])
                self.assertEqual(validate_project(root / 'project.yaml', artifacts=False).status, 'passed')
                self.assertFalse((root / 'node_modules').exists())
                self.assertFalse((root / 'project.qgz').exists())
                self.assertEqual(project['sources']['parcels']['pin']['sha256'], sha256_file(root / 'data/source/parcels.csv'))

class BundledDownloadTests(unittest.TestCase):
    setUp = IntegrationTests.setUp
    save = IntegrationTests.save

    def test_bundle_alias_is_accepted_only_with_identical_bytes(self):
        from openmapstack.checks import presentation
        page = self.root / 'observable.html'
        original = page.read_text()
        copied = self.root / 'areas-bundled.csv'
        copied.write_bytes((self.root / 'data/derived/areas.csv').read_bytes())
        page.write_text(original.replace('data/derived/areas.csv', 'areas-bundled.csv'))
        self.assertEqual(presentation.table_downloads_linked(self.root, dashboard='observable.html').status, 'failed')
        self.assertEqual(presentation.table_downloads_linked(self.root, dashboard='observable.html', allow_identical_copies=True).status, 'passed')
        copied.write_text('id,area_m2\nlarge,0\n')
        self.assertEqual(presentation.table_downloads_linked(self.root, dashboard='observable.html', allow_identical_copies=True).status, 'failed')
        copied.write_bytes((self.root / 'data/derived/areas.csv').read_bytes())
        for link in ['https://example.invalid/areas.csv', '/areas-bundled.csv', '../areas-bundled.csv']:
            page.write_text(original.replace('data/derived/areas.csv', link))
            self.assertEqual(presentation.table_downloads_linked(self.root, dashboard='observable.html', allow_identical_copies=True).status, 'failed')

    def test_clean_rerun_rejects_changed_owner_declaration(self):
        from openmapstack.rerun import perform_clean_rerun
        self.project['runtime']['implementation']['dependencies'] = ['definitions']
        self.save()
        script = self.root / 'pipeline.py'
        script.write_text(script.read_text().replace('    write_evidence(ROOT, project)',
            '    write_evidence(ROOT, project)\n    from openmapstack.integration import write_evidence as integration_evidence\n    integration_evidence(ROOT, project)').replace('project["project"]["status"] =',
            'project.pop("integrations", None)\n    project["project"]["status"] ='))
        with tempfile.TemporaryDirectory() as temp:
            result = perform_clean_rerun(self.root, Path(temp), 30)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'integration_integrity')
