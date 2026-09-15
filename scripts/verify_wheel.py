"""Verify an installed nocturne-memory wheel from outside its source checkout."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic.script import ScriptDirectory

from spine import __version__
from spine.db.migrate import make_alembic_config
from spine.deploy_resources import materialize_app_source, materialize_billing_breaker_source

D2_FILES = {
    "README.md",
    "billing_breaker.py",
    "deploy.sh",
    "deployment_checks.py",
    "main.py",
    "requirements.txt",
}


def main() -> None:
    """Prove metadata, migrations, and both packaged deployment contexts."""

    assert version("nocturne-memory") == __version__

    scripts = ScriptDirectory.from_config(
        make_alembic_config("postgresql+asyncpg://unused:unused@localhost/unused")
    )
    assert scripts.get_base() == "0001"
    assert scripts.get_heads() == ["0021"]

    with TemporaryDirectory(prefix="nocturne-memory-wheel-") as temporary_directory:
        root = Path(temporary_directory)
        app_source = materialize_app_source(root / "app-source")
        d2_source = materialize_billing_breaker_source(root / "d2-source")

        assert {path.name for path in app_source.iterdir()} == {
            "Dockerfile",
            "README.md",
            "infra",
            "pyproject.toml",
            "src",
        }
        assert {path.name for path in d2_source.iterdir()} == D2_FILES
        assert (d2_source / "deploy.sh").stat().st_mode & 0o111 == 0o111

        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                str(root / "rebuilt"),
                str(app_source),
            ],
            check=True,
        )

    print("nocturne-memory installed-wheel smoke passed")


if __name__ == "__main__":
    main()
