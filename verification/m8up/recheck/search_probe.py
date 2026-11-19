"""Compare the shipped grep surface on one synthetic tree, with rg absent."""

import asyncio
import json
import shutil
from pathlib import Path

from harness.toolset import open_standard_toolset

root = Path("/Users/nateoswalt/relay/m8up/scratch/search-fixture")
root.mkdir(exist_ok=True)
(root / "a.txt").write_text("start\nAlpha alpha [x]\nBeta Beta\nend\n")
(root / "sub").mkdir(exist_ok=True)
(root / "sub/b.txt").write_text("alpha\nALPHA\nBeta\n")
(root / ".hidden").write_text("Alpha\n")
(root / "binary").write_bytes(b"Alpha\x00")
(root / "alias.txt").unlink(missing_ok=True)
(root / "alias.txt").symlink_to(root / "a.txt")
(root / "outside.txt").unlink(missing_ok=True)
(root.parent / "outside.txt").write_text("Alpha\n")
(root / "outside.txt").symlink_to(root.parent / "outside.txt")
cases = [
    {"pattern": "alpha", "ignoreCase": True},
    {"pattern": r"(?<=Alpha )alpha"},
    {"pattern": r"(?P<x>Beta) (?P=x)"},
    {"pattern": "[x]", "literal": True},
    {"pattern": "alpha", "ignoreCase": True, "glob": "**/*.txt", "context": 1},
    {"pattern": "Alpha", "path": "a.txt", "context": 1},
    {"pattern": "a", "ignoreCase": True, "limit": 1},
    {"pattern": "Beta", "limit": 3},
    {"pattern": "no matches"},
    {"pattern": "["},
]


async def main():
    assert shutil.which("rg") is None
    tools = await open_standard_toolset(cwd=root, workspace_root=root)
    results = []
    try:
        for args in cases:
            r = await tools.execute("grep", {"path": ".", **args})
            results.append({"arguments": args, "success": r.success, "content": r.content})
    finally:
        await tools.close()
    print(json.dumps({"rg": shutil.which("rg"), "cases": results}, indent=2))


asyncio.run(main())
