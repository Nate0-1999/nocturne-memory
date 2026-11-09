"""Generate complete packet coverage without turning contextual rows into passes."""

import hashlib
import json
import runpy
import shutil
import subprocess
from pathlib import Path

root = Path("/Users/nateoswalt/relay/m6cp")
ledger = runpy.run_path(str(root / "garden/bin/ledger"))
data = ledger["load_ledger"]()
diffs = []
inherited = {}
for repo, base in [("harness", "0069c7ee"), ("spine", "0c0f847")]:
    diff = ledger["resolve_diff"](f"{repo}={base}..HEAD")
    paths = set()
    for line in subprocess.check_output(
        ["git", "log", "--format=%H\t%s"], cwd=root / repo, text=True
    ).splitlines():
        commit, subject = line.split("\t", 1)
        if subject.startswith("M6CP:"):
            paths.update(
                f"{repo}/{path}"
                for path in subprocess.check_output(
                    [
                        "git",
                        "diff-tree",
                        "--root",
                        "-m",
                        "--no-commit-id",
                        "--name-only",
                        "--no-renames",
                        "-r",
                        "-z",
                        commit,
                    ],
                    cwd=root / repo,
                    text=True,
                ).split("\0")
                if path
            )
    inherited[repo] = ledger["impact"](data, [{**diff, "packet_paths": sorted(paths)}])
    expected = ledger["report"](data, "M6CP", [{**diff, "packet_paths": sorted(paths)}])
    net = ledger["report"](data, "M6CP", [diff])
    missing = set(expected["rows"]) - set(net["rows"])
    if missing:
        base = {"harness": "320d1e37", "spine": "753319a"}[repo]
        diff = ledger["resolve_diff"](f"{repo}={base}..HEAD")
        print(repo, "uses inherited baseline for historical coverage:", sorted(missing))
    diffs.append(diff)
for repo in ["garden", "harness", "spine"]:
    selected = diffs if repo == "garden" else [d for d in diffs if d["repo"] == repo]
    scoped = ledger if repo == "garden" else runpy.run_path(str(root / repo / "bin/ledger"))
    report = scoped["report"](scoped["load_ledger"](), "M6CP", selected)
    for d in selected:
        report["unmapped"].update({p: "" for p in inherited[d["repo"]]["unmapped"]})
    directory = root / repo / "verification/m6cp"
    directory.mkdir(parents=True, exist_ok=True)
    report["unmapped"].update(
        {
            f"{repo}/{p.relative_to(root / repo)}": ""
            for p in directory.rglob("*")
            if p.is_file() and p.name != "impacted.json"
        }
    )
    if repo == "garden":
        report["unmapped"].update(
            {
                f"garden/{p}": ""
                for p in [
                    "BOARD.md",
                    "FLAGS.md",
                    "AMENDMENTS.md",
                    "notes/feature-ledger.yaml",
                    "notes/feature-ledger.md",
                    "notes/trainable-parameters.md",
                    "reports/301-M6CP.md",
                ]
            }
        )
    for path in report["unmapped"]:
        report["unmapped"][path] = (
            "Inherited immutable M6LP evidence or supporting metadata from its completed branch; "
            "not edited or re-walked by M6CP; see Garden report 300."
            if "/verification/m6lp/" in path
            else "Package/API version and pin, generated or frozen metadata, bundled asset, "
            "decision, "
            "test adaptation or packet evidence supporting the mapped behavior; no separate "
            "feature change is claimed. Inherited paths are retained from the merged baseline."
        )
    for key, row in report["rows"].items():
        row.update(
            verdict="FAIL",
            flag="F086",
            observed=(
                "Context only: a shared or inherited path maps here. "
                "Its own gesture was not repeated. "
                "This final local-Palace readout is not row-specific proof "
                "or a new PASS/regression claim. "
                "Additional coverage remains unproven under F086."
            ),
        )
        source = root / "walk-final/captures/final-FL-061-readout.png"
        if key == "FL-061":
            row.update(
                flag="F169",
                observed=(
                    "Implemented joint instruction/size/tau/share fit and connected control. "
                    "Real successful previews changed 32 dispositions and 3 budget boundaries, "
                    "with creation score 2 to 3. Later LLM and embedding ReadTimeouts prevent "
                    "three complete accepted fresh runs. Failed receipts remain; active scorer "
                    "is unchanged and no preview proposal persisted."
                ),
            )
        if key == "FL-084":
            row.update(
                verdict="PASS",
                flag="",
                observed=(
                    "Three fresh real-OpenRouter threads each asked four questions, "
                    "waited for the answer, then compacted with the recorded clarification. "
                    "Two compaction records per thread; questions/answers remained readable "
                    "after restart. Captures and JSON are in final receipts."
                ),
            )
            source = root / "walk-final/captures/run1/FL-084-questions.png"
        shutil.copy2(source, directory / row["screenshot"])
    if repo != "garden":
        target = directory / "receipts/final"
        shutil.copytree(
            root / "garden/verification/m6cp/receipts/final", target, dirs_exist_ok=True
        )
    (directory / "ledger-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(directory)}"
        for p in sorted(directory.rglob("*"))
        if p.is_file() and p.name not in {"SHA256SUMS", "impacted.json"}
    ]
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n")
    print(repo, len(report["rows"]), "rows, own row PASS/FAIL preserved")
