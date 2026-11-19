"""M8UP local-Palace walk rig, adapted from M8TO.

Scratch-home db migration, Palace on 8178, installed app on 8878.
The app observer records failure types only; it never changes a response.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from harness.onboarding import load_config

WALK = Path.home() / "relay/m8up/walk"
HOME = WALK / "home"
SPINE_PORT = 8178
MINI = "openrouter:openai/gpt-4.1-mini"


def environment() -> dict[str, str]:
    os.environ["NOCTURNE_HOME"] = str(HOME)
    config = load_config(home=HOME)
    assert config.palace_mode == "local" and config.principal_id.startswith("nocturne-verification-")
    env = {**os.environ, **config.process_environment()}
    env["PATH"] = str(WALK.parent / "scratch/no-rg-bin")
    env["SPINE_URL"] = f"http://127.0.0.1:{SPINE_PORT}"
    env["CHAT_MODEL"] = MINI
    for role in ("CHAT", "SUBAGENT", "JUDGE"):
        env[f"MODEL_POLICY_{role}"] = f"pinned:{MINI}"
    env["SPINE_CHAT_MODEL"] = "openai:gpt-4.1-mini"
    env["SPINE_OWNER_PRINCIPAL_ID"] = config.principal_id
    return env


def db() -> None:
    from harness.lifecycle import _compose_command

    config = load_config(home=HOME)
    with _compose_command(config) as compose:
        subprocess.run([*compose, "up", "--detach", "--wait", "postgres"], check=True)
    from spine.db.migrate import upgrade_head

    upgrade_head(config.database_url)
    print(f"postgres up on {config.postgres_port}, compose project {config.compose_project}")


def spine() -> None:
    import uvicorn
    os.environ.update(environment())
    uvicorn.run("spine.main:create_app", factory=True, host="127.0.0.1", port=SPINE_PORT)


def app() -> None:
    import uvicorn
    from harness.spine_client import SpineClient

    original = SpineClient._request

    async def observed(self, method, path, **kwargs):
        started = time.monotonic()
        try:
            return await original(self, method, path, **kwargs)
        except Exception as exc:
            with (WALK / "logs/client-errors.jsonl").open("a") as log:
                log.write(json.dumps({"method": method, "path": path,
                    "seconds": round(time.monotonic() - started, 2),
                    "type": type(exc).__name__, "cause": str(exc.__cause__)[:200]}) + "\n")
            raise

    SpineClient._request = observed

    env = environment()
    import shutil
    assert shutil.which("rg", path=env["PATH"]) is None
    (WALK / "logs/no-rg-path.json").write_text(json.dumps({"PATH": env["PATH"], "rg": None}))
    os.environ.update(env)
    uvicorn.run("harness.packaged:create_app", factory=True, host="127.0.0.1", port=int(env["NOCTURNE_PORT"]))


if __name__ == "__main__":
    {"db": db, "spine": spine, "app": app}[sys.argv[1]]()
