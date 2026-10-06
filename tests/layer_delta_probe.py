"""Real official-version layer deltas with imported cache in fresh builders.

Never pushes an image or uses customer credentials. Older CLIs are temporary
comparison inputs, not retained releases. Restores the resolved manifest on exit.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolve_tools import get_json, github_json, release_artifact, stable_version


def run(args, **kwargs):
    return subprocess.run(args, check=True, cwd=ROOT, **kwargs)


def image_layers(archive):
    with tarfile.open(archive) as tar:
        def blob(desc):
            digest = desc['digest']
            if not digest.startswith('sha256:') or len(digest) != 71:
                raise ValueError('Invalid OCI digest')
            return json.load(tar.extractfile('blobs/sha256/' + digest[7:]))
        manifest = json.load(tar.extractfile('index.json'))
        for _ in range(4):
            if 'manifests' not in manifest:
                break
            assert len(manifest['manifests']) == 1, 'Expected single-platform OCI export without attestations'
            manifest = blob(manifest['manifests'][0])
        config = blob(manifest['config'])
        history = [item.get('created_by', '') for item in config['history'] if not item.get('empty_layer')]
        assert len(history) == len(manifest['layers'])
        layers = []
        for desc, command in zip(manifest['layers'], history):
            if command.startswith('COPY') and '/opt/codex' in command:
                kind = 'codex'
            elif command.startswith('COPY') and 'multica' in command:
                kind = 'multica'
            elif command.startswith('COPY') and 'dependencies.json' in command:
                kind = 'metadata'
            else:
                kind = 'core'
            layers.append({**desc, 'kind': kind, 'created_by': command})
        assert sum(l['kind'] == 'codex' for l in layers) == 1
        assert sum(l['kind'] == 'multica' for l in layers) == 1
        assert sum(l['kind'] == 'metadata' for l in layers) == 1
        return layers


def compare(baseline, variant, tool):
    assert len(baseline) == len(variant), 'Layer count changed unexpectedly'
    changed = []
    for now, old in zip(baseline, variant):
        assert now['kind'] == old['kind']
        if now['digest'] != old['digest']:
            assert now['kind'] in (tool, 'metadata'), 'Unrelated layer changed: ' + now['created_by']
            changed.append(now)
    assert any(l['kind'] == tool for l in changed), 'Actual tool layer must differ'
    prior = {l['digest'] for l in variant}
    download_bytes = sum(l['size'] for l in baseline if l['digest'] not in prior)
    return {'changed_layers': [{'kind': l['kind'], 'digest': l['digest'], 'bytes': l['size']} for l in changed],
            'update_download_bytes': download_bytes,
            'full_image_compressed_bytes': sum(l['size'] for l in baseline)}


def older_variants(manifest):
    codex = copy.deepcopy(manifest)
    old = get_json('https://registry.npmjs.org/%40openai%2Fcodex/0.159.2')
    assert old['name'] == '@openai/codex' and stable_version(old['version']) == '0.159.2'
    assert codex['versions']['codex'] != old['version']
    codex['versions']['codex'] = old['version']
    codex['npm']['@openai/codex'] = {'version': old['version'], 'url': old['dist']['tarball'], 'integrity': old['dist']['integrity']}
    multica = copy.deepcopy(manifest)
    release = github_json('repos/multica-ai/multica/releases/tags/v0.6.0')
    assert not release['draft'] and not release['prerelease']
    assert stable_version(release['tag_name']) == '0.6.0'
    assert multica['versions']['multica'] != '0.6.0'
    multica['versions']['multica'] = '0.6.0'
    multica['artifacts']['multica'] = {arch: release_artifact('multica-ai/multica', release, f'multica-cli-0.6.0-linux-{arch}.tar.gz') for arch in ('amd64', 'arm64')}
    return {'codex': codex, 'multica': multica}


def main():
    path = ROOT / 'runtime-deps.json'
    original = path.read_bytes()
    manifest = json.loads(original)
    variants = older_variants(manifest)
    arch = os.environ['ARCH']
    assert arch in ('amd64', 'arm64')
    base = os.environ['BASE_IMAGE']
    assert base == manifest['base_image']
    split = [sys.executable, 'scripts/split_dependencies.py', '--manifest', 'runtime-deps.json', '--output', '.build-inputs']
    report = {'architecture': arch, 'current_versions': {k: manifest['versions'][k] for k in variants}, 'comparisons': {}}
    builder = None
    try:
        with tempfile.TemporaryDirectory(prefix='runtime-layer-audit-') as work:
            work = Path(work)
            cache = work / 'cache'
            common = ['docker', 'buildx', 'build', '--platform', 'linux/' + arch, '--provenance=false', '--sbom=false', '--build-arg', 'BASE_IMAGE=' + base]
            baseline = work / 'baseline.tar'
            # Materialize both CLI stages for the baseline cache. A warm imported
            # cache can satisfy the final linked COPY without retaining those
            # intermediate results for a second export to a fresh builder.
            run(common + ['--no-cache-filter', 'codex-tool,multica-tool',
                          '--cache-to', f'type=local,dest={cache},mode=max',
                          '--output', f'type=oci,dest={baseline}', '.'])
            baseline_layers = image_layers(baseline)
            baseline.unlink()
            for tool, candidate in variants.items():
                path.write_text(json.dumps(candidate, sort_keys=True, indent=2) + '\n')
                run(split)
                # A fresh daemon has no in-memory cache from the baseline or the
                # other variant. It must import the persisted max-mode cache.
                builder = f'layer-audit-{arch}-{tool}-{os.getpid()}'
                run(['docker', 'buildx', 'create', '--name', builder, '--driver', 'docker-container'])
                output = work / (tool + '.tar')
                run(common + ['--builder', builder, '--cache-from', f'type=local,src={cache}', '--output', f'type=oci,dest={output}', '.'])
                result = compare(baseline_layers, image_layers(output), tool)
                result.update({'from_version': candidate['versions'][tool], 'to_version': manifest['versions'][tool], 'fresh_builder_imported_cache': True})
                report['comparisons'][tool] = result
                output.unlink()
                run(['docker', 'buildx', 'rm', builder])
                builder = None
            assert set(report['comparisons']) == {'codex', 'multica'}
    finally:
        if builder:
            subprocess.run(['docker', 'buildx', 'rm', builder], cwd=ROOT, check=False)
        path.write_bytes(original)
        run(split)
    (ROOT / 'layer-delta.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    print('PASS actual CLI-version changes preserve every unrelated compressed layer')


if __name__ == '__main__':
    main()
