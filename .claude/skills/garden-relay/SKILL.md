---
name: garden-relay
description: You are one runner in the NOCTURNE relay. Load this before claiming or resuming any packet named in garden/PLAN.md, in Codex or Claude Code alike. It is the runner's contract: boot, claim, work in isolation, the three tiers, the walk, the handoff, and what you may touch.
---

# The relay runner

## Boot (PLAN §0, THE BOOT DIET)
Read garden/PLAN.md §0–§2, your packet's charge, its board row, garden/GATE.md §0
and garden/GLOSSARY.md. Read SPEC.md only by the sections your charge names.
Scouts read the generated ledger report, never the ledger source.

- Sweep first: `bin/sweep_browsers` in garden/ (`../garden/bin/sweep_browsers`
  from harness/ or spine/) stops verification browsers and walk drivers that
  outlived their packet — orphaned, or older than 8 hours. It never touches
  the owner's own Chrome. (2026-10-04: ten browsers from finished packets
  had run for days.)

## Claim
One packet per session. Claim on the board as `<runner> / <date> / <id>`
(runner is `codex` or `claude`). The claim commit is the mutex: if your push
is rejected, pull; if the packet is now claimed, stand down. Never revive a
claim without a death certificate on the board. A packet marked DONE or
AUDITED is finished: verify, do not rebuild, and stand down.

## Work in isolation
An isolated worktree per repo (`git worktree add --detach`), never a shared
checkout. Push only the claim commit mid-flight; everything else at handoff.
Never force-push. A scratch `NOCTURNE_HOME` for every launch; never the
owner's `~/.nocturne`. Anything the packet creates in the cloud goes on a
test palace (`nocturne palace new test-<packet>`), dropped whole after.

## The three tiers (PLAN §3.1)
WHITE is your charge's declared modules (garden/notes/module-map.md): go.
GRAY is every other product file: go, by function — only what your rows
need, check the peers' pushed branches first, rebase on origin/main before
handoff, list every crossing in the report. BLACK needs a word from the
owner or the gate: a function a peer is live in, a protected row's behavior
outside a flag-named corner, the law files (SPEC, AXIOMS, CLAUDE.md,
AGENTS.md), the release and CI workflows unless your grant names them, the
owner's settings and credentials, a peer's evidence folder. The push bell
prints `[BLACK: needs a word]`; treat it as the stop it is.

## Credentials and grants
The disposable verification source is `harness/.env` (git-ignored). Use it
only for `nocturne init --verification`; never copy it into evidence or
print its values. Releases: tag through the release workflows at the next
unused version, both packages together; never a number chosen in advance.
A packet that adds or resolves a flag or changes the ledger runs
`garden/bin/check_rules --export` and ships the frozen copies in its pushes.

## The walk and the handoff (PLAN §1, GATE §5)
Walk your charge's LEDGER ROWS plus the impact bell's rows on the real
Palace and OpenRouter; a plate row needs `<row>-plate.png` beside its plate;
a row only the owner can do is OWNER-PENDING with its steps. Findings freeze:
a FAIL cites an existing flag or opens one in the WHAT-I-DID / EXPECTED /
SAW / WHY form. Report as `reports/NNN-<PACKET>.md`; evidence under
`verification/<packet>/` with SHA256SUMS; `bin/ledger done` before the board
turns DONE; final commits marked `<PACKET>: handoff DONE`. Then stop.

- Before the handoff commit, stop every browser and driver you launched,
  then run `bin/sweep_browsers` once more. A handoff that leaves a browser
  running is not DONE.

## Stops
Stop and hand off honestly, never guess silently, when: a same-function
collision with a peer's live branch; a BLACK file; a contract gap you cannot
complete under PLAN §2; an owner-side setting; a port held by a peer's
daemon (never adopt or kill it). Say what you need in one sentence.

## Looking (owner, 2026-09-28: "we need the agent to look at the ui too")
A judgment about how something looks or reads is made on an image you
have SEEN, never on page text. Two ways to see: (1) the desktop browser
pane — `preview_start` with the `nocturne` configuration in
`.claude/launch.json` after `nocturne init --verification` in
`/private/tmp/nocturne-preview/home`; its screenshots come back to you
as images, and the owner can watch; use the SHEET view for clicks; (2)
Playwright for long scripted runs and clips, then `Read` every capture
you cite before writing its verdict. A verdict on a capture you never
opened is invalid; a TASTE flag quotes the rule and names the image.
