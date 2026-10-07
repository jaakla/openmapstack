"""Dagster owns operational dependencies; assets call the same stage functions."""
from __future__ import annotations
import json
from pathlib import Path


def execute(stages: dict, root: Path) -> dict:
    from dagster import (AssetCheckResult, AssetCheckSpec, AssetExecutionContext,
                         DagsterInstance, Definitions, MaterializeResult, RetryPolicy, asset, materialize)
    @asset(retry_policy=RetryPolicy(max_retries=1, delay=0.1))
    def pinned_load():
        result = stages['load']()
        return MaterializeResult(metadata={'snapshot_sha256': result['snapshot_sha256'], 'owner': result['owner']})
    @asset(deps=[pinned_load], retry_policy=RetryPolicy(max_retries=0))
    def spatial_models():
        result = stages['transform']()
        return MaterializeResult(metadata={'owner': result['owner'], 'definition': 'dbt/models; no SQL in asset'})
    @asset(deps=[spatial_models], check_specs=[AssetCheckSpec(name='net_area_and_identity', asset='validated_outputs')])
    def validated_outputs():
        result = stages['export']()
        return MaterializeResult(metadata={'features': result['features'], 'area_m2': result['area_m2']},
            check_results=[AssetCheckResult(passed=True, check_name='net_area_and_identity')])
    @asset(deps=[validated_outputs])
    def delivery_build():
        stages['render']()
        return MaterializeResult(metadata={'publication': 'local only'})
    assets = [pinned_load, spatial_models, validated_outputs, delivery_build]
    # Same definitions work with `dagster dev`; no second scheduler is built.
    definitions = Definitions(assets=assets)
    (root / 'work/dagster-instance').mkdir()
    with DagsterInstance.local_temp(str(root / 'work/dagster-instance')) as instance:
        result = materialize(definitions.assets, instance=instance, raise_on_error=False)
        events = [{'type': event.event_type_value, 'step': event.step_key} for event in result.all_events]
        receipt = {'owner': 'dagster', 'run_id': result.run_id, 'success': result.success, 'events': events}
        (root / 'work/dagster-run.json').write_text(json.dumps(receipt, indent=2))
        if not result.success:
            raise RuntimeError('Dagster run failed; downstream deliveries did not execute; inspect work/dagster-run.json')
        return receipt
