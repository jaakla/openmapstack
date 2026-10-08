"""Deterministic owner contract fixture and defects; no optional tool accounts."""
import argparse
import subprocess
import sys
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from openmapstack.integration import write_evidence

parser=argparse.ArgumentParser()
parser.add_argument('destination',type=Path)
parser.add_argument('--break',dest='mutation')
args=parser.parse_args()
subprocess.run([sys.executable,str(ROOT/'examples/established-stack/create.py'),str(args.destination)],check=True)
subprocess.run([sys.executable,str(args.destination/'pipeline.py')],check=True)
path=args.destination/'project.yaml';project=yaml.safe_load(path.read_text())
if args.mutation=='owner':
    project['integrations']['bindings'][1]['owner']='observable'
elif args.mutation=='definition':
    with (args.destination/'dbt/models/measured.sql').open('a') as stream: stream.write(' -- changed method')
elif args.mutation=='summary':
    project['processing']['steps'][2]['minimum_area_m2']=999
elif args.mutation=='missing':
    (args.destination/'data/derived/candidates.geojson').unlink()
elif args.mutation=='stale':
    with (args.destination/'data/source/parcels.csv').open('a') as stream: stream.write('\n')
path.write_text(yaml.safe_dump(project,sort_keys=False))
