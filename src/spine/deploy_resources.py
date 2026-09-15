"""Materialize allowlisted deployment source from the installed Spine wheel."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

_DEPLOY_RESOURCE_DIRECTORY = "_deploy"
_D2_FILES = (
    "README.md",
    "billing_breaker.py",
    "deploy.sh",
    "deployment_checks.py",
    "main.py",
    "requirements.txt",
)
_EXECUTABLE_D2_FILES = frozenset({"deploy.sh"})
_SOURCE_EXCLUDES = frozenset({_DEPLOY_RESOURCE_DIRECTORY, "__pycache__"})


def _empty_destination(destination: str | Path) -> Path:
    resolved = Path(destination)
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise FileExistsError(f"deployment destination is not empty: {resolved}")
    else:
        resolved.mkdir(parents=True)
    return resolved


def _copy_file(source: Traversable, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_file, destination.open("wb") as destination_file:
        shutil.copyfileobj(source_file, destination_file)
    destination.chmod(0o644)


def _copy_tree(
    source: Traversable,
    destination: Path,
    *,
    excludes: Iterable[str] = (),
) -> None:
    excluded_names = frozenset(excludes)
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        if child.name in excluded_names or child.name.endswith((".pyc", ".pyo")):
            continue
        target = destination / child.name
        if child.is_dir():
            _copy_tree(child, target, excludes=excludes)
        elif child.is_file():
            _copy_file(child, target)


def _packaged_deploy_resources() -> Traversable:
    resources = files("spine").joinpath(_DEPLOY_RESOURCE_DIRECTORY)
    if resources.is_dir():
        return resources
    checkout = Path(__file__).resolve().parents[2]
    required = (checkout / "pyproject.toml", checkout / "Dockerfile", checkout / "infra")
    if all(path.exists() for path in required):
        return checkout
    raise RuntimeError(
        "Spine deploy resources are unavailable; install a built nocturne-memory wheel"
    )


def _copy_d2_source(resources: Traversable, destination: Path) -> None:
    d2_resources = resources.joinpath("billing-breaker")
    if not d2_resources.is_dir():
        d2_resources = resources.joinpath("infra", "billing-breaker")
    for filename in _D2_FILES:
        source = d2_resources.joinpath(filename)
        if not source.is_file():
            raise RuntimeError(f"packaged D2 resource is missing: {filename}")
        target = destination / filename
        _copy_file(source, target)
        if filename in _EXECUTABLE_D2_FILES:
            target.chmod(0o755)


def materialize_app_source(destination: str | Path) -> Path:
    """Create the minimal source context used to build the Spine service image."""

    target = _empty_destination(destination)
    resources = _packaged_deploy_resources()
    package_root = files("spine")

    _copy_file(resources.joinpath("pyproject.toml"), target / "pyproject.toml")
    _copy_file(resources.joinpath("README.md"), target / "README.md")
    _copy_file(resources.joinpath("Dockerfile"), target / "Dockerfile")
    _copy_tree(package_root, target / "src" / "spine", excludes=_SOURCE_EXCLUDES)
    _copy_d2_source(resources, target / "infra" / "billing-breaker")
    return target


def materialize_billing_breaker_source(destination: str | Path) -> Path:
    """Create the canonical D2 function source and human-only deploy path."""

    target = _empty_destination(destination)
    _copy_d2_source(_packaged_deploy_resources(), target)
    return target
