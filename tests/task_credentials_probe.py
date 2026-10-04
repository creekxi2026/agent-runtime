"""Offline real CLI configuration/env isolation with explicitly INVALID tokens."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess

assert os.getuid() == 1000
base = Path(os.environ['LARKSUITE_CLI_CONFIG_DIR']) / 'config.json'
before = base.read_bytes() if base.exists() else None

def task(name):
    config = Path.home() / 'workspace' / ('.credentials-fixture-' + name)
    env = {**os.environ, 'LARKSUITE_CLI_CONFIG_DIR': str(config), 'GH_TOKEN': 'INVALID-GH-' + name}
    for key in ['LARKSUITE_CLI_APP_ID','LARKSUITE_CLI_APP_SECRET','LARKSUITE_CLI_USER_ACCESS_TOKEN','LARKSUITE_CLI_TENANT_ACCESS_TOKEN']:
        env.pop(key, None)
    result = subprocess.run(['lark-cli','config','init','--app-id','cli_invalid_'+name,'--app-secret-stdin'],
                            input='INVALID-SECRET-'+name+'\n', env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    saved = (config / 'config.json').read_text()
    assert 'cli_invalid_' + name in saved
    assert 'cli_invalid_' + ('b' if name=='a' else 'a') not in saved
    env.update(LARKSUITE_CLI_APP_ID='cli_invalid_'+name, LARKSUITE_CLI_USER_ACCESS_TOKEN='INVALID-UAT-'+name)
    identity = json.loads(subprocess.check_output(['lark-cli','whoami'], env=env, text=True, timeout=30))
    assert identity['appId'] == 'cli_invalid_'+name and identity['identity']=='user', identity
    # whoami is local selection evidence only, NOT token validity.
    selected = subprocess.check_output(['gh','auth','token','--hostname','github.com'], env=env, text=True, timeout=30).strip()
    assert selected == 'INVALID-GH-'+name
    return name

with concurrent.futures.ThreadPoolExecutor() as pool:
    assert set(pool.map(task, ['a','b'])) == {'a','b'}
assert (base.read_bytes() if base.exists() else None) == before
print('PASS concurrent real Lark config dirs/env identity and gh token selection are task-scoped; owner config unchanged; INVALID fixtures only, no authentication claims')
