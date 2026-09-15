"""Product citation and vocabulary checks for rules-ledger r1-5/r2-15/r6-4/r14-2."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

NODE = re.compile(r"\bP\d+(?:\.\d+)*[a-z]?\b")
INTERNAL_WORD = re.compile(r"\b(?:garden|relay)\b", re.IGNORECASE)
PACKET = re.compile(r"\b(?:M[123][A-Z0-9]+|SYM\d+)\b")


def freeze(root: Path) -> list[str]:
    expected = json.loads((root / "docs/frozen-law.json").read_text())
    errors = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        data = (root / name).read_bytes()
        start = data.find(b"## 0.")
        end = data.find(b"\n1. You are one runner", start)
        actual = hashlib.sha256(data[start:end].rstrip(b"\n")).hexdigest()
        if start < 0 or end < 0 or actual != expected["axioms_sha256"]:
            errors.append(f"{name}: frozen axioms differ from the master fingerprint")
    actual = hashlib.sha256((root / "docs/SPEC.md").read_bytes()).hexdigest()
    if actual != expected["spec_sha256"]:
        errors.append("docs/SPEC.md: differs from the master fingerprint")
    for path, key in (
        ("docs/feature-ledger.yaml", "ledger_sha256"),
        ("bin/ledger", "ledger_tool_sha256"),
    ):
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != expected[key]:
            errors.append(f"{path}: differs from the master fingerprint")
    return errors


def decisions(text: str) -> list[str]:
    headings = list(re.finditer(r"^## (\d{3})\b", text, re.MULTILINE))
    if not headings:
        return ["DECISIONS.md: no decision entries found"]
    return [
        f"DECISIONS.md {match[1]}: missing Problem Tree citation"
        for i, match in enumerate(headings)
        if not NODE.search(
            text[match.start() : headings[i + 1].start() if i + 1 < len(headings) else len(text)]
        )
    ]


def terms(root: Path) -> list[str]:
    errors = []
    for base in ("src", "web/src", "tests", "web/tests"):
        for path in (root / base).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".mjs", ".md"}:
                continue
            source = path.read_text()
            relative = path.relative_to(root)
            for number, line in enumerate(source.splitlines(), 1):
                if re.search(r"\bensemble\b", line, re.IGNORECASE):
                    errors.append(f"{relative}:{number}: retired mode name")
                if base == "src" and re.search(
                    r"garden/|\b(?:from|import)\s+garden_adapter\b", line
                ):
                    errors.append(f"{relative}:{number}: product depends on governance source")
            if base != "src" or path.suffix != ".py":
                continue
            tree = ast.parse(source)
            docs = {
                id(n.body[0].value)
                for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and n.body
                and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)
            }
            for node in ast.walk(tree):
                if (
                    not isinstance(node, ast.Constant)
                    or not isinstance(node.value, str)
                    or id(node) in docs
                ):
                    continue
                # A-053's exact historical hygiene identity is data, never product copy.
                if node.value == "d1-relay":
                    continue
                if INTERNAL_WORD.search(node.value):
                    errors.append(
                        f"{relative}:{node.lineno}: governance vocabulary in product string"
                    )
                # Migration SQL and database comments are immutable historical metadata.
                if "/db/" not in str(path) and " " in node.value and PACKET.search(node.value):
                    errors.append(f"{relative}:{node.lineno}: packet id in product copy")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    errors = freeze(args.root)
    if not args.freeze_only:
        errors += decisions((args.root / "DECISIONS.md").read_text()) + terms(args.root)
    for error in errors:
        print(error)
    print(f"Product rules: {'FAIL' if errors else 'PASS'}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
