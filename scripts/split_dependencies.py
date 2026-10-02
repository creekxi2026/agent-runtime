#!/usr/bin/env python3
"""Derive byte-stable, independent Docker build inputs from the shared lock."""
import argparse
import copy
import json
from pathlib import Path


def split_manifest(manifest):
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported manifest schema')
    core = copy.deepcopy(manifest)
    core['component'] = 'core'
    codex = {'schema_version': 1, 'component': 'codex',
             'versions': {'codex': core['versions'].pop('codex')},
             'npm': {'@openai/codex': core['npm'].pop('@openai/codex')},
             'artifacts': {}}
    multica = {'schema_version': 1, 'component': 'multica',
               'versions': {'multica': core['versions'].pop('multica')},
               'npm': {}, 'artifacts': {'multica': core['artifacts'].pop('multica')}}
    return {'core': core, 'codex': codex, 'multica': multica}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    shards = split_manifest(json.loads(args.manifest.read_text()))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, shard in shards.items():
        content = json.dumps(shard, sort_keys=True, indent=2) + '\n'
        target = args.output / (name + '.json')
        if not target.exists() or target.read_text() != content:
            target.write_text(content)


if __name__ == '__main__':
    main()
