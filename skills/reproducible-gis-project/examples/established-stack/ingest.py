"""Incremental operational ingestion; pinned CSV is a distinct analytical input.

CLI permits a persistent upstream state directory. The canonical analysis uses
fresh operational state and replays the immutable snapshot, not a saved cursor.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path


def load(snapshot: Path, database: Path, state: Path, owner: str) -> dict:
    if owner == 'dlt':
        import dlt
        @dlt.resource(name='parcels', primary_key='id', write_disposition='merge')
        def parcels(updated_at=dlt.sources.incremental('updated_at')):
            with snapshot.open(newline='') as stream:
                yield from csv.DictReader(stream)
        pipeline = dlt.pipeline(pipeline_name='spatial_ingestion', destination=dlt.destinations.duckdb(str(database)),
                                dataset_name='raw', pipelines_dir=str(state))
        info = pipeline.run(parcels())
        if info.has_failed_jobs:
            raise RuntimeError('dlt load has failed jobs')
        return {'owner': 'dlt', 'loaded_packages': len(info.load_packages), 'operational_state': pipeline.state,
                'schema': pipeline.default_schema.to_dict(), 'snapshot_sha256': 'sha256:' + hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    import duckdb
    with duckdb.connect(str(database)) as con:
        con.execute('CREATE SCHEMA raw')
        con.execute('CREATE TABLE raw.parcels AS SELECT * FROM read_csv(?, all_varchar=true)', [str(snapshot)])
    return {'owner': 'file', 'snapshot_sha256': 'sha256:' + hashlib.sha256(snapshot.read_bytes()).hexdigest(),
            'columns': ['id', 'wkt', 'updated_at']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('database', type=Path)
    parser.add_argument('state', type=Path)
    args = parser.parse_args()
    print(json.dumps(load(args.snapshot, args.database, args.state, 'dlt'), default=str, indent=2))
