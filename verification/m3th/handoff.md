# Tools, skills and rewind: real-model walks and 0.1.19 release

[Evidence gallery](./gallery.html) ·
[70-row checklist](./ledger-report.json) ·
[Provider proof](./real-model-proof.json) ·
[Release receipts](./release.json)

M3TH is DONE. All twelve charged walks passed against real Palace and
OpenRouter using disposable identities. The baseline already handled core
tools, skill resources, location instructions and model parameters; those
working paths were retained. Missing inventory, whole-toolset selection
and chat/file rewind were implemented. The upstream upgrade exposed an
empty cancellation-history request, repaired in the Harness adapter.

| Requested behavior | Before | Verified result |
|---|---|---|
| Files, shell, browser, skills (069/190/194) | Real baseline calls worked | Read and shell markers, browser copper owl 418, skill violet heron 739 work after upgrade |
| Location and memory refresh (072/073/068) | Normal move works; restarted thread loses injection state | Provider system instructions track location; moving back to sub injects the actual disposable greenhouse fact without search |
| Versioned inventory (191) | No thread inventory | Tools and skills list sources, installed versions and skill content hashes |
| Whole-toolset switch (192) | No selector | Off persists; real requests contain only the two memory tools; enabling restores workspace/browser/skill tools |
| Upstream upgrade (193/186) | Pydantic AI 2.28.0 / harness 0.24.0 | 2.43.0 / 0.31.0; real stop/recovery, skill use and mini-to-nano switch pass |
| Rewind (076) | No control | One click restores chat and files; alpha-one returns, branch-only.txt disappears, abandoned state remains recoverable |
| Model device (107) | Existing device works | Temperature 0.05 and top-p 1 appear in real OpenRouter requests |

ADR-016 specifies a shadow Git directory per human turn, not automatic
commits in the user's repository. Rewind preserves the ordinary Git HEAD,
index and ignored files, and retains the abandoned chat/file continuation.
The existing named scopes are chat, files and both. Active writers must
finish before files can be restored.

## Verification and publication

Both packages are publicly available as **0.1.19**. Memory was published
first; Harness followed after hosted acceptance. No direct main push was
used. Palace API contract remains 0.1.18; this packet adds no server schema.

- [Memory PR 12](https://github.com/Nate0-1999/nocturne-memory/pull/12),
  [release workflow](https://github.com/Nate0-1999/nocturne-memory/actions/runs/35127777623),
  merge `427d0f4`.
- [Harness PR 13](https://github.com/Nate0-1999/nocturne-harness/pull/13),
  [release workflow](https://github.com/Nate0-1999/nocturne-harness/actions/runs/35129401664),
  merge `9a13c42`.
- Fresh registry-only virtualenv resolves both packages at 0.1.19 and the
  two pinned Pydantic versions. A fresh disposable home completed a real
  Palace gate and OpenRouter file-read turn. The same install restored the
  previous disposable home's 26-message conversation and rewind controls.
- Local Harness suite: 1767 passed, 5 skipped, 3 live contracts deselected;
  the final additional rewind-writer test passed with the 111-test focused
  run. Hosted final Python, package/rebuilt-wheel, live contract, web canon,
  Rules and impact checks all passed. Memory: 313 passed, 3 skipped.
- Web: 141 unit tests, lint and build passed. Fresh packaged heartbeat,
  compaction/reinstall and 43-state browser regression canon passed. Their
  screenshots retain the explicit fixture curtain.

The first Harness CI attempt exposed the old Memory checkout pin; it was
advanced to the released commit. Rules caught missing new rewind citations
and refusal-message coverage; both were repaired and rechecked. The first
registry install preceded index propagation; the fresh retry passed. These
were resolved verification failures, not waived checks.

The full impact checklist has **31 PASS / 39 FAIL or unproven** rows.
All twelve charged rows are PASS. The other failures state exactly which
additional gestures were not proved, under F086; fixture evidence is not
represented as a real-model walk. Gallery review found several obstructed
baseline screenshots; raw provider/journal records support those baseline
claims, and final captures show the relevant results. Coverage PASS proves
complete verdict/screenshot/hash accounting, not universal product success.

## Remaining finding and ownership

F105 is resolved. F095's cancellation/upgrade corner was re-proven; its
other findings remain. New **F113** records the pre-existing daemon-restart
case: an old thread repeats prepare, Palace returns 409, and the in-memory
injection snapshot is absent. Fresh-thread movement refresh succeeds.
Durable injection recovery belongs with M3OB lifecycle work; no unrelated
runtime repair was added here.

Under v2.120, crossings into shared files were limited to inventory/rewind
functions and payloads: `daemon.py` toolset/inventory/rewind routes,
`rack_query.py`, Rack actions/bridge, optional envelope/protocol checkpoint,
the existing run-start reducer, App control wiring and matching CSS.
M3LL/M3VZ product branches had no pushed same-function changes at the final
check; the current M3VZ board claim explicitly excludes tool/rewind functions.
The Garden worktree was fast-forwarded to `ff44a68` before handoff; both
product worktrees include their released main. Updated ledger/frozen copies
ship in the same evidence handoff.

## Cleanup

All three disposable principals have **zero active memories and zero
pending cards**. The sole created memory is tombstoned at revision 15;
its audit records remain. Task-owned daemons were stopped and all three
task credential copies removed. The original disposable credential source,
owner home, owner Palace data and peer work were untouched. No credential
matches were found in the evidence. No next packet was claimed.
