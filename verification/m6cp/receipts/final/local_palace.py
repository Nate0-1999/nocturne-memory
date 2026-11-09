"""M6CP disposable local Palace rung, following reports 297 and 298."""
import os
import secrets
import subprocess
import sys
from dataclasses import replace
from importlib import resources
from pathlib import Path

from harness.onboarding import _write_config, load_config

ROOT = Path('/Users/nateoswalt/relay/m6cp')
HOME = ROOT / 'scratch/local'
PG, PALACE, APP = 55453, 8013, 8873

def setup():
    bootstrap = load_config(home=ROOT / 'scratch/bootstrap')
    if not (HOME / 'env').exists():
        _write_config(replace(bootstrap, home=HOME, palace_mode='local',
                              spine_url=f'http://127.0.0.1:{PALACE}',
                              postgres_port=PG, daemon_port=APP, palace_name='local-m6cp',
                              spine_token=secrets.token_urlsafe(32), postgres_volume='',
                              database_password=secrets.token_urlsafe(24)))
    c = load_config(home=HOME)
    with resources.as_file(resources.files('harness').joinpath('resources/docker-compose.yml')) as compose:
        subprocess.run(['docker', 'compose', '-p', 'm6cp-local', '--env-file', str(c.path),
                        '-f', str(compose), 'up', '-d', '--wait', 'postgres'], check=True)
    from spine.db.migrate import upgrade_head
    upgrade_head(c.database_url)
    print('M6CP local database ready')

def serve(service):
    c = load_config(home=HOME)
    env = c.process_environment()
    env.update(SPINE_URL=f'http://127.0.0.1:{PALACE}', SPINE_OWNER_PRINCIPAL_ID=c.principal_id,
               SPINE_CHAT_MODEL='openai:gpt-4.1-mini',
               CHAT_MODEL='openrouter:openai/gpt-4.1-mini',
               MODEL_POLICY_CHAT='pinned:openrouter:openai/gpt-4.1-mini',
               MODEL_POLICY_SUBAGENT='pinned:openrouter:openai/gpt-4.1-mini',
               MODEL_POLICY_JUDGE='pinned:openrouter:openai/gpt-4.1-mini')
    app, port = ('spine.main:create_app', PALACE) if service == 'spine' else ('harness.packaged:create_app', APP)
    os.execvpe(sys.executable, [sys.executable, '-m', 'uvicorn', app, '--factory',
                              '--host', '127.0.0.1', '--port', str(port)], env)

if __name__ == '__main__':
    setup() if sys.argv[1] == 'setup' else serve(sys.argv[1])
