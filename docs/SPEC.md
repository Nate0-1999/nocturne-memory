# NOCTURNE — Harness + Memory Palace Specification

**Version 2.113** (2026-09-13) — THE RULES LEDGER (owner: 'ask the same questions of all the concise rules to follow of the build before we mechanically implement both'): notes/rules-ledger.md — 169 rules, each with the owner's verbatim where it was aligned, a label stating only what he said, its kind (127 must · 28 ruling · 14 principle), and what enforces it today (102 test · 6 canon · 4 code · 20 gate-only · 37 none); 19 testable rules have no mechanical check and get one through M3RC (the rules machine). Rules and code that disagree go to the owner before enforcement. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).
content-preserving. Audience: implementing agents (via /goal) and the human owner.
Everything here is binding unless marked OPEN or given a non-accepted status.
ADR numbers are immutable; superseding requires a new ADR. The chronological
history of every decision is Appendix D.2.

**Reading guide:** Section 1 is the whole system in one page; Section 2 is
the Problem Tree — the why-lineage from root pain to atomic build element,
and the Blight Protocol for local defects. Part A holds the architecture
decisions (grouped by pillar, not by the order we argued them). Part B is
roadmap + scope law. Part C is the literal build spec for M1. Implementing
agents: read 1 → 2 → B → C, consult A when deeper rationale is needed;
packets touching user-facing surface also read garden/NATES_VISION.md
(GUIDANCE per 1.4); cite tree node IDs in DECISIONS.md. Loops begin with Agent Zero (C.10) and end
with a judge (B.6, C.9).

---

## 0. Vision & Non-Negotiables

Two loosely coupled modules, designed together, usable apart:

1. **Harness** — a modern agent harness (queuing, interjection, loops, skills,
   compaction, subagents, MCP) differentiated by visualization:
   - Live per-agent **context window visualization** (existing prototype: Code
     Puppy context-visualizer-popout — category breakdown, compaction
     threshold line, compaction table).
   - Live **agent location visualization**: agents move through the filesystem
     of the machine(s) they inhabit; the UI shows where every agent is, across
     machines.
2. **Memory system (the Memory Palace)** — the persistent "heart" of the agent:
   - Cloud-resident (GCP), identical behavior across machines and subagent
     instances.
   - Intercepts every prompt submission and injects scored memories into
     context.
   - Injection output is user-modifiable in real time; modifications are
     training signals that tune the injection scorer over time.

Both are model/token agnostic.

---

## 1. The System on One Page

### 1.0 Vernacular (the words of this project, used consistently hereafter)

- **NOCTURNE** — the product entire: the Harness and the Memory Palace as
  one instrument, named 2026-07-20 (D.2 046) — night music: the agent
  plays through the night so the composer hears the premiere in the
  morning. The install name (`pipx install nocturne-ai`, command
  `nocturne` — PyPI collision resolved, D.2 073) and the
  front door; every component name below is unchanged beneath it.
- **The Escher view** — the Cube's formal name (ADR-018): six projections
  of one object no single face can fully show. R10 (D.2 114b) dissolved the
  rotating container into six independent 3D modules sharing one selection —
  see ADR-018's Reconciliation.
- **The Chrysopoeia** — the learning loop (ADR-005 signals → M2 weight
  learning): the transmutation of gate decisions into scorer gold.
- **A shared Palace** — a team's combined memory palace, built by
  selective contribution from personal Palaces (ADR-020, HORIZON).
- **The Memory Palace** — the memory product: the database, the algorithms
  that curate it (scoring, dedup bands, the learning loop), and — from M3 —
  the curator (maintenance) agent. In M1 it is the spine deployable's
  memory module (ADR-008) and remains extractable.
- **The spine** — the always-on cloud backbone: the single deployable that
  houses the Palace plus the connective modules (auth/API plumbing,
  presence, relay/control) that local daemons and browsers connect
  through. "The heart" is a description of this always-on center, not a
  second name.
- **The Harness** (capitalized) — our local product: the per-machine
  daemon, the agent runtime, and the web command center. Lowercase
  pydantic-ai-harness is the upstream library behind the ADR-013 seam.
- **The Garden** — where this system is grown: the governance repo
  (PLAN/BOARD/FLAGS/AMENDMENTS/reports) and its culture — gardeners =
  builder agents, groundskeeper = Agent Zero, blight = defects.
- **The relay** — the Garden's execution methodology: runners, packets,
  baton handoffs, judges; ADR-012 promotes it into the product's own work
  protocol. Distinct from the spine's relay/control MODULE, which forwards
  messages between browser and daemons (M3).
- **Deck, gallery, Ant Farm, fleet color** — interface vocabulary
  (ADR-008/009): the flashcard deck is the primary interaction, the
  gallery the multiplexer view, the Ant Farm the agent visualizer; each
  top-level agent wears one fleet color everywhere.
- **Repo mapping:** local `spine/` (remote `nocturne-spine`), `harness/`
  (remote `nocturne-harness`), `garden/` (remote `nocturne-garden`);
  `nocturne` is the SPLASH repo — the single front door for new users
  (quickstart lands there with D3). Renamed to the nocturne-* family
  2026-07-21; GitHub redirects the old labels. Local directory names are
  load-bearing law; remote names are labels.

### 1.1 Topology

```
                    ┌─────────────────────────────────────┐
                    │        SPINE (Cloud Run, GCP)       │
                    │  ┌────────┐ ┌──────────┐ ┌───────┐  │
                    │  │ memory │ │ presence │ │ relay │  │   one deployable,
                    │  │  (P1)  │ │   (P2)   │ │ (P2)  │  │   three modules
                    │  └────┬───┘ └────┬─────┘ └───┬───┘  │
                    │       └──── Cloud SQL Postgres ─────│── pgvector, logs,
                    └───────────▲───────────▲─────────────┘   DBOS checkpoints
                      HTTPS/SSE │           │ outbound WS
                    ┌───────────┴───┐   ┌───┴────────────────────────┐
                    │  BROWSER SPA  │   │  MACHINE (owned or rented) │
                    │ command center│   │  daemon ──spawns── agents  │
                    │ chat·gate·viz │   │  (pydantic-ai loops at the │
                    └───────────────┘   │   workspace, SQLite cache) │
                                        └────────────────────────────┘
   M1 shortcut: browser ↔ localhost daemon directly, same envelope.
```

### 1.2 Life of a prompt

1. User types in the command center; candidates pre-fetch on debounce.
2. Send → daemon calls spine `inject/prepare`; nothing reaches the model yet.
3. First prompt of a thread: **gate** renders scored memories (full bodies) +
   near-misses. User removes (typed reasons) / adds back. Hard pause.
4. Commit → spine logs outcomes, returns the rendered `<memory_system>` block.
5. Harness prepends it system-adjacent; the agent loop runs at its workspace;
   the panel stays live for ad-hoc removal; agent may save/edit/search
   memories via tools (write-time dedup curates).
6. Every event lands in the append-only log — the future learner's fuel.

### 1.3 The Invariants

Everything else in this document elaborates these fourteen laws:

1. **The heart is singular and cloud; bodies run where the work is.** Memory
   (the Palace, carried by the spine) is the one always-on component; agent
   loops are workspace-anchored.
2. **Every module boundary is a wire** (HTTP, WS envelope, spawned process).
   Languages earn their place per component, after interfaces stabilize.
3. **Memories are atomic**: one fact, ≤128 tokens; a human can always read
   the whole thing at a glance.
4. **Nothing is deleted; everything is lineage.** Revisions append; deletes
   tombstone; history is sacred.
5. **No silent overwrites.** Every write is compare-and-set; a conflict is a
   first-class reviewable event at any timescale — a millisecond race or a
   three-week voyage are the same mechanism.
6. **Every injection is explainable.** Full per-feature breakdown travels
   with every score. No black-box ranking, ever.
7. **The log is primary; learned state is derived.** Rich enough to replay;
   weights are recomputed from it, never synced.
8. **The user's hand is a signal.** Gate/panel modifications carry typed
   reasons and train the system.
9. **Memory failure never bricks the harness.**
10. **Every visual channel encodes a real variable.** No decoration without
    data.
11. **Git owns workspace reconciliation; the spine owns memory.** We build no
    file-sync system and store no memory in Git.
12. **The scope ledger overrides enthusiasm.** FORBIDDEN means stop and flag.
13. **Model/token/provider agnostic throughout**; embedding spaces are tagged
    and re-derivable.
14. **Least attention.** Human attention is the system's scarcest resource;
    the architecture — never habit, never fatigue — decides when it is
    spent. Safety comes from walls (sandboxes, boundaries), not questions:
    per-action approvals inside the walls are forbidden theater. Attention
    is pulled only at genuine boundary crossings and judge-released
    returns — once, at the moment of highest leverage. Everything is
    always watchable; almost nothing may demand.


### 1.4 Normativity: how this spec governs

Three classes of content, deliberately different in force:

- **CONTRACTS (normative, follow literally):** the Invariants (1.3), the DDL
  (C.2), API request/response bodies (C.4), the WS envelope (C.7), the
  feature ledger and anti-scope rules (B.4, B.5), and acceptance criteria
  (C.8). These are what modules, milestones, and collaborators agree on;
  deviation requires human sign-off — the sole exception is a qualifying
  COMPLETION, below.
- **COMPLETIONS (self-service contract repair, declared):** where a CONTRACT
  is SILENT or SELF-INCONSISTENT on a detail a packet needs — a missing
  field, an undefined response shape, a promise the DDL forgot — the agent
  does not halt. It enacts the minimal completion that honors the contract's
  stated intent and declares it as an exact spec diff in
  `garden/AMENDMENTS.md` (mechanics: Garden PLAN §2). Enacted amendments are
  law, equal to this spec, for every later agent and the judge; the human
  audits them between sessions and may veto (veto → a FIXER reverts). A
  completion QUALIFIES only if it touches no Invariant, no FORBIDDEN ledger
  row, no auth or data-loss semantics; reverses no ADR; changes nothing an
  already-DONE packet built; and contradicts no explicit spec sentence — it
  fills silence, or repairs an internal contradiction in the direction the
  spec itself points. A genuine design fork — two readings yielding
  materially different products — does not qualify: FLAG it.
- **GUIDANCE (motivated, follow the reasoning):** everything else. Decisions
  travel with their motivation and rejected alternatives so implementing
  agents understand *why*. Where an agent sees a way to serve the stated
  motivation better, it may take it — provided no CONTRACT is touched — and
  MUST record the deviation and its reasoning in the repo's `DECISIONS.md`
  (append-only journal, reviewed by the human). Silence outside a CONTRACT
  is resolved the same way: act in the spirit of the Invariants and the
  Problem Tree (§2), record the choice with its node ID. Silence inside a
  CONTRACT is a COMPLETION, above.

The intent is cultivation, not transcription: this document should produce
collaborators who grow the project in its spirit, not contractors who
implement its letter and no more.

**ADR style law (for all future decisions):** decision, motivation, rejected
alternatives. Specify mechanism only where mechanism IS the contract.

---

## 2. The Problem Tree (why-lineage)

Every element of this system exists because of a problem; every problem is
nested inside a larger one. This tree is the chain of custody from root pain
to atomic build element. An agent parachuted into any leaf reads UPWARD to
understand why its component exists, and treats defects at the deepest node
that contains them (2.1). Node IDs are citable everywhere (DECISIONS.md,
commits, reviews).

Component pointers (v2.82; D.2 114b): each top-level P-node carries a brief
"(components: …)" list naming what answers it, staged in
notes/component-registry.md and folded here as pointers only. A packet's
MOTIVATION names a P-node; the node's component list is the "account for the
relevant ones" answer; RECIPE surfaces name what the packet touches from that
list. The registry describes what exists; it mandates nothing architectural —
Nocturne is one product with a few processes.

**P0.** An agent's knowledge of its human dies with the context window;
agents scattered across machines each start ignorant; and when retrieval is
wrong, the human has no lever to correct it.
→ **S0.** One persistent, corrigible memory shared by every agent everywhere
("the heart"), plus a harness that makes agents' work visible and steerable
("the glass"). [the two pillars, §0]

- **P1. Memory must outlive every process and machine.**
  → S1: cloud spine, Postgres as single source of truth. [ADR-003 · C.2]
  (components: the spine — Palace API; the palace DB; the journal; the
  memory bridge [planned, Symphony S2])
  - **P1.1** Many machines must see one memory identically.
    → S1.1: HTTP service + thin MemoryCapability; the API is the coupling.
    [ADR-002 · C.4]
  - **P1.2** Model calls need the right memories without human effort per call.
    → S1.2: scored injection at prompt time. [ADR-005 · C.3]
    - **P1.2.1** Relevance guesses will sometimes be wrong.
      → gate: human sees the injection before it binds; hard pause on the
      thread's first prompt. [C.6]
      - **P1.2.1a** Corrections must not be wasted. → typed removal reasons +
        append-only injection_event log with full feature vectors. [C.2, C.4]
      - **P1.2.1b** "Remove" is ambiguous (irrelevant? false? hated?).
        → three reasons: not_relevant / wrong / never — each with a distinct
        consequence (train / edit / quarantine). [ADR-005 table]
      - **P1.2.1c** Humans can only correct what they can read at a glance.
        → atomic units ≤128 tokens; gate shows full bodies. [decision 015]
      - **P1.2.1d** Some corrections happen mid-thought, not at the gate.
        → live panel + /v1/feedback. [C.6]
    - **P1.2.2** Scoring must improve from sparse, single-user signals.
      → transparent linear model; global weights first, hierarchy when data
      demands; near-misses shown so add-backs create positive signal;
      log-everything-first (M1), learn-second (M2). [ADR-005, decision 010]
    - **P1.2.3** A score no one can explain cannot be trusted or fixed.
      → per-feature breakdown travels with every MemoryCard. [Invariant 6]
    - **P1.2.4** Injection must not starve the real conversation.
      → token budget min(3000, 5%); greedy by score. [C.3, C.5]
  - **P1.3** Many hands (agents, humans, jobs) will edit the same memory.
    → CAS writes, append-only revisions, tombstones; conflict is a
    first-class event. [ADR-004 · C.2]
    - **P1.3.1** Divergence windows can be arbitrarily long (the sailing
      example) and multi-party. → ULID revisions + parent lineage, event_uids,
      union-merged logs, learned state re-derived not synced. [ADR-011;
      HORIZON — footprint only]
  - **P1.4** Accumulated memory rots four ways — redundancy, staleness,
    contradiction, misvaluation — and only time reveals it.
    → write-time triage at the door now [C.4, ADR-021]; the curators —
    surgeons over a deterministic tool set — later [ADR-022, M3];
    quarantine for the persistently unwanted [C.3].
  - **P1.5** Agent-written memories must cost the build almost nothing,
    yet no earned lesson may ever be lost. → the admit-then-curate write
    law: fire-and-forget saves, deterministic door triage, reinforcement
    coalescing, the ≤10% attention principle. [ADR-021]
  - **P1.6** Parallel search agents learn lessons inside timelines that
    may lose. → branch-scoped staging, sibling invisibility, judge-gated
    promotion, pruned-branch negative-result capture. [ADR-021 R1–R5]
- **P2. A human cannot trust or steer what they cannot see.**
  → S2: the visibility layer. [ADR-009]
  (components: the rack — web SPA; the module family [3D, planned W2];
  the recipe module [planned S4])
  - **P2.1** Where are my agents? → presence events → Ant Farm. [ADR-006, M3]
  - **P2.2** What is filling each context window? → Context Bars. [M2]
  - **P2.3** What does the memory know, and why was THIS injected?
    → Memory Graph + hyperparameter console; every visual channel encodes a
    real variable. [M2, Invariant 10]
  - **P2.4** Is the memory system earning its keep, and what does it cost?
    → Palace Vitals gauges: lifecycle rates/hr, token spend by category/hr,
    palace counters. [ADR-009 item 5, M2]
  - **P2.5** Every user wants their own way to see AND modify the work
    while making it. → the plugin system: rack, ground-up composition,
    controls + parameter registry, afternoon authoring, contributor
    skill. [ADR-023]
- **P3. Agents must work where the files are, yet be commanded from anywhere**
  (including a phone).
  → S3: workspace-anchored loops; daemons dial out; relay-through-spine;
  browser command center; machines as an ownership-agnostic abstraction.
  [ADR-008, ADR-010] (components: the relay — codex CLI/app + the gate; the
  garden adapter [planned S1]; the beads+Dolt store [planned S1]; the
  supervisor; the conductor [planned S2]; the judge panel [planned S3])
- **P4. One human, with agents as the workforce, must be able to build and
  evolve all of the above without it collapsing.**
  → S4: Python-first polyglot-by-boundary [ADR-001]; two repos, contracts at
  the seams [B.2, C.1]; scope ledger with FORBIDDEN [B.4]; normativity model
  and DECISIONS.md [1.4]; this tree [§2]. (components: harness daemon + CLI;
  the PI toolset unit; the golden suite + UI canon; CI; release machinery)
  - **P4.1** Spend must be TRUE — attributable, fine-grained,
    self-auditing against the invoice. → the spend ledger + canonical
    views + Vitals. [ADR-024]
  - **P4.2** Spend must be GOVERNED — before (policy), during (walls),
    and at the cliff (breaker). → token-cost policy [A-020/021 → C.5, v2.82];
    spend walls [ADR-024]; the D2 breaker.

### 2.1 The Blight Protocol (local defect, local remedy)

When an implementing agent discovers a defect, mismatch, or bad smell:

1. **Locate:** identify the DEEPEST problem node that fully contains the
   defect. (A gate rendering bug lives at P1.2.1c; it does not license
   touching the scorer at P1.2.2.)
2. **Treat locally:** fix within that node's solution space, honoring all
   CONTRACTS (1.4). This is pesticide.
3. **Escalate landscaping:** if the honest fix would alter an ANCESTOR node's
   solution or any contract, stop — that is not a local remedy. Flag the
   human with: the node, the defect, the proposed change, and which ancestors
   it disturbs.
4. **Record:** every fix and every escalation cites its node ID in
   DECISIONS.md.

**Corollary (feature attachment):** new functionality must attach to an
existing problem node or begin by proposing a new node (problem first, then
solution). Features that cannot name their problem do not get built.

---
---

# PART A — ARCHITECTURE DECISIONS

## A.1 Foundations (cross-cutting)

### ADR-001 — Framework substrate & language policy

**Problem Tree:** P4.

**Status: ACCEPTED (2026-07-07)** — polyglot policy amended in same day, folded in.

**Decision:** Build on **Pydantic AI v2** (stable 2026-06). Use **Code Puppy as
a reference/donor codebase**, not a hard fork: port the context visualizer and
proven pieces; implement the harness as a clean composition of v2
*capabilities* (plus pydantic-ai-harness library capabilities where they fit).
Durable execution: pydantic-ai's native durable-execution API with the **DBOS
backend**, pointed at the same Cloud SQL Postgres (one database for memory,
presence, training log, and execution checkpoints). Adopted in M3 alongside
loops/subagents — the features that give durability something to protect;
M1/M2 sessions are plain DB rows.

**Polyglot-by-boundary policy:** Rust was weighed seriously (user-raised;
industry precedent: agent CLIs rewritten in Rust for distribution/footprint).
Ruling: components may adopt any language once their interface is stable AND a
concrete pain justifies it; **speculative Rust is forbidden**. Per component:
- **Agent runtime:** Python, locked (pydantic-ai; I/O-bound; agent-written code).
- **Spine + scorer:** Python, defended — bottleneck is idea-iteration, not CPU
  (scorer = trivial math; vector search runs compiled inside Postgres).
- **Web app:** TypeScript; WASM escape hatch for graph layout at extreme scale.
- **Daemon:** Python in M1 (single machine, minimal role). **Rust rewrite
  pre-approved** as its own repo (`daemon-rs`), trigger-gated: 2+ machines to
  install on, Ant Farm fs-watch load, or runtime-install friction. Rationale:
  long-lived per-machine process, static-binary distribution, `notify` fs
  events — the one component matching the profile that historically forces
  Rust migrations.
- **Hot spots anywhere:** PyO3 module — a file-level change, not architectural.
  The WS envelope (C.7) and daemon↔agent process contract are frozen
  language-neutral for exactly this reason.

**Alternatives considered:**
- **pi-mono (TypeScript)** — best-designed pure harness internals (AgentHarness
  hooks incl. `before_agent_start` message injection; session trees; TUI + web
  UI libs). Rejected primarily on language: the memory system needs Python's
  ML ecosystem. Single-maintainer governance also a factor. We may still crib
  UI patterns and the hook taxonomy.
- **OpenCode** — good client/server split and TUI; least leverage for deep
  memory-pipeline and visualization surgery.
- **Fork Letta** — drags in an entire competing agent runtime. We adopt the
  *memory block abstraction* (label, description, value, size limit,
  shareable, always-in-context) but none of the code. Letta's documented
  last-write-wins on concurrent block writes is explicitly rejected (ADR-004).
- **Hard fork of Code Puppy** — tempting continuity, but couples us to
  upstream churn; v2 capabilities make a clean rebuild cheaper than untangling
  a fork.

**Consequences:** memory ships as a standalone service + thin
`MemoryCapability` (the loose-coupling boundary, ADR-002); model/provider
agnosticism inherited from pydantic-ai model strings; pydantic-ai's AG-UI
event vocabulary used for agent↔daemon streaming inside the daemon (no
framework provides multi-machine control planes — that custom layer is the
point of the project).

### ADR-010 — Agent placement: workspace-anchored fleet

**Problem Tree:** P3.

**Status: ACCEPTED (2026-07-07)**

**Law:** the agent loop runs adjacent to its workspace (filesystem), always.
Rationale: tool calls (fs I/O, builds, tests) outnumber memory calls by orders
of magnitude per thread; differentiator #1 (agents moving through a machine's
file structure) requires workspace locality; future local models put the model
on user hardware, which a cloud-anchored loop would invert absurdly.

**Movement law (ACCEPTED 2026-07-19; CONTRACT from the milestone agents
hold fs tools — M3; D.2 030):** agents and their sub-agents are both
permitted and *instructed* to move through the repository as they work. To
work on a file, an agent MUST first move to that file's directory —
location is a statement of attention, not an implementation detail. Every
directory change triggers a memory refresh: the injection layer re-prepares
against the new location and the procedurally injected set is re-rendered.
(Movement-triggered refresh is a distinct trigger from OQ-15's per-prompt
re-scoring question; refresh events log full location context like any
injection.)

**Fleet model:** "machine" is an abstraction over ownership. A daemon on a
personal desktop and a daemon on a provisioned cloud VM register identically
with the spine, appear identically in the command center/Ant Farm, and share
the same memory heart. **Managed/cloud agents = machines you rent.** No
special cases in relay, identity, memory, or presence.

**Explicitly rejected:**
- Fully cloud-managed harness (Letta-style server-side agents) as the sole
  pattern — kills workspace locality, the local-model future, and
  differentiator #1.
- Colocating harness VMs with the spine *for latency* — optimizes the rarest,
  smallest communication (one injection/thread, ~KB) while model calls
  dominate wall-clock by 100–1000x. Cloud machines are justified by always-on,
  parallelism, blast-radius safety, and mobile-initiated work — never by
  memory latency.

**Division of continuity:** the spine (memory + identity + logs) is the
singular always-on cloud component — "the heart." Agent loops are ephemeral
bodies at the work site. DBOS checkpoints make loops resumable in place;
cross-machine thread migration is NOT promised (workspace state is not in
Postgres). Machine provisioner: M4 (Part B).

## A.2 Pillar 1 — Memory

### ADR-002 — Service boundary (memory ↔ any harness)

**Problem Tree:** P1.1.

**Status: ACCEPTED for M1 scope (normalized 2026-07-31; D.2 071, judge
verdict PASS)** — HTTP service boundary, typed SpineClient, MemoryCapability
prepare–gate–commit flow, system-adjacent final block all built and judged.
Later SSE/compaction/third-party-adapter clauses remain milestone-scoped.

**Decision:** The memory system is an HTTP(+SSE) service with a versioned API
(full M1 surface with exact bodies: C.4). The harness knows only this
interface; the harness-side integration is a thin `MemoryCapability` that
calls `inject/prepare` on prompt submit, renders the gate/ad-hoc UI, calls
`inject/commit`, and prepends the returned block. **The coupling is
deliberately symmetric** (founding requirement): any other harness gains our
memory by implementing the same small client; conversely, our harness gains
any other memory system via an adapter exposing this API — `spine_client` is
the harness's ONLY knowledge of memory, so the backend is swappable by
construction.

- Every `MemoryCard` carries the full feature vector and per-feature
  contributions → any UI can always answer "why was this injected?"
  (Invariant 6).
- The injected block is **pinned across compaction** (re-injected, not
  summarized away).
- **Block placement (RESOLVED, was OQ-1):** system-prompt-adjacent as one
  stable structured block (Letta-style). Rationale: pinned across compaction;
  changes are rare mid-thread (the gate happens before the first API call, so
  turn-1 prompt cache is unaffected); system placement signals "ground truth"
  better than user-turn placement. Ad-hoc mid-thread edits bust prompt cache —
  acceptable: rare and user-initiated.

**Rejected:** in-process library as primary interface (kills multi-machine
uniformity); MCP-only interface (fine as an *additional* adapter later, but
the gate UX needs richer semantics than tool calls).

### ADR-003 — Storage, cloud & offline

**Problem Tree:** P1.

**Status: ACCEPTED (2026-07-07)**

**Decision:** GCP: **Cloud Run** (spine) + **Cloud SQL Postgres with
pgvector**. One database provides ACID transactions, vector similarity,
keyword/full-text (tsvector), relational metadata, the injection/feedback
log, and DBOS checkpoints.

- Local **SQLite read-through cache** per machine; cloud is the single source
  of truth; writes always go to cloud when online. Cache mirrors the cloud
  schema exactly (ADR-011 requirement — replica promotion is config, not
  software). Freshness: pull-on-connect + SSE invalidation; cache stores full
  units incl. embeddings and current scorer weights.
- **Degraded offline mode (RESOLVED, was OQ-2):** when the spine is
  unreachable the harness runs with **read-only memory** from the cache:
  - Semantic scoring disabled (prompt embedding needs the API); scorer
    renormalizes over keyword/temporal/project/session/frequency/history;
    gate shows an OFFLINE badge.
  - Memory writes fail loudly; the agent's create tool appends to a local
    `memory_drafts` queue surfaced for gate review on reconnect. Drafts are
    candidates, not memories — no silent sync conflicts by construction.
  - No weight updates offline; queued events upload on reconnect as log-only
    (audit, not training — feature vectors were degraded).
- Embeddings: provider-pluggable; model name + dim stored per vector;
  re-embedding on model change is a maintenance job (dim fixed 1536 in v1).
- Auth: single static bearer token until horizon; keyed by `principal_id`
  so multi-user is never a schema migration (feature itself: horizon).

**Rejected:** SQLite-only ("entirely local for now" from the original proposal
sheet) — contradicts the cloud/multi-machine requirement, and retrofitting
sync is far costlier than starting cloud-first. Firestore (no clean
vector+relational combo). Spanner (overkill). Separate vector DB (operational
overhead, two-phase consistency problems).

**The memory lifecycle map (v2.12)** — one glance for implementing agents:
where each stage's law lives. Read the ADR named for your stage FIRST; its
Motivation section is why your packet exists.

| lifecycle stage | law | milestone |
|---|---|---|
| birth — agent saves, /remember, extraction, seeds | ADR-021; C.4/C.6; ADR-019 cl. 4 | M1–M2 |
| injection & the gate | ADR-005; C.3/C.5 | M1 |
| staging & promotion (search runs) | ADR-021 R1–R5 | M3 |
| tending — dedup, contradiction, promotion, slop | ADR-022 | M3 |
| learning (the Chrysopoeia) | ADR-005 signals + hygiene + actor classes | M2 |
| observability | ADR-009 item 5 (Palace Vitals) | M2 |
| sharing across people | ADR-020 | M4+ |
| death & resurrection | ADR-004 tombstones; C.3 quarantine; gate add-backs | M1 |

### ADR-004 — Memory unit model & concurrency

**Problem Tree:** P1.3.

**Status: ACCEPTED (normalized 2026-07-31; D.2 071, judge verdict PASS)** —
the shipped M1 unit/CAS/revision/tombstone/quarantine/pin law is built and
verified end to end (unit shape refined ACCEPTED in review: atomic ≤128
tokens, no summary field — decision 015)

**Unit model (authoritative DDL: C.2):** a memory unit is an ATOMIC fact —
label (short handle), description, body (≤128 tokens), kind (fact |
preference | procedure | project_note | persona | pinned), agent-chosen
keywords, embedding (+model tag), optional project/thread/path stamps, pin flag,
status (active | quarantined | tombstoned), stats (injections, removals,
citations, never_kills, last_injected_at), per-memory bias b_m.

**Concurrency & history:**
- All writes compare-and-set on `revision`; conflict → HTTP 409 with current
  unit, retry is deliberate (agent sees fresh state), never silent overwrite —
  explicit rejection of Letta's last-write-wins.
- Revisions are append-only, identified by client-mintable ULIDs with
  `parent_uid` lineage (DAG-capable, ADR-011); the integer `revision` is
  solely the cloud head's CAS counter. Deletes are tombstones; nothing is
  hard-deleted (the "update history" memory index is this revision table).
- **Thread snapshot pinning:** first `inject/prepare` stamps the thread's
  `snapshot_ts`; automatic scoring within the thread reads as-of that
  snapshot so memory doesn't mutate under a running agent. **Ad-hoc
  mid-thread injections read LIVE memory (RESOLVED, was OQ-3)** — the user is
  explicitly reaching outside the snapshot.

**Promotion (resolves the original proposal-sheet contradiction):**
promotion does NOT write to agents.md/user.md. It flips
`pin=true` (always injected, exempt from scoring, still user-removable in the
gate → unpins). The "does not manipulate sessions/skills/agents.md" invariant
holds. Manual pinning: free from M1. Automatic promotion is curator work —
criteria and timing in ADR-022.

**Maintenance → ADR-022 (consolidated v2.12).** All curator law — the rot
taxonomy that motivates them, doctrine, architecture, operations, triggers —
lives in ADR-022. What remains true here: curators are writers like any
other; every curator write obeys the same CAS, lineage, and tombstone rules
above.

### ADR-005 — Injection scoring, gate & the learning loop

**Problem Tree:** P1.2.

**Status: PARTIALLY ACCEPTED** (annotated 2026-07-31; D.2 071: M1 scorer v0,
full gate logging, removal/add-back/never incl. the A-022 near-miss veto,
and quarantine are JUDGED VERIFIED; M2 online learning remains future) —
removal semantics, gate behavior, learning
scope ACCEPTED; scorer constants remain tunable config. This is the
differentiator; the spec is deliberately detailed. (Exact v0 formulas and
constants: C.3, C.5.)

**Scorer:** transparent linear model per candidate m for prompt q:
`score(m,q) = w_sem·f_sem + w_kw·f_kw + w_time·f_time + w_proj·f_proj
+ w_freq·f_freq + w_hist·f_hist + b_m` — features ∈ [0,1]: semantic cosine,
keyword overlap, recency decay, project match, citation frequency,
human-edit recency, plus per-memory bias. (f_sess excised 2026-07-07: a
feature "kept for parity" that was near-always zero on first prompts —
thread_origin remains on units as metadata.) SELECTION LAW (clarified
v2.31, owner): the BINDING constraints are the threshold τ and the token
budget — every memory above τ enters greedily by score until the budget is
spent. The top-k cap is a v0 DISPLAY/SAFETY CONSTANT (gate readability),
tunable config like every scorer constant, never architecture — M2 replay
tunes it as a hyperparameter; **near-misses** (next 3) are shown in
the gate so add-backs generate positive signal. Near-miss rows ALSO offer
"never show this" (v2.16; resolves F007's deadlock — two kills push a unit
below τ where only add-back existed, making the third kill unreachable):
same event semantics and kill counter as removed:never. The near-miss lane
is the exploration mechanism, and exploration needs a veto — a near-miss
the owner never wants is the strongest signal the lane can produce. PIN OVERFLOW (v2.14): pins
are exempt from scoring AND from the budget cap — explicit human intent
outranks the cap; scored candidates fill only what pins leave. A pin block
that alone exceeds the budget still injects whole, is surfaced in Vitals,
and ticks the injection-pressure trigger (ADR-022) — the system makes
over-pinning VISIBLE rather than silently truncating the owner's choices.

**Signals → updates** (all logged via injection_event; applied online from M2):

| action | label | update |
|---|---|---|
| removed: "not relevant here" (one-tap DEFAULT — ACCEPTED) | strong neg | SGD step on w against f(m,q); b_m small − |
| removed: "wrong/stale" | content | NO weight update; opens edit/expire flow on m |
| removed: "never show this" | kill | b_m large −, quarantine at 3 kills; small global step |
| added back a near-miss | strong pos | SGD + on w; b_m small + |
| "never" on a near-miss (v2.16) | kill | identical to removed:never — b_m large −, kill counter, quarantine at 3 |
| kept (survived gate) | weak pos | tiny + step (heavily discounted — passive) |
| agent cited memory in work | strong pos | + step; increments f_freq numerator |
| ad-hoc mid-thread removal | strong neg | as "not relevant", flagged mid_thread |

Weights versioned (`scorer_version` on every event) → auditability, rollback,
offline replay. Citation detection v1: n-gram heuristic; OPEN (OQ-5):
LLM-judge later (cost).

**Folded amendment (A-036, v2.82)** — enacted by M2L against [ADR-005 citation
heuristic; C.3 f_freq; C.4 /v1/feedback], node(s) P1.2; law text verbatim from
AMENDMENTS.md:
Citation v1 tokenizes with C.3's maximal runs of Unicode alphanumeric
characters after lowercase conversion. For each memory selected in the
exact context of one ordinary model call, let n be the smaller of eight
and that memory body's token count. A body with fewer than four tokens is
not autonomously cited. Otherwise the memory is cited exactly when at
least one of its contiguous n-token sequences occurs contiguously in the
successful final assistant text. Labels, prompts, thinking, tool traffic,
system instructions, and failed or cancelled outputs do not participate.
Repeated matches in one output still produce one signal for that memory.

Harness snapshots the selected bodies and each member's current
injection-event source at the same model/feedback lock boundary M2G uses,
then submits one existing `cited` feedback per detected member after the
successful model call and before releasing that boundary. Thus every
post-first message attributes reuse to its own autonomous scoring batch;
the first gated message attributes it to the committed gate batch. A
citation-feedback failure emits the existing safe memory-unavailable
error with phase `citation` but does not retract or replace an assistant
answer already produced. Each member is attempted independently; no
background retry queue or cross-member batch endpoint is introduced.

Spine permits `cited` from `kept`, `added_back`, or `auto_entered`.
Its first successful transition atomically changes that event outcome to
`cited`, increments the current head's `stats.citations` once through C.2
CAS, and appends the ordinary revision with editor `system:feedback`, the
event machine, and reason `feedback/cited`. An identical replay is an
idempotent no-op. This is the f_freq numerator; no weight, bias, status,
or other statistic changes. Existing outcome-conflict rules remain.

**Learning scope (ACCEPTED, phased):** M1 = no learning, log the full context
tuple (project_key, agent_kind, memory_kind, scorer_version) so any scope is a
modeling change, not a migration; offline replay is a first-class evaluation
tool. LEARNER V1 SCOPE + AUTHORITY (v2.34, wave-2 pass): v1 fits ONLY the
global weights + shrinkage-regularized b_m — τ, budget, and the k-cap stay
manual console territory (smallest learnable surface; the explanation stays
the weights). A challenger that wins the v2.32 binary scoreboard on
held-out gates (after a minimum-dispositions floor, config) becomes a
PROPOSED scorer version; IN THE EARLY ERA THE OWNER ACTIVATES IT — one
tap — and the learner earns auto-activation exactly as curators earn
auto-merge: by the owner's own approval history. M2 = **global weights** (fastest learner on sparse single-user signal;
b_m absorbs memory-specific corrections; f_proj expresses project affinity).
TRAINING-DATA HYGIENE (v2.7): learning and replay MUST exclude events from
verification/test principals — filter on machine_id/editor prefixes used by
packet verification (e.g. `*-verification-*`, `verification:*`) — so fixture
runs never tune the scorer; the events remain in the log (append-only law).
M3 = **hierarchical per-project offsets**, zero-initialized, partial-pooling:
`score = (w_global + w_offset[project])·f + b_m`; adopt when the log shows a
context whose removals fight the global trend. Rejected: fully per-context
scorers (signal starvation for a single user).

**Per-message re-scoring (RESOLVED OQ-15, v2.31; owner; M2):** the human
gate opens ONCE, at the thread's first message. Every subsequent message
re-scores autonomously against the live palace — the context's memory
block is refreshed per message (most relevant above τ, within budget) with
NO further modals (walls, not questions): the panel is where the human
watches and modifies, and every autonomous entry/exit is logged as an
injection_event (entries that persist unremoved = passive keeps, C5
class; mid-thread removals/additions = typed signals, existing law).
Snapshot semantics follow: each re-score reads as-of its own prepare;
the first-gate's human-confirmed set is never silently dropped within
its thread — re-scoring may ADD and may demote only autonomous entries,
never gate-confirmed or pinned ones, without a human action.
DISPOSITIONS ARE BINARY LOCKS, BOTH DIRECTIONS (v2.33, owner): the
symmetric rule — a memory the human REMOVED in a thread is EXCLUDED from
that thread's re-scoring until the human re-adds it; a re-score must
never resurrect a killed card. In-thread human actions are locks, never
score boosts — no arbitrary magnitudes (the scoreboard philosophy applied
to the thread itself). Graded within-thread effects (kin/affinity boosts
from what the human added) are the resurrected f_sess candidate: OQ-15's
resolution voided its founding excision rationale, and it enters through
the agentic-axes pipeline — earning weights by replay against the binary
scoreboard, never hand-set. Unlocks the
f_phase axis candidate (parked in notes explicitly until re-scoring
existed). M1's once-per-thread behavior stands until the M2 packet lands.

**The scoreboard (RESOLVED, v2.32; owner — closes the M2 agenda's item 1):**
replay acceptance is BINARY, never graded: a challenger scorer is graded
against every recorded human disposition on held-out gates — for each
memory the human dispositioned (kept, removed:not_relevant, removed:never,
added back), the challenger's decision either AGREES (right) or DISAGREES
(wrong); FEWEST WRONGS WINS. No arbitrary point weights: an override is an
override, in either direction — add-backs punish timid challengers,
removals punish pushy ones, so the metric self-balances. Exclusions and
rails: removed:wrong is EXCLUDED (content error, not injection error —
counting it would teach the scorer to hide stale-but-relevant memories);
undispositioned near-misses are ungraded; passive keeps count but carry
their C5 actor-class discount in the tally; at equal wrongs the CHEAPER
challenger wins (fewer injected tokens — the total-time-to-completion
proxy); a challenger must beat the incumbent by a real margin (config,
not law) on held-out future gates before its weights take the gate.
Storage is already complete: every disposition is an injection_event row
with full feature vector + scorer_version (every override traceable to
the exact weights that made the wrong call), and SUCCESSES ARE STORED TOO
— they are the denominators, the anchor for what must not change, and
dispositions in their own right.

**Folded amendment (A-031, v2.82)** — enacted by M2F against [ADR-005 learning
scope and scoreboard], node(s) P1.2; law text verbatim from AMENDMENTS.md:
The M2F learner reads one repeatable-read snapshot of `injection_event`,
ordered by gate time then injection UUID, and groups rows by injection_id.
Hygiene excludes a group when any row's principal or machine identity is
test/fixture/verification-class: exact or delimited `test`, `fixture`, or
`verification`, including a machine value ending in `-verification`.
Eligible positive dispositions are human `kept`, every `added_back`,
`cited`, and `mid_thread_added`, plus passive `kept` and `auto_entered`;
eligible negatives are every `removed:not_relevant`, `removed:never`, and
`mid_thread_removed`. Explicit disposition/feedback outcomes have weight
1 even when they replace a row originally created with actor_class
`passive`; only passive `kept` and `auto_entered` positives receive the
configurable discount, default 0.25. Null, `removed:wrong`, `auto_exited`,
and every other outcome are ungraded.

Sort eligible gates by `(max(ts), injection_id)`. The newest configurable
fraction, default 0.20 rounded up to whole gates, is holdout; every older
gate is training. At least one gate and one eligible disposition must
remain on each side. Before the first challenger, require configurable
minimum eligible dispositions, default 25. Training creates every
positive-negative pair within each training gate and performs one
deterministic whole-log convex re-fit using squared hinge loss,
configurable pair margin default 0.05, non-negative global weights
constrained to sum exactly 1, and L2-shrunk per-memory bias offsets with
configurable coefficient default 1.0. A pair containing a passive example
receives the smaller actor weight. The online head bias remains the
immediate never-kill safety term; a version's learned offset is added to
it, not substituted for it.

Replay grades each eligible holdout disposition as one binary decision.
A pin predicts injected. A regular row predicts injected exactly when its
recomputed score meets the incumbent version's manual tau; budget and k
remain manual and are not learner variables. This per-disposition replay
is the v1 meaning of ADR-005's storage-complete claim: it uses the recorded
feature tuple and frozen body, never fabricates candidates outside the
logged gate. Each mismatch adds its actor weight; cheaper-at-tie sums
`cl100k_base` body tokens for predicted injected dispositions. A challenger
wins when its weighted mismatches improve on the incumbent's recorded
decisions by the configurable absolute margin, default 1.0, or when
weighted mismatches tie exactly and it injects fewer tokens.

POST `/retrain` has no request body and returns
`{status, incumbent_version, proposal_version, eligible_dispositions,
training_dispositions, holdout_dispositions, training_pairs, incumbent,
challenger, reason}`. Status is `insufficient_data`, `not_better`, or
`proposed`; proposal_version and both score objects are nullable where no
comparison exists. A score object is
`{disagreements, weighted_disagreements, injected_tokens}` with the
weighted value serialized as an exact decimal string. The trigger never
activates a version.

A winner inserts one inactive `scorer_config`. Its deterministic version
derives from a canonical digest of the incumbent, ordered training and
holdout examples, and learner settings. It copies every manual scorer
parameter unchanged and stores under params `_learner`: status
`proposed`, algorithm id, source digest and boundary, settings, fit
diagnostics, replay scores, and the learned memory-id-to-bias-offset map.
Repeating an identical fit returns the same proposal idempotently. Active
scorer loading applies that map when such a version is later activated;
old rows have an empty map. Optional positive
`SPINE_LEARNER_SCHEDULE_HOURS` runs the same operation periodically;
unset means manual-only. Concurrent triggers serialize on one database
advisory transaction lock.

**Location relevance (ACCEPTED 2026-07-19; D.2 030):** every memory carries
`origin_path` — the workspace-relative directory context where it was
learned (NULL when there is none; M1 writes it as inert metadata only, like
thread_origin). From the milestone where agents hold filesystem tools (M3),
scorer versions add f_loc:
- d = hop distance between the agent's current working directory and
  origin_path in the workspace directory tree (each step up or down one
  level = 1 hop; same directory d=0); a memory from a different
  workspace/repo takes f_loc = 0 (cross-repo affinity stays f_proj's job).
- f_loc = 2^(−d / h_loc), h_loc default 2 hops (config).
- **Null rule (CONTRACT):** when either side lacks a location (origin_path
  NULL, or the agent has no working directory), the location term is
  OMITTED and the score renormalizes over the remaining weights —
  score = (Σ_{i≠loc} w_i·f_i) / (1 − w_loc) + b_m — so a location-less
  memory is NEVER penalized relative to a location-aware one. Scorer v0
  (no w_loc) is unaffected.

**Gate UX (ACCEPTED in review):** first prompt of thread = hard pause, no
timeout (config: auto-continue T or non-blocking per agent/project);
candidates pre-fetched on typing debounce so the gate renders instantly. Rows
show label + FULL body (atomic units make summaries unnecessary) + score;
one-tap ✕ = not_relevant; modifier menu: wrong / never; near-misses grayed,
tap to add. After the first prompt: no pause; the panel stays live, removals
apply from the next model call. Gate rows show per-feature contribution bars
(context-visualizer visual language) — Invariant 6. One gate per thread in
M1; per-prompt re-scoring is OPEN (OQ-15, M2 decision).

### ADR-011 — Divergence & reconciliation ("the sailing example") — HORIZON

**Problem Tree:** P1.3.1.

**Status: ACCEPTED (2026-07-07)** as end-state design + M1 schema footprint.
Building the machinery is FORBIDDEN before M5 (Part B).

**Scenario it must survive:** weeks offline on rented hardware with a local
model and a writable local memory replica; meanwhile multiple principals keep
using the same agent memory in the cloud; at reconnect, workspace and memory
reconcile.

**Jurisdictions:**
- **Workspace = Git's job.** Boat work is a long-lived branch; reconciliation
  is push + merge. We never build a file-sync system; agents commit as they
  work; non-repo files wanting merge semantics move into a repo.
- **Memory = per-unit three-way merge**, enabled by atomic units:
  new-on-either-side → union (+ sync-time dedup pass reusing write-time
  similarity machinery); edited-one-side → fast-forward; edited-both →
  conflict into the same review queue as curator merges (ADR-022 autonomy
  law applies);
  edit-vs-tombstone → review queue.
- **Logs = union.** Append-only; conflicts impossible.
- **Learned state = derived, never synced.** After log union, weights are
  RE-DERIVED by replaying merged history (the M2 replay tool doubles as the
  sync mechanism for learned state). Events scored in a non-canonical
  embedding space are flagged via embed_model and excluded/down-weighted.
- **Unifying model:** online CAS and offline sync are one mechanism at
  different timescales — one conflict taxonomy, one review UI (Invariant 5).

**Foundation footprint applied in M1 (the ONLY committed part):** revision
ULIDs + parent_uid lineage; client-minted `event_uid` on every event
(idempotent union-safe upload); client-mintable IDs on all creates; cache
schema mirroring; `/v1/sync/*` namespace reserved (M1 drafts flow is its
degenerate ancestor). Local model support needs nothing (model-agnostic +
embed_model tagging, verified).

**Explicitly rejected:** storing memory in Git (multi-writer DB semantics
don't fit); CRDTs for memory bodies (atomic units + review queue beat
automatic text merging for facts a human must trust); syncing learned weights.

## A.3 Pillar 2 — Harness & Interface

### ADR-007 — Harness feature parity (now an index)

**Problem Tree:** P4.

**Status: ACCEPTED as index (2026-07-20; D.2 041)** — the 2026-07-19/20
gate sessions designed what this draft only named. Parity items and their
owning law:
- Queuing / steering / interruption → ADR-014 (three-level send gesture).
- Human-in-the-loop & tool approval → ADR-015 (walls and boundaries; the
  approval ladder is deliberately DEAD — read ADR-015 before assuming one).
- Sessions (durable, resumable, branching) + checkpoints/rewind → ADR-016.
- Loops / autonomous iteration / subagent orchestration at scale → ADR-012
  (work protocol + mode scale) and ADR-017 (Symphony search).
- Skills, MCP, compaction, cost batteries → adopted through the ADR-013
  seam (pydantic-ai capabilities; compaction chassis = ProcessHistory,
  plan-mode variant parked in D.4).
- Visualization & interfacing → ADR-008 (deck, scrubber, gallery) and
  ADR-018 (the Cube, plugins, stack).
Remaining parity items still owned here until their milestone planning:
per-model tokenizer abstraction for honest context accounting; DBOS
durable execution (arrives with loops/subagents in M3; M1 sessions are
plain DB rows).

### ADR-008 — Interface layer & control plane

**Problem Tree:** P3.

**Status: ACCEPTED; M1 stack details ACCEPTED (normalized 2026-07-31; D.2
071)** — React/TypeScript/Vite command center over the browser→local-daemon
path, snapshot-authoritative state, built and judged; relay/deck clauses
stay later-milestone. Originally ACCEPTED (2026-07-07) — direction confirmed ("browser-based, no
TUI maintenance"); stack details PROPOSED.

**Decision:** Prompt entry, memory gate, queuing/interjection, and all
visualizations live in a single **web command center**. No interactive TUI is
built or maintained.

**Primary interaction: the flashcard deck (ACCEPTED 2026-07-19; D.2 031;
built when the multi-agent era opens — M1/M2's single-thread chat pane is
unchanged):** the command center's main mode is a deck of flashcards, one
per top-level agent, each card wearing its agent's fleet color — the same
color that agent has in the Ant Farm and every other visualizer (one color
per agent, everywhere). Exactly one card is on top: that agent's latest
response, awaiting the human. Queue order is purely time-based — whichever
agent finishes next takes the next slot; responding advances the deck. The
human may also manually cycle through the deck to watch running agents
without dequeuing them. Expanding a card opens the full thread view, whose
scrollbar is a scrubber of one line per HUMAN input — click a line to jump
the transcript to that exchange (navigation indexed by what the human
remembers saying). Secondary mode — the gallery: on demand, or when the
queue is empty, live conversations tile like a terminal multiplexer, at
most 4 tiles visible at once, panning to reach the rest. Sub-agents never
surface cards and never address the human directly; they are visible only
through the visualizers (Ant Farm et al.), and their work reaches the
human, if at all, through their top-level agent's card.

- **Frontend (stack RESOLVED 2026-07-20 — D.2 041; was PROPOSED):** React +
  TypeScript + Vite SPA. DOM for rails/panels/text; ONE WebGL/WebGPU stage
  scene for the Cube (three.js + react-three-fiber, TSL dual-target
  shaders) per ADR-018; SVG for small panel charts; WebSocket
  (control/streams) + SSE (memory/presence). Chromium-class browsers only
  through M4. M1's H4 chat shell is plain React DOM — the stage arrives
  with the M2/M3 viz buildout.
- **Not Electron.** Desktop feel = Chrome app mode (`chrome --app=<url>`),
  matching the existing popout pattern; Tauri can wrap the same SPA later.
  Rationale: zero desktop-runtime maintenance for a single-user tool.
- **Harness daemon (per machine):** small Python/FastAPI process that spawns
  and supervises pydantic-ai agent processes, owns local FS access, emits
  presence + context-accounting events, executes prompt/queue/interject
  commands. Thin CLI only to install/start/register and one-shot `--print`.
- **Relay-through-spine (RESOLVED, was OQ-13, PURE relay):** daemons open
  *outbound* WebSockets and register `{machine_id, capabilities}`; the
  browser talks only to the spine, which relays commands down and streams
  events up. No inbound ports/VPN; works from anywhere; single origin + auth.
  Rejected: browser→daemon direct (LAN latency win; port/auth/NAT pain,
  second auth story). M1 shortcut: browser↔localhost daemon, same envelope —
  M3 changes the path, not the messages.
- **Spine = three segregated modules, one deployable:** memory (P1), presence
  (P2), relay/control (P2). No cross-module imports beyond shared db/auth
  plumbing; presence/relay must remain extractable (split = deploy change).
- **Identity (RESOLVED, was OQ-11):** client-mintable/service-minted ULIDs:
  `principal_id` (human), `machine_id` (daemon registration, stored in
  config), `agent_id` (per spawned instance), `thread_id`. All events carry
  the full tuple.
- OPEN (OQ-12): minimal localhost fallback page for offline prompt entry —
  which milestone.

### ADR-012 — The work protocol: spec → loop → judge (default grammar of work)

**Problem Tree:** P4.

**Status: ACCEPTED (2026-07-19; D.2 032).** CONTRACT from the milestone the
multi-agent harness ships (M3); M1/M2's single-thread chat is its degenerate
predecessor. This is the Garden relay methodology that built this very
system, promoted to product behavior.

**The protocol — every unit of project work, no exceptions:**
1. **Spec alignment.** Work begins as a conversation that produces a spec
   artifact the human ratifies. The spec scales with the task — one
   ratified sentence for a rename, a document for a feature — but it always
   exists: it is the judge's rubric, and work without a rubric cannot be
   judged.
2. **Agent loop.** The orchestrator dispatches the work: one agent, or N
   parallel attempts at the same spec in isolated git worktrees. The system
   sizes N from the task, capped by the `max_parallel_project_agents`
   setting (harness config; the knob ships with this milestone). Agents
   follow the movement law (ADR-010) as they work.
3. **Judge.** Judging inherits B.6 doctrine: fresh context, different model
   preferred, sees only the spec and the artifacts, never the builders'
   reasoning. Verdicts: COMPLETE → release to the human; INCOMPLETE or
   defective → dispatch a continuation/fix agent charged with the verdict
   (loop back to 2). For parallel attempts the judge picks the winner
   against the spec and may charge a graft agent with folding the losers'
   best pieces into it.
4. **Return.** Only judge-released items enter the flashcard deck
   (ADR-008). The system never pulls human attention otherwise.

**Blockers (judge-triaged):** an agent that hits a genuine blocker (missing
credential, spec ambiguity) routes its question to the judge, not the
human. If the answer is derivable from the spec, the judge sends the agent
back with it; only questions the spec truly cannot answer are released as
cards. Agents cannot "block" their way to human attention.

**Interjection rule:** the human may always watch (gallery, deck cycling).
Interjection is allowed while ONE agent holds the work; when N>1 parallel
attempts run, the run is watch-only — an interjection mid-swarm would fork
the attempts' common ground. Attention-pulls are judge-gated always;
attention-pushes are solo-run only.

**Rejected:** direct-mode escapes for "small" tasks (the human chose no
exceptions — the spec simply scales down); human-picked winners among
parallel attempts (reopens human attention mid-protocol; the judge exists
precisely so that attention is spent once, at the end).

**The mode scale (amended 2026-07-20; D.2 041).** One protocol, a movable
JUDGE SEAT, three rungs (D.2 130) — scalar so new rungs slot in without machinery:
- **Solo** (pp): one agent, headless one-shot; the output is the verdict.
- **Duet** (mp): turn-based pair — THE HUMAN HOLDS THE JUDGE SEAT, every
  turn. Attention flows continuously by the human's choice, which is an
  Invariant-14-legal spend: the architecture's decision is that the human
  elected it. For trust-building and craft.
- **Symphony** (ff): full ADR-017 search; judges conduct; attention
  arrives as the premiere card.
Roles in the music: the human is the COMPOSER (writes the score — spec,
objective, budget); the CONDUCTOR is the orchestrator plus its judges;
agents are SECTIONS. The composer does not play in the pit; the composer
hears the premiere. The seat transfers per-thread, mid-thread, both
directions, in one gesture ("take it from here" / "let me drive"). Pair
mode is not an escape from the protocol — it is the human occupying one of
its offices.
*Verification:* mode state is an envelope-visible thread property; judged
by driving a seat transfer both directions and observing that attention
pulls (cards) occur only per the active rung's law.

### ADR-013 — Framework seam: own the interface, adapt the implementation

**Problem Tree:** P4.

**Status: ACCEPTED (2026-07-19; D.2 034).** CONTRACT for all harness-side
feature construction from H3 onward. Motivation: pydantic-ai v2 +
pydantic-ai-harness now cover real harness territory (capabilities,
history processors, provider compaction, CodeMode, fs/shell batteries,
cost tracking) — we want their institutional implementations without ever
waiting on them, and our differentiators must never be trapped inside
their API.

**The two-module law:**
1. Every harness feature is written against OUR minimal internal
   capability protocol (typed pydantic models: instructions, tools,
   lifecycle hooks, history transforms, event-stream taps) — defined in
   the harness repo, owned by us, versioned by us.
2. Exactly ONE adapter module translates our protocol ↔ pydantic-ai's
   capability machinery, in BOTH directions. Outbound: our features ship
   as standard pydantic-ai v2 `Capability` subclasses (MemoryCapability
   first), mountable by any vanilla pydantic-ai user — honoring ADR-002's
   bidirectional coupling. Inbound: upstream batteries are adopted
   individually, version-pinned, and wrapped behind the same protocol, so
   consumers cannot tell home-grown from adopted.
3. **Import fence (greppable):** nothing outside the adapter module
   imports pydantic-ai capability machinery. Swapping an implementation —
   ours for theirs when upstream matures, theirs for ours when 0.x churn
   breaks or stalls — touches only the adapter, never consumers.
4. **Wrap on first use** — no speculative wrapping of upstream's catalog
   (the "no speculative Rust" principle applied to adapters).

**Known adoption targets (through the seam, at their milestone):**
- `defer_loading=True` per capability when toolsets widen (M3) — opt-in,
  NOT automatic; collapses unused bundles to one-line catalog entries.
- CodeMode for fs/shell-heavy work (M3): one sandboxed program replaces
  per-call round-trips (~11 calls → ~2 on a 10-item fan-out). Adoption
  MUST preserve ADR-010's movement law: refresh-on-move fires at the tool
  boundary inside code-mode execution too.
- Provider compaction capabilities + ProcessHistory as the chassis for
  D.4's plan-mode compaction (M3).
- Cost-tracking battery (pairs with the D2 billing breaker).

**Rejected:** wholesale dependence on pydantic-ai-harness (0.x, breaking
minor releases); bypassing the seam for expedience; rebuilding commodity
batteries ourselves when upstream's are adoptable through the seam.

### ADR-014 — The prime loop: turn lifecycle, interruption & budgets

**Problem Tree:** P4.

**Status: ACCEPTED (2026-07-19; D.2 037).** M1 implements the subset wired
by C.7 v1.12 (cancel, queue, snapshot, usage); the full law is CONTRACT for
the M3 harness buildout. This designs what ADR-007's draft list only named.

**Turn lifecycle.** A turn: context assembly (memory injection per C.6;
M3+ adds skills/files) → model call → tool execution → repeat until the
model yields or a limit trips → verify/report. Every turn is correlated by
run_id; every state change is an envelope event — the UI renders the loop
exclusively from the event stream, no side channels.

**Interruption (work preserved).** Cancel aborts the in-flight model call
and tool batch, keeps everything already produced, marks aborted tool
calls with a terminal cancelled state (history stays well-formed), and
returns control with stop_reason:"cancelled". Cancellation is always
confirmed by run.done — never fire-and-forget.

**The three-level send gesture (M3).** While a run is live the composer
stays active; sending escalates:
1. QUEUE — deliver after the run completes, as a new turn (M1 ships this).
2. STEER — inject into the running turn at the next tool boundary,
   cancelling the remainder of the current tool batch (run.steer).
3. INTERRUPT — stop now (run.cancel), keep partial work, redirect.
A visible queue with per-message edit/delete is part of the composer.

**Failure & retry.** Provider 429/5xx: bounded exponential backoff,
retries surfaced as events, never silent. A turn that dies on a terminal
provider error ends run.done{stop_reason:"error"} with partial output
preserved and labeled partial — the loop never fabricates completeness.

**Budgets are loop inputs.** Each run accepts request and token ceilings
(defaults in C.5: run_request_limit=40, run_total_tokens_limit=500000,
harness config); consumption streams via run.usage; breach is a DISTINCT
terminal status (stop_reason:"budget_exceeded"), rendered differently from
error. Enforcement seat: pydantic-ai UsageLimits through the ADR-013 seam.

### ADR-015 — Walls and boundaries: the permission model

**Problem Tree:** P4.2.

**Status: ACCEPTED (2026-07-20; D.2 041).** CONTRACT from the milestone
agents hold fs/shell tools (M3). Codifies the human-gate discussion that
produced Invariant 14.

**Motivation (the intuition).** Per-command approval is security theater:
an agent with write+bash already holds full capability the moment one
command runs; fifty "allow" clicks change nothing except training the
human to click. The field's approval-mode ladders replace judgment with
fatigue. Meanwhile our own gate/judge design already knows the answer:
spend attention only where it is decisive.

**Decision — safety from walls, attention at the walls:**
1. DEFAULT posture, zero setup: every agent runs inside an OS-level
   sandbox scoped to its workspace/worktree (Seatbelt on macOS,
   Landlock/container on Linux) with a network allowlist. INSIDE the
   walls: full autonomy — no per-action approvals exist. (Pi's honesty
   with Codex's kernel enforcement, made the default.)
2. Attention is spent only at BOUNDARY CROSSINGS: leaving the sandbox —
   remote pushes, deploys, credentials, network beyond allowlist,
   spending money. A crossing is not an "approval"; it routes like every
   attention pull: through the judge first (ADR-012 blocker triage — a
   PermissionJudge answers what the boundary list can answer; only
   decisions genuinely reserved for the human become cards).
3. There is no permission-mode ladder. Configuration = sandbox profiles
   (what the walls permit) + the boundary list (what makes a card).
   Worktree isolation per parallel attempt means every swarm branch is
   its own walled garden.
4. Trust invariant: the repository and all tool output are UNTRUSTED
   input; only human-owned configuration escalates capability.

**Rejected:** approval-mode ladders (fatigue generation); pure
Pi-style YOLO without default walls (right honesty, wrong default —
"a clean protected environment should be the default"); LLM screening of
every in-wall action (attention theater relocated into compute).
*Verification:* drive an in-wall destructive-looking action (no prompt
appears) and a boundary crossing (card appears, judge-triaged); attempt
capability escalation from repo config (refused).

### ADR-016 — Two ledgers, one tree: sessions, checkpoints, rewind

**Problem Tree:** P1.

**Status: ACCEPTED (2026-07-20; D.2 041).** CONTRACT for M2/M3 session
machinery; ONE M1 seed already landing with H4 (see below).

**Motivation.** Two user stories are one mechanism: the Duet user who went
down a bad path wants one-click return of conversation AND code to any
earlier point (Cursor's revert, SolidWorks' rollback bar); the Symphony
search needs cheap branchable states. Undo and exploration are the same
tree walked in different directions.

**Decision:**
1. TWO LEDGERS. The real git repo stays deliberate — commits happen when
   work means something; history stays clean. A SHADOW ledger (second git
   dir over the same worktree) checkpoints file state automatically at
   every HUMAN TURN. A checkpoint is a restore point, never a commit.
2. ONE TREE. Every message carries `parentId`; a session is a tree that
   usually never branches. REWIND = continue from an earlier node (the
   old continuation survives as a sibling). FORK = new child from any
   node, conversation + checkpointed files traveling together into a
   fresh worktree. Restore scope is always explicit:
   conversation | files | both.
3. The scrubber (one line per human input — ADR-008) is the tree's UI:
   every line anchors a checkpoint; click to jump, rewind, or fork.
4. Every query surface accepts `as_of` (timestamp/checkpoint ref):
   live view, scrubbing, and time-lapse are one mechanism at three
   speeds. Client state is event-sourced with keyframe snapshots so
   seeking is cheap.
5. M1 SEED (H4): browser message objects already carry ULID ids; the
   `parentId` column lands with the first persistent transcript store so
   no migration is ever needed. Tree semantics activate in M2/M3.

**Rejected:** committing every turn to the real repo (noise; the two
ledgers exist so history stays human); flat sessions with git-only undo
(kills forking, the scrubber-as-time-machine, and Symphony's substrate);
conversation-only rewind (Cursor taught that code and chat must travel
together).
*Verification:* scrub to an earlier human turn, rewind scope=both,
verify workspace + transcript match that moment; fork two siblings from
one node and verify divergent worktrees with a shared prefix.

**The journal law (owner, v2.39; ratifies M2D's fail-closed posture):**
HISTORY IS MANDATORY. The daemon does not run without a writable
conversation journal — conversations without history are UNSUPPORTED BY
DESIGN, a deliberate scoping decision ("we can't support everything"),
and the precedent is the industry's best tools: Claude Code and Codex
keep complete session history on disk as plain files, always. Corollaries:
(a) the hard stop must only ever follow an ignored warning — `doctor`
and startup check free disk and WARN EARLY (M2N); (b) THRESHOLD PROMPT:
when NOCTURNE's total on-disk footprint (journal + local db + backups)
crosses a configured share of the system's storage, a card surfaces on
the DECK — the one surface permitted to demand — because preventing a
data-loss stop is one of the few interruptions that earns its cost;
the Vitals resources gauge carries the always-on view; (c) the spend
receipt asymmetry stands (B.6 r11): receipts are reconcilable and fail
open; history is unrecoverable and fails closed. Fail open on anything
reconcilable; fail closed on anything unrecoverable.

### ADR-017 — Symphony search: the work protocol at scale

**Problem Tree:** P1.6.

**Status: ACCEPTED (2026-07-20; D.2 041).** CONTRACT for the M3+ Symphony
rung of ADR-012. Generalizes the one-round swarm ADR-012 already defines.

**Motivation.** ADR-012's parallel attempts are one round of breadth.
Real problems (optimize this kernel) want ITERATED, COORDINATED search:
many stratagems advancing in parallel, evidence reallocating effort,
partial wins combining — Karpathy-style auto-research, but multi-agent
and budget-aware. The relay that built this system is the manual version;
Symphony is its automation.

**Decision — value-guided tree search over materialized workspaces:**
- A NODE is (conversation, workspace checkpoint, spec) — cheap to mint
  (ADR-016 shadow ledger), isolated to run (worktree).
- Four operators: EXPAND (an agent advances a leaf) · FORK (N siblings
  seeded with named STRATAGEMS — approach hypotheses enumerated at spec
  alignment by human or strategist agent) · GRAFT (git-native crossover:
  merge the best of two branches; elevates ADR-012's graft to a
  first-class move) · PRUNE (defund; branches PERSIST forever —
  auditable, revivable).
- The VALUE FUNCTION is the judge: a BENCHMARK when the spec declares its
  objective MEASURED (latency, tests, tokens/sec — full autonomy); a
  judge MODEL when JUDGED (taste; premiere cards deserve more
  skepticism). Every spec declares which.
- The BUDGET is a portfolio: allocation follows expected value PLUS
  uncertainty (bandit-style); beam width = max_parallel_project_agents.
  Evaluation competes with expansion for tokens (value-of-information:
  sometimes the right spend is to listen, not play).
- CONVERGENCE: target metric hit, budget exhausted, or judge COMPLETE →
  exactly one premiere card (Invariant 14).
- THE REMEMBERING ORCHESTRA (the moat): every branch — especially pruned
  ones — files atomic lessons to the Palace, stamped project +
  origin_path. The next search on similar ground opens with those
  lessons injected. Search compounds across projects; this is only
  possible because the Palace was built first.

**Rejected:** fixed-round tournaments (evidence should move money);
human-picked winners mid-search (ADR-012's rejection, inherited);
deleting pruned branches (their lessons and audit trail are half the
value).
*Verification:* on a MEASURED toy objective, verify budget reallocation
away from a weak stratagem, one graft producing a leaf that outscores
both parents, pruned-branch lessons appearing as Palace units, and
exactly one card at convergence.

**Reconciliation (editor pass IV, 2026-08-18).** Appended so this ADR reads
true beside the law it grew into; no clause above is deleted. The governing
law is D.2 102 (the three-judge minimum + the recipe card), D.2 103 (the
location fence + recipe-card practice), D.2 114b (THE SYMPHONY ENACTMENT:
planning rulings R1-R23, acceptance bar S1-S4, the primitive alignment
charter, the four-wave plan in notes/symphony-plan.md, and the G-rulings), and
AMENDMENTS A-059 (the SYM6 memory-bridge contract). Rulings cited by number
are in notes/m3-planning-agenda.md; the surfaces are named in GLOSSARY.md (the
judge panel, the recipe card, ratatouille criteria, the Athanor). Clause by
clause:

**Resolution (v2.83, D.2 124).** The tension above is resolved: the 'always-3D rendering rejected' clause is superseded; R10 governs and the family renders through a real game engine under the engine law; every visual binds to real data under the function law.

1. "A NODE is (conversation, workspace checkpoint, spec)" — SUPPLEMENTED, not
   replaced. A node is now also a BEAD in a typed dependency DAG with cycle
   rejection, a ready frontier, atomic claim, and gates-as-nodes (the beads
   register, R23; D.2 114b), held behind the garden adapter (Wave S1, SYM1). A
   `symphony`-marked step becomes a parent node and the conductor spawns N
   diverse child attempts (S2). Worktree isolation and the ADR-016 shadow
   ledger stand (SYM5 reuses the M3E worktree locations).

2. "Four operators … STRATAGEMS enumerated at spec alignment" — SUPERSEDED in
   mechanism, kept in vocabulary. Spec alignment is now DELIBERATION (SYM10):
   judge charters and performance metrics are fixed in conversation before any
   builder runs (D.2 102), and charters are immutable per deliberation —
   changing one FORKS the symphony (G19, D.2 114b). Child attempts are
   authored by the conductor as WORKER BRIEFS (G5) under
   subdivide-never-add-scope (G4), with admission handles and the distillate
   contract (R23). EXPAND and FORK become the packet explosion of S2; GRAFT
   survives as graft lineage edges in rounds (SYM9, delta frontier: passed
   work stands); PRUNE survives with the loser-tombstone rule of clause 6
   below.

3. "The VALUE FUNCTION is the judge: a BENCHMARK when MEASURED, a judge MODEL
   when JUDGED" — SUPERSEDED by the THREE-JUDGE MINIMUM (D.2 102): every
   symphony emits at least a MOTIVATION judge (holds the whole why), an
   IMPLEMENTATION judge (sensibleness of the how), and a PERFORMANCE judge
   that judges against PRECALCULATED METRICS agreed at plan time and persisted
   in a cross-symphony ledger so the bar rises — the MEASURED case now lives
   inside that seat. Users may add judges; none may be removed. Judges sit
   fresh (B.6 separation), evaluate at the recipe card's merge points, and
   UNANIMITY terminates; a FAIL persists its feedback as new packets minted
   through the adapter (SYM8, the enacted feedback law). STAGED JUDGING — a
   cheap smoke gate before expensive completion — is the SYM7 design.

4. "The BUDGET is a portfolio … beam width = max_parallel_project_agents …
   evaluation competes with expansion" — BOUNDED by R22 (D.2 114b): per search
   node, attempts 3, spend wall $10, max rounds 3, depth cap 2, children per
   attempt 4, duration ceiling 30 min, overridable per charge AUTHORITY. Spend
   walls remain ADR-024 boundary crossings; the dual brakes (spend wall,
   same-failure-twice) stand from D.2 102. Portfolio reallocation inside those
   caps (beam narrowing, SYM7) stands as design intent; where the portfolio
   language and the R22 caps disagree, the caps govern.

5. "CONVERGENCE: target metric hit, budget exhausted, or judge COMPLETE →
   exactly one premiere card" — RESTATED: convergence is judge-panel UNANIMITY
   or a brake (D.2 102; R22 ceilings). The premiere card stands and is the
   SYMPHONY CARD on the Deck (SYM11); BLOCKED surfaces with its lineage (T4).
   The owner steers the conductor only, and owner interventions classify per
   G19 (D.2 114b): (a) clarification / logged follow-up, (b) attempt
   cancellation within T2, (c) charter or scope change — class (c) forks the
   symphony and the old run's lineage stands append-only.

6. "THE REMEMBERING ORCHESTRA … every branch — especially pruned ones — files
   atomic lessons to the Palace" — SUPERSEDED as written by the SWARM MEMORY
   LAW (R12, S3, G11; D.2 114b; ADR-021 R1-R5; A-059): workers read the shared
   palace; an attempt's own writes are STAGED and visible only to itself
   during the run; at resolution the WINNER's batch routes to the PALACE QUEUE
   with its judged context attached (consent surface intact, no fast lane),
   and the LOSERS' memories TOMBSTONE as attempt lineage — never injectable,
   never discarded. Pruned branches therefore do not file lessons directly; a
   negative-result lesson reaches the Palace only through the judge's verdict
   duties (ADR-021 R3) and the ordinary queue. Kernel compaction is never
   palace memory (S3). The moat survives in its intended form: the next search
   on similar ground opens with the admitted lessons injected.

7. The Motivation paragraph — EXTENDED by the PRIMITIVE REGISTERS (R23), which
   bound what may enter: from Prime/RLM — context-as-variable, child-as-call
   with handles, the distillate contract, distill-and-die, supervisor/worker
   separation, parent-attributed cost, bounded-autonomy caps; from beads —
   typed DAG with cycle rejection, ready frontier, atomic claim, lease
   machinery (post-release), gates-as-nodes, append-only history, versioned
   truth with generated views; ours, binding — motivation/template, judge
   unanimity, consent surfaces, fence/locations, palace policies, brakes.
   NOTHING outside the registers is taken. The adapter refuses to mint a bead
   without a validated MOTIVATION (S4). There is no mode switch: symphony is a
   property of the work (mode-scale law; SYM10). The plan artifact is the
   RECIPE CARD (D.2 102: rows, per-row prep, merge cells as join barriers,
   ratatouille criteria — D.2 103), rendered live by the recipe module (SYM12
   = M3J); the live search is watched through the ATHANOR (GLOSSARY,
   owner-coined 2026-08-16). The iterative build test is Symphony's first real
   customer (symphony-plan cross-wave law). The manual relay remains the
   standing fallback until parity earns trust.

8. The Rejected list — UNCHANGED. "Deleting pruned branches" stays rejected: a
   tombstone is lineage, not deletion.

9. The Verification clause — "pruned-branch lessons appearing as Palace units"
   is SUPERSEDED: verify instead that loser rows tombstone with reason
   `symphony/loser-tombstone` and the winner's rows arrive as one Palace-queue
   batch carrying judged context (A-059); budget reallocation is verified
   against the R22 caps; "exactly one card at convergence" stands.

### ADR-018 — The Cube and the plugin rack

**Problem Tree:** P2.5.

**Status: ACCEPTED (2026-07-20; D.2 041).** CONTRACT for the M2/M3 viz
buildout; supersedes nothing — it UNIFIES ADR-009/decision-016's committed
visualizers into one object. Detail and aesthetics: garden/NATES_VISION.md
(GUIDANCE) + garden/notes/cube-visualizer.md (design notes).

**Motivation.** The viz suite was a list; the Cube is its geometry. One
underlying object — the work — projected on faces the human rotates
between, with ComfyUI's lesson (the visualization IS the app; you get
where you're going by clicking into it) and Ableton's lesson (a rack the
user rearranges, not furniture).

**Decision:**
1. FACES with true spatial logic: FARM front (the colony: whole directory
   tree as chambered burrows, files as cells, zoom, CAD-floating on black
   + faint grid) · ROOTS the depth axis (organic meandering roots growing
   horizontally from each colony; dead roots preserved and desiccated;
   thickness = tokens spent; opacity = SELECTION FOCUS — the selected
   score's roots at full presence, every other score dimmed but always
   visible, floor 0.25; EV is NOT an alpha channel — OQ-17 resolved
   2026-07-21: the owner wants focus, not valuation, in opacity) · TIPS opposite the
   Farm (roots end-on; the frontier grid of next-round starts) · plus
   DECK (left rail, time-ordered, auto-advance, conductor drafts — the
   ONLY surface that may demand) · LEDGER (capital alluvial) · PALACE
   (memory; inverted light scheme) · SCORE (context bars + timeline).
   ORBIT rotates around (90°-class azimuth moves), zoom scrolls, faces
   render FLAT when focused (the cube is navigation, not decoration).
2. ONE SELECTION shared by every face; selection is also navigation.
   The rollback bar (ADR-016) scrubs every face to any `as_of`.
3. MEMORY-TRACE SANCTITY: selecting anything traces its injections —
   injected list with one-tap pop-off, near-miss suggestions with
   one-tap add — as pure CONSUMERS of the injection_event log. No
   visualizer may write to, delay, or bias the scorer. Ever.
4. THE RACK → extracted to ADR-023 (v2.23, content-preserving).
   What remains binding here: face plugins parameterize clause 6's
   generators and never emit meshes.
5. STACK (resolves ADR-008's PROPOSED details): React DOM for rails and
   all text; ONE WebGL/WebGPU stage scene — three.js + react-three-fiber
   inside the existing React+Vite app, TSL shaders dual-targeting
   WGSL/GLSL; instancing for populations, compute for living systems
   (curators, root growth); text never enters the canvas; refs +
   useFrame, never per-frame setState; parallel DOM/table rendering for
   accessibility. Chromium-class browsers are the ONLY support target
   through M4. Fleet palette machine-validated on final grounds
   (dataviz six checks) — revalidate on any ground change.

6. PROCEDURAL LAW (amended 2026-07-20; D.2 042). All stage geometry is
   PROCEDURAL: generated at runtime as a DETERMINISTIC function of work
   metadata, seeded by stable ids — no hand-authored scene assets, ever.
   The visuals are model-like — real 3D parts with materials and lighting
   (the CAD/SolidWorks feel), built by generators, not drawn: roots are
   GROWN from search metadata (thickness = spend, meander seeded by
   branch id, length = depth, junctions = fork/graft events, desiccation
   = prune state); colonies are GROWN from the live directory tree
   (chambers from directories, cell counts from files, tunnels from
   hierarchy); Palace architecture and curators derive from memory-unit
   populations and curator events. Same data → same geometry, which is
   what makes the ADR-016 rollback bar work on the scene: scrubbing
   re-runs generation at that `as_of`. Generators live behind the face
   schema — plugins parameterize them with data and never emit meshes.
   Mechanism: parametric/instanced mesh generation with TSL/compute for
   growth animation (clause 5's stack).
7. THEMES (amended 2026-07-20; D.2 043). The aesthetic is a swappable
   STYLE CONFIGURATION on the rack — tokens, motif set, material
   language — loadable and user-authorable like any panel. Default theme:
   NEO-NOIR (Syd Mead / tech-noir: neon against dark urban, street-level
   claustrophobic composition, holographic-display language, rain-slicked
   materials, volumetric lighting — full vocabulary in NATES_VISION §8).
   Named alternates: THE TWO FACES of COBALT-SERAPH — SERAPH DRESSED
   (night) and GOLD LINES (day) — DERIVED from its committed reference
   plate (deterministic extraction: tokens + area shares; same plate →
   same tokens; notes/cobalt-seraph-reference-plate.md) and selectable
   beside the worn NEO-NOIR default (D.2 113; audition history D.2
   107–112). Theme law: themes STYLE meaning and
   never re-encode it — data encodings, fleet identity, and Invariant 14
   survive every theme; each theme's fleet palette is machine-validated
   on that theme's grounds before it ships; one danger color per theme.
   Themes ride the plugin distribution path (versioned, sandbox-safe:
   a theme is tokens and materials, never code with surface access).
8.-10. Ground-up composition, control plugins & the parameter
   registry, authoring ease & reference plugins → extracted to ADR-023
   (v2.23, content-preserving).

**Rejected:** literal always-3D rendering (readability loses to
spectacle); Safari/Firefox support before M4 (single-user product; compat tax steals
packets); hand-modeled or imported scene assets (cannot time-scrub,
cannot track live projects, and would let art drift from truth — the
organism must be GROWN from its data or it is a picture of an organism).
*Verification:* per B.6 rule 7 throughout — plugin-system verification
moved to ADR-023 (v2.23) — plus: selection made
on any face must appear on all faces and the trace drawer within one
event cycle; the palette validator must pass on the shipped grounds;
and procedural determinism is judged directly — render the same `as_of`
twice and diff the geometry (must be identical), change one metadata
input and verify exactly the corresponding visual property moves.

**Reconciliation (editor pass IV, 2026-08-18).** Appended so this ADR reads
true beside the later law; no clause above is deleted. The governing law is
R10 and R21 (planning rulings, notes/m3-planning-agenda.md; enacted and
blessed in D.2 114b), D.2 083 (the module scope law, superseded in part), D.2
107-119 (the theme lineage), GLOSSARY.md (the Athanor, the Cauldron),
notes/component-registry.md (the module family) and the Wave 2 backlog (agenda
item 10: the 3D stage). Clause by clause:

1. Clause 1 — FACES, ORBIT, faces render FLAT when focused — SUPERSEDED by R10
   (D.2 114b): THE CUBE DISSOLVES INTO MODULES. Each of the six faces — FARM,
   ROOTS, TIPS, LEDGER, PALACE, SCORE — becomes ITS OWN MODULE: an
   independent, literally-3D asset on the stage ("super liquid and smooth
   looking"). The single rotating container is out, ORBIT is mooted, and no
   face renders flat on focus because there is no cube to rotate. The DECK
   remains the left rail and the only surface that may demand (unchanged).
   Every per-face encoding above — colony burrows, roots as spend / meander /
   depth / junctions / desiccation, selection-focus opacity (OQ-17), TIPS as
   the frontier grid — is unchanged and travels with its module. HARDWARE
   TIERS are required (R10): graceful quality degradation (LOD and the gaming
   ecosystem's standard solutions) on ordinary hardware; the upper end must
   look amazing. The module family also takes in the LANDSCAPE (ADR-009 item
   4, v2.43; colocated with the curators), the ATHANOR (the live symphony
   view; GLOSSARY, owner-coined 2026-08-16), the CAULDRON (the pixel-art
   Chrysopoeia module — hardware-free, the family's contrast member; working
   name pending the owner's word; GLOSSARY, D.2 115,
   notes/m3-planning-seeds.md), the recipe module (SYM12) and the device chain
   (D.2 114b(3)). Wave 2 item 10 builds FARM (the Ant Farm), ROOTS and TIPS
   first.

2. Clause 2 — ONE SELECTION shared by every face — SURVIVES in substance (R10:
   the one-object / one-selection law holds across the six modules), but its
   LINKING mechanism is SUPERSEDED by R21 SPATIAL LINKING (blessed D.2 114b):
   POSITION is the primitive (layer + place — the true-primitives doctrine,
   R19). WITHIN A LAYER, modules snapped together form a FRAME and share one
   selection — proximity links. GLOBAL is the one deliberate escape from
   position: a setting, and a GLOBAL module watches everything from any layer.
   ACROSS LAYERS, deliberately nothing: layers are rooms, and watchers live
   beside what they watch — cross-layer invisible wiring is the inelegance
   being escaped. PORTALS (a spatial mirror of a frame on another layer) are a
   future exception on the YAGNI clock, only if mission-control needs arrive
   twice. R21 replaces the GLOBAL|CURRENT toggle of ADR-023 clause 5 (D.2 083)
   at the module family's build and replaces the link-channels candidate
   (agenda item 5; R8). "Selection is also navigation" stands. The ROLLBACK
   BAR stands and is sharpened into a REQUIREMENT ON the 3D module wave:
   generators take `as_of` from day one; the scrubber UI may phase (D.2
   114b(3)).

3. Clause 3 — MEMORY-TRACE SANCTITY — UNCHANGED.

4. Clause 5 — STACK — STANDS, with R10's consequence: the stage is still one
   renderer (React DOM for rails and all text; one WebGL/WebGPU scene; TSL
   shaders; instancing and compute; text never in the canvas; Chromium-class
   only through M4), but the six modules are placed on it as independent 3D
   assets rather than as faces of one container, and hardware tiers / LOD join
   the stack's requirements. The parallel DOM/table rendering for
   accessibility is unchanged.

5. Clause 6 — PROCEDURAL LAW — UNCHANGED and extended: to aesthetics (D.2 107
   the reference plate; D.2 114 the plate press), to script (D.2 118-119 the
   sigil forge, grammar not glyphs), and by the `as_of` requirement of item 2.

6. Clause 7 — THEMES — UNCHANGED and extended by D.2 112-114 (first-written
   set: the audition loop and the two faces, the three-theme ruling, the plate
   press), D.2 115 (the grimoire themes + the conjuration law) and D.2 116-119
   (the rune crank, the elder scripts, the sigil forge, grammar not glyphs).

7. Rejected — "literal always-3D rendering (readability loses to spectacle)" —
   IN TENSION with R10's "independent, literally-3D asset". R10 governs as the
   later owner ruling (D.2 114b); the readability motive survives through
   Invariant 10, the hardware tiers, and clause 5's parallel DOM/table
   rendering. Recorded here for the owner's confirmation rather than resolved
   by the editor.

8. Verification — "selection made on any face must appear on all faces and the
   trace drawer within one event cycle" now reads: a selection made in any
   module of a FRAME appears in every module of that frame and in every GLOBAL
   watcher within one event cycle, and in no module of another layer. The
   double-render determinism diff and the palette validation stand.

### ADR-019 — Onboarding: the two-secrets rule and packaging

**Problem Tree:** P4.

**Status: ACCEPTED (2026-07-20; D.2 046).** CONTRACT for the packaging
wave (packet D3, opens after the M1 judge); the principle binds all
future onboarding surface immediately.

**Motivation.** The three-repo workspace is the RELAY's reality, not the
user's. A newcomer must reach a working Nocturne without cloning
anything, building web assets, or hand-assembling env files — the
onboarding tax is a product surface, and Invariant 14 applies to it:
setup attention is still attention.

**Decision:**
1. THE TWO-SECRETS RULE. A newcomer reaches a working Nocturne with at
   most two secrets: an OpenRouter key (always — the broker carries chat
   AND embeddings per v2.3) and GCP credentials (cloud mode only, via
   their existing `gcloud auth`). LOCAL MODE NEEDS ONE SECRET. Everything
   else — spine bearer token, database passwords, runtime identities,
   budget guard, billing breaker — is generated, defaulted, or derived.
2. THE FOUR COMMANDS (contract names): `nocturne init` (first-run wizard;
   asks only for the secrets), `nocturne up` (local: pull pgvector
   container, migrate, start spine + daemon, open the browser),
   `nocturne deploy` (cloud: the D1 runbook as idempotent code with
   --dry-run; includes the D2 breaker), `nocturne open`.
3. PACKAGING. Two wheels, zero clones: `nocturne` (harness repo — daemon,
   CLI, agent, spine client, WEB ASSETS PRE-BUILT AND BUNDLED as package
   data; users never run Node) and `nocturne-spine` (spine repo — app +
   alembic migrations as package data; a dependency of `nocturne` for
   local mode; `nocturne deploy` ships its packaged source). Versioning
   is LOCKSTEP: one product version stamps both wheels; the C-contracts
   are the compatibility surface. THE GARDEN NEVER SHIPS — it is the
   methodology that grew the organism, not the organism. And THE GARDEN
   NEVER SHOWS (owner, v2.48 — "don't sell your customer your org
   chart"): no ADR number, packet id, amendment reference, problem-tree
   node, or build reasoning may appear in any customer-facing surface —
   UI text, labels, command output, error messages, public docs. Product
   surfaces speak the customer's language about the customer's problem;
   internal provenance lives in DECISIONS, reports, and code comments.
   Pairs with vision §18's test: read it aloud — and if it names our
   process instead of their purpose, it doesn't ship. The next UI packet
   sweeps existing surfaces for leaks.
   THE UPDATE PROMPT (owner, v2.50): `nocturne up` performs a VERSION
   HANDSHAKE with the Palace it just authenticated. Local palace behind
   → migrate automatically (standing behavior). REMOTE palace behind →
   one plain sentence and an offer: "Your Palace is running older
   software (schema X; this app expects Y). Update now? Updates back up
   first and take a few minutes." Accepting runs the deploy path
   (backup receipt ALWAYS precedes migration, M2N law); declining
   leaves a visible notice, never a 503 wall. The app half of updates
   is the standard channel (pipx upgrade, once publishing un-defers);
   the next `up` prompts for the Palace half. Supersedes gate finding
   #1 (2026-08-05) from complaint to contract.
   PLAIN SECRET NAMES (owner, v2.50): third-party secrets are called by
   their EXACT vendor names — "OpenRouter API key", "Google Cloud
   service account key" — never aliases; NOCTURNE-generated secrets are
   called by plain function — "your Palace access token (generated for
   you at setup)". Auth jargon ("bearer") is banned from customer
   surfaces. init's "Palace bearer:" prompt is the first fix.
   THE CAPABILITY LADDER (owner ruling, refined v2.36 — supersedes
   v2.35's positioning language, which read like editions): NOCTURNE is
   ONE PRODUCT — one codebase, two lockstep wheels, one install — with
   RUNGS of capability, never forks: RUNG 1, the local palace (everyone,
   today: same spine, same schema, same scorer, complete and honest);
   RUNG 2, your palace in YOUR OWN cloud — the MIRROR PRINCIPLE:
   `nocturne deploy` is the reference deployment as a recipe, and its
   current hard-coding to the owner's project is a V0.1 IMPLEMENTATION
   STATE, not an identity; the road is per-install targets + an
   ownership manifest so anyone mirrors the setup into their own cloud
   supplier (a named future packet). The support boundary stands
   regardless: THEY run it, THEY own it — "we operate no one's database
   but our own until we choose to be a SaaS"; RUNG 3, multi-machine
   replicas + sync — ADR-011 blue-water mode (M5), whose first customer
   is the owner's own founding sailing example (P1.3.1), and whose
   schema footprint every M1 table already carries. If offline use
   becomes urgent before M5 (viable only once local models arrive — no
   connectivity kills the broker before it kills the palace), the cheap
   intermediate rung is a READ-ONLY OFFLINE CACHE, noted here so it is
   proposed as a rung, never a fork. Palace identity law unchanged;
   moving a palace between rungs is explicit export/import until M5's
   sync engine exists.
4. SEED INGESTION — the cold open (added 2026-07-21; builds in M2). Cold
   start is product surface: at init (or any time) the user uploads
   markdown files and curator-style splitters break them into ATOMIC
   memories (the 128-token law) through the STANDARD create/dedup
   pipeline, landing in an approval queue — the user confirms what enters
   the Palace. Rides M2's extraction + approval-queue machinery; no new
   persistence. A palace should never start empty unless its owner wants
   it to.

**Folded amendment (A-033, v2.82)** — enacted by M2I against [ADR-019 clause
4; ADR-021 unified queue; ADR-022 splitting], node(s) P1.4, P1.5; law text
verbatim from AMENDMENTS.md:
Harness exposes JSON POST `/v1/seeds` to its own rack surface with
`{batch_uid,source_name,markdown}`. `batch_uid` is a client-minted UUID;
`source_name` is the basename of a `.md` or `.markdown` file; markdown is
nonblank valid UTF-8 and at most 24 KiB. The browser may submit several
selected files, but each file is one independent request and one batch.
Markdown is the only M2I format. The tools-free splitter receives the
complete document and emits one or more independently comprehensible
candidates, each with one claim, its own label, kind, and 2-5 keywords.
It may emit at most 64 children, and every child must satisfy the standing
128-token memory limit. If those bounds cannot preserve the source, the
request fails visibly; it never truncates, mechanically chops, summarizes,
or partially substitutes the document.

Harness fetches the active neighborhood and proposes the standing
new/merge/supersede/contradict verdict for every split child, then submits
the whole result to bearer-protected Spine POST `/v1/seeds`. Spine verifies
the SHA-256 digest of the supplied markdown, creates one queue-invisible
tombstoned-as-split source head whose first revision retains the exact
markdown, and runs every child through M2H's same candidate create/dedup
path. Each admitted child's first revision has `parent_uid` equal to that
source revision; every admitted sibling pair receives symmetric
`relates_to` edges. Hard duplicates produce no child card and increment
the batch duplicate count. Candidate children remain invisible to list,
search, and injection.

Extend each approval queue row/card with birthplace exactly `thread` or
`seed`, nullable thread UUID, and for seed rows non-null `batch_uid`,
`source_name`, and lowercase SHA-256 digest. Existing rows migrate as
thread-born. Queue reads can filter birthplace; thread-end surfaces always
request thread-born rows, and seed rows render only in the law-bound Palace
queue module. Reusing a batch UID with the same principal, source name, and
digest returns its existing batch without re-splitting; any mismatch is
409. There is no stored upload blob beyond the tombstoned source revision
and no filesystem copy.

POST `/v1/approval-queue/batches/{batch_uid}/decisions` accepts only
`{decision:approve|deny,approval_mode:explicit,actor_class:human,machine_id}`
and decides every pending card in that seed batch in one transaction.
Approval enacts each card's standing verdict; denial tombstones every
candidate. An identical replay returns the existing decisions; a mixed or
conflicting replay is 409 and changes nothing. Any stale implicated target
makes the whole approval 409 and leaves the batch pending. Seed batches
never passive-approve, expire, notify, or appear in a thread-end card.

**Owner lifecycle contracts (folded v2.82 — editor pass IV).** The M2N
lifecycle-hardening amendments A-041 through A-046 (config versions, local
backup generations, doctor, resources gauge, informed restore, owner-cloud
backup receipts) are folded here in enactment order; A-044 is folded once, at
ADR-009 item 5 (Palace Vitals), and pointed to from its place below.

**Folded amendment (A-041, v2.82)** — enacted by M2N against [ADR-019 v0.1
positioning; C.2 migration discipline], node(s) P1.3, P4; law text verbatim
from AMENDMENTS.md:
Local config version 2 adds `NOCTURNE_BACKUP_GENERATIONS`, an integer in
the inclusive range 1..50 with default 5. Loading a private version-1
config upgrades it atomically in place before returning: preserve every
existing value byte-for-byte, add the default retention value, set the
version to 2, fsync and replace under mode 0600. An unknown or future
version is refused without mutation. New initialization writes version 2.

Every online Alembic run acquires the PostgreSQL session advisory lock
keyed by signed bigint `5642809481902573646` on the same connection before
configuring or beginning migration work, holds it through the complete
upgrade/downgrade transaction sequence, and releases it in `finally`.
Local and owner-cloud paths both use the packaged migration entrypoint and
therefore the same lock. Offline SQL generation takes no database lock.

The local Compose database image is the multi-platform OCI index
`pgvector/pgvector:pg16@sha256:a36250871de0833b8757561c72f2477ef1ddd1101afa4e617fb552e0de514c6b`.
Historical support currently means every packaged revision from 0001
through head; the upgrade matrix must execute each of those revisions to
the single current head against real Postgres.

**Folded amendment (A-042, v2.82)** — enacted by M2N against [ADR-019 owner
lifecycle; C.2 migration discipline], node(s) P1.3, P4; law text verbatim from
AMENDMENTS.md:
A local backup is one mode-0700 generation directory under
`NOCTURNE_HOME/backups`, named by a client-minted ULID. It contains exactly
`palace.pgdump`, a mode-0600 PostgreSQL custom-format dump made with
`--no-owner --no-privileges`, and mode-0600 `receipt.json`. The receipt is
UTF-8 JSON with schema version 1 and these fields: `backup_id`, aware UTC
`created_at`, `reason` (`manual` or `pre_migration`), `database`, nullable
`alembic_revision`, `postgres_image`, `archive`, `archive_bytes`, and
lowercase `archive_sha256`. Before publication, `pg_restore --list` must
accept the completed archive and the recorded digest and byte count must
match it. Failure publishes no generation. A successful manual backup and
every local packaged migration attempt use this same writer. After a new
generation is durably published, prune oldest valid generation directories
by ULID order until the configured A-041 retention count remains; ignore
every unrecognized file or directory rather than deleting it. Backup and
receipt command failures expose safe owner-language errors and never print
config values, database credentials, or raw subprocess output.

**Folded amendment (A-043, v2.82)** — enacted by M2N against [ADR-019 owner
lifecycle; ADR-016 journal law], node(s) P1.3, P4; law text verbatim from
AMENDMENTS.md:
`nocturne doctor` is a read-only local inspection. It reports the logical
database size, conversation-journal size, retained-backup size, and free
space on the filesystem containing `NOCTURNE_HOME`. It warns early when
free space is at or below the greater of 5 GiB or 10 percent of that
filesystem's capacity. This is a conservative diagnostic boundary, not
the later configured Deck threshold and never a deletion or hard stop.

Doctor verifies the private home/config modes, local PostgreSQL
reachability, and every recognized A-042 generation. Generation
verification requires the exact receipt schema and values, an aware UTC
timestamp, private modes, no extra generation entries, matching archive
byte count and SHA-256, and acceptance by the pinned image's
`pg_restore --list`. Unknown backup entries remain ignored and untouched,
as in retention. The command prints no secret or raw subprocess output.

The result is `healthy`, `warning`, or `failed`: healthy exits 0; an early
disk warning exits 1 so scripts can observe it; a failed privacy,
reachability, or backup-integrity check exits 2. Every result still prints
the measurements it could safely obtain and one short owner-language line
per warning or failure.

**A-044 (v2.82)** — the RESOURCES gauge, startup disk warning and the daemon
soak bound: law text folded once, at ADR-009 item 5 (Palace Vitals); it amends
ADR-016's journal law and this ADR's owner lifecycle equally.

**Folded amendment (A-045, v2.82)** — enacted by M2N against [ADR-019 owner
lifecycle; D.2 092 informed restore], node(s) P1.3, P4; law text verbatim from
AMENDMENTS.md:
Local config version 3 adds `NOCTURNE_POSTGRES_VOLUME`. Version 2 upgrades
atomically by setting it to Compose's existing
`<compose-project>_nocturne_postgres` volume; new initialization writes
that same default. Compose mounts exactly the configured named volume.
Restore-created names are `<compose-project>_restore_<backup-id-lower>`.
Config accepts only that project's default or restore-name grammar, so a
config value cannot redirect lifecycle commands to an unrelated volume.

`nocturne restore BACKUP_ID` is local-only and refuses while the Harness
or Spine service is reachable. It accepts only one recognized A-042
generation whose receipt, digest, modes, contents, and pg_restore listing
reverify. It creates a fresh managed volume and isolated pinned-image
PostgreSQL container, restores the archive there, upgrades that candidate
through the packaged locked migration path, and queries both live
databases before asking anything. Any failure removes only the candidate
container and candidate volume; the live container, config, and volume
remain unchanged.

The ROLLBACK MANIFEST names, by label and UUID: current memory heads absent
from the candidate as `memories lost`; shared heads whose revision or
stored unit values differ as `edits reverted`; and current pins absent or
unpinned in the candidate as `pins undone`. It also prints current,
candidate, and signed delta counts for `memory_revision`,
`injection_event`, `spend_event`, `approval_decision`,
`scorer_activation`, and `spend_reconciliation`. The owner confirms by
typing the exact `RESTORE BACKUP_ID`; every other response cancels and
removes the candidate without touching live state.

After confirmation, restore stops the live PostgreSQL container, atomically
changes only the active-volume config value, starts the candidate through
normal Compose, and verifies reachability. If start or verification fails,
it atomically restores the former pointer and restarts the former volume.
Success retains the former volume and writes a private mode-0600 rollback
receipt under a mode-0700 `NOCTURNE_HOME/rollback-volumes` directory,
recording the restore id, backup id, switch time, former and active volume
names, and manifest counts. Restore never prunes volumes; the receipt makes
the former live volume discoverable for explicit future recovery.

**Folded amendment (A-046, v2.82)** — enacted by M2N against [ADR-019 owner
lifecycle; C.2 migration discipline], node(s) P1.3, P4; law text verbatim from
AMENDMENTS.md:
Before an owner-cloud packaged migration, Harness mints a ULID receipt ID
and creates one Cloud SQL ON_DEMAND backup of the fixed D1 instance with
description `nocturne-pre-migration-<lowercase-receipt-id>`, explicit
project, instance, and `us-central1` location. It may request the backup
asynchronously only to capture its operation and backup IDs; it waits at
most 30 minutes for that exact operation, then independently describes the
exact backup. Migration may begin only when the operation is DONE with
type BACKUP_VOLUME for the fixed project and instance and the described
backup is SUCCESSFUL, ON_DEMAND, in the fixed location, with the exact
instance, ID, and description.

Before migration begins, Harness atomically persists mode-0600 UTF-8 JSON
at `NOCTURNE_HOME/cloud-backups/<receipt-id>.json` beneath a mode-0700
directory and fsyncs both file and directory. Schema version 1 contains
exactly: `schema_version`, `receipt_id`, aware UTC `created_at`,
`reason=pre_migration`, `provider=gcp_cloud_sql`, `project`, `region`,
`instance`, `database`, `operation_id`, `backup_id`, `description`,
`status`, `type`, `location`, `enqueued_time`, `start_time`, and
`end_time`. Cloud or receipt failure stops before Alembic and exposes only
a safe owner-language error, never credentials or raw command output.
These receipts are evidence and locators, not backup archives. M2N neither
restores nor deletes a Cloud SQL backup and never claims automated cloud
recovery; cloud restore remains an explicit human operation.

MULTIMODALITY (named future surface, v2.37 — so it is never accidental
scope): today NOCTURNE is text end-to-end — envelope payloads, chat UI,
and memory units (one 1536-dim text embedding space) are all text. The
broker and the model policy are already multimodal-READY (the frontier
table is full of vision models; OpenRouter passes image parts), so the
rungs, in order of lift: (1) INPUT passthrough — attach an image, carry
content parts through the C.7 envelope to the broker — UPGRADED TO
COMMITTED (owner, v2.38: 'I must be able to copy in images and have my
agents review them'); ships with M3's harness-parity wave beside browser
use and command-line tooling, the modern-harness basics; (2) OUTPUT rendering — model-generated images in the chat
pane + storage; (3) MULTIMODAL MEMORY — the deep one: what is the
embedding space, atomicity law, and curation story for an image memory?
Joins the horizon holes beside embedding-model migration. Nothing in law
forbids any rung; nothing builds one without a packet.

**Rejected:** mono-repoing the workspace (relay law is repo-shaped and
working); container-registry distribution as the primary path (wheels +
source-deploy are simpler and registry-free); shipping the Garden
(users need the product, not our scaffolding).
*Verification:* a fresh machine with Docker + pipx and ONE OpenRouter key
reaches a working browser chat via the three local commands; a --dry-run
deploy prints a complete, correct plan without mutating anything.

### ADR-020 — Shared Palaces: combining memory across people

**Problem Tree:** P1.1.

**Status: ACCEPTED as HORIZON (2026-07-20; D.2 047)** — end-state design
accepted now, like ADR-011; BUILDING IS FORBIDDEN before real
multi-tenant identity exists (M4 headline; ADR-011's M5 multi-principal
soak is its stress test). Required M1–M3 schema footprint: NONE — the
existing design already carries it (verified below).

**Motivation.** Different people working the same project should be able
to pool chosen memories into a COMBINED palace — the remembering
orchestra, scaled from a soloist to a section. The owner's requirement:
each user CHOOSES what to contribute; combination is consent-based and
selective, never automatic.

**Decision:**
1. A shared Palace IS a principal. `principal_id` already scopes every
   table; a team palace is simply a principal that multiple humans'
   harnesses may address. No parallel machinery.
2. CONTRIBUTION = COPY-WITH-LINEAGE, never move: contributing creates a
   new unit in the shared Palace whose first revision's `parent_uid`
   points at the contributor's source `rev_uid`. Because rev_uids are
   globally unique ULIDs in one revision DAG, CROSS-PALACE LINEAGE
   ALREADY WORKS WITH ZERO SCHEMA CHANGE — provenance (who gave what,
   from where) is the existing editor/machine/lineage record.
3. SELECTIVE AND REVOCABLE. Contribution is an explicit act through the
   panel's existing list/filter machinery; originals never leave the
   personal Palace; revocation tombstones the contributed copy (the
   shared Palace obeys the same never-delete law).
4. COLLISIONS ARE ALREADY SOLVED MACHINERY. Two teammates contributing
   overlapping knowledge hit the write-time dedup bands like any create;
   the M3 curator's merge queue and ADR-011's single conflict taxonomy
   (union / fast-forward / review) govern the rest. Combining palaces and
   reconciling divergent replicas are THE SAME MECHANISM — ADR-011's
   per-unit three-way merge, applied across people instead of machines.
5. INJECTION FROM THE UNION. An agent on a shared project draws
   candidates from the personal Palace plus subscribed shared Palaces;
   scorer v-current runs unchanged over the union; the gate shows
   PROVENANCE on every card (whose memory this is). Per-source trust
   weighting is a future scorer feature, decided by replay evidence.
6. The gating dependency is IDENTITY: real multi-tenant auth (per-human
   principals, membership, tokens) replaces M1's single bearer before any
   of this ships. Federation across separate spine deployments is
   deliberately open — OQ-19.

**Rejected:** automatic whole-palace merging (consent is the point);
moving memories out of personal palaces (contribution must be reversible
and non-destructive); a separate "team memory" schema (a shared Palace
that isn't a Palace would fork every downstream mechanism).
*Verification (at build time):* contribute → provenance lineage resolves
to the source revision; revoke → tombstone hides it from the union
without touching the original; two overlapping contributions → dedup
band fires; gate cards show contributor provenance.

### ADR-021 — Memory write law & the attention budget

**Problem Tree:** P1.5.

**Status: ACCEPTED (2026-07-22; D.2 053)** — governs how memories are BORN
when agents (not humans) write them, and what memory management may cost a
working agent. Owner doctrine: err on the side of simple rules.

**Motivation.** At write time three goods compete: CAPTURE (never lose a
lesson an agent spent real compute earning), ATTENTION (a build agent's
context belongs to the build), and TRUTH (a deduplicated, consistent
palace). Only truth is deferrable: an admitted duplicate costs fractions of
a cent and mild scoring noise until a curator reconciles it; a lost lesson
or a burned context window is unrecoverable. And the writer is the
least-informed actor in the system about the corpus (it has thread context,
not citation history or lineage) — so it must never operate on the corpus.
This asymmetry decides everything below.

**Decision — admit-then-curate:**
1. **Fire-and-forget saves for build agents.** save_memory costs the agent
   its output tokens and a one-line ack, nothing else. No neighbor reading,
   no blocking dialogs, no 409 dance. Mode-aware surface (ADR-012): 
   interactive/chat kinds keep the richer C.6 neighbor dialog
   (edit-over-create); build/conduct kinds get fire-and-forget.
2. **Deterministic spine-side triage at the door** (no LLM in the write
   path): hard-duplicate → COALESCE AS REINFORCEMENT — no new unit; the
   existing unit's stats record an independent re-derivation (an importance
   signal feeding f_freq and the M3 promotion blend; the collision IS the
   signal). Near-similar band → ADMIT the unit and auto-stamp a
   `possible-dup` typed edge to its neighbors — pre-populating the Palace
   Health Report's duplicate-candidates list (curator food). Distinct →
   admit clean. The reinforcement stats bump fires in EVERY mode (v2.14):
   interactive agents additionally get the dialog, build agents just the
   ack — what varies by mode is what the agent SEES, never what the log
   LEARNS. /remember hitting a hard-dup confirms to the user as
   "already known — reinforced", never as an error.
3. **Async micro-verdicts never block.** A cheap spine-side model MAY later
   upgrade stamps to queued merge/supersede proposals (curator taxonomy);
   the writer is never waiting.
4. **Birth-time deconfliction for ambient extraction (M2).** TRIGGER
   (clarified v2.34 — M1 threads never formally close and compaction is
   M3): extraction fires on the explicit thread ARCHIVE/CLOSE action, with
   an idle-hours fallback (config) whose cheap-model pass reads the M2D
   durable transcripts; compaction integration arrives with M3. The
   thread-born queue card renders as a RACK-MODULE thread-end card in M2
   (the Deck's predecessor — same contract, promoted to the Cube face at
   M3). Extraction otherwise rides the thread-close/compaction moment IN-CONTEXT — the closing model
   emits three outputs: working summary, open loops, durable memory
   candidates (≤5 per thread, atomic, keyworded) — with a cheap-model
   fallback pass for threads that die unclosed. Candidates flow through the
   standard create/dedup pipeline; each approval-queue card shows the
   candidate, its machine-fetched neighbors, and an extractor-proposed
   verdict (new / merge / supersede / contradict); approving enacts the
   verdict. The extractor and the curator are the same role at different
   moments with opposite blind spots (thread context vs corpus context) —
   same taxonomy, same tools, complementary vision. CANDIDATE-status units
   are INVISIBLE to injection and search until approved (v2.14) — they
   exist only in the queue; an unapproved candidate must never shape an
   agent. REJECTED candidates are tombstoned-as-rejected and logged with
   human actor class (v2.14) — a rejection is Chrysopoeia signal about
   extraction quality, never a silent discard.
5. **The 10% attention budget (DESIGN PRINCIPLE).** A build agent spends at
   most ~10% of thread tokens on memory: the injected block (≤5% via C.3),
   tool traffic (near-zero under fire-and-forget), optional search_memory
   pulls. Not yet a metered invariant (owner call, D.2 053) — but every
   memory-facing design decision is tested against it: injection stays PUSH
   (the gate curates; the agent never shops), saves stay fire-and-forget,
   and no build agent ever blocks on a memory dialog. Revisit metering
   (run.usage per-agent %) if memory creep appears.

**The approval queue — unified law (owner, v2.25; resolves the M2 agenda's
queue item):** ONE queue per palace; every entry is a typed card carrying
its verdict payload (extraction candidate, curator proposal, Symphony
digest, seed batch). TWO RENDER SURFACES, ROUTED BY BIRTHPLACE:
(a) THREAD- AND RUN-BORN cards ride their origin's DECK card — the
flashcard shows the agent's final post needing response up top and the
memory approve/deny list beneath (locality: reviewing a candidate beside
the thread that produced it is the cheapest possible review; a Symphony
digest rides its run's completion card the same way). PASSIVE APPROVAL:
the owner need not act — items ACTUALLY RENDERED IN THE VIEWPORT that
are passed without denial are APPROVED when the card resolves;
visibility is literal (below-the-fold and collapsed-group members never
passive-approve; they flow to the module as pending). This is the gate's
grammar applied to the write door: enter-unless-denied mirrors
stay-unless-removed. (b) CORPUS-BORN proposals (curator verdicts, seed
batches) live in a separate PALACE QUEUE MODULE — pull-only, explicit
action; curators never inject into deck cards (their proposals belong to
no live thread; groundskeepers don't interrupt dinner). EXCEPTIONS AND
SIGNALS: CONTRADICTION cards never passive-approve — always the module,
always an explicit human tap, no expiry ever; grouped batches require
explicit batch action. Every decision logs `approval_mode: explicit |
passive` — explicit approve/deny is strong signal on producer quality,
passive approval is weak (the C5 class discount), and passively-admitted
units are stamped for the curators' slop sweep as its priority lane. NO
EXPIRY TIMERS: deck-ridden items resolve with their card; module items
persist and shrink structurally via earned autonomy. Nothing pending is
ever visible to injection or search (standing law), and the queue never
notifies — a Vitals depth count and the module are its whole presence.

**Folded amendment (A-032, v2.82)** — enacted by M2H against [ADR-021 clause 4
and unified queue; ADR-022 kinships], node(s) P1.4, P1.5; law text verbatim
from AMENDMENTS.md:
Add `candidate` to memory-unit status and add append-only
`memory_edge` plus `approval_queue_item` and `approval_decision` rows.
A queue item owns one candidate unit, one birthplace thread, ordered
machine-fetched neighbor IDs, zero or more implicated target IDs, and one
proposed verdict: `new`, `merge`, `supersede`, or `contradict`. Its state
is `pending`, `approved`, or `rejected`. Candidate creation runs under the
same principal advisory lock, embedding provider, body/label limits, and
cosine thresholds as ordinary creation, but deduplicates against both
ACTIVE and CANDIDATE heads. A hard duplicate creates no candidate or queue
row; a similar match is admitted and appears in ordered neighbors.

POST `/v1/extractions` accepts one thread-born batch of at most five
atomic, nonblank, keyworded candidates and returns the admitted queue
cards. Each candidate supplies its proposed verdict and implicated target
IDs; targets must be same-principal ACTIVE units and must be present in
the machine-fetched neighbor set. GET `/v1/approval-queue` reads pending
cards, optionally narrowed to birthplace thread. POST
`/v1/approval-queue/{item_uid}/decisions` accepts `approve` or `deny`,
`approval_mode` explicit or passive, and actor class human or passive.
Deny is always explicit/human, tombstones the candidate with revision
reason `rejected`, and appends the decision. Approve activates the
candidate and appends the decision. `new` changes nothing else; `merge`
and `supersede` revision-tombstone their implicated targets and append
respectively `merged_from` or `supersedes` edges from the new unit;
`contradict` leaves targets active and appends `contradicts` edges.
Contradiction rejects passive mode mechanically. A repeated identical
decision is idempotent; a conflicting second decision is 409. These are
the minimal deterministic verdict mechanics M2H needs, not the M3 curator
agent, report, autonomy, generic tool SDK, or graph retrieval.

Harness extraction reads the durable M2D transcript on explicit
`POST /v1/threads/{thread_id}/archive`; a daemon idle sweep uses the same
path after configurable positive `EXTRACTION_IDLE_HOURS`. The model emits
working summary, open loops, and at most five candidate drafts. Archive is
idempotent per transcript tail. Failed extraction leaves the thread
unarchived and returns a visible service error; an idle failure is logged
and retried without blocking chat. The thread-end rack card shows the
final assistant post above its candidate list. An IntersectionObserver
marks an individual uncollapsed, non-contradiction row seen only when its
whole row intersects the literal viewport. Resolving the card explicitly
denies selected rows and passively approves only those marked seen;
unseen, collapsed, and contradiction rows remain pending for the Palace
queue module. Passive sends actor class passive and approval_mode passive;
taps send human/explicit. No timer resolves queue items and no queue event
notifies the owner. The law-bound module declares CURRENT by default,
follows the shared thread selection, and persists GLOBAL|CURRENT only as
rack layout presentation state.

**Multi-agent write rules (Symphony / sub-agents; owner's simple rules):**
- R1. ALL agents may save memories, always fire-and-forget.
- R2. While sub-agents/attempts are active their saves are STAGED, not
  corpus: search and injection see the long-term corpus PLUS the agent's
  OWN staged additions only. Sibling staging is invisible (attempt
  independence — judge-panel diversity is never contaminated mid-run).
- R3. At run close the JUDGE is the memory gate (the agent-side mirror of
  the human gate): the WINNING agent's staged memories promote to the
  long-term corpus through the standard birth pipeline (triage, stamps,
  queue). The judge may additionally extract negative-result lessons from
  pruned branches ("approach X fails because Y" — the most reusable
  artifact a search produces) as part of its verdict duties. Unpromoted
  staging is archived with the run — never deleted, never corpus.
- R4. Identical lessons re-derived by k branches coalesce at promotion as
  k-way reinforcement (rule 2 of the triage, applied across branches).
- R5. Deterministic per-thread save rate cap (spine-side) against memory
  storms; excess is staged and flagged for curator review — data is never
  dropped, attention is never spent.

**Provenance — the constructed agent id.** Memory birth records gain
`origin_agent`: a materialized-path lineage id `run_id/root[.i[.j…]]`
(premiere = `root`, its attempt 3 = `root.3`, that attempt's sub-agent 1 =
`root.3.1`; generation = path depth). Own-staging visibility is an exact
match; promoting a winning branch is a prefix match; audits walk the
subtree with LIKE — the parent-pointer shape, materialized as a string.
Schema lands with the M3 staging migration (C.2 unchanged until then).
origin_agent joins machine_id/editor/thread_origin/origin_path as birth
stamps, and provenance-aware curation is the standing mitigation for
untrusted-content poisoning (an agent that read a hostile page and saved a
"fact" is traceable to the thread and branch that did it).

**Lifecycle closures (v2.11; found by red-teaming the law across harness
configurations):**
- C1. STAGED REINFORCEMENT HOLDBACK: when a staged (mid-run) save
  hard-collides with a CORPUS unit, the reinforcement event is held in
  staging and folds into corpus stats only if the branch wins — a losing
  branch must never inflate corpus importance.
- C2. QUEUE-AWARE DEDUP: extraction candidates dedup against the corpus
  AND the pending approval queue — a long thread that compacts five times
  must not queue five copies of the same lesson.
- C3. Staged writes do NOT tick `curator_write_trigger`; promotions do
  (curators scan the corpus, not scratch space).
- C4. Symphony promotions enter the approval queue as a PER-RUN GROUPED
  DIGEST, judge-ranked — a big search dumps one reviewable card set, not
  dozens of stragglers.
- C5. SIGNAL PROVENANCE CLASSES (extends the v2.7 hygiene rule): every
  gate/queue event logs its actor class — human / judge / passive — and
  the Chrysopoeia may weight classes differently (human strongest);
  unattended-thread passive keeps must never drown attended-thread labels.
- C6. SUPERSEDED-SINCE-INJECTION: when another machine or a curator
  supersedes a memory that a live thread already has injected, the block
  is not yanked mid-thread (ADR-002 stands); the panel/gate marks it
  "superseded since injection" on next render.

**Rejected:** squash-at-write (the least-informed actor destroying
information irreversibly; wrong merges cost more than temporary dupes);
reject-at-write / blocking 409s for build agents (spends capture and
attention to buy deferrable truth); direct mid-run corpus writes from
attempts (counterfactual pollution from losing branches + independence
breakage); a metered budget invariant now (principle first, meter if creep).
*Verification (at build time):* build-agent save returns in one round-trip
with no neighbor payload; hard-dup save increments stats without creating a
unit; near-similar save lands with a possible-dup edge visible in the next
health report; staged sibling memories never appear in each other's search
or injection; judge promotion moves only the winning prefix; rate-cap
excess lands staged+flagged, not dropped.

### ADR-022 — The Curators

**Problem Tree:** P1.4.

**Status: ACCEPTED** (doctrine + architecture owner-declared 2026-07-22,
D.2 050–052; operational details PROPOSED until the M3 build). Consolidated
here from ADR-004's accretions in the v2.12 editor pass — content-preserving;
the law below is D.2 050–052 verbatim in substance.

**The problem — memory rots four ways, and only time reveals it.** The gate
protects each THREAD (every injection auditable, reversible); nothing
protects the CORPUS over time. Left alone a palace accumulates: REDUNDANCY
(near-duplicates saved weeks apart sail past write-time bands), STALENESS
(facts expire and nothing notices), CONTRADICTION (the world changes; two
active memories assert conflicting things — and injection might surface
either), and MISVALUATION (load-bearing memories sit buried while mediocre
ones ride frequency). Write-time triage (ADR-021) catches only what is
visible at the door with thread context. Everything time reveals belongs to
the curators. Division of labor: WRITERS OPTIMIZE RECALL (never lose a
lesson), CURATORS OPTIMIZE PRECISION (keep the corpus true). Boundary with
the learning loop: curators tend the CONTENTS, the Chrysopoeia tunes the
ALGORITHM — curators ask "is this memory in good condition?", the
Chrysopoeia asks "did we show the right memories?" Curators never touch a
scorer weight.

**Doctrine (owner, v2.8):**
- **Surgeons.** Minimally invasive; target the ROOT problem and let the
  system sort out the symptoms (supersede the bad seed memory, don't patch
  each conflicting derivative); prefer the smallest intervention that
  restores health — a typed edge over an edit, an edit over a merge, a
  merge over a rewrite; when in doubt, queue rather than cut.
- **Palace-anchored.** The exception that proves the ADR-010 movement law:
  their work IS the palace, so they run at the palace's location —
  spine-side scheduled job at scale; local /maintain_memory acceptable in
  the first M3 era.
- **Triggers:** every N active-unit writes since the last pass (config
  `curator_write_trigger`, default 25; staged writes don't tick it —
  ADR-021 C3), plus cron, /maintain_memory, and INJECTION PRESSURE
  (defined v2.13; the founding doc's undefined "80%-budget trigger"):
  when injected blocks saturate ≥80% of the C.3 token budget in ~8 of
  the last 10 threads, schedule a pass — crowding is a curation signal,
  measured from injection_event with no new machinery.
- **Slop removal is curator work:** units that are vague, non-atomic, or
  demonstrably never useful (zero citations plus repeated removals across
  a full era) are proposed for quarantine → tombstone through the same
  review queue — never hard-deleted (append-only law).
- **Written standard procedures:** curator operations follow versioned SOP
  documents (the curation counterpart of B.6 rule 8) so every pass is
  auditable against its procedure and procedure changes are reviewable
  diffs, not prompt drift.

**Architecture (owner, v2.9) — diagnose deterministically, operate
surgically.** A curator pass is a smart LLM over a DETERMINISTIC TOOL SET:
1. A scripted diagnostic pre-pass — code, not LLM — generates a full
   PALACE HEALTH REPORT: cluster map, duplicate/contradiction candidate
   pairs (pre-seeded by ADR-021's possible-dup stamps), staleness and
   never-useful lists, keyword coverage, stats deltas since last pass;
   versioned format.
2. The curator agent MUST read the report before intervening — no
   intervention without diagnostics.
3. All writes go through the deterministic tools (merge_units, add_edge,
   propose_quarantine, split_unit, …) which enforce CAS, lineage, and
   queue rules mechanically — the LLM supplies judgment at the VERDICT
   LAYER only; it can call diagnostic tools ad hoc but can never
   free-hand a write.
Giving curators the tools and context to genuinely understand the palace
is acknowledged HARD — the health report format is a first-class design
surface, not an afterthought.

**Operations (M3):** semantic clustering over embeddings → consolidation
proposals with the four-verdict taxonomy (v2.7): **merge** (new unit with
`merged_from` lineage, sources tombstoned), **new** (keep separate),
**contradict** (two active memories assert conflicting facts → review
queue; truth calls are never automatic), **supersede** (newer memory
replaces older → typed edge + demote/tombstone proposal); plus staleness
review and the promotion/demotion pass. **Promotion criteria (v2.7):** the
score blends reuse success (citations/kept outcomes from the log),
stability (survives revisions/time), and manual signal (pins, add-backs) —
never raw injection frequency alone (frequency alone promotes nonsense);
the mechanism it drives is ADR-004's pin flip. Curators may maintain a
TYPED EDGE OVERLAY (supersedes / contradicts / relates-to) as rows in the
same database — used for 1-hop expansion during consolidation and audit,
never as first-pass retrieval (the graph is an overlay, not the
architecture). Curators run as their own agent (editor='maintenance')
under the same CAS rules as every writer; their actions log with actor
class per ADR-021 C5 and never masquerade as human preference signal.

**Splitting (v2.15) — semantic, lineaged, never summarized.** Splits
happen at three doors, always agentically: the WRITER at save time (C.6:
split before saving — it holds the context to split well), the seed
SPLITTERS at ingestion (ADR-019 clause 4), and curators via split_unit.
Split points are SEMANTIC, never mechanical token-chopping: each
resulting unit must stand alone — one claim, independently
comprehensible (no unresolved references), independently retrievable
(its own label and keywords). Summarizing to fit the cap is FORBIDDEN
(decision 015: information loss chosen at the least-informed moment).
LINEAGE: a split is copy-with-lineage times N — each child's first
revision carries parent_uid → the source revision (the same mechanism as
merged_from and ADR-020 contribution), siblings are auto-stamped
relates-to edges (they were one memory; co-relevance is likely), and the
source is tombstoned-as-split RETAINING its stats history (children
start fresh and earn their own — history is preserved in lineage, never
copied as unearned importance). Split families are visible in the Health
Report and to the scorer's future sibling-aware features; the algorithm
and curators can always see that one memory became many.

**Kinships (routing, no new law):** the birth-time extractor is this same
role at a different moment with the opposite blind spot — thread context
without corpus context (ADR-021 clause 4; same taxonomy, same tools). The
judge's run-close memory gate is the run-scoped sibling (ADR-021 R3).
Combining shared Palaces reuses curator merge machinery wholesale (ADR-020
clause 4). Seed ingestion's splitters are curator-style workers (ADR-019
clause 4).

**Autonomy (RESOLVED OQ-4, v2.13; D.2 056): EARNED PER VERDICT CLASS.**
Everything is queued in the first era. Per verdict class (high-similarity
merge, supersede, staleness demotion, slop quarantine, …) the system
tracks the human approval rate; a class GRADUATES to auto-execute when
approval runs near-unanimous over a real sample (guideline: ≥50 decisions
at ≥98% — constants tunable config, not law). Graduated classes report
passively — a "curator merged 3 units" line in the Vitals feed, watchable
never demanding — and DEMOTE back to queued if rejections return.
CONTRADICTION never graduates: truth calls are never automatic (standing
operations law). Rationale: subsumes the threshold-split idea (a
similarity band is just a class), converges to full economy without ever
ASSERTING trust, is reversible at every step, and mirrors the system's
founding philosophy — log first, learn second; curators get autonomy the
way the scorer gets weights. ANTI-NAG LAW (v2.14): a rejected proposal is
REMEMBERED — the rejection stamps the implicated unit/pair (a rejection
record the health report reads) and curators MUST NOT re-propose the same
verdict on it unless material change occurs (a new revision on an
implicated unit). A queue that re-asks answered questions violates
Invariant 14 as surely as a popup.

*Verification (at build time, per B.6):* health report is deterministic
(same palace state → byte-identical report, double-run diff); a free-hand
curator write (bypassing the tool set) is refused; every intervention
cites the report section that motivated it; merge produces merged_from
lineage and tombstoned sources; contradict never auto-resolves; a
promotion/demotion pass logs its blended score inputs.

### ADR-023 — The Plugin System

**Problem Tree:** P2.5.

**Status: ACCEPTED** (extracted from ADR-018 in the v2.23 editor pass,
content-preserving: the rack is v2.0 law, D.2 041; composition, controls,
authoring are owner law 2026-07-28, D.2 061-063). Build milestones
unchanged: composition principle + registry bind all UI work now;
reference plugins M2; public SDK + contributor skill M3.

**Motivation (owner; NATES_VISION §16).** The intuition is Ableton:
everyone is making music, but they each want different ways to SEE and
MODIFY things while making it. The shipped interface is version one of
the default opinion — a factory preset, not furniture. Visualizers AND
input modules, never just visualizers. General project rule born here:
DOCUMENT THE EDGES, DON'T POLICE THE INTERIOR.

**Decision:**
1. 4. THE RACK: layouts are savable per-mode SETS. Plugins (panels, console
   widgets, card renderers, stage faces) receive exactly THREE surfaces —
   the C.7 event stream, the query surface (+as_of), the selection bus —
   and no notify API exists: Invariant 14 is structural. Panel plugins =
   sandboxed iframes (postMessage bridge); face plugins = data-driven
   scene schemas (never raw renderer access). Plugin API versioned;
   churn contained per ADR-013.
2. GROUND-UP COMPOSITION (owner, v2.18). The shipped interface is not a
   program with a plugin slot — it IS plugins, all the way down: every
   rail, panel, strip, and face (chat pane, thread list, deck, context
   bars, the spend strip, memory graph, Cube faces) is a rack module
   built on the SAME public plugin API. The current mock is "version one
   of the default opinion" — a factory preset, replaceable like any
   Ableton set. DOGFOODING IS THE ENFORCEMENT: if a first-party surface
   needs an API the public one lacks, the public API grows; a private
   path is FORBIDDEN. LAW-BOUND SURFACES (the gate, boundary cards, the
   approval queue, the deck's demand right) are plugins too, but their
   ACTION CONTRACTS are law: an alternate gate plugin must render every
   card, expose every removal reason and add-back, emit every C.4
   outcome event, and suppress nothing. LOCAL FREEDOM, DOCUMENTED EDGES
   (owner, v2.19): what a user does in their own instance is their
   business — nothing polices local code. The duty is DOCUMENTATION:
   the MODIFICATION GUIDE ships with the SDK and marks every surface in
   three tiers — RECOMMENDED (visualizers, controls, themes, layouts:
   go wild), MODIFY WITH CARE (faces, deck arrangement), and
   DISCOURAGED-LAW-BOUND (gate, boundary cards, queue: here is the
   contract your replacement must honor, and here is WHY it exists —
   the training signal and the consent surface). The CONTRACT-
   CONFORMANCE test is a tool offered to everyone and a GATE only at
   the contribution door: plugins contributed back as shipped defaults
   or marketplace entries must pass it; law-bound replacements in
   shipped sets must pass it. This is a GENERAL PROJECT RULE beyond
   plugins: document the edges, don't police the interior. Extends the
   theme law upward: plugins compose the stage; contracts guard the
   meaning. The deck remains the only surface that may demand, whoever
   renders it.
3. CONTROL PLUGINS & THE PARAMETER REGISTRY (owner, v2.18). A second
   plugin class beside visualizers: CONTROLS — Ableton-device-style
   input blocks (knobs, sliders, XY pads) that WRITE. Enabling law: the
   PARAMETER REGISTRY — harness and spine declare every controllable
   parameter as a typed descriptor {id, label, type, range/options,
   default, scope: global|thread|run|agent-kind, authority:
   free-journaled | law-bound}. Controls BIND to descriptors and never
   free-hand a write (the curator-tools pattern again: judgment at the
   knob, mechanics in the registry). Every change is a journaled A-016
   event (who, when, old→new) — as_of-scrubbable, so knob history
   replays like everything else. First residents: per-thread MODEL
   PARAMS — temperature, top_p, top_k, max_tokens, reasoning effort —
   joining the M2 per-thread model selector (C.5), plus A-020's
   MODEL_INTELLIGENCE_FLOOR as a literal dial. Scorer hyperparameters
   (τ, top_k, budget, half-lives, weights) are LAW-BOUND descriptors:
   binding them requires the console's versioned-insert semantics — the
   hyperparameter console is hereby reclassified as the first first-
   party law-bound control plugin. Invariant 10 extends to controls:
   EVERY KNOB BINDS A REAL PARAMETER — decorative controls are
   forbidden; and clause 3's sanctity is untouched — visualizers still
   never write, and no control bypasses a wall, a gate, or the scorer's
   versioning law.
4. AUTHORING EASE & REFERENCE PLUGINS (owner, v2.18: "very easy").
   A plugin is a FOLDER: a manifest (name, version, class: visualizer |
   control | face | theme; declared streams; declared parameter
   bindings; layout hints) plus an entry html/js. The SDK is small by
   law — subscribe(stream), query(+as_of), selection bus, bind(param),
   theme tokens auto-injected — and dev mode is drop-the-folder-in,
   hot reload. Capability floor: no network egress, no notify, no
   writes outside bound descriptors; the hostile-plugin verification
   extends to controls (a write to an unbound or law-bound descriptor
   is refused MECHANICALLY, not by convention). Distribution rides the
   theme path. ACCEPTANCE BAR: a competent user ships a working
   visualizer in an afternoon starting from the reference examples.
   REFERENCE PLUGINS, shipped first-party and doubling as the tutorial:
   (a) the SPEND STRIP (v2.17 Vitals presentation) — the visualizer
   reference; (b) the MODEL DEVICE (owner) — an Ableton-esque device
   strip showing the thread's resolved model (the A-020 slug) with
   registry-bound knobs for its parameters — the control reference.
   Both replaceable by user versions; the defaults are presets, not
   privileges. RESIZE LAW (owner, v2.19): rack modules are RESIZABLE
   within declared bounds — the rack is a grid; a plugin's manifest
   declares min/preferred/max size in GRID UNITS (the Ableton device
   model: constrained, not freeform); the rack owns drag/resize/dock
   and delivers the plugin its rectangle via SDK resize events; panel
   iframes simply reflow, stage faces receive viewport allocation.
   CONTRIBUTOR SKILL (owner, v2.19; sharpened v2.20): the repo ships a
   `nocturne-plugin-contributor` AGENT SKILL (the SKILL.md open
   standard, per ADR-007) written for an agent that KNOWS NOTHING about
   this repo — fully self-contained, motivating every load-bearing
   decision it asks the author to respect (why exactly three surfaces,
   why sandboxed, why no notify exists, why writes only bind registry
   descriptors) so the agent builds effectively rather than compliantly.
   Its HEART IS THE DATA CATALOG: every subscribable stream, canonical
   view (v_spend_rate and kin), event shape, and parameter descriptor —
   each with a real sample payload (the samples principle) — because
   exposed data is what builds visualizers AND input modules. It covers
   BOTH plugin classes as equals: a full visualizer walkthrough (the
   spend strip) and a full control walkthrough (the model device),
   plus the manifest schema, SDK surfaces, modification guide, and the
   contribution checklist (conformance test, palette validation,
   B.6 r8 screenshots) — so any agent, in any harness, can be asked
   "build me a burn-rate gauge" or "build me a sampler-style knob rack
   for my model params" and produce a correct, contributable plugin.
   Lands with the public SDK. Milestones: the composition principle and registry bind
   all UI work immediately; spend strip + model device land M2 (with
   Vitals and the per-thread selector); the public SDK + third-party
   authoring surface + contributor skill land with the M3 Cube buildout.

5. SCOPE: GLOBAL or CURRENT (owner, v2.40). Every rack module —
   visualizer and control alike — carries a scope toggle: CURRENT
   follows the shared selection bus (ADR-018 clause 2's one-selection
   law, generalized from Cube faces to the whole rack): navigating
   ANYWHERE — a thread click, a project jump, a face — turns every
   CURRENT module with you, one stage refocusing as one organism.
   GLOBAL pins the module wide (all agents, all projects) and ignores
   the turn — the ambient walls (total burn, palace vitals) you chose
   to keep peripheral. Motivation: CONTEXT COHERENCE — switching
   projects must never mean re-pointing five panels. The toggle is
   PRESENTATION STATE: it persists with saved layout sets, never
   through the parameter registry; each manifest declares its default
   scope. Existing modules (the spend strip) adopt the toggle at their
   next touch.
   R21 SPATIAL LINKING (blessed D.2 114b) supersedes this toggle at the
   module family's build — proximity frames within a layer share one
   selection, GLOBAL stays as the one deliberate escape, layers are rooms;
   see ADR-018's Reconciliation. The toggle remains law until that build.

**Folded amendment (A-034, v2.82)** — enacted by M2J against [ADR-023 clauses
3 and 5; C.5; v2.26 resolution points], node(s) P2.5, P3, P4; law text
verbatim from AMENDMENTS.md:
The M2J Harness registry declares exactly these free-journaled thread
descriptors: `model.slug` (nonblank OpenRouter model string),
`model.temperature` (nullable number, 0..2), `model.top_p` (nullable
number, 0..1), `model.top_k` (nullable integer, 0..500),
`model.max_tokens` (nullable integer, 1..131072), and `model.effort`
(nullable option: none|minimal|low|medium|high|xhigh). Null means inherit
the provider default and is every model-parameter descriptor's default.
The selector's displayed default is the thread's resolved slug. The
superseded A-020 environment floor is not resurrected: A-021's policy
grammar remains configuration, and PLAN M2J's delivered controls are the
per-thread selector and five request parameters.

Extend ADR-023's public rack query surface with resource `parameters`,
requiring a thread UUID for CURRENT. A live result is
`{thread_id,as_of,resolved_model,descriptors,values,changes}`; descriptors
carry the clause-3 fields, values contains the six current values, and
changes is ordered by timestamp then event id. `as_of=now` reads current
state; an aware ISO-8601 timestamp replays daemon-lifetime registry events
at or before that instant. A timestamp earlier than the retained process
history is still an honest replay from descriptor defaults and the
thread's first resolution; M2D's capture-only journal is not served back.

The public control action submits
`{module_id,thread_id,parameter_id,value}`. The host and daemon both require
the module manifest to bind that exact descriptor. A successful write
validates the descriptor, atomically changes thread state, and publishes
one C.7 `parameter.change` event with event id, actor `human`, timestamp,
old and new values, and scope/thread identity; transcript capture makes
the event durable. Null resets a request parameter to provider inheritance.
`model.slug` invokes the existing `resolve_named` seam, preserves the five
request overrides, starts a new stickiness epoch, and emits the standing
`model_change` event as well as `parameter.change`. Writes while that
thread has a live run are refused so a request cannot change underneath
execution.

Any unknown, unbound, law-bound, invalid, or busy write changes nothing
and publishes one C.7 `parameter.refused` event with the attempted id,
module id, timestamp, and stable reason; values and credentials are not
copied into refusal events. The MODEL DEVICE manifest is a control plugin
bound only to the six descriptors above. CURRENT follows the shared thread
selection and may write. GLOBAL shows the same registry descriptors and
inherited defaults without a thread target and is read-only until a real
global descriptor ships; the GLOBAL|CURRENT toggle remains saved rack
presentation state and never enters the registry.

**Rejected:** per-visualizer data plumbing (the three surfaces are the
whole API); plugin notify capability (would re-open the attention wall);
privileged first-party internals (dogfooding law, clause 2); freeform
window resizing (grid units keep racks tidy — the Ableton device model).
*Verification (per B.6):* a hostile test plugin must be unable to
notify, write, or escape its rectangle; a hostile CONTROL plugin
attempting an unbound or law-bound write must be refused mechanically
with the refusal journaled; an alternate law-bound surface must pass
contract conformance (every action, every event, nothing suppressed)
before selection; every shipped knob must be traced to its registry
descriptor (no decorative controls); resize within manifest bounds
reflows without breakage at min and max.

### ADR-024 — The Cost Domain

**Status: ACCEPTED** (owner design sessions 2026-07-27/28; D.2 054, 057,
060 and the M2 agenda notes, consolidated here in the v2.23 editor pass).
Build: the M2 INSTRUMENT PACKET (ledger DDL, views, Vitals, spend strip);
spend walls ride M2/M3 config. Model-selection POLICY law lives in
A-020/A-021 (AMENDMENTS; fold into Part C deferred to the pre-M2 editor
pass — done: folded into C.5 at v2.82, editor pass IV) — policy chooses the price BEFORE spend; this ADR records the truth
AFTER; walls govern BETWEEN.

**Motivation (P4).** One human funds the workforce; input-token spend on
growing histories is the collapse vector the D2 breaker can only stop,
never economize. The cost domain has three tenses: the LEDGER knows the
past, VITALS shows the present, WALLS govern the future — one meter, many
lenses, and the owner's attention (the currency all of this defends) is
deliberately NOT a dollar column; it is measured in Vitals and protected
by the 10% principle. North-star metric (owner): TOTAL TIME TO
COMPLETION — injection quality buys back latency; per-pause delay is an
investment, not a tax.

**The seven cost families:** (1) intelligence — broker tokens in four
price classes (fresh input, cached reads, cache writes, output; plus
reasoning where billed); burned by chat, attempts, judges, extraction,
curation, /remember, seed-splitting, scouts (hygiene-excluded from
learning, never from billing), and embeddings (including future
re-embedding migrations — priced by this ledger BEFORE triggering);
(2) memory at rest — instance-hours (fixed), storage GB-months (grows
forever by the perpetuity directive), backups, index share; (3) service
compute — serving + every scheduled job (curators, re-fits, health
report, reconciliation), plus observability ingestion and secrets ops;
(4) network egress; (5) the M4 fleet — leases, spot, snapshots;
(6) fees — broker credit-purchase cut (attaches to deposits, own
product_type) and future paid tools; (7) attention — named, not
dollared.

**The ledger — spend_event, append-only, perpetual, RECEIPT-LINE NORMAL
FORM (owner vocabulary):** one line per price class; an LLM request
emits 3-4 lines sharing a `ref` (broker generation id) — that shorthand is
replaced by A-027's receipt-line rule, folded below (v2.82). Columns:
`event_uid` ULID PK (the id IS the timestamp) · `ts` · `product_type`
(what was bought: llm.request, llm.embedding, llm.fee,
infra.db.instance, infra.db.storage, infra.run.serve, infra.run.job,
infra.observability, net.egress, fleet.lease, fleet.snapshot,
ext.api.*) · `quantity_type` (price class: input_fresh, input_cached,
cache_write, output, storage, …) · `unit_of_measure` + `quantity` ·
`cost_usd` (nullable — billing arrives late) · `basis` (measured |
allocated | estimated — the honesty column; measured never masquerades
as allocated) · `behavior` (variable | fixed | step — each has its own
management lever: economize per action / right-size periodically /
capacity-alert before the jump) · `purpose` (building, extraction,
curation, judge, remember, embedding, scout) · lineage (`principal_id`,
`machine_id`, `origin_agent` path, `thread_id`, `run_id`, `prompt_id`,
`memory_id` nullable) · `model`/`provider`/`quantization` · `ref` ·
`meta` jsonb. THE GLOSSARY SHIPS WITH THE DDL in receipt language; the
intuition test is ROW-READS-AS-A-SENTENCE.

**Folded amendment (A-027, v2.82)** — enacted by M2A against [ADR-024 ledger
and broker seam], node(s) P4; law text verbatim from AMENDMENTS.md:
Spine exposes bearer-protected POST `/v1/spend/events` with body
`{events:[SpendEvent...]}` and response `{accepted:n}`. The nonempty batch
contains unique client-minted ULID `event_uid` values and every ADR-024
spend_event column. M2A accepts only `llm.request` and `llm.embedding`.
The write is atomic and append-only. Replaying an event_uid with every
normalized field equal is an idempotent acceptance; reusing one with any
different field returns RFC7807 409 and inserts nothing. `accepted` is the
number of supplied lines, including identical replays. The route uses the
standing application bearer unchanged.

Replace "an LLM request emits 3-4 lines" with: "A successful broker
request emits one receipt line for each nonzero price class the provider
reports, all sharing the broker response or generation id as `ref`.
Unreported and zero-value classes emit no synthetic line. When reasoning
tokens are reported as a distinct billed subset of output, subtract them
from the ordinary output quantity and emit a `reasoning` line; otherwise
they remain output and the raw provider detail stays in `meta`. Embeddings
emit `llm.embedding` input_fresh lines. If a broker supplies only aggregate
native USD, allocate that exact total across its lines, mark those lines
`basis=allocated`, and record the allocation source in `meta`; a directly
attributable broker amount is `measured`. Missing cost remains NULL and
never changes a measured token quantity into an estimate."

Harness mints chat receipt ids and submits the complete response batch
before terminalizing the turn. Spine's production embedding adapter mints
and appends its receipt before returning vectors to memory behavior.
Receipt-write failure fails the enclosing operation visibly; no
best-effort background queue is introduced. A missing broker generation id
uses the provider request-id header, or finally the first event_uid, as
`ref`, with that fallback named in `meta`.

**Grains are GROUP BYs, never new tables:** cost of a request = ref; a
query = prompt_id (embedding line + allocated db sliver); a thread, a
run, a sub-agent subtree (origin_agent prefix), a memory, a model, an
hour — all lineage-column rollups. CANONICAL DERIVED VIEWS (materialized,
minute-refresh, never authoritative): v_spend_rate (the strip's lanes),
v_thread_cost, v_run_cost, v_memory_cost, v_cache_efficiency
(cached/(cached+fresh) — proves KV stickiness earns its keep).

**Sourcing & self-audit (wave-2 scope clarified v2.34):** M2 reconciles
llm.* lines against the BROKER's own usage/credits API — the dominant cost,
zero new cloud resources; the GCP billing-export ingestion (which needs an
owner-granted BigQuery export dataset) is DEFERRED to a small owner-gated
setup packet. The broker seam writes llm.* lines
synchronously (A-020(e) grown up — broker cost is native USD; the broker
IS the meter); a daily reconciliation job ingests the GCP billing export
into infra.* lines AND reconciles ledger-sum against the actual invoice —
drift alerts. The D2 breaker stays the independent backstop, never a
consumer of this ledger.

**Folded amendment (A-037, v2.82)** — enacted by M2M against [ADR-024 sourcing
and self-audit], node(s) P4.1; law text verbatim from AMENDMENTS.md:
M2M uses the existing OpenRouter bearer only; it never requests a
management key. Spine reads cumulative current-key usage from official
`GET /api/v1/key` field `data.usage`, a finite non-negative USD number.
Reconciliation is active only when the configured embedding base URL is
the OpenRouter API and `SPINE_OPENAI_API_KEY` is present. Direct-provider
overrides remain honest `not_recorded`, never queried with the wrong key.

Add append-only table `spend_reconciliation` with ULID `event_uid` primary
key, aware database timestamp `ts`, provider `openrouter`, status exactly
`baseline|balanced|drift|unavailable`, nullable cumulative
`broker_usage_usd` and `ledger_cost_usd`, nullable
`broker_since_baseline_usd`, `ledger_since_baseline_usd`, and signed
`drift_usd`, non-negative `tolerance_usd`, non-negative
`unpriced_lines`, and nullable stable `error_code`. Successful numeric
fields use the ledger's 12-decimal USD precision. `unavailable` has null
USD observations except tolerance, may retain the current ledger
unpriced-line count, and carries only
`broker_unavailable` or `invalid_broker_response`; raw errors, response
bodies, headers, and credentials are never persisted.

One advisory-locked reconciliation reads broker usage and, in the same
run, sums every non-null `cost_usd` on `llm.request` and `llm.embedding`
rows and counts their null costs. The first successful observation is a
baseline. Every later success compares cumulative growth from the oldest
successful baseline:
  `broker_since = broker_now - broker_baseline`
  `ledger_since = ledger_now - ledger_baseline`
  `drift = ledger_since - broker_since`.
A negative cumulative growth is an invalid broker response. Status is
`drift` exactly when `abs(drift) > tolerance`, otherwise `balanced`.
Default `SPINE_RECONCILIATION_TOLERANCE_USD` is `0.000001`; default
`SPINE_RECONCILIATION_HOURS` is 24, both positive. The owned Spine
process runs once at startup and then at that cadence. Failures append an
unavailable observation and are logged; they never block receipts, chat,
embeddings, Vitals, or the next scheduled run. No GCP resource or billing
export is read or created.

Both Vitals scopes add Palace-wide `reconciliation` with status exactly
`not_recorded|baseline|balanced|drift|unavailable`, nullable `checked_at`,
the latest row's exact decimal-string observations, tolerance,
`unpriced_lines`, source `openrouter:/api/v1/key` when configured, and a
safe nullable error code. With no configured reconciler or no row, status
is `not_recorded` and observations are null. The rack renders this as a
compact watchable line: baseline, aligned, signed drift, temporarily
unavailable, or not yet recorded. Drift uses the theme's sole danger
color but creates no popup, notification, card, or new attention channel.

**Storage:** the palace's own Cloud SQL Postgres, segregated module (the
presence precedent: one spine, two schemas; extraction is a deploy
change). ~1M skinny rows/year = milliseconds for bucketed rollups.
BigQuery is the designated OVERFLOW path only if analytics outgrow the
instance (an append-only ULID table streams there trivially); AlloyDB
never (premium baseline for muscle we lack the problem for).

**Spend walls (prospective):** soft budgets ("this Symphony run may burn
$5"; "curation gets $2/day") with the ADR-015 posture — hitting a wall is
a BOUNDARY CROSSING, never a popup: the run pauses at the wall, the JUDGE
triages (release / prune weakest branches / stop), owner-worthy releases
surface as boundary cards. Checked against the canonical views; walls
are config, the ledger is the meter.

**Horizon:** `memory_id` on the ledger enables COST-PER-CITATION — "what
has this memory cost vs earned" as a curator slop/promotion signal; the
economics term rides the schema key.

**Rejected:** AlloyDB (above); a second data system before scale forces
it (two-secrets onboarding); mixing measured with allocated unlabeled;
metering the attention budget in dollars (attention is not a dollar).
*Verification (per B.6):* same rows → byte-identical views (double-run);
a seeded ledger/invoice mismatch trips the reconciliation alert; every
shipped dashboard lane traces to a canonical view over spend_event; a
sample row reads aloud as a sentence in the shipped glossary's terms.

### ADR-006 — Presence

**Problem Tree:** P2.1.

**Status: PROPOSED**

- Every harness instance emits presence events: `{agent_id, machine_id,
  session_id, event: spawn|cwd_change|read|write|idle|exit, path, ts}`.
- "Location" = CWD (badge) + heat-decayed working set (recently touched paths).
- Events flow to `/v1/presence` and fan out on SSE; Postgres keeps a
  short-retention (24h) presence table — presence is ephemeral, not memory.
- **Transport (RESOLVED, was OQ-6):** presence rides the spine ("one spine,
  two schemas") in a segregated module with its own tables; extraction to a
  separate channel later is a deploy change, not a rewrite.

### ADR-009 — Visualization suite & mobile

**Status: ACCEPTED (2026-07-07, direction) / details PROPOSED**

Three named visualizations, all fed by data the spine already logs
(Invariant 10: every visual channel encodes a real variable):

1. **Context Bars** (per agent) [M2] — port of the existing Code Puppy
   context visualizer: category breakdown, compaction threshold line,
   compaction table; extended with a "memory" category for the injected
   block's share.

   **Folded amendment (A-039, v2.82)** — enacted by M2R against [ADR-009 item
   1, ADR-023 clauses 1 and 5], node(s) P2.2, P2.5; law text verbatim from
   AMENDMENTS.md:
   Harness owns one daemon-lifetime context observation per thread, replaced
   only after a model request returns. The observation's `used_tokens` is the
   terminal broker response's per-request input-token count, never the run's
   cumulative usage. Its `context_tokens` is the exact context length on that
   request's immutable `ThreadModelResolution`. Before a completed request,
   no observation exists and the instrument says that it is waiting for the
   first model response.

   The four displayed categories are `system`, `history`, `memory`, and
   `tools`. Because the broker does not report this split, Harness labels it
   `estimated breakdown`: memory is the cl100k count of the exact injected
   MEMORY BLOCK; system and tools are cl100k counts of the owned capability
   instruction text and stable public tool definitions; history is the
   non-negative remainder of broker-reported input after those three values.
   If the three estimates exceed broker input, they are reduced in
   tools-then-system-then-memory order until the category sum equals the
   broker total. The aggregate used count and context limit remain measured
   facts even when category estimates are imperfect.

   The instrument draws a presentation-only threshold at 80% of the resolved
   context length, says `Compaction is not active`, and shows a compact table
   of category token counts. It never compacts, warns, blocks, predicts, or
   changes a run. CURRENT returns the selected thread's observation. GLOBAL
   returns every observed thread plus aggregate used and capacity totals; its
   bars and table show those category sums. Missing CURRENT selection or an
   unobserved thread is a truthful empty state. The first-party CONTEXT BARS
   module is compact by default beside Palace Vitals and uses only the public
   Rack query, selection, and scope surfaces.

   Customer-facing copy across the existing owner app must not expose Garden
   identifiers, packet names, amendment numbers, ADR references, scenario
   lineage, or implementation vocabulary as product explanation. Internal
   protocol values and developer-only diagnostics may remain stable; visible
   labels and recoverable errors are rephrased in owner language without
   changing their behavior.

2. **Ant Farm** (per machine / fleet) [M3] — directory tree with agent badges
   at CWDs, heat-decay trails on touched paths; fed by ADR-006 events.
   (Spawn/exit animations excised: encoded nothing — Invariant 10.)
3. **Memory Graph** ("tesseract") [M2] — force-directed graph of memory
   units. Encodings (authoritative): node size = stats.injections; color =
   kind; brightness = recency; halo = pin; ghosted = quarantined; border
   pulse = injected in a live thread. Edge type A: cosine similarity ≥
   cfg.graph_edge_sim (default 0.75) — the SAME similarity used by
   dedup/scorer, so the picture is honest. Edge type B: revision lineage
   (merged_from / edit trail). Click node → CAS-safe edit flow. (Lasso/bulk
   actions excised from M2; may return with M3 maintenance workflows, which
   would give bulk operations a purpose.)
4. **The Injection Console** [console core M2K; audition + what-if M2P]
   (owner, v2.42 — expanded from the hyperparameter console: "a
   visualization AND input module for memory injection score"; the
   interpretability heart, one module, four layers, scope-aware per
   ADR-023 clause 5):
   READ — for the selected thread/gate (CURRENT) or palace-wide
   (GLOBAL): every candidate's score DECOMPOSED — stacked per-feature
   contributions (each w·f plus b_m; the bars sum EXACTLY to the
   score), the τ line, the budget fill, above/below-line placement,
   near-misses in position; per-memory score history over time; the
   v2.41 accuracy series per scorer generation. Cards everywhere keep
   their Invariant-6 bars; the console is the deep dive.
   CONTROL (law-bound, unchanged): τ, top_k, budget, half-lives, weights
   — every ENACTED change INSERTs a new scorer_config version and
   activates it; in-place mutation forbidden; PROPOSED learner versions
   list here with their replay manifests and the one-tap.
   WHAT-IF (v2.42, new): knobs turn in PREVIEW first — the visible
   gate re-ranks live under tentative values, unmistakably marked and
   NEVER persisted; COMMIT is the versioned insert. Turn the knob,
   hear the mix, then print the record.
   AUDITION (v2.42, new — supersedes the "ghost gate" pitch): while a
   PROPOSED version exists, an overlay renders its counterfactual picks
   beside the incumbent's on live gates and the panel — faint for
   would-also-show, marked for would-drop — fed by scoring the same
   prepare under both versions. The incumbent governs what is actually
   injected until activation; the overlay is a PURE CONSUMER
   (memory-trace sanctity holds). Watch the challenger work, then tap.
   THE SCORE GRAPH (owner, v2.43; read layer, M2K): two series, one
   timeline — the ACTIVE generation's live trailing agreement rate
   (the sawtooth: dips with every override, because each manual
   add/remove is a moment the scorer was wrong) and each generation's
   held-out backtest point at proposal/activation (the rising edge).
   Annotations: activations, forces, retrains. All knobs LABELED AND
   NORMALIZED as decimals (weights on their natural simplex, τ as-is,
   half-lives/budget normalized beside raw). WHAT-IF is renamed
   SIMULATE (owner, v2.43), two speeds: INSTANT (knobs re-rank the
   visible gate, never persisted) and DEEP (one click scores the
   tentative set against held-out history — "this set backtests at
   96.4%" — via a small spine endpoint reusing M2F's replay engine;
   M2P). FORCE (owner, v2.43; informed-force v2.44): any set may be
   committed regardless of its backtest — but ONLY after DEEP SIMULATE
   has scored that EXACT set and the user has seen the number: never
   blocked, never blind. The score binds to the exact values; changing
   any knob stales it and disarms force until re-simulated. Delta shown
   honestly, force journaled — because
   forcing is THE EXPLORATION LEVER: it changes what is shown, which
   changes the evidence future retrains learn from; the user jumps the
   system to a new basin and the next optimize climbs from there.
   DEFAULT: the console ships in the factory set, DOCKED COMPACT — the
   score graph visible at a glance, knobs/simulate/audition on demand.
   THE LANDSCAPE (owner, v2.43; M3): backtest accuracy over any TWO
   chosen parameters rendered as 3D terrain (the rest held; the simplex
   makes pairwise weight trade-offs the natural slice), with the
   DESCENT TRAIL — the learner's path from incumbent to challenger —
   drawn on it. COLOCATED with the curator visualization on the M3
   stage: one memory world, two territories — the curators tending the
   CONTENTS beside the Chrysopoeia's TERRAIN. M2P ships 2D slices
   (accuracy-vs-parameter curves) as the interim.
   *Verification:* bars sum to the score exactly; preview never
   persists; the audition overlay never alters injection (double-score
   the same prepare, diff actual context: identical); scope toggle
   honors clause 5; deep-simulate returns the same score replay would
   (same engine, same holdout); a forced set's journal entry carries
   its honest delta.

   **Folded amendment (A-035, v2.82)** — enacted by M2K against [ADR-009 items
   3-4; ADR-023 clauses 3 and 5; Invariant 6], node(s) P1.2.3, P2.3, P2.5; law
   text verbatim from AMENDMENTS.md:
   Spine exposes bearer-protected POST `/v1/memory-graph/query` with
   `{principal_id,memory_ids}` where `memory_ids` is null for GLOBAL or a
   unique UUID list for CURRENT. GLOBAL returns every non-candidate memory
   head of that principal; CURRENT returns only requested same-principal
   non-candidate heads and names any omitted IDs. The live result is
   `{as_of,graph_edge_sim,nodes,edges,omitted_memory_ids}`. Each node carries
   the complete current MemoryUnit, `in_current_context`, and its ordered
   revision trail `{rev_uid,parent_uid,revision,ts,reason}`. Node size derives
   only from `stats.injections`; color only from kind; brightness only from
   updated_at recency; pin alone supplies a halo; quarantine is ghosted;
   tombstone is visibly struck/faded; CURRENT membership alone supplies the
   live-thread border pulse.

   Graph edges join returned nodes only. `similarity` carries exact cosine
   similarity and exists iff it is at least configurable
   `SPINE_GRAPH_EDGE_SIM` (default 0.75), using the stored embedding space.
   `lineage` carries the exact stored memory_edge type. A head with more than
   one revision also receives one self-edge `edit_trail` carrying its
   revision count; the inspector renders the ordered revision trail. Node
   click publishes a memory selection only: the existing Memory Panel owns
   the C.4 CAS edit. The Memory Graph remains a visualizer and never writes.
   Historical graph reconstruction is `historical_unavailable` until head
   status/stats/pin have replay-complete logs; current heads never masquerade
   as past truth.

   Spine exposes bearer-protected POST `/v1/scorer-console/query` with
   `{principal_id,thread_id,as_of}`. Null thread is GLOBAL; a UUID thread is
   CURRENT. It returns the typed law-bound descriptors, configuration and
   activation history, active version, PROPOSED learner versions with their
   replay manifests, accuracy points, and scored-candidate histories. GLOBAL
   includes the principal's events; CURRENT includes that thread only.
   Candidate points retain event_uid, time, version, score, rank, shown_as,
   outcome, six raw features, and seven decimal-string contributions:
   `sem|kw|time|proj|freq|hist = stored feature * that event version's
   weight`; `bias = stored score - exact decimal sum of those six`.
   Consequently the seven decimal contributions sum exactly to the stored
   score. Gate and Memory Panel cards render these weighted contributions;
   a unit with no scored event says `Not scored yet` rather than inventing
   bars. Accuracy is
   `100 * (holdout_dispositions - disagreements) / holdout_dispositions`
   from the learner replay manifest, or null/`not_recorded` when that
   denominator is absent. M2K may add the denominator to future M2F proposal
   manifests; existing proposal content is never rewritten.

   The console declares exactly these GLOBAL law-bound descriptors:
   `scorer.tau` number 0..1; `scorer.top_k` integer 1..8;
   `scorer.budget_tokens` integer >=1; `scorer.half_life_time_days` and
   `scorer.half_life_hist_days` numbers >0; and six numbers 0..1 named
   `scorer.weight.sem|kw|time|proj|freq|hist`, whose enacted vector must sum
   to 1 within 1e-9. Step hints are .01, 1, 128, .5, and .01 respectively;
   a step is presentation, never rounding authority. The console manifest
   binds all eleven IDs. Its scope toggle changes the READ candidate set;
   the descriptors remain visibly Palace-wide in both scopes and never
   become thread parameters.

   POST `/v1/scorer-configs` accepts the base version, the complete eleven-
   value set, and daemon-owned human/machine attribution. It validates the
   base is active, copies every non-controlled parameter unchanged, INSERTs
   one new scorer_config version, deactivates the prior version, activates
   the new one, and appends one scorer_activation row in one transaction.
   POST `/v1/scorer-configs/{version}/activate` accepts only an inactive
   learner-PROPOSED version and atomically makes it active. Every activation
   appends `{event_uid,version,previous_version,actor_class,machine_id,
   reason,changes,ts}`; exact old/new controlled values are journaled and a
   matching `scorer.change` C.7 event is published. Replays of an event UID
   are idempotent only when identical. No update mutates a configuration's
   weights or params. WHAT-IF re-ranking and AUDITION remain entirely M2P.

   For the spend strip's CURRENT scope, Spine adds bearer-protected GET
   `/v1/vitals/threads/{thread_id}`. It returns the existing Vitals shape,
   but spend lanes group trailing-hour authoritative spend_event rows for
   that exact thread and label `source_view="spend_event"`; palace gauges
   remain explicitly Palace-wide. GET `/v1/vitals` and its canonical
   `v_spend_rate` projection are unchanged. Every graph, console, and Vitals
   module persists its GLOBAL|CURRENT toggle only in rack layout state and
   every CURRENT query follows the shared selected thread without a second
   selector.

   **Folded amendment (A-047, v2.82)** — enacted by M2P against [ADR-009 item
   4; ADR-023 clauses 3 and 5], node(s) P1.2.3, P2.5; law text verbatim from
   AMENDMENTS.md:
   Spine exposes bearer-protected POST `/v1/scorer-simulations` with
   `{principal_id,injection_id,base_version,values,slice_parameter_id}`.
   `injection_id` is nullable; when present it names one same-principal
   frozen injection. `values` is the complete existing eleven-value
   ScorerValues contract. `slice_parameter_id` is one of those eleven
   descriptor IDs. The response contains `{simulation_digest,base_version,
   values,source_boundary,holdout_dispositions,accuracy_percent,
   incumbent_accuracy_percent,delta_percent,instant,slice}`.

   DEEP uses exactly M2F's hygiene, actor weighting, chronological whole-gate
   holdout split, binary target, pin rule, and percentage denominator. It
   scores the supplied weights and tau; top_k and budget remain manual
   selection controls and therefore do not alter A-031's binary replay
   referee. A changed half-life deterministically rescales each nonzero
   stored decay feature by recovering its elapsed age from that event's
   scorer version; zero remains zero. `source_boundary` is the greatest
   eligible event ULID, or null when no score is available. Percentages are
   exact decimal strings or null with zero holdout. `delta_percent` is
   tentative minus incumbent. The digest is SHA-256 over the canonical
   base version, exact values, source boundary, denominator, both scores,
   and delta.

   INSTANT re-scores only the named frozen injection's visible candidate
   rows under the tentative values, preserving pins, score/UUID order,
   top_k, and greedy token budget. Each row reports incumbent and preview
   score/rank/selected state plus `also_shown|would_add|would_drop|still_out`.
   Future prepare events retain the exact model-context limit in their
   private replay metadata; a legacy batch without enough frozen inputs
   returns `instant.status=not_replayable`, never an approximation. Instant
   writes nothing.

   `slice` is nine ordered accuracy points for the requested descriptor.
   Bounded descriptors span their complete range; positive unbounded
   descriptors use multipliers 0.25 through 2 around the tentative value.
   A weight slice changes that weight and rescales the other five
   proportionally to preserve the simplex (equal shares if their prior sum
   is zero). Selection-only controls produce an honestly flat replay curve
   because A-031 does not grade them. Every point uses the same held-out
   source boundary.

   POST `/v1/scorer-auditions` accepts
   `{principal_id,injection_id,proposal_version}` and returns the same
   instant row comparison for the active incumbent versus that inactive
   learner-PROPOSED version. Missing, active, or non-learner versions are
   refused. The operation is read-only; the incumbent remains the sole
   injection authority.

   Extend POST `/v1/scorer-configs` with required `simulation_digest` and
   `force:true`. In the same advisory-locked transaction as the version
   insert, Spine recomputes DEEP from the current evidence and exact body
   values. A stale base, changed evidence, changed value, or mismatched
   digest refuses the write. Success keeps the existing immutable version
   insert and activation journal, and adds `_force` to that journal's
   changes with the digest, source boundary, denominator, incumbent score,
   tentative score, and signed delta. No confirmation dialog exists: the
   displayed score is the confirmation; any knob change clears the browser's
   receipt and disables FORCE until another DEEP simulation.

   Harness exposes these operations only through the Injection Console's
   existing public rack query/action bridge. CURRENT follows the shared
   thread selection and may preview/audition its latest frozen injection;
   GLOBAL keeps the Palace-wide deep score and slice but has no fabricated
   gate. Gate and panel audition marks are presentation-only consumers of
   the returned comparison and never enter commit, feedback, or context.

   **Folded amendment (A-048, v2.82)** — enacted by M2P against [ADR-009 item
   4; ADR-023 clause 5], node(s) P1.2.3; law text verbatim from AMENDMENTS.md:
   In A-047, read "nine ordered accuracy points" as "up to nine distinct
   ordered accuracy points." Bounded integer descriptors enumerate their
   complete legal domain when it contains fewer than nine values. All other
   A-047 slice rules remain unchanged.

5. **Palace Vitals — usage gauges** [M2 basic; categories grow with their
   machinery] (owner directive, v2.11) — main-window gauges, read-only
   views over logs that already exist (injection_event, memory_revision,
   run.usage; Invariant 10 — every gauge encodes a real logged variable;
   Invariant 14 — watchable, never demanding):
   (a) MEMORY LIFECYCLE RATES per hour: created, reinforced (re-derivation
   coalesces), superseded, merged, quarantined, tombstoned, add-backs,
   queue depth (+ staged count during runs);
   (b) TOKEN SPEND BY CATEGORY per hour: building vs memory tools vs
   curation vs judge vs extraction — the observability half of ADR-021's
   10% attention principle (gauges show the ratio; nothing enforces it);
   (c) PALACE COUNTERS: active units, pinned, candidates pending, edges;
   (d) THE ACCURACY SCORE (owner, v2.41): the memory system's performance
   as a living time series — each scorer generation's held-out agreement
   percentage (the v2.32 binary scoreboard in percentage form: "this
   generation injects correctly 98% of the time against the backtest;
   the previous did 97%"), plotted per version over the harness's life.
   Published from the same replay manifests the learner already emits;
   hyperparameter updates that would not raise the line are never even
   proposed. The chart that should only ever climb.
   Rendered as a first-party rack plugin (ADR-023; ledger schema and
   canonical views: ADR-024); themes style it,
   never re-encode it. Presentation (owner, v2.17; NATES_VISION §15): an
   Ableton-style BOTTOM STRIP of spend-per-unit-time line graphs — lanes
   selectable by agent, sub-agent (origin_agent subtree), model, category,
   total; dollar-true from the broker's native per-call cost, attributed
   via the purpose-enum + origin_agent tags; lane click = selection focus;
   scrubs on the shared as_of timeline; collapsible (Invariant 14).

   **Folded amendment (A-028, v2.82)** — enacted by M2C against [ADR-009 item
   5; ADR-023 query surface; ADR-024 views], node(s) P2.4, P4.1; law text
   verbatim from AMENDMENTS.md:
   Spine exposes bearer-protected GET `/v1/vitals` with no query parameters.
   It returns one live trailing-hour snapshot with this exact shape:
     `{as_of, window_minutes:60,
       spend:{source_view:"v_spend_rate", latest_minute,
         lanes:[{dimension:"total"|"purpose"|"model", key, label,
           points:[{minute, cost_usd, receipt_lines, unpriced_lines}]}]},
       lifecycle_rates:[{metric, status, per_hour, source}],
       palace_counts:[{metric, status, count, source}]}`.
   `as_of`, `minute`, and nullable `latest_minute` are offset-aware timestamps.
   A total lane has `key=null`; purpose/model keys are their exact canonical
   values, with a null model represented by the stable key `unreported` and
   human label "Model not reported". `cost_usd` is an exact non-negative
   decimal string or null. Counts are non-negative integers. Lane points are
   grouped only from rows of `v_spend_rate` whose minute is within
   `(as_of - 60 minutes, as_of]`, ordered by minute; lanes are ordered total,
   purpose key, then model key. Total, purpose, and model regroup the same
   canonical rows, so priced dollars, receipt lines, and unpriced lines
   conserve across all three dimensions. A mixed-price point carries the sum
   of known dollars plus its positive `unpriced_lines`; an all-unpriced point
   keeps `cost_usd=null`. Missing cost is never rendered or aggregated as
   free. The endpoint reads the materialized view at its ordinary cadence and
   never refreshes it on demand.

   Gauge `status` is exactly `measured`, `not_recorded`, or `placeholder`.
   A non-measured gauge has a null numeric value and null source; it is never
   encoded as zero. M2C measures only `created` per hour from
   `memory_unit.created_at`, plus current `active_units` and active
   `pinned_units` counts from `memory_unit`. It returns the named lifecycle
   metrics `reinforced`, `superseded`, `merged`, `quarantined`, `tombstoned`,
   and `add_backs` as `not_recorded` until a canonical transition timestamp
   exists. It returns `candidates_pending`, `edges`, and `staged_units` as
   `not_recorded`, and `queue_depth` as the packet's explicit `placeholder`.
   The UI says these signals are not recorded or not active yet; it does not
   parse revision reasons, borrow prepare timestamps for later decisions, or
   infer transitions from a head's current `updated_at`.

   The compiled first-party Vitals resident obtains this response only through
   ADR-023's public query surface with
   `{resource:"vitals", as_of:null|"now"}`. Other `as_of` values return the
   existing `historical_unavailable` result. Hover/touch scrub publishes the
   minute on the shared selection surface together with the currently focused
   spend lane; it does not claim historical Palace counters. The frame retains
   `connect-src 'none'` and never receives Spine credentials or a private
   transport. M2C implements PLAN's wave-one total/purpose/model lanes; no
   plugin loader, authoring SDK, parameter registry, or lifecycle writer is
   introduced.

   **Folded amendment (A-029, v2.82)** — enacted by M2C against [A-028 model
   lane keys; ADR-024 views], node(s) P2.4, P4.1; law text verbatim from
   AMENDMENTS.md:
   Reserve public model-lane key `unreported` solely for a null canonical
   model. A non-null canonical model normally remains its exact public key.
   If its value equals `unreported` or begins with `~`, prefix one `~` to form
   the public key; its human label remains the exact canonical value. Thus a
   real `unreported` model has key `~unreported`, a real `~unreported` model
   has key `~~unreported`, and neither collides with the null-model lane.
   Deterministic model-lane ordering uses these resulting public keys. This
   key escape affects only the Vitals read projection and selection identity;
   it never rewrites a canonical ledger row.

   **Folded amendment (A-044, v2.82)** — enacted by M2N against [ADR-009
   Palace Vitals; ADR-016 journal law], node(s) P1.3, P2.4, P4; law text
   verbatim from AMENDMENTS.md:
   Both Spine Vitals scopes add `resources` with exact shape
   `{status,daemon_rss_bytes,daemon_uptime_seconds,disk_free_bytes,
   disk_total_bytes,database_bytes,journal_bytes,backup_bytes,warning}`.
   Every byte/count value is a non-negative integer or null. `status` is
   `partial` or `measured`; `warning` is null or `low_disk`. Spine measures
   `database_bytes` with `pg_database_size(current_database())` in the same
   read transaction as the Vitals snapshot, leaves every Harness-local value
   null, sets status partial, and never guesses them.

   Harness enriches that same object only at its public Rack boundary. It
   measures current daemon RSS, integer monotonic uptime seconds, the free and
   total capacity of the filesystem containing `NOCTURNE_HOME`, and the
   symlink-ignoring journal and backup byte counts shared with doctor.
   `measured` means every field is present; if current RSS cannot be read,
   status remains partial and only RSS is null. Harness sets `low_disk`
   exactly at A-043's greater-of-5-GiB-or-10-percent boundary. Both GLOBAL
   and CURRENT expose the same process/filesystem/database resources.

   `nocturne up` performs the same filesystem measurement before pulling or
   starting a container. At `low_disk` it prints one owner-language warning
   and continues; this is the required warning before any future journal
   hard stop, not the configured Deck threshold and not a prompt.

   The rack renders RESOURCES as a compact passive gauge: free disk, database
   size, daemon RSS and uptime, with journal and backup size available in the
   expanded view. `low_disk` may use the theme's sole danger color but has no
   alert role, popup, notification, card, or new attention channel. Partial
   data says unavailable rather than zero.

   The rule-7 resource soak drives 10,000 sequential live Rack Vitals queries
   through a production-composed daemon after 500 warm-up queries, samples
   current RSS at least every 250 queries, and requires both peak and final
   RSS growth from the post-warm-up baseline to remain at or below 32 MiB.
   Provider and Spine responses are deterministic fakes; the daemon, HTTP
   query path, resource measurement, validation, and serialization are real.

**Mobile:** the SPA is responsive from day one (hard requirement on all M1 UI
work; gate and chat must be phone-usable). PWA manifest + install flow [M3].
Chrome app mode remains the desktop packaging; no native builds.

---
---

# PART B — ROADMAP & SCOPE LAW

This part governs WHAT gets built WHEN. It overrides enthusiasm. If a task
appears to require a feature marked OFF or FORBIDDEN for the current
milestone, the implementing agent STOPS and flags the human — it does not
build ahead.

## B.1 Commitment tiers

- **COMMITTED (M1, M2):** build to acceptance criteria; deviation requires
  human sign-off.
- **DIRECTIONAL (M3):** sequenced and believed; formally re-planned after M2
  ships. Do not build M3 features during M1/M2.
- **HORIZON (M4, M5):** design constraints are BINDING (do not make these
  impossible); building them is FORBIDDEN. Committed footprint is exactly the
  ADR-011 list (revision ULIDs + parent_uid, event_uids, client-mintable IDs,
  `/v1/sync/*` reservation, cache schema mirroring). Nothing else.
- **Single-user assumption** holds through COMMITTED and DIRECTIONAL work.
  principal_id is a stamped column, not a feature. Auth = one static bearer
  token until horizon. Multi-principal anything: FORBIDDEN.

## B.2 Pillars & repositories

- **P1 — Memory** (repo `spine`, module memory): the harness-agnostic
  product. Units, revisions, scoring, injection, feedback log, learning,
  maintenance, (horizon: sync). The local cache is P1.
- **P2 — Harness** (repo `harness` + spine modules presence/relay): pydantic
  runtime, capabilities, daemon, WS envelope, command center web app —
  including ALL visualizers. **Visualization is a LAYER of P2, not a pillar.**
- **Deployment note (do not confuse):** the spine repo hosts three modules —
  memory (P1), presence (P2), relay (P2). Colocation is operational
  convenience; presence/relay must remain extractable.

ADR → pillar: 001 P2+policy · 002 P1↔P2 contract · 003 P1 · 004 P1 ·
005 P1 scorer+log / P2 gate UI · 006 P2 (deployed in spine) · 007 P2 ·
008 P2 · 009 P2 · 010 policy (provisioner horizon) · 011 P1 horizon
(schema footprint only in M1).

## B.3 Milestones

**M1 — core memory loop (COMMITTED):** chat with one agent in the browser;
memories accumulate (agent tool + /remember), curated at write time
(dedup/similar bands), injected through the gate; every event logged with
full context. Single machine, browser→localhost daemon (relay-shaped
envelope), spine on Cloud Run, hand-set weights. Minimal UI: thread list,
chat pane, gate modal, memory side panel. No visualizations beyond the gate.
**The M1 agent is chat + memory tools ONLY** — no fs/shell/skills/compaction/
HITL/durable execution (its acceptance criteria test nothing but the memory
loop; stock capabilities make these cheap to add when their milestone
arrives). Full build spec: Part C.

**M2 — learning loop + extraction (COMMITTED):** online weight updates per
ADR-005 table (with the v2.7 training-data hygiene filter); scorer versioning
+ offline replay + rollback; per-feature
contribution bars in gate; citation heuristic; end-of-thread extraction with
approval queue (per ADR-021: rides thread-close/compaction in-context —
three outputs — with verdicts-at-birth on queue cards, ≤5 candidates/thread,
cheap-model fallback for unclosed threads); markdown SEED INGESTION (upload → curator split →
approval queue — ADR-019 clause 4); HYBRID CANDIDATE RETRIEVAL (v2.7): the
candidate pool becomes the UNION of vector top-50 and Postgres full-text
search matches — an exact-keyword memory with a weak embedding must be able
to reach the scorer; the scorer ranks the union, nothing else changes;
a formal `candidate` status for approval-queue units (extraction + seed
ingestion) instead of overloading `active`; Context Bars port (+memory category); Memory Graph +
hyperparameter console.

**M3 — scale-out + harness buildout + curation + presence (DIRECTIONAL):**
cloud relay (multi-machine), harness parity (fs/shell/skills/compaction/HITL,
queuing/interjection, loops, subagents) + DBOS durable execution, the
CURATORS (ADR-022: health report + deterministic tools, verdict taxonomy,
promotion blend, slop removal, branch-staging promotion per ADR-021), memory_drafts
offline flow, Ant Farm, PWA install, hierarchical scorer offsets when data
justifies, Rust daemon if triggers fire.

**M4 — fleet expansion (HORIZON):** machine provisioner (ADR-010): leased
cloud workspaces as first-class machines, GCS snapshot lifecycle, spot
instances, parallel workspace fan-out.

**M5 — blue-water mode (HORIZON):** writable replica promotion, sync engine
(union/fast-forward/conflict queue), conflict review UI, local embedding
space, re-derivation pipeline, multi-principal soak test (ADR-011).

## B.4 Feature ledger

States: ON = build; SCHEMA = columns/namespace only, zero behavior;
OFF = later milestone; FORB = forbidden — flag if seemingly needed.

| Feature | M1 | M2 | M3 | M4/M5 |
|---|---|---|---|---|
| Chat loop, sessions, model-agnostic (incl. local via model string) | ON | ON | ON | ON |
| Memory CRUD + agent tools + /remember | ON | ON | ON | ON |
| Write-time dedup (dup/similar bands) | ON | ON | ON | ON |
| First-prompt gate + panel + removal reasons | ON | ON | ON | ON |
| Injection/feedback logging (full context) | ON | ON | ON | ON |
| Scorer: hand-set weights | ON | superseded | — | — |
| "never" bias + quarantine | ON | ON | ON | ON |
| Harness parity: fs/shell tools, skills, compaction, HITL, queuing, loops | FORB | FORB | ON | ON |
| Durable execution (DBOS) | FORB | FORB | ON | ON |
| Responsive SPA (phone-usable) | ON | ON | ON | ON |
| Online weight learning (global) | FORB | ON | ON | ON |
| Offline replay evaluation | OFF | ON | ON | ON |
| Scorer versioning + rollback + hyperparam console | OFF | ON | ON | ON |
| End-of-thread extraction + approval queue | FORB | ON | ON | ON |
| Context Bars (+memory category) | OFF | ON | ON | ON |
| Memory Graph | OFF | ON | ON | ON |
| Per-prompt re-scoring (OQ-15 RESOLVED v2.31) | FORB | ON | ON | ON |
| Citation heuristic | OFF | ON | ON | ON |
| Cloud relay (multi-machine) | FORB* | OFF | ON | ON |
| Subagents, queue/interjection | OFF | OFF | ON | ON |
| Branch memory staging + judge promotion (ADR-021) | FORB | FORB | ON | ON |
| Maintenance agent (cluster/dedup/promote/stale) | FORB | OFF | ON | ON |
| Pin-promotion by frequency | SCHEMA | OFF | ON | ON |
| Ant Farm + presence pipeline | SCHEMA | OFF | ON | ON |
| PWA install | OFF | OFF | ON | ON |
| memory_drafts offline flow | SCHEMA | OFF | ON | ON |
| Rust daemon rewrite | FORB | FORB | trigger-gated | trigger-gated |
| Machine provisioner / rented machines | FORB | FORB | FORB | M4 |
| Writable replica + sync engine + conflict UI | FORB | FORB | FORB | M5 |
| Local embedding space | FORB | FORB | FORB | M5 |
| Multi-principal (auth, sharing) | FORB | FORB | FORB | M5 |

*the envelope is relay-SHAPED in M1 (C.7) — that is ON; the cloud relay
itself is not built.

## B.5 Anti-scope rules for implementing agents

1. FORBIDDEN features: do not build, stub, scaffold, or "prepare for" beyond
   the explicit schema footprints in B.1.
2. When in doubt which milestone you are in: you are in M1.
3. A feature's SCHEMA state grants exactly the DDL in Part C — no handlers,
   no UI, no background jobs.
4. If an acceptance criterion seems to require an OFF/FORB feature, the spec
   has a bug: stop and report; do not resolve it by building.

## B.6 Verification doctrine (judge law)

A milestone is DONE when a judge agent, acting as the user, experiences every
acceptance criterion — never merely when code exists or unit tests pass.

1. **Three evidence classes, all mandatory, per criterion:**
   - **Experiential:** drive the real UI as a user (browser automation or
     manual): real clicks, real prompts, screenshots at every meaningful
     state. If it can't be screenshotted being used, it isn't done.
   - **Traced:** follow the SAME action through the internals — log lines,
     DB rows, API payloads — and prove they agree with the surface (e.g. the
     injection_id visible in the gate maps to injection_event rows whose
     features/outcomes match what the screenshot shows).
   - **Adversarial:** deliberately trigger the failure paths named by the
     criterion (kill dependencies, force 409s, go offline) and verify the
     specified degradation, not a crash.
2. **Separation of powers:** the judge is a distinct agent from the builders
   of the loop it judges (no self-certification). Judges do not fix — a FAIL
   returns to the builders with the verdict; the Blight Protocol governs the
   fix.
3. **Verdict artifacts:** screenshots, log excerpts, and SQL outputs are
   committed under `verification/<milestone>/` ; the verdict file lists each
   criterion → PASS/FAIL → evidence paths → tree node. Any FAIL = milestone
   not done.
4. **Scope audit is judgeable:** the judge greps for FORBIDDEN-feature code
   (B.4) and verifies every DECISIONS.md entry cites a tree node. Scope
   violations are automatic FAILs regardless of functionality.
5. **Responsive law is judged:** experiential passes are repeated at a phone
   viewport (390×844) for all user-facing criteria.
6. Each milestone's concrete checklist is authored at milestone planning
   time from this doctrine (M1's is C.9).
7. **UI packets are BUILT the way they will be judged (v1.14; D.2 039).**
   Any packet delivering user-facing interface (chat panes, gates, panels,
   visualizers) must verify by emulating true human use FROM THE FIRST
   PACKET, not at judgment time: drive the real rendered interface with
   browser automation exactly as a user would (click, type, scroll,
   rotate, select), screenshot every acceptance-relevant state, and assert
   on RENDERED OUTCOMES — visible text, layout, encodings — never only on
   internal state or component props. Screenshots are first-class builder
   evidence, committed under verification/<packet>/ beside the tests that
   produced them. A UI behavior that was never exercised through the real
   interface is not DONE. Rationale: rendering defects (cascade
   collisions, encoding drift, label overlap) are invisible to unit tests
   and expensive to find at refinement; the screenshot IS the test.
8. **Agent walkthroughs — SOPs for agents (v2.5; D.2 048).** Rule 7's
   scripted checks prove regressions; they do not prove EXPERIENCE. Every
   UI packet additionally ships and EXECUTES a written SOP — a
   human-style operating procedure ("open the app; create a thread; type
   this; watch what happens; now try to break it") — performed LIVE by
   the agent through interactive browser use, first person, exactly as
   the human would: look at every rendered screen, click, type, scroll,
   wait, and OBSERVE before choosing the next action. This is not
   automated testing and cannot be discharged by running a script.
   Requirements: (a) the SOP and its execution log live at
   verification/<packet>/SOP.md — every step records the action taken,
   its screenshot, and a PROSE OBSERVATION of what the agent actually saw
   and judged (surprises, friction, and wrongness are findings, not
   noise); (b) at least one UNSCRIPTED exploration segment — wander,
   click around, try odd inputs — recorded the same way; (c) observed
   defects route through the Blight Protocol; observed DESIGN friction is
   surfaced to the human gate in the handoff; (d) the judge RE-EXECUTES
   the packet SOPs at I1/J rather than only reading their logs. The
   agent's eyes on the rendered pixels are the point.
9. **Scout before owner (v2.15; D.2 058).** The owner's attention is the
   scarcest resource in the system — Invariant 14 applies to it above all.
   Therefore every HUMAN gate (a HUMAN packet, a human-use hold, any
   future gate day or benchmark) is PRECEDED by an agent SCOUT pass, as
   the DEFAULT relay behavior (PLAN Section 3 dispatches it — the owner
   fires the same one-line relay kickoff and the session self-routes):
   the scout opens the harness in the browser and uses it exactly as the
   owner would (rule 8 discipline: look, click, type, observe, first
   person), executing the gate's checklist; classifies every item
   PASS / FAIL / NEEDS-TASTE; burns down the easy passes with evidence;
   surfaces FAILs for owner consultation (Blight/flag) and lists
   NEEDS-TASTE items for the owner's personal session. Scouts run under a
   verification principal (machine_id '<gate>-sop-verification') so their
   events are hygiene-excluded from learning (v2.7/C5 — an agent's taps
   must never impersonate the owner's signal), and tombstone every
   fixture they create. THE SCOUT NEVER CLEARS THE GATE — mechanics can
   be delegated; taste and authentic training signal cannot. The owner's
   session is reserved for real use, feel judgments, and the signal only
   a human hand generates.
10. **Real calls (owner doctrine, v2.37).** The running product ALWAYS
   talks to the real broker — there is no mock-model mode, and no packet
   may introduce one as a default or a "dev server" convenience. Spend
   the pennies: rule-8 SOPs, scout passes, judge re-executions, and the
   owner's own use hit real OpenRouter routes, real embeddings, real
   money (the spend ledger prices it; the D2 breaker bounds it).
   Deterministic stubs are permitted ONLY as rule-7 regression fixtures —
   clearly labeled, never the default path, never SOP or judge evidence.
   A demo that works against a fake is a fake that demos.
   FIXTURE ISOLATION (v2.38 — enacted after the owner unknowingly
   transmitted to a leftover scenario daemon, 2026-08-02): any fixture or
   scenario server MUST (a) bind a DISTINCT port, never the product
   default; (b) render an unmistakable full-surface FIXTURE banner in any
   UI it serves; (c) terminate with the session that launched it — no
   orphans; (d) run SOPs in an isolated browser profile so fixture
   threads never pollute the owner's real thread catalog. Reachability
   is the standard: if the owner can hit a fake without knowing, the
   isolation has failed regardless of where the code lives.
11. **Accounting fails open (v2.38).** The conversation is the product;
   the receipt is the record. A turn is NEVER blocked, failed, or
   uncommitted because a spend receipt could not persist: the turn
   completes, a line is queued with basis=estimated, and the broker
   reconciliation surfaces the drift loudly. Invariant 9's posture
   extends to accounting — no ledger failure may brick the harness.

   **Folded amendment (A-038, v2.82)** — enacted by M2O against [B.6 rules
   10-11], node(s) P3, P4.1; law text verbatim from AMENDMENTS.md:
   Every Harness fixture/scenario app has one nonblank identity ending in
   ` REGRESSION`, exposes it from GET `/__scenario__/identity` as
   `{fixture,deterministic:true}`, and redirects a top-level UI request that
   lacks the exact `fixture` query value to the same URL with that value.
   It refuses every request received on product port 8765 before serving UI
   or accepting a WebSocket. The rack renders the verified identity as a
   pointer-transparent, viewport-covering FIXTURE overlay; a query string
   alone never creates the overlay. Fixture launchers run in the foreground
   or own the child in a `finally` cleanup, reject port 8765, and browser
   automation creates a fresh temporary profile which it removes on exit.

   The one-time polluted-catalog cleanup is explicit and narrow. The thread
   rail offers `Remove fixture threads` only when the browser-local catalog
   contains a title produced from the first prompt of a pre-M2O H4, H5, H6,
   H8, or M2G deterministic scenario. It shows the count and removes only
   those exact catalog entries after the human activates it; it never clears
   all storage, guesses from substrings, deletes transcripts, or touches a
   catalog on a distinct fixture origin.

   A failed or incomplete POST `/v1/spend/events` never changes a completed
   model outcome. Harness changes each failed batch line's basis to
   `estimated`, retains its event_uid and broker timestamp, records the
   original basis plus queue time in safe meta, and stores one atomic
   mode-0600 JSON batch beneath `NOCTURNE_HOME/receipt-queue`. It retries
   queued batches oldest-first at daemon startup and before a later current
   receipt. A file is removed only after Spine accepts its complete batch;
   replay therefore rides A-027 idempotency. Queue or retry failure emits a
   safe `spend_pending` event but does not fail, retract, or replace the turn.
   If disk enqueue itself fails, the process retains the batch in memory and
   reports degraded accounting; accounting remains fail-open.

   Harness enriches only its public Rack Vitals result (the Spine A-028 body
   is unchanged) with `accounting:{status,pending_lines,oldest_queued_at,
   source}`. Status is `clear`, `pending`, or `degraded`; source is always
   `harness.receipt_queue`; counts include disk and memory batches; oldest is
   null exactly when no line is pending. `degraded` means at least one
   pending batch is not durable. Vitals renders pending/degraded in the
   theme's sole danger color as a persistent accounting-drift line, without
   an alert role, popup, notification, or card. Successful replay returns it
   to clear. Broker reconciliation remains the dollar authority and exposes
   any cumulative mismatch on its existing cadence.

12. **Test motivation (owner, v2.45).** A test is LAW MADE EXECUTABLE,
   and it cites its statute: every test function carries a motivation
   docstring — one or two sentences naming what law or problem it
   defends and what failure it prevents — containing at least one
   citation token (SPEC section, ADR, amendment, Invariant, Problem
   Tree node, or regression flag). Enforced MECHANICALLY: a
   deterministic pre-commit checker in both repos fails unmotivated
   tests; the existing suite is grandfathered by a baseline file that
   the M2Q sweep retires (ratchet: new or modified tests comply now).
   A test that cannot be motivated is a discovery, not an exemption:
   it defends unwritten law (propose the completion) or defends
   nothing (delete it, journaled). THE INVERSE INDEX: the citations
   generate the LAW-COVERAGE REPORT — every section with its defending
   tests, zero-defender sections highlighted; untested law becomes as
   visible as unpriced spend.
   [Body moved here from the end of D.4 in editor pass IV (v2.82), per
   A-040's first sentence. The v2.81 stub — "Every test carries a
   motivation docstring citing its statute; mechanically enforced. (Body
   stub — full text lives in D.2 088; editor pass folds it.)" — is retired,
   its words preserved in this note.]

   **Folded amendment (A-040, v2.82)** — enacted by M2Q against [SPEC B.6 rule
   12], node(s) P4; law text verbatim from AMENDMENTS.md:
   Treat the paragraph beginning `12. Test motivation (owner, v2.45)` as
   B.6 rule 12 immediately after B.6 rule 11. D.4's Session-affinity bullet
   ends after `session affinity would have changed rankings.`; no normative
   test law lives in D.4.

   In each repository, the deterministic checker scans every Python
   `test_*` function or method under `tests/`. Its motivation is the
   function docstring. It also scans JavaScript test calls under
   `web/tests/` when that directory exists; their equivalent motivation is
   the immediately preceding JSDoc block. A motivation is nonblank prose
   containing at least one exact citation token: `SPEC <section>`,
   `ADR-NNN`, `A-NNN`, `Invariant N`, Problem Tree node `P...`, or regression
   flag `FNNN`.

   The temporary baseline keys a test by repository-relative path plus
   qualified name (or JavaScript call line) and stores a SHA-256 digest of
   its normalized source. Python normalization spans decorators through the
   function body, normalizes newlines, removes common indentation, and
   ignores trailing line whitespace. Only the exact unchanged test is
   grandfathered: new, renamed, or source-modified tests comply immediately. A
   deleted baseline entry is reported as stale debt but does not fail the
   ratchet. The checker itself and its local pre-commit hook have no network
   dependency.

   The law-coverage report catalogs SPEC numbered parts, ADR headings, and
   B/C/D sections from the repository's committed `docs/SPEC.md`, maps exact
   citations from compliant motivations to sorted test identities, and
   explicitly marks catalog entries with zero defenders. Amendment,
   invariant, problem-node, and regression citations are listed in a
   separate referenced-statutes index because they are valid statutes but
   are not SPEC headings. The report also states current baseline and stale
   debt. Mechanical citation validity is checker-owned; whether the prose
   truthfully names the defended law and prevented failure remains the M2Q
   human sweep.


13. **The scout's word (v2.81; D.2 121).** A single scout's report is
   EVIDENCE, not verdict. Two classes of item, and every scout labels
   each item with its class:
   - ALIGNED — the item executes a specific test the owner and the
     gate have aligned on (a ratified checklist item, a golden-
     behavior proof, a suite). A scout's PASS/FAIL on an aligned
     item is a finding.
   - DERIVED — the item is one the scout designed itself under rule
     9. Its verdict is a CLAIM. A claim becomes a finding only after
     the gate has mechanism-checked it against the actual code path
     (or the owner has aligned on the test); until then it is
     presented as a claim, in neutral words, with no severity
     attached. A claim that names a core-law violation (memory
     fence, isolation, injection, spend, consent) is NEVER presented
     to the owner before its mechanism check.
   Out of context, a scout's report is never gospel. Provoked by
   F054 (a module proven only against its own fixture) and F056 (a
   derived "isolation leak" that the code's fence made impossible;
   the real defect was a UI binding).
14. A GUARD CITES ITS INCIDENT (v2.110): every raise, gate, limit or reconciliation check in the core loop carries, in one line beside it, the incident, flag or wall it prevents; none named, none kept — the framework's own error surfaces instead. Kept guards have a test quoting their message. 'Fail-closed' is a wall's word, never a core-loop habit.

---
---

# PART C — M1 IMPLEMENTATION SPEC (core memory loop)

This part is implementation-grade. Implementing agents follow it literally;
anything not specified here follows Part A; anything covered by neither gets
flagged back to the human, not improvised.

## C.1 Repositories

**Repo 1: `spine`** (name FIXED by the 1.0 vernacular; OQ-14 resolved — do
NOT rename; the GitHub remote's `memory-palace` label is cosmetic only)
```
spine/
  pyproject.toml            # python 3.12; fastapi, sqlalchemy[asyncio] 2.x,
                            # alembic, pgvector, asyncpg, httpx, pydantic v2
  src/spine/
    main.py                 # FastAPI app factory; mounts routers
    config.py               # pydantic-settings; all defaults from C.5
    db/                     # engine, session, models, alembic migrations
    memory/                 # router /v1/memories, /v1/search
    inject/                 # router /v1/inject/*, /v1/feedback; scorer.py
    embeddings/             # provider-pluggable embed(texts) -> vec[1536]
    events/                 # injection_event writer (SSE /v1/stream arrives in M2)
    auth.py                 # M1: single static bearer token (env SPINE_TOKEN)
  tests/                    # pytest + testcontainers postgres w/ pgvector
```
**Repo 2: `harness`**
```
harness/
  pyproject.toml            # python 3.12; pydantic-ai>=2, fastapi, websockets
  src/harness/
    daemon.py               # FastAPI: serves web/ static + WS /ws
    agent.py                # pydantic-ai v2 agent assembly (capabilities)
    memory_capability.py    # MemoryCapability (C.6)
    spine_client.py         # typed httpx client for spine API (C.4)
    tools_memory.py         # save_memory / search_memory / edit_memory tools
    envelope.py             # WS message envelope (C.7)
    config.py
  web/                      # React+TS+Vite SPA (chat, gate, memory panel)
  tests/
```
- Contract between repos: spine's generated OpenAPI is committed to spine repo
  (`openapi.json`); harness CI runs contract tests of `spine_client` against a
  spun-up spine container. Models are hand-mirrored pydantic classes (accepted
  duplication; two repos was a deliberate choice).

## C.2 Postgres DDL (authoritative)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE memory_unit (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  principal_id  TEXT NOT NULL,
  label         TEXT NOT NULL,                  -- <= 64 chars, unique per principal (partial idx, status='active')
  body          TEXT NOT NULL,                  -- <= cfg.memory_max_tokens (128) when tokenized;
                                                -- ATOMIC: one fact/preference/procedure step per unit
  kind          TEXT NOT NULL CHECK (kind IN
                ('fact','preference','procedure','project_note','persona','pinned')),
  keywords      TEXT[] NOT NULL DEFAULT '{}',
  embedding     vector(1536) NOT NULL,
  embedding_model TEXT NOT NULL,
  project_key   TEXT,                           -- NULL = global
  thread_origin TEXT,
  origin_path   TEXT,                           -- workspace-relative directory context at creation;
                                                -- NULL = no location. M1: inert metadata (D.2 030)
  pin           BOOLEAN NOT NULL DEFAULT FALSE,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                ('active','quarantined','tombstoned')),
  revision      INTEGER NOT NULL DEFAULT 1,
  stats         JSONB NOT NULL DEFAULT '{"injections":0,"removals":0,"citations":0,"never_kills":0,"last_injected_at":null}',
  bias          REAL NOT NULL DEFAULT 0.0,      -- b_m
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON memory_unit USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memory_unit (principal_id, status, project_key);
CREATE UNIQUE INDEX memory_unit_active_label ON memory_unit (principal_id, label)
  WHERE status = 'active';                      -- labels are handles: unique among
                                                -- ACTIVE units; tombstone/quarantine
                                                -- frees the label for reuse

CREATE TABLE memory_revision (                  -- append-only "update history" index
  rev_uid     TEXT PRIMARY KEY,                 -- client-mintable ULID (ADR-011)
  parent_uid  TEXT REFERENCES memory_revision(rev_uid),  -- lineage DAG; NULL = root
  memory_id   UUID NOT NULL REFERENCES memory_unit(id),
  revision    INTEGER,                          -- cloud-head CAS counter only; NULL on divergent branches
  body        TEXT NOT NULL,
  label       TEXT NOT NULL,
  editor      TEXT NOT NULL,                    -- 'user' | 'agent:<agent_id>' | 'maintenance'
  origin_machine_id TEXT NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  ts          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON memory_revision (memory_id, ts);

CREATE TABLE thread (
  id              UUID PRIMARY KEY,
  principal_id    TEXT NOT NULL,
  agent_id        TEXT NOT NULL,
  machine_id      TEXT NOT NULL,
  project_key     TEXT,
  snapshot_ts     TIMESTAMPTZ,                  -- set by first inject/prepare
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE injection_event (                  -- append-only training log
  id            BIGSERIAL PRIMARY KEY,
  event_uid     TEXT NOT NULL UNIQUE,           -- client-minted ULID; union-safe upload (ADR-011)
  injection_id  UUID NOT NULL,
  thread_id     UUID NOT NULL,
  agent_id      TEXT NOT NULL,
  machine_id    TEXT NOT NULL,
  principal_id  TEXT NOT NULL,
  project_key   TEXT,
  agent_kind    TEXT NOT NULL DEFAULT 'general',
  prompt_text   TEXT NOT NULL,                  -- full text; own infra, needed for replay
  scorer_version TEXT NOT NULL,
  memory_id     UUID NOT NULL,
  memory_kind   TEXT NOT NULL,
  features      JSONB NOT NULL,                 -- {sem,kw,time,proj,freq,hist}
  score         REAL NOT NULL,
  rank          INTEGER NOT NULL,
  shown_as      TEXT NOT NULL CHECK (shown_as IN ('injected','near_miss','pinned')),
  outcome       TEXT,                           -- NULL until commit/feedback:
                -- kept | removed:not_relevant | removed:wrong | removed:never
                -- | added_back | cited | mid_thread_removed
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON injection_event (injection_id);

CREATE TABLE scorer_config (
  version     TEXT PRIMARY KEY,                 -- 'v0'
  weights     JSONB NOT NULL,
  params      JSONB NOT NULL,                   -- tau, top_k, near_miss_k, half_lives...
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  active      BOOLEAN NOT NULL DEFAULT FALSE
);
```
Rules: UPDATEs on memory_unit require `WHERE revision = :expected` and bump
revision + write memory_revision, all in one transaction; conflict → HTTP 409
with current unit in body. A successful cloud-head CAS from revision n to n+1
sets `memory_unit.updated_at = now()` and appends exactly one
`memory_revision` with `revision = n+1`, `parent_uid` equal to the prior
cloud-head revision's `rev_uid`, and `body` / `label` equal to the resulting
`memory_unit` values. A failed CAS changes neither table. [folded from A-001]
Tombstone = status change, never DELETE.

Schema extensions enacted by amendment and folded at v2.82 (the DDL above is
the M1 authority; each addition's exact law sits at its fold site): A-030
`injection_event.actor_class` (C.4); A-032 status `candidate`, `memory_edge`,
`approval_queue_item`, `approval_decision` (ADR-021); A-033 queue birthplace
`thread|seed`, `batch_uid`, `source_name`, digest (ADR-019); A-035
`scorer_activation` (ADR-009 item 4); A-037 `spend_reconciliation` (ADR-024);
A-041 the migration advisory lock (ADR-019). Later amendments (A-049 onward)
remain in AMENDMENTS.md until the next editor pass.

## C.3 Scorer v0 (hand-set)

For prompt q and candidate m (status='active', principal match, and
(project_key IS NULL OR project_key = thread.project_key)):

- f_sem  = max(0, cosine(embed(q), m.embedding))            ∈ [0,1]
- f_kw   = |kw(q) ∩ (m.keywords ∪ tokens(m.label))| / max(1,|kw(q)|), kw = lowercased
           non-stopword tokens; cap at 1.0
- f_time = 2^(−age_days(m.updated_at)/14)
- f_proj = 1.0 if m.project_key = thread.project_key else (0.5 if m.project_key IS NULL else 0.0)
- f_freq = min(1.0, m.stats.citations / 10)   (inert in M1: citations arrive with the M2 heuristic)
- f_hist = 2^(−days_since_last_human_edit/7); 0 if never human-edited
- score  = 0.42·f_sem + 0.16·f_kw + 0.11·f_time + 0.16·f_proj
         + 0.08·f_freq + 0.07·f_hist + m.bias
- Candidates: top 50 by vector similarity → score all → injected = top k≤8 with
  score ≥ τ=0.55, subject to token budget min(3000, 5%·model_ctx) (greedy by
  score; a memory that would exceed budget is skipped, next tried).
- near_misses = next 3 below the injected set regardless of τ.
- pinned units (pin=TRUE) bypass scoring, always injected first, count against
  budget, shown_as='pinned'.
- M1 sets `bias` only via removed:never (−0.15 each, quarantine at
  never_kills≥3). No other learning in M1; everything else is logged only.

  For scorer v0, `tokens` are maximal runs of Unicode alphanumeric characters
  after lowercase conversion. `kw` removes the exact stopword set `{a, an,
  and, are, as, at, be, by, for, from, has, he, in, is, it, its, of, on,
  that, the, to, was, were, will, with}`; each stored keyword is tokenized by
  the same rule before union. Time and human-edit ages are measured from
  `thread.snapshot_ts`, in elapsed seconds divided by 86400, with negative
  ages clamped to zero; a human edit is a `memory_revision` whose editor is
  exactly `user`. Semantic cosine is clamped to `[0,1]`. A project match
  requires a non-null thread project equal to the memory project; a null
  memory project scores 0.5. Eligible `pin=true` units are fetched outside
  the non-pinned vector pool and top-k limit, scored only to populate the
  explainability fields, ordered by `memory_id` ASC, and injected first
  regardless of threshold or budget. Their `cl100k_base` body-token costs
  reduce the regular budget to no less than zero; pins alone may exceed it.
  The regular budget is
  `min(budget_tokens, floor(budget_pct * model_context_tokens))`, and each
  regular card costs its body's `cl100k_base` tokens. The non-pinned vector
  pool orders cosine DESC then `memory_id` ASC; scored regular candidates
  order score DESC then `memory_id` ASC. Greedy selection scans that order,
  accepts at most `top_k`, and after an over-budget card continues to the
  next. Near misses are the first `near_miss_k` unselected regular candidates
  in score order, including candidates excluded by threshold, budget, or the
  top-k cap. Rank is one-based in the combined complete order: pins first,
  then all regular candidates in score order; returned lists retain those
  ranks even when intervening candidates are not returned. `kind='pinned'`
  without `pin=true` does not bypass scoring. [folded from A-007]

## C.4 Spine API (M1 surface, exact bodies)

Auth: `Authorization: Bearer $SPINE_TOKEN` on all routes. Unless a route
specifies an exact error body below, errors use RFC7807 JSON. The
`label_conflict`, `duplicate_of`, and `conflict` 409 bodies specified by
POST/PATCH `/v1/memories` are exact `application/json` responses and are the
only C.4 exceptions. [folded from A-002]

POST /v1/inject/prepare
  req: {thread_id, agent_id, machine_id, principal_id, project_key?,
        agent_kind?, prompt, model_context_tokens}
  Require `model_context_tokens > 0`; a violation returns RFC7807 422 before
  embedding or any database write. [folded from A-014]
  beh: create thread row if new (sets snapshot_ts=now()); embed prompt;
       candidates read as-of thread.snapshot_ts (latest revision ≤ snapshot_ts);
       write injection_event rows (outcome NULL); update stats.injections.
  M1 accepts exactly one successful prepare per thread, as required by C.6's
  one-injection flow. A thread row with non-null `snapshot_ts` returns
  RFC7807 409 on another prepare. An existing unstamped row may be stamped
  only when its principal, agent, machine, and project fields exactly match
  the request; mismatch returns RFC7807 409. Prompt embedding completes
  before the atomic database phase. That phase uses one repeatable-read
  transaction, stamps `snapshot_ts` from the database clock, reads and scores
  the heads visible at that boundary, writes events, and updates statistics;
  a conflict rolls back the entire phase. Each returned card writes exactly
  one `injection_event`: injected `pin=true` cards use `shown_as='pinned'`,
  other injected cards use `shown_as='injected'`, and near misses use
  `shown_as='near_miss'`; stored score, six features, and rank equal the
  response and outcome is null. To preserve the frozen card for replay and
  commit without changing C.2's DDL, the event's features JSON additionally
  contains `_memory: {label, body, pin, updated_at}` from the scored
  snapshot; the wire `features` object remains the exact six-field C.4
  shape. `stats.injections` increments once and `stats.last_injected_at` is
  set to `snapshot_ts` for each card in `injected`, including pins, and never
  for a near miss. Those server writes use C.2 CAS, append revisions with
  editor `system:inject`, origin machine from the request, and reason
  `inject/prepare`. [folded from A-008]
  res: {injection_id, snapshot_ts, scorer_version,
        injected: [MemoryCard], near_misses: [MemoryCard]}
  MemoryCard: {memory_id, label, body, kind, pin, score,
               features: {sem,kw,time,proj,freq,hist}, rank}
  MemoryUnit (shared shape; the C.2 row minus embedding):
    {memory_id, principal_id, label, body, kind, keywords, project_key,
     thread_origin, origin_path, pin, status, revision, stats, bias,
     embedding_model, created_at, updated_at}
  MemoryCard.score/features/rank are populated only by inject/prepare; in
  dedup and /v1/search responses score = cosine similarity, features/rank null.

POST /v1/inject/commit
  req: {injection_id,
        removed: [{memory_id, reason: "not_relevant"|"wrong"|"never"}],
        added_back: [memory_id]}
  beh: set outcomes (kept for the untouched injected set); apply
       removed:never bias/quarantine rule; reason "wrong" additionally returns
       the unit so the UI can open the edit flow.
  The event batch is every `injection_event` row with the requested
  `injection_id`. Each `removed` memory ID must be distinct and name a batch
  row whose `shown_as` is `injected` or `pinned`; each `added_back` ID must
  be distinct and name a `near_miss` row; the lists must be disjoint.
  Duplicate, foreign, or wrong-class choices return RFC7807 422 with no
  write. No batch rows plus nonempty choices returns RFC7807 404. No batch
  rows plus both lists empty succeeds with the canonical zero-member C.6
  block and `wrong_removed=[]`; because A-008 persists no batch row for a
  zero-card prepare, this also accepts any unknown injection UUID with empty
  choices in M1.

  The desired commit outcome is `removed:<reason>` for each removed row,
  `kept` for every untouched injected or pinned row, and `added_back` for
  each selected near miss; an unselected near miss remains NULL. Outcomes
  transition from NULL exactly once. Repeating the same desired outcome is
  idempotent. For retry comparison, `cited` or `mid_thread_removed` is a
  descendant of `kept` when `shown_as` is injected/pinned and of
  `added_back` when `shown_as` is near_miss. Any different non-NULL outcome
  returns RFC7807 409 with no write. An all-near-miss commit with no add-back
  is necessarily a durable no-op under the frozen C.2 schema.

  Only a new NULL-to-outcome transition changes head statistics. Each newly
  removed row increments `stats.removals` once. A new `removed:never` also
  increments `stats.never_kills`, adds that event's scorer-version
  `never_bias_step` to `bias`, and changes an ACTIVE head to quarantined when
  the resulting kill count is at least that version's `quarantine_kills`;
  an already quarantined or tombstoned status is preserved. Each newly
  `added_back` row increments `stats.injections` once and sets
  `stats.last_injected_at` to the commit transaction's database clock. Load
  the two never parameters from the event's `scorer_version` row. Apply all
  new event outcomes, head changes, and revisions atomically; update each
  affected head once through C.2 CAS in memory-ID order with editor
  `system:inject`, the event's `machine_id`, and reason
  `inject/commit:<outcome>`. `wrong_removed` contains the post-stat CURRENT
  MemoryUnit for every requested `removed:wrong`, ordered by event rank.

  Final members are rows whose outcome is `kept`, `added_back`, or `cited`,
  ordered by rank ASC then memory_id ASC. Render them only from the frozen
  event `_memory` payload and `memory_kind`, never from a current head.
  [folded from A-009]
  res: {final_block: string,        # the exact rendered block (C.6) the harness injects
        wrong_removed: [MemoryUnit]} # current unit for each removed:"wrong" so the
                                     # UI opens the edit flow with a valid
                                     # expected_revision; [] when none

  **Folded amendment (A-022, v2.82)** — enacted by H5 FIXER against [SPEC C.4
  commit, C.6, ADR-005], node(s) P1.2.1b; law text verbatim from
  AMENDMENTS.md:
  In POST `/v1/inject/commit`, a `removed` item may name an injected or
  pinned row with any existing removal reason, or a near_miss row only
  when its reason is `never`. A near-miss ID in both `removed` and
  `added_back` remains an RFC7807 422 with no write. The desired outcome
  for a near-miss veto is `removed:never`; an untouched near miss remains
  NULL. The veto uses exactly the same event transition, scorer-version
  `never_bias_step`, removals and never_kills increments, quarantine
  threshold, CAS revision, retry/idempotency rule, and final-block
  exclusion as removed:never on an injected or pinned row. A-019 browser
  membership validation uses the same class/reason rule. C.6's gate renders
  mutually exclusive Add and Never controls for every near miss.

POST /v1/feedback            # mid-thread, ad-hoc
  req: {injection_id, memory_id, signal: "mid_thread_removed"|"cited"}
  res: {ok: true}
  Feedback targets the single `injection_event` matching both IDs; no match
  returns RFC7807 404. A new signal may transition only `kept` or
  `added_back` to the signal's literal outcome. Repeating that same signal is
  idempotent `{ok:true}` with no new write. Outcome NULL, any `removed:*`
  outcome, or the other feedback signal returns RFC7807 409.
  `mid_thread_removed` also increments the current head's `stats.removals`
  exactly once through C.2 CAS; its event transition, head update, and
  revision are one transaction using editor `system:feedback`, the event's
  `machine_id`, and reason `feedback/mid_thread_removed`. It changes no bias,
  status, citation, or other statistic. In M1, `cited` is event-log-only: it
  writes the outcome but does not change `stats.citations`, write a memory
  revision, or affect scorer v0, whose citation feature remains inert.
  [folded from A-010]
  Citation v1 (`cited` from kept / added_back / auto_entered; the
  `stats.citations` transition): A-036, folded at ADR-005 (v2.82).
  Autonomous re-scoring, `actor_class`, `mid_thread_added`: A-030, folded
  below (v2.82).

  **Folded amendment (A-030, v2.82)** — enacted by M2G against [SPEC C.2, C.4
  POST /v1/inject/prepare and /v1/feedback; ADR-005], node(s) P1.2, P1.4; law
  text verbatim from AMENDMENTS.md:
  Add non-null `actor_class TEXT NOT NULL DEFAULT 'human'` to
  `injection_event`, constrained for M2G writes to `human` or `passive`.
  Existing rows are `human`. Extend POST `/v1/inject/prepare` with optional
  `mode`, exactly `gate` (default) or `autonomous`, and optional UUID lists
  `current_memory_ids`, `confirmed_memory_ids`, and `excluded_memory_ids`,
  each defaulting empty. Extend its response with nullable `final_block`,
  which is null in gate mode and the canonical C.6 block in autonomous mode.

  Gate mode requires all three lists empty and retains the existing one-shot
  prepare/commit behavior unchanged. Autonomous mode requires an already
  prepared thread with identical principal, agent, machine, and project
  identity; lists contain no duplicates,
  `confirmed_memory_ids` is a subset of `current_memory_ids`, and
  `excluded_memory_ids` is disjoint from both. One repeatable-read
  transaction locks that thread, advances `thread.snapshot_ts` to its own
  database-clock snapshot, retrieves the ordinary live candidate union plus
  every active eligible current or confirmed unit, and excludes every listed
  excluded unit before scoring. Missing, inactive, wrong-principal, or
  project-ineligible current/confirmed IDs return RFC7807 409 rather than
  silently weakening a lock.

  Selection remains score-descending threshold plus token-budget selection;
  configured top-k remains only its standing safety/display cap. Active pins
  and confirmed units are binary forced members: pins order first by UUID,
  then non-pin confirmed units by score DESC and UUID ASC. Both classes
  bypass threshold and top-k, their token costs reduce the ordinary regular
  budget to no less than zero, and they alone may overflow it. Remaining
  regular candidates retain C.3 order and greedy selection. Each autonomous
  batch writes one event for every returned selected or near-miss unit and
  for every prior current unit that exits. Selected prior members have
  outcome `kept`; selected new members `auto_entered`; unselected prior
  members `auto_exited`; undispositioned near misses remain null. Those rows
  have actor_class `passive`; gate rows have actor_class `human`.
  `final_block` is rendered from exactly the selected events in rank order.

  Extend feedback signal with `mid_thread_added`. It is valid only as the
  exactly-once/idempotent transition from `mid_thread_removed` on the same
  event membership; a later `mid_thread_removed` may symmetrically replace
  `mid_thread_added` on that membership. A first `mid_thread_removed` may
  transition a passive `kept` or `auto_entered` membership as well as the
  standing human outcomes; otherwise the panel could not remove context
  that entered through autonomous scoring. Re-addition does not change
  corpus counters. The Harness keeps
  daemon-lifetime per-thread current, confirmed, excluded, and event-source
  sets as trusted state: first-gate survivors become confirmed; panel remove
  moves a member from confirmed/current to excluded after Spine accepts
  `mid_thread_removed`; panel add moves it from excluded to confirmed/current
  after Spine accepts `mid_thread_added`. Each post-first ordinary prompt
  supplies those sets to autonomous prepare, atomically replaces only the
  autonomous portion of current context from `final_block`, and emits an
  unprompted `memory.panel.update` state when membership changes, using a
  daemon-minted request_id and state result `rescored`. Remember
  commands retain their command path and do not invoke re-scoring. The first
  gate remains the only modal memory review. Memory-panel state items add
  boolean `thread_excluded`; only those non-context rows expose re-add.

POST /v1/memories
  req: {principal_id, label, body, kind, keywords?,
        project_key?, thread_origin?, origin_path?, editor, machine_id,
        force?: bool = false}
  beh: label check first: another ACTIVE unit of principal with this label →
       409 {label_conflict: {memory_id, label}} (force does not override;
       tombstoned/quarantined labels are reusable — see C.2 partial index);
       embed; dedup check vs active units of principal:
       max cosine ≥ 0.92 → 409 {duplicate_of: MemoryCard} (force does NOT
         override a hard duplicate; the caller's move is editing that unit);
       0.80–0.92 and force=false → 200 {created: null, similar: [MemoryCard...]}
         (caller decides, may retry with force=true; force skips ONLY this band);
       else insert (revision 1 + memory_revision row,
         origin_machine_id = machine_id).
  Dedup comparisons include only ACTIVE units with the same principal.
  `duplicate_of` is the unit with greatest cosine similarity, breaking
  equal-score ties by `memory_id` ASC. `similar` contains every such unit
  whose score satisfies `dedup_sim <= score < dedup_dup`, ordered by score
  DESC then `memory_id` ASC; M1 applies no additional result cap. The
  configured thresholds are inclusive at `dedup_sim` and `dedup_dup` as those
  inequalities state. [folded from A-003]
  res: 201 {created: MemoryUnit}

PATCH /v1/memories/{id}
  req: {expected_revision, body?, label?, keywords?, kind?, pin?, status?,
        origin_path?, editor, reason, machine_id}
  beh: CAS per C.2 rules; the revision row gets origin_machine_id = machine_id;
       a label change colliding with another active unit → 409 {label_conflict}
       as on create.
  A mutable property whose JSON value is null is treated as omitted. With
  zero remaining mutable properties, return RFC7807 422; for an absent memory
  ID, return RFC7807 404. A supplied non-null `body` is embedded before the
  CAS, and a successful CAS writes its `body`, `embedding`, and
  `embedding_model` atomically. The CAS condition is evaluated before
  active-label uniqueness: a stale revision returns `{conflict: MemoryUnit}`.
  Any successful-revision write whose resulting status and label would
  collide with another ACTIVE unit of that principal returns
  `{label_conflict}`, including reactivation without a label change.
  [folded from A-004]
  For M1, label length is measured in Unicode code points and body length is
  measured with the `cl100k_base` tokenizer. On create, and for each non-null
  replacement value supplied to PATCH, require `len(label) <= cfg.label_max`
  and `tokens(body) <= cfg.memory_max_tokens`; a violation returns RFC7807
  422 before embedding or any database write. [folded from A-006]
  res: 200 MemoryUnit | 409 {conflict: MemoryUnit}   # current unit, per C.2 rules

GET  /v1/memories?project_key=&status=&q=&limit=&offset=
  beh: panel list; limit default 50 max 200, offset default 0;
       ordered by updated_at DESC, memory_id ASC (stable paging).
  Supplied filters are ANDed; omitted `project_key` and `status` apply no
  filter. Trim `q`; blank applies no filter, otherwise it is a
  case-insensitive literal substring match over `label` or `body`. `total` is
  the filtered count before paging. Require `1 <= limit <= 200` and
  `offset >= 0`. [folded from A-005]
  res: {items: [MemoryUnit], total, limit, offset}
POST /v1/search             req: {principal_id, query, k=10, project_key?}
                            res: {results: [MemoryCard]}  (agent tool backend)
  Require `1 <= k <= 50`; a violation returns RFC7807 422 before embedding.
  An omitted or JSON-null `project_key` applies no project filter. A non-null
  `project_key` admits only ACTIVE units whose `project_key` is NULL or
  exactly equal to the request value. Results are the first k ordered by raw
  cosine similarity DESC then `memory_id` ASC. Search applies no scorer
  threshold, weights, bias, pin priority, or candidate re-ranking; `score` is
  that raw cosine and `features` / `rank` are null. [folded from A-012]

## C.5 Config defaults (single source: spine config.py / harness config.py)

tau=0.55, top_k=8, near_miss_k=3, budget_tokens=3000, budget_pct=0.05,
half_life_time_days=14, half_life_hist_days=7, dedup_dup=0.92, dedup_sim=0.80,
never_bias_step=-0.15, quarantine_kills=3, candidate_pool=50,
embed_base_url="https://openrouter.ai/api/v1" (v2.3 — D.2 044: embeddings
route through the token broker by default, same as chat; verified live
2026-07-20 with openai/text-embedding-3-small returning 1536-dim vectors;
direct-provider URLs remain a config override; the bearer-key slot accepts
any OpenAI-compatible key — OpenRouter primary),
embed_model="openai/text-embedding-3-small" (dim 1536, provider-pluggable),
memory_max_tokens=128, label_max=64.
run_request_limit=40, run_total_tokens_limit=500000 (harness; ADR-014).
Chat model defaults: development/testing `openrouter:minimax/minimax-m3`
(verified live 2026-07-19; ≈$0.30/M in, $1.20/M out); flagship
`anthropic:claude-sonnet-4-6` for real use; OpenRouter configured as the
any-model escape hatch; keys (Anthropic, OpenAI, OpenRouter) live in
harness/.env (untracked; .env.example is the shape); model selectable per
thread.

**Model selection policy (folded v2.82 — editor pass IV).** The token-cost
policy law enacted as amendments A-020, A-021, A-025 and A-026 (AMENDMENTS.md)
lives here as C.5 config law from this version; ADR-024 records the truth
after spend, this block chooses the price before it. A-021 supersedes A-020
clauses (a) and (c); A-020 is folded whole because A-021 incorporates their
mechanics by reference (the floor rule, price-sorted providers).

  **Folded amendment (A-020, v2.82)** — enacted by H9 against [SPEC C.5;
  C.6/A-019 model_context_tokens], node(s) P4; law text verbatim from
  AMENDMENTS.md; clauses (a) and (c) are SUPERSEDED by A-021
  (a-revised)/(c-revised) below and stand only as the mechanics A-021 names by
  reference:
  (a) FLAT INTELLIGENCE FLOOR. Harness config gains optional
  model_intelligence_floor (env MODEL_INTELLIGENCE_FLOOR, positive
  number, default unset). When unset, model selection is unchanged. When
  set, the daemon resolves each new thread's chat model at that thread's
  first run: among the broker's benchmark listing
  (GET /api/v1/benchmarks?source=artificial-analysis), the model with
  the lowest prompt price whose intelligence_index is >= the floor;
  ties break by completion price, then permaslug. The benchmark response
  may be cached for at most 24 hours. Selection is deterministic given
  the cached table, and the daemon logs the chosen slug, its
  intelligence_index, both prices, and the table's fetch timestamp. An
  explicit per-thread model override (existing C.5 law) beats the floor.
  Per-prompt classifier routing (openrouter/auto*) is REJECTED as law:
  model choice must be reproducible and auditable from a numeric table,
  never a third party's opaque per-prompt judgment.
  (b) THREAD-STABLE, CACHE-STICKY ROUTING. A thread's resolved model does
  not change within the daemon lifetime of that thread. Every OpenRouter
  chat request carries session_id equal to the C.7 thread_id, so the
  broker pins the serving provider (and model) from the first turn and
  KV-cache prefix reuse survives the whole run. Broker-side expiry of
  sticky state after inactivity is acceptable. Provider fallbacks remain
  enabled; the daemon never sets allow_fallbacks=false.
  (c) CHEAPEST HOST. Floor-selected requests set provider sort to price.
  Config gains optional provider_quantizations (default unset); when set
  it is forwarded verbatim as the broker's provider.quantizations filter,
  because a host serving an int4 quantization of a floor-qualified model
  silently defeats the floor. The default stays unset so an incomplete
  host table can never empty the provider pool.
  (d) CONTEXT WINDOW FOLLOWS THE MODEL. A-019 requires
  model_context_tokens to match the model in use. When the floor selects
  the model, the daemon sources that model's context length from the
  broker's model listing at selection time and sends it to
  inject/prepare for that thread, in place of the static default. The
  static MODEL_CONTEXT_TOKENS default continues to govern when the floor
  is unset.
  (e) TRUTHFUL ACCOUNTING, NO NEW SURFACE. Broker-reported usage,
  including cached-token and cache-write details, is recorded on the
  existing run usage path. ADR-014 run walls are unchanged. No cost UI
  is built in M1; Palace Vitals gauges remain M2 law.
  (f) FENCES. The floor never applies to embed_model — C.2 fixes the
  1536-dimension space and a changed embedding model corrupts it — and
  the /remember label agent simply follows the thread's resolved model
  per decision 008. Benchmark-endpoint failure, an empty eligible set,
  or a missing context length fails open to the static chat_model
  default (with its static context tokens) and a logged warning — never
  a blocked run and never a question to the human.

  **Folded amendment (A-021, v2.82)** — enacted by H9 against [SPEC C.5;
  supersedes A-020 clauses (a) and (c)], node(s) P4; law text verbatim from
  AMENDMENTS.md:
  (a-revised) TOKEN-COST POLICY TYPE. Model selection is a POLICY — a
  role-generic type with exactly five M1 values:
    pinned:<model> — the named model verbatim (existing chat_model
      syntax); no benchmark table consulted.
    max — the model with the highest intelligence_index in the cached
      benchmark table; ties break by lower prompt price, then permaslug.
    elbow — the point of diminishing returns on the cost-intelligence
      Pareto frontier, computed deterministically as follows. A model
      is DOMINATED when another model has intelligence_index >= its
      AND prompt price <= its, at least one strictly; the FRONTIER is
      the set of non-dominated models (duplicate price/index pairs
      keep the lexically first permaslug), sorted by
      intelligence_index ascending — along it, price rises with
      intelligence. Map each frontier model to
      x = (intelligence_index - min) / (max - min) and
      y = (log10 prompt_price - min) / (max - min), min and max taken
      over the frontier, so the cheapest, least-intelligent model
      sits at (0,0) and the priciest, most-intelligent at (1,1). The
      CHORD is the diagonal y = x; its slope is the frontier's own
      average exchange rate of log-price for intelligence. Each
      model's SIGNED OFFSET is x - y: positive means its log price
      sits below the chord — a better-than-average trade. The elbow
      is the model with the greatest positive offset, the deepest
      bargain relative to the frontier's average rate; ties break by
      lower prompt price, then permaslug; if no model has a positive
      offset, elbow falls back to max. This is a slope rule whose
      threshold the table computes for itself: the offset grows
      wherever intelligence is being bought below the frontier's
      average log-price rate and shrinks wherever it is bought above
      that rate, so there is no hand-set slope constant, and dense
      pockets of similar models — point spacing, not value — cannot
      move the answer the way they whipsaw a local-derivative rule.
      Worked example (index, $/M prompt): A(20, 0.10) B(35, 0.30)
      C(55, 1.00) D(60, 8.00), none dominated. log10 prices: -1,
      -0.523, 0, 0.903. x: 0, .375, .875, 1. y: 0, .251, .525, 1.
      Offsets x - y: 0, .124, .350, 0 → elbow = C. D buys five more
      index points at eight times the price; C is the last
      better-than-average trade.
      [Clause revised by the owner's pen 2026-07-28, pre-claim, in
      the audit window: terms defined for agents without the design
      session's context, and the original unsigned perpendicular-
      distance rule — which on a concave frontier could select a
      worse-than-average point — replaced by signed offset, an
      identical ranking on the intended side of the chord.]
    slope:<λ> — pay for intelligence until its marginal price
      exceeds λ, a positive number in prompt-dollars per M tokens
      per intelligence-index point (raw units, never normalized).
      Compute the frontier exactly as defined under elbow, sorted by
      intelligence_index ascending, in raw (intelligence_index,
      prompt price) space — no logs, no normalization. Take its
      LOWER CONVEX HULL: the chain of frontier models running from
      the cheapest to the most intelligent such that every frontier
      model lies on or above every chain segment — the taut string
      stretched beneath the points. Hull segment slopes
      (delta price / delta intelligence_index) are non-decreasing by
      construction, so the slope crosses any λ at most once; models
      the hull skips are strictly worse buys than the hull vertices
      bracketing them. The selection is the upper endpoint of the
      last hull segment whose slope is <= λ — the most intelligent
      model reachable while every marginal index point still costs
      <= λ. If the first segment's slope already exceeds λ, select
      the hull's cheapest vertex; if no segment's slope exceeds λ,
      select the hull's most intelligent vertex. Collinear frontier
      models on a hull segment count as hull vertices; remaining
      ties break by cheaper prompt price, then permaslug.
      Worked example: frontier (54, $0.90) (55, $1.00) (55.3, $1.60)
      (58, $1.70) (65, $9.00). Adjacent secant slopes 0.10, 2.00,
      0.04, 1.04 whipsaw around any threshold, but the lower hull is
      (54, $0.90)-(55, $1.00)-(58, $1.70)-(65, $9.00) with slopes
      0.10, 0.23, 1.04 — monotone — so at λ=0.5 the crossing is
      unique and the selection is (58, $1.70). Design intent for M3,
      non-binding, nothing built now: the frontier models a hull
      segment skips — the dense pocket around the selection — are
      natural diversity shards for Symphony search pools.
      [slope:<λ> added by the owner's pen 2026-07-28, pre-claim:
      the owner's ratified marginal-price rule; the hull is part of
      the law so the λ-crossing is unique on a discrete table.]
    [Calibration record, owner's pen 2026-07-28. On the owner's
      reference chart of the budget tier (frontier, prices read
      approximately: 44.4/$0.09, 51.2/$0.20, 55.0/$0.53, 57.1/$0.60,
      60.7/$1.15), elbow computes signed offsets 0, +.10, -.05,
      +.03, 0 and selects 51.2 — matching the owner's independently
      stated "52 today" with zero configuration. The owner's
      underlying intent, recorded for whoever tunes role policies:
      buy the COMMODITY TIER — the capability level competition has
      already priced to the bone — and do not pay the scarcity
      premium single models charge for the last few index points.
      The dense pocket of near-frontier models crowding just above
      the elbow is the market confirming that tier (the same skipped
      pocket noted under slope as M3 diversity shards); density is
      the market's echo of the elbow, not an independent selection
      signal, which is why no density policy exists. elbow is
      therefore the recommended chat policy value at enable time.
      slope:<λ> remains for expressing an absolute willingness-to-
      pay, with a measured caution: λ=0.5 is far too generous for
      the budget tier — on this chart it buys to the top; the
      owner's revealed rate there is nearer $0.03-0.05 per index
      point.]
    floor:<n> — A-020(a)'s rule unchanged: lowest prompt price with
      intelligence_index >= n; ties by completion price, then permaslug.
  Table sourcing, the 24-hour cache, per-thread resolution timing,
  logging of the choice (now including the policy value), the per-thread
  manual override, and the REJECTION of per-prompt classifier routing
  all stand as A-020 wrote them. A-020 clauses (b), (d), (e), (f) stand
  unchanged and apply to every policy; read A-020(d)'s "floor-selected"
  as "policy-selected (non-pinned)". Env MODEL_INTELLIGENCE_FLOOR is
  superseded by the policy grammar and is not built.
  (a2) ROLES. Config binds one policy per agent role. M1 has two roles:
  chat (env MODEL_POLICY_CHAT, default pinned:<chat_model> — unset
  preserves today's behavior) and the /remember label agent, which
  follows the chat thread's resolved model per harness decision 008 and
  receives no independent policy in M1. Later-milestone roles MUST adopt
  this same policy type when their milestones arrive; their config keys
  do not exist until then (B.5). Design intent recorded for those
  milestones: price a role by the blast radius and reviewability of its
  errors, not by task difficulty — high-volume judge-filtered leaf work
  runs economic policies (slope, elbow, floor); low-volume compounding roles
  (orchestrators, judges, curators) run pinned or max. A spend-rate
  policy (budget:$/hr) is deliberately absent until the M2 Vitals spend
  lanes (v2.17) supply measured burn.
  (c-revised) The quantization filter is struck, not built:
  Artificial Analysis publishes per-model indices and polices — rather
  than measures — endpoint precision, and the broker's benchmark rows
  carry no endpoint dimension. provider_quantizations does not exist.
  Non-binding future exploration: adopt endpoint-level intelligence data
  if a source ever publishes it. A-020(c)'s surviving sentence — sort
  providers by price — applies to every non-pinned policy.
  (g) DEGENERATE TABLES. elbow over a frontier of fewer than three
  points falls back to max over that frontier; all other degenerate or
  unavailable-table cases keep A-020(f)'s fail-open to the static
  chat_model default.

  **Folded amendment (A-025, v2.82)** — enacted by H9 against [SPEC C.5;
  A-021(a-revised elbow), (g)], node(s) P4; law text verbatim from
  AMENDMENTS.md:
  For elbow only, every frontier prompt price must be strictly positive. If
  any frontier prompt price is zero, the elbow table is degenerate under
  A-021(g), and resolution fails open to the static `chat_model` and static
  context tokens. Other policies continue to evaluate non-negative prices
  under their existing rules.

  **Folded amendment (A-026, v2.82)** — enacted by H9 against [SPEC C.5;
  A-020(d); A-021(a-revised), (g)], node(s) P4; law text verbatim from
  AMENDMENTS.md:
  A non-pinned policy first selects its benchmark row unchanged, then joins
  that row's `model_permaslug` to model-list rows by exact
  `canonical_slug`. A row whose `id` has no `:<variant>` suffix is a
  standard route. If exactly one standard route exists, the daemon resolves
  the request model to that row's `id` and the context window to that same
  row's positive `context_length`; explicit variant rows do not participate.
  If no standard route exists and exactly one joined row exists, that sole
  route and its context are used. No joined row, multiple standard routes,
  or multiple variant-only routes is a missing/ambiguous context under
  A-021(g) and fails open to the static model and context pair.

## C.6 MemoryCapability (harness) — exact flow

First prompt in thread:
 1. User hits send → daemon calls inject/prepare (prompt is NOT yet sent to model).
 2. WS `gate.open` → UI modal: injected rows show label + FULL body (memories
    are atomic ≤128 tokens by design; no summaries, no expand step) + score;
    one-tap ✕ = not_relevant; long-press/alt-✕ menu: wrong / never; grayed
    near-misses (tap = add back); Enter/"Continue". Hard pause, no timeout
    (per ADR-005). ACCEPTED in review 2026-07-07. Near-miss rows also carry a
    mutually exclusive NEVER control (A-022, folded at C.4 commit, v2.82).
 3. On continue → inject/commit → spine returns final_block.
 4. Capability prepends final_block system-adjacent; run starts.
**Folded amendment (A-023, v2.82)** — enacted by H5 FIXER against [SPEC C.6
step 3; A-019 gate.*], node(s) P1.2.1a; law text verbatim from AMENDMENTS.md:
The memory gate has two typed stages under the same run and hard pause.
The existing stage is `review` (the default when `stage` is omitted).
After its successful inject/commit, if `wrong_removed` is nonempty, the
daemon keeps the model stopped and, in event-rank order, replaces the
open gate with a `gate.open` whose `stage` is `wrong_resolution`, whose
existing injected and near_misses arrays are empty, and whose
`wrong_removed` array contains exactly one current MemoryUnit returned by
that commit. It may also carry a visible `resolution_error` from a prior
failed attempt. The browser answers with `gate.commit` carrying empty
removed and added_back arrays plus
`wrong_resolution:{memory_id,expected_revision,action,body?}`, where
action is `edit` or `expire`; edit requires a nonblank body and expire
forbids one.

The daemon validates the run, thread, injection, stage, exact memory ID,
displayed revision, and one-submit boundary before accepting the
resolution. Edit PATCHes that unit's body; expire PATCHes its status to
tombstoned. Both use the returned current revision, editor `user`, the
trusted run machine_id, and reason `gate/wrong:edit` or
`gate/wrong:expire`. A CAS conflict replaces the stage with the conflict's
current MemoryUnit and a visible error; any other Spine client failure
keeps the same stage open with a visible error, so neither silently loses
the requested correction nor invokes the model. Cancellation retains
A-016 ordering. Once every wrong unit is resolved, the daemon emits the
single final gate.dismiss and invokes the model with the already-committed
final_block. A review with no wrong removals remains the one-stage flow.

Subsequent prompts: no prepare/no gate in M1 (one injection per thread;
per-prompt re-scoring is an M2 decision — OQ-15). Memory panel stays live:
✕ on a memory → /v1/feedback mid_thread_removed → block re-rendered without it
from the next model call. Panel wire contract: A-024 (folded at C.7, v2.82);
re-add after autonomous re-scoring: A-030 (folded at C.4, v2.82).

Rendered block format (final_block):
```
<memory_system>
The following long-term memories were retrieved for this conversation.
Treat them as your own accumulated knowledge; they may be imperfect.
<memory label="..." kind="..." updated="ISO8601">
[body]
</memory>
...
</memory_system>
```

  Join structural lines with LF, with no blank separator lines and no
  terminal LF. Emit attributes in `label`, `kind`, `updated` order. In
  attributes escape `&`, `<`, `>`, and `"` as `&amp;`, `&lt;`, `&gt;`, and
  `&quot;`, and encode tab, LF, and CR as `&#9;`, `&#10;`, and `&#13;`. In
  body text escape only `&`, `<`, and `>`; preserve every other character and
  existing newline. Use frozen event values verbatim apart from that
  escaping. With zero members, return these four lines exactly:
  `<memory_system>`, the two literal preamble lines from the template, and
  `</memory_system>`. [folded from A-011]

Agent tools (tools_memory.py; docstrings are the model-facing spec):
- save_memory(label, body, kind, keywords?, project_scoped: bool,
  force: bool = false)
  → handles 409/similar responses by surfacing them to the model
    ("similar memory exists: ...; update it, or call again with force=true").
  Forward force unchanged to C.4 POST /v1/memories. The tool never enables
  or retries force automatically. `true` skips only the near-similar band and
  does not override label or hard-duplicate 409s. [folded from A-015]
  NO SILENT SCOPE BROADENING (v2.16; owner consult on F008): when a
  project_scoped save finds no active project context, the tool surfaces
  the missing-context result to the model and the agent MUST NOT fall back
  to a global save without explicit user confirmation — persistence scope
  is never widened silently.
- search_memory(query, k=5) → MemoryCards rendered compactly.
- edit_memory(label_or_id, new_body, reason) → CAS retry once on 409.
Agent instructions addition (verbatim, part of capability):
  "Save a memory when you learn a durable user preference, a correction to
   something you got wrong, a stable project fact, or a procedure the user
   wants repeated. Keep every memory ATOMIC: one fact per unit, at most a
   few sentences (hard cap 128 tokens); split larger content into multiple
   units. ALWAYS include 2-5 lowercase keywords (searchable nouns/terms —
   a memory without keywords is handicapped in retrieval). Prefer editing
   an existing memory over creating a near-duplicate.
   Never save secrets or credentials."
User command: `/model <openrouter-model-string>` (v2.26; D.2 069) → an
explicit MODEL RESOLUTION POINT: the daemon re-resolves the thread's model
to the named slug (validated against the broker model list; unknown slug →
visible error, no change), re-fetches that model's context window, breaks
cache stickiness into a new epoch (session stickiness re-pins on the next
request), journals a model_change event (old→new slug, reason
human_command, timestamp — the sacrificed cached-prefix size rides the
event for the future spend ledger), and updates the H8 header display.
MOTIVATION (read this before implementing): the stability law's enemy was
always DRIFT — a model switch nobody chose, silently re-billing a thread's
entire cached prefix at full input price. A human command is different in
kind: a CONSCIOUS PURCHASE of a cold cache, priced and journaled. And this
seam is not a demo affordance — it is the exact mechanism the M2 selector
knob and M3's cost-optimizing role policies will ride when THEY switch
models at work boundaries; J1 certifies it early because it is real
product surface. LAW REFINEMENT (F012 resolution): A-020(b)/A-021 thread
stability means a
thread's model changes ONLY at explicit journaled resolution points —
thread start, this human command, or (M3, reserved) an algorithmic role
boundary — NEVER by drift (broker load-balancing, mid-thread policy
re-evaluation, or benchmark-table refresh). The M2 selector knob and M3
role policies bind to this same seam.
User command: `/remember <text>` → daemon calls POST /v1/memories with
kind=fact, editor='user', label AND 2-5 keywords auto-generated by the agent
model (one short completion produces both; v2.7 — keyword-less units were
observed handicapped on f_kw in live gate-day data), then confirms in chat.

## C.7 WS envelope (relay-shaped from day one)

All daemon↔browser messages:
```
{v:1, id: ulid, ts, machine_id, agent_id?, thread_id?, type, payload}
```
M1 types (as amended v1.12 — D.2 037):
- thread.create, prompt.submit, gate.open, gate.commit, run.delta, run.done,
  memory.panel.update, error — as before, plus the field requirements below.
- run.started (D→C): acks prompt.submit; payload {run_id}. Every subsequent
  run.* / gate.* payload carries that run_id.
- run.cancel (C→D): request clean abort. The daemon aborts model work,
  dismisses any open gate, and ALWAYS answers with
  run.done{stop_reason:"cancelled"} — cancellation is confirmed, never
  fire-and-forget.
- prompt.submit received mid-run is NOT an error: the daemon acks with
  prompt.queued (D→C) and delivers the prompt as a fresh turn when the
  current run ends (M1 queues to the turn boundary; mid-turn steering is
  reserved for M3 — ADR-014).
- thread.snapshot (D→C): authoritative full resync — message history, open
  gate, active-run state — sent on WS (re)connect for the active thread and
  on request; the UI hydrates from snapshots, never from replayed deltas.
- run.usage (D→C): incremental {requests, input_tokens, output_tokens}
  during a run.
- gate.dismiss (D→C): the daemon withdraws an open gate (e.g. its run was
  cancelled); the UI must close the modal.
Field requirements: run.done carries stop_reason ∈ end_turn | cancelled |
error | budget_exceeded. run.delta payload is a discriminated union
{kind: text | thinking | event, ...} (AG-UI events pass through under
kind:"event"). gate.open carries kind (M1: always "memory_gate") so the
gate generalizes to tool approval / elicitation / plan review in M3.
RESERVED M3 type names (relay and clients MUST forward/ignore unknown
types unchanged): run.steer, plan.update, checkpoint.created,
checkpoint.restore, presence.update. The relay in M3 forwards all types
unchanged; nothing in the UI may assume localhost.

  Each browser-to-daemon message occupies exactly one JSON text frame whose
  top-level value validates as the C.7 envelope. A binary frame, invalid
  JSON, non-object JSON value, or object that fails envelope validation is
  malformed. On the first malformed message, the daemon invokes no type
  handler, closes that `/ws` connection with WebSocket code 1008 and reason
  `invalid C.7 envelope`, and performs no further processing on that
  connection. [folded from A-013]

  `run_id` is a daemon-generated ULID allocated once when prompt.submit is
  accepted, including when it is queued; `prompt_id` is that inbound
  envelope's `id`. prompt.submit has a non-blank string `prompt` and requires
  outer `thread_id`. These payload members are required: run.started
  `{run_id,prompt_id}`; prompt.queued `{run_id,prompt_id}`; run.cancel
  `{run_id}`; run.delta text/thinking
  `{run_id,kind:"text"|"thinking",text:string}` or event
  `{run_id,kind:"event",event:object}`; run.usage
  `{run_id,requests,input_tokens,output_tokens}`; run.done
  `{run_id,stop_reason,partial}`; gate.open
  `{run_id,kind:"memory_gate",...}`; gate.commit `{run_id,...}`; and
  gate.dismiss `{run_id,...}`. Usage fields are non-negative integer
  cumulative totals for that run and never decrease; C.7's "incremental"
  means that updated cumulative snapshots may be emitted while the run
  advances. `stop_reason` is
  `end_turn|cancelled|error|budget_exceeded`; `partial` is false exactly for
  end_turn and true otherwise. These are minimum object members: additional
  JSON members are allowed and preserved so later gate and event contracts
  can extend them.

  thread.snapshot is bidirectional. A C→D request requires outer
  `thread_id` and payload `{request:true}`. Its D→C response carries
  `{messages,open_gate,active_run}`: `messages` is the daemon's ordered array
  of JSON message objects, including queued prompts and already-produced
  partial work; `open_gate` is null or the current gate.open payload; and
  `active_run` is null or `{run_id,prompt_id,state,usage,queued}`, with state
  `running|waiting_gate|cancelling`, usage in the run.usage shape without a
  second run_id, and queued an ordered array of
  `{run_id,prompt_id,prompt}`. Additional members are allowed and preserved.
  For M1 the daemon keeps one process-local active thread, selected by the
  latest valid thread.snapshot request or prompt.submit. On every WS connect
  it sends exactly one snapshot before live events when that thread exists;
  when none exists it sends none until a request or prompt selects one.
  Requesting an unknown thread returns the empty snapshot. Thread/run state
  survives a socket disconnect for the daemon process lifetime; reconnect
  sends the snapshot only and never replays old deltas or other prior
  events. This local selection is not a session or authorization boundary.

  A prompt received while its thread has a live run is appended once to a
  process-local FIFO and immediately acknowledged by prompt.queued. After
  every terminal run.done, the oldest queued prompt starts once: the prior
  run.done is emitted before its run.started, and the reserved run_id is
  reused. Cancellation applies only to the matching active run. The daemon
  first requests cancellation, awaits model/tool-batch termination and
  records terminal cancelled tool results, preserves all prior
  messages/output, emits gate.dismiss before run.done when a gate was open,
  and emits exactly one run.done(cancelled); no run.delta or run.usage for
  that run follows it. A duplicate cancellation while cancellation is
  pending shares that one confirmation. A stale, unknown, or already-
  terminal run_id produces error `{code:"run_not_active",run_id}` and
  cancels nothing. Queued prompts survive every terminal reason.

  Every daemon-created envelope has a fresh outer ULID `id` and timestamp;
  prompt_id supplies acknowledgement correlation. `type` accepts any
  non-blank string. The required payload validation above applies to known
  M1 behavior types. Reserved names and all other unknown types retain
  arbitrary JSON payloads and pass outer-envelope validation; an endpoint
  with a forward target forwards them unchanged, and an M1 daemon with none
  ignores them without emitting the not-implemented error. A-013 malformed-
  frame handling remains unchanged. [folded from A-016]

  In M1, daemon-authored `thread.snapshot.messages` use these minimum JSON
  objects. A user message is `{message_id,run_id,role:"user",content,state}`
  where `message_id` is the run's prompt_id, both IDs are ULIDs, content is
  a string, and state is one of `queued`, `running`, `end_turn`,
  `cancelled`, `error`, or `budget_exceeded`. An assistant message is
  `{message_id,run_id,role:"assistant",content,thinking,events,partial}`
  where `message_id` equals `run_id`, both are ULIDs, content and thinking
  are strings, events is an array of JSON objects, and partial is boolean.
  Additional JSON members are allowed and ignored by clients that do not
  understand them. A matching snapshot replaces the browser's transcript,
  open gate, active run, and usage for that thread; it is never merged with
  cached messages or prior deltas.

  The M1 browser owns a per-browser navigation catalog of
  `{thread_id,title,created_at,updated_at}` in local storage. `thread_id` is
  a browser-generated UUID, timestamps are ISO 8601 strings, and title is
  `New thread` until the first submitted prompt, then the first 48 Unicode
  code points of that prompt after collapsing whitespace, with `…` appended
  exactly when the normalized prompt exceeds 48 code points. `created_at`
  is fixed at catalog insertion; `updated_at` changes on each prompt submit.
  Creating or selecting a catalog entry sends a `thread.snapshot` request
  with payload `{request:true}` for that UUID; the first prompt creates
  daemon state through the existing prompt.submit path. The catalog is only
  local navigation metadata: snapshot remains transcript authority, the
  daemon provides no M1 thread enumeration or persistence, and
  `thread.create` gains no M1 behavior.

  A browser-authored direct-link envelope uses the literal machine_id
  `direct` until it has observed a daemon envelope, then echoes the latest
  non-blank daemon machine_id. The M1 direct daemon does not consult this
  field for identity or authority; future relay targeting supersedes the
  direct-link sentinel. Every browser-authored envelope still receives a
  fresh ULID and timestamp.

  When bounded live delivery or a connection outbox drops a subscriber, the
  daemon closes that WebSocket with code 1013 and reason
  `snapshot resync required`. The browser reconnects, requests its selected
  thread snapshot, and replaces local thread state before consuming later
  live events; it does not poll or replay buffered deltas.
  [folded from A-017]

  **Folded amendment (A-024, v2.82)** — enacted by H6 against [SPEC C.7
  memory.panel.update; C.6 live panel], node(s) P1.2.1d, P1.3; law text
  verbatim from AMENDMENTS.md:
  `memory.panel.update` is bidirectional and requires outer `thread_id`.
  Its C→D payload is a discriminated union with exactly these required
  members:
    `{action:"refresh"}`;
    `{action:"remove", memory_id}`;
    `{action:"edit", memory_id, expected_revision, body}`;
    `{action:"pin", memory_id, expected_revision, pin}`.
  Memory IDs are UUIDs, `expected_revision >= 1`, body is a string, and pin
  is boolean. The D→C payload is one of:
    `{action:"state", request_id,
      result:"refreshed"|"removed"|"edited"|"pin_changed",
      items:[{memory:MemoryUnit,in_context:bool}], total}`;
    `{action:"conflict", request_id, operation:"edit"|"pin",
      memory:MemoryUnit, message}`;
    `{action:"error", request_id,
      operation:"refresh"|"remove"|"edit"|"pin", code, message}`.
  `request_id` echoes the triggering C→D envelope id. `code` and `message`
  are safe display strings and never carry credentials, response bodies, or
  raw exception text.

  The daemon derives principal, machine, editor, PATCH reason, and current
  injection ID from trusted configuration and thread state; the browser
  supplies none of them. State contains every ACTIVE C.4 list unit for that
  configured principal, in C.4 stable order, and `total` is that filtered
  count. `in_context` is true only for a member still retained in the
  thread's successfully committed injection.

  Remove is valid only for an `in_context` unit. It submits C.4
  `mid_thread_removed` using the server-held injection ID. Only after
  `{ok:true}` does the daemon remove that unit from the thread's current
  rendered block and add its ID to the thread's memory-tool exclusions;
  every later model call receives exactly that updated block, with stale
  dynamic memory blocks removed from provider history. Failure changes
  neither context nor exclusions.

  Edit and pin call C.4 PATCH with the displayed expected revision,
  daemon-owned `editor="user"` and machine ID, and reason `panel/edit` or
  `panel/pin`. The daemon never retries a human CAS conflict: it returns the
  conflict's current MemoryUnit so the UI replaces the stale row, visibly
  explains the conflict, and requires a fresh action. A successful mutation
  returns a refreshed state. Pin affects future injection candidacy; neither
  edit nor pin rewrites the already-frozen current-thread injection.

## C.8 M1 acceptance criteria

1. Fresh clone → `docker compose up` (spine+postgres) + `harness dev` →
   browser chat works end to end with any pydantic-ai model string.
2. Telling the agent a preference results in a memory (visible in panel) and
   a 409/similar path is demonstrable by repeating it.
3. New thread on a related topic → gate shows that memory with per-feature
   scores; removing it as "not relevant" logs outcome and it is absent from
   final_block; near-miss add-back works and is logged.
4. "never" removal 3× quarantines the unit (stops appearing).
5. /remember works; edit via panel bumps revision and memory_revision row.
6. injection_event table replays: every gate interaction reconstructable
   (features, prompt_text, scorer_version, outcome) — verified by a test.
7. Kill spine mid-thread → daemon surfaces a clear error; chat without
   injection still functions (memory failures never brick the harness).

## C.9 M1 Judge Protocol (concrete checklist; expands C.8 per B.6)

Judge persona: "the owner opens the product for the first time." Every J-item
records experiential + traced + (where named) adversarial evidence under
`verification/m1/`.

- **J0 — Scope & journal audit.** Every DECISIONS.md entry in both repos
  cites a tree node. Grep for FORBIDDEN code (weight updates, extraction,
  relay client, maintenance jobs, multi-principal auth). Any hit: FAIL.
- **J1 (AC1) — Cold start.** Fresh clone both repos → `docker compose up` +
  `harness dev` → browser loads chat. Screenshot: empty state. Send "hello"
  on the default model; retain that exchange's hello-specific daemon trace.
  Then `/model <distinct OpenRouter model string>` (the v2.26 resolution-
  point command); one exchange. Evidence couples: both exchanges' traces
  under the SAME thread, the header showing the new resolved model, the
  journaled model_change event (old→new slug, reason human_command, new
  stickiness epoch), and run.delta envelopes carrying the C.7 shape.
  [Rewritten v2.26, resolving F012: the original same-thread switch is now
  lawful because switching happens at an explicit journaled resolution
  point — never by drift.]
- **J2 (AC2) — Accumulation & curation.** Tell the agent a durable
  preference ("I prefer tabs over spaces"). Screenshots: agent acknowledges;
  panel shows the new memory (label + full body ≤128 tokens). Trace:
  memory_unit row (revision 1, embedding NOT NULL), memory_revision root
  (parent_uid NULL, editor 'agent:<id>'). Re-state the same preference in
  fresh words → screenshot the agent handling the similar/duplicate path.
  Trace: 409 or similar[] response in spine log.
- **J3 (AC3) — The gate, end to end.** New thread on a related topic.
  Screenshot: gate open, that memory present with per-feature scores;
  near-misses grayed. Remove one as not_relevant; add one near-miss back;
  continue. Trace: injection_event outcomes = removed:not_relevant /
  added_back / kept, features JSONB populated, scorer_version='v0'; the
  final_block in the daemon log excludes the removed unit and includes the
  added one. Cross-check: injection_id on screen == rows traced.
- **J4 (AC4) — Quarantine.** Across three fresh threads, remove the same junk
  memory with "never." Screenshots of each modifier menu. Trace:
  stats.never_kills increments to 3, bias steps −0.15 each, status flips to
  'quarantined'. Fourth thread: gate screenshot proves absence; prepare
  response traced without it.
- **J5 (AC5) — Human hands.** `/remember` a fact → screenshot confirmation +
  panel. Edit a memory body in the panel → trace: revision bumped,
  memory_revision row with editor='user', parent_uid = prior head. Force a
  CAS conflict (stale expected_revision via curl) → 409 traced, UI surfaces
  fresh state, no silent overwrite.
- **J6 (AC6) — Replay integrity.** Run the replay test suite; then manually:
  pick J3's injection_id, SELECT its events, and reconstruct the gate the
  judge saw — memories, scores, outcomes must match J3's screenshots
  one-for-one. This is the log-through-functionality trace.
- **J7 (AC7) — Adversarial: memory death.** Mid-thread, kill the spine
  container. Screenshots: clear error surfaced; chat continues functioning.
  Start a NEW thread: proceeds memoryless with visible warning (default
  stands). Trace: daemon logs graceful degradation, zero stack-trace crashes.
  Restart spine; next thread's gate works.
- **J8 — Mobile.** Repeat J3 at 390×844. Screenshots: gate readable, full
  bodies visible, one-tap remove and near-miss add work by tap, chat usable.

Verdict file: `verification/m1/VERDICT.md` — J0–J8 → PASS/FAIL → evidence
paths → tree nodes. All PASS = M1 done.

## C.10 Agent Zero — the n=0 bootstrap charge (verbatim; paste as the first /goal)

```
You are Agent Zero, the groundskeeper. You prepare the environment; you
build NO features. Your successors are gardeners; your job is dirt, water,
and fences.

READ FIRST, fully, in order: docs/SPEC.md sections 1 -> 2 -> B -> C.
You are governed by its CONTRACTS (1.4), the feature ledger (B.4), and the
Blight Protocol (2.1). The ledger's FORBIDDEN column applies to you
absolutely: scaffolding only, zero business logic.

TASKS
1. Create two git repositories per C.1: `spine` and `harness`, with exactly
   the listed skeletons. Commit the current spec into each at docs/SPEC.md
   with its version noted (frozen reference copy).
2. Prime DECISIONS.md in each repo:
   - Entry 000: "Read docs/SPEC.md 1 -> 2 -> B -> C before touching dirt.
     Every entry in this journal cites a Problem Tree node. Local defects
     follow the Blight Protocol (SPEC 2.1). Features that cannot name their
     problem do not get built."
   - Entry 001: your own bootstrap decisions (tooling choices etc.), each
     citing node P4.
3. spine: pyproject (deps per C.1); docker-compose.yml (postgres:16 +
   pgvector, spine service); alembic initialized with the FULL C.2 DDL as
   migration 0001; config.py with every C.5 default; seed row
   scorer_config('v0', C.3 weights/params, active=true); routers for every
   C.4 endpoint returning 501 with an RFC7807 body naming the endpoint;
   /healthz returning {ok, version}; auth middleware (static bearer);
   pytest + testcontainers wired with one passing test (migration applies,
   healthz 200, 501s are 501).
4. harness: pyproject (deps per C.1); daemon.py serving web/ static + WS /ws
   echoing valid C.7 envelopes (type 'error', payload 'not implemented');
   envelope.py with the C.7 schema as pydantic models + tests;
   spine_client.py typed stubs mirroring C.4 bodies; web/: Vite React TS
   scaffold rendering a placeholder shell (responsive, 390px sane);
   .env.example (SPINE_TOKEN, ANTHROPIC/OPENAI/OPENROUTER keys, model
   default per C.5).
5. Create verification/ in both repos with a README quoting B.6.
6. CI stub in each: lint + tests; harness CI includes a contract-test job
   skeleton targeting a spine container.

WHERE THE SPEC IS SILENT on scaffolding minutiae (linter, formatter, port
numbers), decide in the spirit of the Invariants, record in DECISIONS.md
citing P4. Where a decision would touch a CONTRACT: if it qualifies as a
COMPLETION (1.4), enact it via garden/AMENDMENTS.md and proceed; otherwise
stop and flag the human.

EXIT CRITERIA (self-verify, evidence in verification/bootstrap/):
- `docker compose up` boots postgres + spine; /healthz 200; migration clean.
- `harness dev` serves the shell; WS echo works; envelope tests pass.
- Both DECISIONS.md primed; spec copies committed; CI green on the stubs.

FINISH by writing BOOTSTRAP_REPORT.md at each repo root: what exists, what
is stubbed (every 501), and the exact C-section where the next agent begins
(C.2 rules -> C.4 memory endpoints first). You are done when a gardener can
start planting without asking where anything is.
```

---
---

# APPENDIX

## D.1 Open questions (all currently open items; everything else is folded
into its owning ADR above)

- **OQ-5:** Citation signal — n-gram heuristic (M2 v1) vs LLM judge (cost).
  [M2]
- **OQ-12:** Localhost fallback page for offline prompt entry — which
  milestone. [M3 planning]
- **OQ-18:** The full Palace scene (pale monumental architecture, ghost
  curator drones restructuring memory live) — dedicated design workstream.
  [M3 planning; aesthetics anchored in NATES_VISION §8]
- **OQ-19:** Shared-Palace federation — can contributions flow between
  SEPARATE spine deployments, or does a team share one spine? (ADR-020
  assumes one; federation would need cross-deployment identity + the
  ADR-011 sync path.) [M4 planning]

## D.2 Decision log (chronological, immutable)

| # | Date | Decision | Status |
|---|------|----------|--------|
| 001 | 2026-07-07 | Pydantic AI v2, Code Puppy as donor | ACCEPTED |
| 002 | 2026-07-07 | Memory = HTTP service + MemoryCapability | PROPOSED |
| 003 | 2026-07-07 | Cloud Run + Cloud SQL pgvector; degraded read-only offline | ACCEPTED |
| 004 | 2026-07-07 | CAS revisions, tombstones, thread snapshots, pin-promotion | PROPOSED |
| 005 | 2026-07-07 | Linear scorer; one-tap removal = "not relevant"; near-misses | PARTIAL |
| 006 | 2026-07-07 | Presence rides memory service (segregated module) | PROPOSED |
| — | 2026-07-07 | Memory block placement: system-adjacent | PROPOSED |
| — | 2026-07-07 | Durable execution: pydantic-ai API + DBOS on same Postgres | PROPOSED |
| 008 | 2026-07-07 | Web command center, no TUI; harness daemon; relay-through-spine | ACCEPTED |
| — | 2026-07-07 | M1 vertical slice: hand-set weights + full logging; learning in M2 | PROPOSED |
| 009 | 2026-07-07 | Pure relay topology; AG-UI events inside daemon | ACCEPTED |
| 010 | 2026-07-07 | Learning scope: context-rich logs (M1) → global (M2) → hierarchical offsets (M3) | ACCEPTED |
| 011 | 2026-07-07 | Two repos: `spine` and `harness` (web UI lives in harness) | ACCEPTED |
| 012 | 2026-07-07 | M1 re-scoped to core memory loop; visuals deferred | ACCEPTED |
| 013 | 2026-07-07 | M1 accumulation = agent tool + /remember; auto-extraction = M2 | PROPOSED |
| 014 | 2026-07-07 | M1 browser→localhost daemon with relay-shaped envelope | ACCEPTED |
| 015 | 2026-07-07 | Review R1: gate + once-per-thread confirmed; memories atomic ≤128 tok, no summary field, gate shows full bodies | ACCEPTED |
| 016 | 2026-07-07 | Viz suite: Context Bars / Ant Farm / Memory Graph + hyperparam console; every channel encodes data; scorer tweaks versioned | ACCEPTED |
| 017 | 2026-07-07 | Responsive SPA from day one; PWA in M3; mobile is the web app | ACCEPTED |
| 018 | 2026-07-07 | ADR-001 amended: polyglot-by-boundary; Rust daemon pre-approved with triggers; no speculative Rust | ACCEPTED |
| 019 | 2026-07-07 | ADR-010: workspace-anchored fleet; cloud agents = rented machines; provisioner M4; colocation-for-latency rejected | ACCEPTED |
| 020 | 2026-07-07 | ADR-011: sailing end-state; Git owns workspaces; per-unit 3-way merge; logs union; learned state re-derived; ULID revisions + event_uids in M1 schema; M5 blue-water | ACCEPTED |
| 021 | 2026-07-07 | Scope law: commitment tiers; two pillars, viz = layer of P2; feature ledger with FORBIDDEN state; single-user through M3 | ACCEPTED |
| 022 | 2026-07-07 | Spec reorganized to v1.0 (pillar-grouped ADRs, invariants, scope law, appendix); content-preserving; ADR-004 Part-I schema drift removed (Part C DDL is sole authority) | ACCEPTED |
| 023 | 2026-07-07 | Excisions: f_sess feature, description column, ant-farm animations, graph lasso, plan-mode compaction (parked), M1 SSE. Demotions: M1 agent = chat + memory tools only; harness parity + DBOS → M3 | ACCEPTED |
| 024 | 2026-07-07 | Normativity model (1.4): CONTRACTS followed literally; GUIDANCE followed by reasoning with recorded deviations in DECISIONS.md; ADR style law | ACCEPTED |
| 025 | 2026-07-07 | Problem Tree (§2): nested problem→solution lineage with citable node IDs; Blight Protocol (locate deepest node, treat locally, escalate landscaping); feature-attachment corollary | ACCEPTED |
| 026 | 2026-07-07 | Founding-prompt audit: all original objects verified represented; ADR-002 coupling made explicitly bidirectional; f_sess divergence recorded in D.4 with resurrection condition | ACCEPTED |
| 027 | 2026-07-07 | Verification doctrine (B.6): experiential + traced + adversarial evidence, judge/builder separation, verdict artifacts, judgeable scope audit; M1 checklist J0–J8 (C.9); Agent Zero verbatim charge (C.10) | ACCEPTED |
| 028 | 2026-07-17 | v1.5 human-gate amendment resolving Garden F001–F005: active-label partial unique index + label_conflict 409 (F001); force flag on create, similar-band only, never overrides ≥0.92 (F002); machine_id on create/PATCH → origin_machine_id (F003); commit returns wrong_removed: [MemoryUnit] (F004); MemoryUnit shape + limit/offset paging + PATCH 200/409 bodies (F005); create returns MemoryUnit; card score = similarity in dedup/search | ACCEPTED |
| 029 | 2026-07-17 | Normativity (1.4) gains COMPLETION class: agents self-resolve silent/self-inconsistent contract details by enacting exact-diff entries in garden/AMENDMENTS.md — law for later agents and the judge, human audit-with-veto between sessions (veto → FIXER). Hard FLAG reserved for Invariants, FORBIDDEN rows, auth/data-loss, ADR reversals, changes to DONE-packet behavior, and genuine design forks. Motivated by F001–F005 all being qualifying completions that stopped the line | ACCEPTED |
| 030 | 2026-07-19 | v1.6 location law: origin_path on memory_unit + C.4 surfaces (M1 inert metadata; S5 packet); f_loc = 2^(−hops/h_loc) directory distance with weight renormalization when location is null (no-penalty CONTRACT); movement law — agents must cd to a file's directory to work on it, every move refreshes injected memories (CONTRACT from the M3 fs milestone) | ACCEPTED |
| 031 | 2026-07-19 | Command center primary interaction = flashcard deck: one card per top-level agent in its fleet color (shared across all visualizers), completion-time FIFO, respond-to-advance, manual deck cycling; card expands to thread view with a line-per-human-input scrubber; gallery mode (≤4 multiplexer tiles) secondary; sub-agents card-less, visualizer-only | ACCEPTED |
| 032 | 2026-07-19 | v1.7 ADR-012 work protocol: ALL project work = spec alignment → agent loop (system-sized N parallel worktree attempts ≤ max_parallel_project_agents) → independent judge (COMPLETE → deck card; else continuation agent; picks swarm winner, may graft) → human. Judge triages blockers; interjection solo-run only, watching always; no size exceptions — the spec scales down instead | ACCEPTED |
| 033 | 2026-07-19 | v1.8 config: dev/test chat default openrouter:minimax/minimax-m3 (live-verified; sonnet remains flagship); D1 executed — GCP project n8-memory-palace (us-central1), Cloud SQL Postgres 16 + pgvector (db-f1-micro), Cloud Run spine, $100/mo budget with 50/90/100% alerts; /v1/search (only remaining 501) assigned to new packet S6 | ACCEPTED |
| 034 | 2026-07-19 | v1.9 ADR-013 framework seam: internal capability protocol + single bidirectional adapter to pydantic-ai v2; import fence outside the adapter; wrap-on-first-use; outbound features ship as standard Capability subclasses (MemoryCapability first at H3); adoption targets — defer_loading (opt-in), CodeMode (must preserve movement-law refresh), ProcessHistory compaction chassis, cost tracking. Never blocked on upstream; 0.x churn contained in the adapter | ACCEPTED |
| 035 | 2026-07-19 | v1.10 vernacular (1.0): Memory Palace = memory product (db + curation algorithms + M3 curator; the spine's memory module, extractable); spine = always-on backbone housing the Palace + connective modules; Harness = local product; Garden = governance; relay = methodology (vs spine's relay module); heart = description not name; local dir names load-bearing, remote names labels | ACCEPTED |
| 036 | 2026-07-19 | v1.11 deconflict pass: OQ-14 resolved — repo names fixed by 1.0, C.1's "cosmetic rename allowed" removed (do NOT rename); vision §0 and Invariant 1 aligned to Palace/spine wording. Full-term audit found no other conflicts: "heart" reads as description throughout, relay methodology/module disambiguated by 1.0. No semantic change | ACCEPTED |
| 037 | 2026-07-20 | v1.12 after 3-agent harness-field survey: C.7 gains run.started/run.cancel (confirmed)/prompt.queued/thread.snapshot/run.usage/gate.dismiss, stop_reason on run.done, typed run.delta, gate.open kind field, reserved M3 names (run.steer, plan.update, checkpoint.*, presence.update); ADR-014 prime loop — interruption preserves work, three-level send gesture (queue M1 / steer / interrupt M3), budgets as loop inputs with distinct terminal status (C.5: run_request_limit=40, run_total_tokens_limit=500k); H7 packet implements the M1 subset before H4. Permissions + sessions ADRs deferred pending human discussion | ACCEPTED |
| 038 | 2026-07-20 | v1.13 Invariant 14 LEAST ATTENTION codified as core philosophy: attention is the scarcest resource and the architecture decides when it is spent; walls not questions (per-action approvals inside the sandbox are forbidden theater); pulls only at boundary crossings + judge releases, once, at highest leverage; all watchable, almost nothing demands. Ground-rules template gains rule 9 so every relay agent boots into it | ACCEPTED |
| 039 | 2026-07-20 | v1.14 B.6 rule 7: UI/visualizer packets verify by EMULATED HUMAN USE from the first packet — browser automation drives the real rendered interface, screenshots at every acceptance state are first-class builder evidence (verification/<packet>/), assertions on rendered outcomes never internals. Motivated by mock iteration: cascade collisions and encoding drift are invisible to unit tests; the screenshot is the test. Applies to H4/H5/H6 and every viz packet after | ACCEPTED |
| 040 | 2026-07-20 | v1.15 garden/NATES_VISION.md codified (GUIDANCE per 1.4): concatenated owner intent from the 2026-07-19/20 human-gate sessions — Invariant-14 soul, founding differentiators, organism/time thesis, modes-as-music with movable judge seat, ADR-012 protocol + conductor drafts, Symphony search (4 operators, portfolio budgets, MEASURED/JUDGED, lessons to Palace), the Cube (faces, selection, memory-trace sanctity, Ableton rack, aesthetics: black/white/orange chrome-seraph), stack (R3F/WebGPU hybrid, Chromium-only ≤M4), quality (B.6 r7), anti-vision. UI-surface packets read it at Boot STEP 5; full ADR-015/016/017 codification still pending human blessing | ACCEPTED |
| 041 | 2026-07-20 | v2.0 EDITOR PASS (content-preserving, mirroring 022's v0.x→v1.0 consolidation): ADR-012 gains the mode scale (Solo/Duet/Ensemble/Symphony, movable judge seat); ADR-015 walls & boundaries, ADR-016 two-ledgers/one-tree, ADR-017 Symphony search, ADR-018 Cube + plugin rack + resolved stack (R3F/WebGPU, Chromium-only ≤M4) authored from the gate sessions with Verification clauses per B.6; ADR-007 converted to a parity index; amendments A-001–A-017 folded verbatim into Part C (AMENDMENTS.md preserved as historical record; future completions still enact there first); header lineage compressed; D.1 gains OQ-17/18. No semantic change to built behavior | ACCEPTED |
| 042 | 2026-07-20 | v2.1 ADR-018 clause 6 PROCEDURAL LAW: all stage geometry is a deterministic function of work metadata, seeded by stable ids — model-like 3D parts GROWN by generators (roots from search metadata, colonies from the live directory tree, Palace from memory populations), never hand-authored; same data → same geometry, so as_of scrubbing re-runs generation; plugins parameterize generators, never emit meshes; determinism is directly judgeable (double-render diff) | ACCEPTED |
| 043 | 2026-07-20 | v2.2 ADR-018 clause 7 THEMES: aesthetics are swappable style configurations on the rack (tokens/motifs/materials, user-authorable, plugin-distributed, never code). Default = NEO-NOIR (Syd Mead tech-noir: neon on dark urban, street-level composition, holographic displays, rain-slicked materials, volumetric light — owner's vocabulary preserved verbatim in NATES_VISION §8); alternate = COBALT-SERAPH (the mock's current skin). Themes style meaning, never re-encode it; per-theme palette validation mandatory; Invariant 14 is not a style | ACCEPTED |
| 044 | 2026-07-20 | v2.3 broker-routed embeddings (owner's token-broker thesis; Invariant 13 completed): C.5 gains embed_base_url defaulting to OpenRouter, embed_model becomes the namespaced openai/text-embedding-3-small; the key slot accepts any OpenAI-compatible bearer. Verified live against OpenRouter's /embeddings before enactment. Chat AND embeddings now flow through one third-party broker by default; direct providers are overrides. New packet S7 wires config→adapter; D1 no longer requires OpenAI credits | ACCEPTED |
| 045 | 2026-07-20 | D1 factual/operations correction (no product-contract or version change): live audit proved D.2 033's "D1 executed / Cloud Run spine" status clause false; immutable row 033 remains as the historical record and this entry supersedes only that factual status claim. The foundation actually present is the active project, budget, and bare Cloud SQL instance. Garden D1 is reset as a relay-owned packet with authority narrowly bounded to the named app database/user, migration, backup protection, secrets, dedicated runtime identity, regional image repository, one Cloud Run service, and remote verification. GCS remains M4; billing/budget/D2, deletes, broad IAM, Cloud Build, and destructive recovery remain human boundaries | ACCEPTED |
| 046 | 2026-07-20 | v2.4 CHRISTENING + ADR-019: the product is NOCTURNE (night music — plays through the night so the composer hears the premiere; install name pipx install nocturne); Escher view = the Cube; Chrysopoeia = the learning loop. ADR-019 onboarding: two-secrets rule (OpenRouter always, GCP cloud-only; local mode = ONE secret), four contract commands (init/up/deploy/open), two wheels + bundled web assets + lockstep versioning, the Garden never ships; packet D3 opens after J | ACCEPTED |
| 047 | 2026-07-20 | ADR-020 SHARED PALACES (HORIZON like ADR-011; build forbidden until multi-tenant identity, M4): a shared Palace is a principal; contribution = consent-based selective COPY-WITH-LINEAGE (global rev_uid DAG already carries cross-palace provenance — zero schema footprint needed); revocation = tombstone the copy; collisions ride the existing dedup bands + curator merge queue + ADR-011 conflict taxonomy (combining palaces ≡ reconciling replicas); injection draws from the palace union with provenance in the gate; federation across spines = OQ-19 | ACCEPTED |
| 048 | 2026-07-21 | v2.5 B.6 rule 8 AGENT WALKTHROUGHS (SOPs for agents): every UI packet also executes a written human-style SOP live through interactive browser use — look, click, type, observe, judge, first person — with per-step screenshots + prose observations in verification/<packet>/SOP.md, a mandatory unscripted exploration segment, defects → Blight, design friction → human gate, and judges RE-EXECUTING SOPs at I1/J. Explicitly NOT dischargeable by scripts (rule 7 proves regressions; rule 8 proves experience). Owner's framing: traditional SOPs for humans, performed by agents | ACCEPTED |
| 049 | 2026-07-21 | v2.6 owner decisions: OQ-17 resolved — the Roots alpha channel encodes SELECTION FOCUS (selected full, others dimmed-but-present, floor 0.25), never expected value; ADR-019 clause 4 SEED INGESTION (markdown upload → curator split → approval queue; M2) so cold start is product surface; remotes renamed to the nocturne-* family with a new `nocturne` splash repo as the single new-user front door | ACCEPTED |
| 050 | 2026-07-22 | v2.7 GATE-DAY DATA + v0.2 PROPOSAL ADOPTIONS (live Palace audit + owner's Code-Puppy v0.2 sheet review; notes/scorer-evolution.md): (1) HYBRID CANDIDATE RETRIEVAL — M2 candidate pool = vector top-50 ∪ Postgres FTS matches (exact-keyword memories with weak embeddings must reach the scorer); (2) KEYWORDS MANDATE — C.6 agent instructions require 2-5 keywords per save; /remember generates label AND keywords in one completion (live data showed keyword-less units handicapped on f_kw); (3) TRAINING-DATA HYGIENE — Chrysopoeia learning/replay excludes verification/test principals (H5 fixtures observed in the live log); (4) curator CONSOLIDATION TAXONOMY merge/new/contradict/supersede + TYPED EDGE OVERLAY (1-hop, never first-pass retrieval); (5) M3 promotion blends reuse/stability/manual — never raw frequency; (6) formal `candidate` status for approval-queue units. Rejected from v0.2: questionnaire tuning (Invariant 14 — gate signals collect the same passively), SQLite local-first, discrete path-multiplier ladder (continuous f_loc superior). Entity/importance axes routed to the agentic-axes replay pipeline (notes) | ACCEPTED |
| 051 | 2026-07-22 | v2.8 CURATOR DOCTRINE (owner decide-and-declare): curators exist because memories conflict and overlap over time; their job is remediation — deconflict, deduplicate, remove slop. (1) TRIGGER: every N active-unit writes since last pass (config `curator_write_trigger`, default 25), composing with cron + /maintain_memory + 80%-budget; (2) LOCATION: palace-anchored — curators run at the Memory Palace's location (spine-side at scale), the exception that proves the ADR-010 movement law; (3) SURGEONS: minimally invasive, root problem not symptoms, smallest intervention that restores health (edge < edit < merge < rewrite), queue when in doubt; (4) SLOP REMOVAL: vague/non-atomic/never-useful units → quarantine → tombstone via review queue, never hard-deleted; (5) curator ops follow versioned written SOPs (curation counterpart of B.6 rule 8) | ACCEPTED |
| 052 | 2026-07-22 | v2.9 CURATOR ARCHITECTURE (owner): diagnose deterministically, operate surgically — a curator pass = smart LLM over a deterministic tool set: (1) scripted diagnostic pre-pass (code, not LLM) generates a versioned PALACE HEALTH REPORT (cluster map, dup/contradiction candidates, staleness + never-useful lists, keyword coverage, stats deltas); (2) the agent MUST read the report before intervening; (3) all writes go through deterministic tools (merge_units, add_edge, propose_quarantine, split_unit, …) that mechanically enforce CAS/lineage/queue rules — LLM judgment lives at the verdict layer only, ad-hoc diagnostic calls allowed, free-hand writes never. Curator context assembly acknowledged HARD; the health-report format is a first-class design surface | ACCEPTED |
| 053 | 2026-07-22 | v2.10 ADR-021 MEMORY WRITE LAW & ATTENTION BUDGET (owner: simple rules; AskUserQuestion: admit-then-curate, branch staging + judge gate, 10% as principle not meter): fire-and-forget saves for build agents (mode-aware — interactive kinds keep the C.6 neighbor dialog); deterministic spine-side triage (hard-dup → reinforcement coalesce — independent re-derivation IS importance signal; near-similar → admit + possible-dup edge stamp feeding the Health Report; distinct → admit); async micro-verdicts never block; ambient extraction rides thread-close/compaction in-context (summary + open loops + ≤5 durable candidates) with verdicts-at-birth on approval-queue cards; 10% memory-attention budget as DESIGN PRINCIPLE (meter only if creep); Symphony rules R1-R5 (all agents save; staging visible own-only — sibling invisibility preserves attempt independence; judge promotes the winning branch + may extract pruned-branch negative results; k-way re-derivation coalesces; deterministic rate cap); `origin_agent` materialized-path lineage id (run_id/root.i.j, generation = depth; schema lands with M3 staging). Rejected: squash-at-write, blocking 409s for build agents, direct mid-run corpus writes, metered budget now | ACCEPTED |
| 054 | 2026-07-22 | v2.11 PALACE VITALS + LIFECYCLE CLOSURES: (1) owner directive — main-window USAGE GAUGES as viz suite item 5 / first-party rack plugin [M2 basic]: memory lifecycle rates per hour (created/reinforced/superseded/merged/quarantined/tombstoned/add-backs, queue depth, staged), token spend by category per hour (building vs memory tools vs curation vs judge vs extraction — the observability half of the 10% principle; gauges show, never enforce), palace counters; read-only views over existing logs (Invariants 10+14). (2) ADR-021 closures from red-teaming configurations: C1 staged reinforcement holdback (losing branches never inflate corpus stats), C2 queue-aware extraction dedup (repeated compactions don't flood), C3 staging doesn't tick curator_write_trigger, C4 Symphony promotions arrive as judge-ranked per-run digest, C5 signal provenance classes human/judge/passive extending v2.7 hygiene (passive keeps must not drown attended labels), C6 superseded-since-injection marker (no mid-thread yank; ADR-002 stands) | ACCEPTED |
| 055 | 2026-07-22 | v2.12 EDITOR PASS II — memory lifecycle consolidation (content-preserving, mirroring 041's method): ADR-022 THE CURATORS extracted from ADR-004's v2.7–v2.9 accretions — rot-taxonomy motivation (redundancy/staleness/contradiction/misvaluation), doctrine (surgeons, palace-anchored, triggers, slop, SOPs), architecture (Health Report → verdict layer → deterministic tools), operations (four verdicts, edge overlay, promotion blend), kinships (extractor/judge-gate/shared-palace/seed routing), OQ-4 moved with sharpened earned-autonomy leaning recorded as UNENACTED; ADR-004 retains unit/CAS/tombstone law + pin mechanism and points to ADR-022; problem tree: P1.4 rewritten to the four rots, NEW P1.5 (attention vs capture → ADR-021), P1.6 (lessons from losing timelines → staging), P2.4 (is memory earning its keep → Vitals); MEMORY LIFECYCLE MAP added ahead of ADR-004 (stage → law → milestone; motivation-first reading order for implementing agents); B.3 M3 + D.1 OQ-4 pointers updated. NO semantic change to any decision | ACCEPTED |
| 056 | 2026-07-22 | v2.13 owner decisions (AskUserQuestion): (1) OQ-4 RESOLVED — curator autonomy EARNED PER VERDICT CLASS: first era queues everything; a class graduates to auto-execute at near-unanimous human approval over a real sample (guideline ≥50 decisions ≥98%, tunable config); graduated classes report passively via Vitals; rejections demote back to queued; CONTRADICTION never graduates (truth never automatic). Subsumes threshold-split; trust earned from the owner's own decision log — curators get autonomy the way the scorer gets weights. (2) the founding doc's undefined "80%-budget trigger" DEFINED as INJECTION PRESSURE: injected blocks saturating ≥80% of the C.3 budget in ~8 of the last 10 threads schedules a curator pass (crowding is a curation signal; measured from injection_event). (3) ADR status normalization delegated to the runtime agent: J's charge gains the duty — judge PROPOSES status updates in its verdict, human gate enacts | ACCEPTED |
| 057 | 2026-07-22 | v2.14 FLOW-WALKTHROUGH CLOSURES (message-sequence audit of every memory flow): (1) reinforcement stats bump fires in EVERY mode — mode changes what the agent sees, never what the log learns; /remember hard-dup confirms "already known — reinforced", never errors; (2) CANDIDATE-status units invisible to injection and search until approved — an unapproved candidate must never shape an agent; (3) rejected queue candidates tombstoned-as-rejected + logged (human actor class) — rejection is extraction-quality signal, never silent discard; (4) ANTI-NAG LAW — rejected curator proposals are remembered (rejection record read by the health report); no re-proposal without material change (new revision on an implicated unit); re-asking answered questions violates Invariant 14; (5) PIN OVERFLOW — pins exempt from budget cap (explicit human intent outranks it), overflow injects whole, surfaced in Vitals + ticks injection pressure, never silent truncation. Horizon holes noted (unlawed, dormant): embedding-model migration/re-embedding, archival tiers at scale, palace export/portability | ACCEPTED |
| 058 | 2026-07-22 | v2.15 SCOUT-BEFORE-OWNER + SPLIT LAW (owner decide-and-declare): (1) B.6 rule 9 — every HUMAN gate is preceded by an agent SCOUT pass as DEFAULT relay behavior; PLAN Section 3 gains the SCOUT role (priority 3) so the owner's one-line relay kickoff self-routes: scout opens the harness in the browser and uses it as the owner would (rule 8 discipline), executes the gate checklist, classifies PASS/FAIL/NEEDS-TASTE, burns down passes with evidence, escalates FAILs for owner consultation; verification principal '<gate>-sop-verification' (hygiene-excluded), fixtures tombstoned, no re-scouting unchanged ground; the scout NEVER clears the gate — taste and authentic training signal cannot be delegated. (2) ADR-022 SPLITTING law — splits are semantic (each child stands alone: one claim, own label/keywords), never mechanical chopping, summarize-to-fit FORBIDDEN (decision 015); lineage = copy-with-lineage ×N (child parent_uid → source revision, same mechanism as merged_from/ADR-020), siblings auto-stamped relates-to, source tombstoned-as-split retaining stats history, children earn fresh stats; split families visible to Health Report + future sibling-aware scorer features | ACCEPTED |
| 059 | 2026-07-27 | v2.16 H5 SCOUT CONSULT (owner decisions on F006-F010): (1) F007 deadlock RESOLVED — near-miss rows gain a "never show this" veto with identical event semantics/kill counter (two kills push a unit below τ where only add-back existed; the exploration lane needs a veto — a near-miss the owner never wants is its strongest signal); C.8 AC4 becomes satisfiable through the gate. (2) F008 — NO SILENT SCOPE BROADENING law in C.6: project_scoped save with no project context surfaces the missing-context result; global fallback requires explicit user confirmation. (3) F009 — checklist was ahead of its milestone (owner: nothing existed to destroy — C.7 conformance confirmed); closing checklist corrected, durable sessions remain an M2 planning item. F006 (removal must bind the turn + wrong→edit/expire flow) and F010 (390px gate clipping) are straight FIXER repairs needing no law change. FIXER inherits all five; affected SOP slices re-run before the owner's personal gate | ACCEPTED |
| 060 | 2026-07-27 | v2.17 SPEND DASHBOARD (owner; NATES_VISION §15): Palace Vitals' presentation is an Ableton-style bottom strip — spend/time line graphs, lanes by agent / sub-agent (origin_agent subtree) / model / category / total; dollar-true via the broker's native per-call cost (OpenRouter returns USD per call — attribution is just logging: purpose enum + origin_agent tag at the broker seam); lane click = selection focus; scrubs the shared as_of timeline; collapsible per Invariant 14. Doubles as the 10%-principle watchdog and the D2 budget's early-warning sibling | ACCEPTED |
| 061 | 2026-07-28 | v2.18 PLUGINS ALL THE WAY DOWN (owner; ADR-018 clauses 8-10 + NATES_VISION §16): (8) GROUND-UP COMPOSITION — the shipped UI IS plugins on the same public API (mock = factory preset, 'version one of the default opinion'); dogfooding enforced (private first-party paths FORBIDDEN); law-bound surfaces (gate, boundary cards, queue, deck's demand right) are plugins whose ACTION CONTRACTS are law, alternates gated by contract-conformance tests. (9) CONTROL PLUGIN class + PARAMETER REGISTRY — typed descriptors {id,type,range,scope,authority}; controls bind, never free-hand; every change a journaled as_of-scrubbable event; first residents: per-thread model params (temperature/top_p/top_k/max_tokens/effort) + A-020 floor dial; scorer hyperparams law-bound via console versioning (console reclassified as first law-bound control plugin); Invariant 10 extends — every knob binds a real parameter. (10) AUTHORING EASE — plugin = folder (manifest + entry), small SDK by law, hot-reload dev mode, afternoon acceptance bar; reference plugins: SPEND STRIP (visualizer) + MODEL DEVICE (control); capability floor mechanical (hostile control writes refused + journaled). Milestones: principle+registry bind immediately; references M2; public SDK M3 | ACCEPTED |
| 062 | 2026-07-28 | v2.19 PLUGIN LAW REFINEMENTS (owner): (1) LOCAL FREEDOM, DOCUMENTED EDGES — nothing polices local code; the MODIFICATION GUIDE marks surfaces in three tiers (recommended / modify-with-care / discouraged-law-bound with the contract and its WHY); conformance testing is a tool for all and a GATE only at the contribution door (shipped defaults/marketplace). Elevated to a GENERAL PROJECT RULE: document the edges, don't police the interior. (2) RESIZE LAW — rack is a grid; manifests declare min/preferred/max in grid units (Ableton device model: constrained not freeform); rack owns drag/resize/dock, SDK delivers resize events, iframes reflow, faces get viewport allocation. (3) CONTRIBUTOR SKILL — repo ships nocturne-plugin-contributor (SKILL.md standard, ADR-007) encoding manifest schema + SDK + modification guide + contribution checklist so agents can author and contribute plugins; lands with the public SDK (M3) | ACCEPTED |
| 063 | 2026-07-28 | v2.20 CONTRIBUTOR SKILL SHARPENED (owner): written for an agent that knows NOTHING about the repo — self-contained, motivating every load-bearing constraint (three surfaces, sandbox, no-notify, registry-bound writes) so authors build effectively not compliantly; its HEART is the DATA CATALOG (every stream, canonical view, event shape, parameter descriptor — each with a real sample payload, per the samples principle) because exposed data is what builds plugins; BOTH classes as equals — full visualizer walkthrough (spend strip) AND full control walkthrough (model device). Owner affirmation: visualizers AND input modules, never just visualizers | ACCEPTED |
| 064 | 2026-07-28 | v2.21 RELAY UNBLOCK (owner): (1) F011 RESOLVED — single-use forward-only cloud grant: one update of n8-memory-palace-spine to immutable Spine d41b286, preserving project/region/service/SQL/identity/secrets/IAM/scale/traffic/breaker, no migrations/deletes/rotations/new resources; remote F007 verification + Harness smoke required; grant consumed by that session. (2) HUMAN USE HOLD RESCOPED — gates J only, not build packets: H6/H8/H9/I1 proceed while owner use continues in parallel (population-feel verdict already deferred to the real-work era); H5 reset TODO for the deploy-verify session; I1 deps widened to H1-H9; J deps gain the hold. Board-order sprint: H5-deploy → H6 → H8 → H9 → I1, no owner decisions required until J | ACCEPTED |
| 065 | 2026-07-28 | v2.22 PACKET EXCLUSION LAW (owner): board packets may declare symmetric EXCLUSION SETS — file-surface conflicts, never ordering (deps own ordering); claim law extends (claimable only if no excluded packet IN_PROGRESS). Dormant under the serial relay; makes concurrent relay sessions legal and safe: any independent set of ready, mutually non-excluded packets may be claimed in parallel, each session still one-packet + full boot; claim-commit is the lock, push races rebase. Exclusion sets chosen over lanes (precise pairwise conflicts beat coarse partitions — the board becomes a conflict graph). First entry: H6 ⊗ H8 (shared web SPA) | ACCEPTED |
| 066 | 2026-07-28 | v2.23 EDITOR PASS III (content-preserving, mirroring 041/055): ADR-023 THE PLUGIN SYSTEM extracted from ADR-018 (rack clause 4 + clauses 8-10 moved verbatim; 018 keeps the Cube, stack, procedural law, themes, with pointers); ADR-024 THE COST DOMAIN consolidates the 2026-07-27/28 cost design (seven families, receipt-line spend_event with owner vocabulary, grains-as-GROUP-BYs, canonical views, sourcing + invoice self-audit, storage decision, spend walls, cost-per-citation horizon, TTC north star) from notes into law — M2 instrument packet builds it; problem tree gains P2.5 (plugin system) and P4.1/P4.2 (spend truth / spend governance); A-020-A-023 fold into Part C DEFERRED to the pre-M2 editor pass (AMENDMENTS remains law per boot STEP 1). No semantic change to any decision | ACCEPTED |
| 067 | 2026-07-28 | v2.24 THE GLOSSARY (owner: 'our vernacular is spiraling — get a dictionary growing'): garden/GLOSSARY.md created — every NOCTURNE proper noun with a two-line receipt-language definition, grouped by domain (product, memory loop, orchestration, cost, visualization/plugins, governance); Boot STEP 1 now requires reading it; agents PROPOSE entries in handoff reports when they coin or meet undefined terms; the human gate maintains it — every new coinage gets its entry the turn it is coined; law wins over glossary, then the glossary gets fixed | ACCEPTED |
| 068 | 2026-07-28 | v2.25 UNIFIED QUEUE LAW (owner design: 'flashcards with approve/deny lists; pass without denying = approved; curators get their own module'): one queue, typed cards, TWO render surfaces routed by BIRTHPLACE — thread/run-born cards ride their origin's deck flashcard (final post up top, memory approve/deny list beneath) with PASSIVE APPROVAL on literal viewport visibility (the gate's stay-unless-removed grammar applied to the write door: enter-unless-denied); corpus-born proposals (curators, seeds) live in a separate Palace queue module, pull-only — curators never inject into deck cards. Contradictions never passive-approve (module, explicit tap, no expiry); collapsed/below-fold items never passive-approve (flow to module pending); grouped batches need explicit action. approval_mode explicit|passive joins the C5 signal classes (passive = weak, discounted); passively-admitted units are the slop sweep's priority lane. Expiry timers dead: deck items resolve with their card, module items shrink via earned autonomy. Closes M2 agenda item 3 | ACCEPTED |
| 069 | 2026-07-31 | v2.26 F012 RESOLVED — RESOLUTION POINTS (owner: 'in duet with a pinned model we should be able to switch on command; good proof point for when cost-optimization algorithms switch models'): thread stability REFINED from immutable to changes-only-at-explicit-journaled-RESOLUTION-POINTS (thread start / human command / M3 algorithmic role boundary, reserved) — never by drift; the law's enemy was always drift, not deliberate choice. M1 gains `/model <slug>` (the /remember pattern: no new C.7 type, no M2 registry knob): validates slug, re-fetches context, breaks stickiness into a new epoch, journals model_change (old→new, reason, sacrificed-prefix size for the future ledger), updates the H8 header. C.9 J1 REWRITTEN to its now-lawful original spirit (hello trace → /model → one exchange; evidence couples traces + header + journaled event + new epoch). The repair FIXER implements /model and produces the evidence; J resets to TODO for a fresh CLAUDE CODE judge (B.6 independence). The M2 selector and M3 role policies bind to this same seam | ACCEPTED |
| 070 | 2026-07-31 | v2.27 motivation-in-place (owner: 'will subagents without context understand the intuition? motivation is important!'): C.6 /model block gains its MOTIVATION paragraph — drift vs conscious purchase, and why the seam is real product surface (M2 knob, M3 role policies) — so the intuition reaches implementing agents at STEP 5, not just the D.2 appendix. No semantic change | ACCEPTED |
| 071 | 2026-07-31 | v2.28 M1 JUDGED PASS + ADR STATUS NORMALIZATION (superseding verdict, claude-code/f648 — B.6 independence satisfied; enacting the judge's proposals per D.2 056's delegation, one immutable entry, no historical rows rewritten): ADR-002 → ACCEPTED for M1 scope (boundary/client/gate-flow/final-block judged); ADR-004 → ACCEPTED (unit/CAS/revision/tombstone/quarantine/pin verified end to end); ADR-005 stays PARTIALLY ACCEPTED, annotated (scorer v0 + gate signals + near-miss veto + quarantine judged verified; M2 learning future); ADR-008 → M1 stack details ACCEPTED (React/TS/Vite command center, snapshot-authoritative); ADR-006 remains PROPOSED (presence is not M1); ADR-002 block placement, M1 vertical slice, and decision 013's phased accumulation boundary accepted at M1 scope; A-021 verification recorded as context (policy law lives in AMENDMENTS pending the pre-M2 fold). M1 closes on the owner's VERDICT.md read; D3 claimable; M2 planning opens | ACCEPTED |
| 072 | 2026-07-31 | v2.29 M2 WAVE 1 OPENED PRE-PLANNING (owner: 'reorder so we have packets ready'): the scoreboard-independent, law-complete half of M2 is minted as packets M2A (spend ledger core, ADR-024), M2B (rack refound + NEO-NOIR identity, ADR-023 — the plain shirt dies), M2C (Vitals + spend strip, deps A+B), M2D (durable transcripts capture-only), M2E (hybrid retrieval), plus M1C (closing report as a packet so the board never dead-ends). M2B ⊗ M2C exclusion. WAVE 2 (minted at M2 planning proper, after the owner's scoreboard conversation): the Chrysopoeia learner + replay acceptance metric, extraction + queue module + deck flashcards, seed ingestion, parameter registry + model device, Memory Graph + console, contribution bars, citation heuristic, GCP reconciliation job | ACCEPTED |
| 073 | 2026-07-31 | v2.30 INSTALL NAME (owner + gate; PyPI collision found by D3): the PyPI package is `nocturne-ai` (`nocturne` is taken; `-ai` is the standard convention and searchable); the CONSOLE COMMAND remains `nocturne` (package and entry-point names are independent — the brand is untouched everywhere but one install line). Spelling variants rejected (typo-fracture; `nocturn`/`noct` taken anyway); `nocturnist` noted as the charming runner-up. D.2 046's install phrase is superseded on this one point | ACCEPTED |
| 074 | 2026-08-01 | v2.31 FIRST-PRINCIPLES LEVEL-SET (owner): (1) SELECTION LAW clarified — binding constraints are τ + token budget, greedy most-relevant-first; the top-k 8 is a v0 display/safety constant, tunable config, never architecture ('top 8 sounds like a weak simplification' — correct); (2) OQ-15 RESOLVED for M2 — gate the FIRST message only, then AUTONOMOUS per-message re-scoring with ambient panel visibility/modification, no further modals; autonomous persistence = passive keeps; re-scoring may add freely and demote only autonomous entries — never human-confirmed or pinned ones; per-prepare snapshots; f_phase candidate unlocked; (3) confirmed standing design: every rejection/addition is notated (injection_event) and hyperparameters re-fit every ~N events via champion/challenger with replay verification — the scoreboard metric is that verification's referee and remains the open owner decision | ACCEPTED |
| 075 | 2026-08-01 | v2.32 THE SCOREBOARD RESOLVED (owner: 'no arbitrary scoring — a memory is injected or not; overrides stored traceably; tune to do better next pass'): BINARY replay acceptance — challengers graded against every recorded human disposition on held-out gates, agree/disagree, fewest wrongs wins; self-balancing (add-backs punish timid, removals punish pushy); no graded point weights (flavors stay STORED for traceability, ungraded in v1); removed:wrong excluded; passive keeps counted with C5 discount; cheaper-at-tie (TTC proxy); real win margin (config). Storage confirmed complete in existing injection_event (full features + scorer_version = every override traceable to the weights that erred; successes stored as denominators/anchors/dispositions). M2 agenda item 1 CLOSED — the Chrysopoeia learner packet is now fully specifiable | ACCEPTED |
| 076 | 2026-08-01 | v2.33 LIVE THREAD PARAMETERS (owner: 'human added it, so its score increases?'): resolved as BINARY LOCKS, not boosts — human-added/confirmed = thread-sticky (v2.31, standing) and now symmetrically human-REMOVED = thread-excluded from re-scoring until human re-adds (a re-score never resurrects a killed card); no arbitrary boost magnitudes. The graded form of the idea = f_sess RESURRECTED: its founding excision rationale ('zero at the only scoring moment') was voided by OQ-15's per-message re-scoring; it joins f_gen/f_phase in the agentic-axes pipeline, earning weights by replay against the v2.32 binary scoreboard | ACCEPTED |
| 077 | 2026-08-01 | v2.34 WAVE-2 CLARITY PASS (owner: 'anything unmotivated or unclear? clarify then mint'): (1) learner v1 fits weights+b_m ONLY (τ/budget/k stay manual); winning challengers become PROPOSED versions the owner activates in the early era — the learner earns auto-activation like curators earn auto-merge; minimum-dispositions floor before first challenger (config); (2) extraction TRIGGER pinned — explicit archive/close + idle-hours fallback reading M2D transcripts (M1 threads never close; compaction is M3); thread-born queue cards render as a rack-module thread-end card (the Deck's M2 predecessor); (3) reconciliation scoped to broker usage/credits API (GCP billing export = deferred owner-gated setup); (4) the missing packet found: per-message re-scoring (OQ-15's implementation) had law but no packet — minted as wave 2's headliner with the v2.33 disposition locks. Wave 2 minted: M2F-M2M | ACCEPTED |
| 078 | 2026-08-01 | v2.35 PACKAGING REVIEW RULINGS (owner + gate audit of the D3 back-and-forth): (1) ADR-019 gains V0.1 POSITIONING — local `nocturne up` is the public path; `nocturne deploy` is owner/internal (hard-wired to the owner's foundation); generic cloud provisioning + fleet db lifecycle = deliberate future product, never accidental scope; Palace identity law UNCHANGED (owner's cloud heart stands; trial users' local palaces are honest complete products; move = export/import, never sync). The mid-conversation local-default-for-everyone proposal was correctly WALKED BACK by the owner — us-first, no refactor. (2) ERRATUM recorded on D3 evidence: 'live Alembic head proven incompatible' overstated — the drift label arose from a db/user/secret tuple mismatch without inspecting alembic_version (agent self-reported; gate note appended to report 034). (3) M2N minted — OWNER DB LIFECYCLE HARDENING: pinned pgvector image digest, nocturne backup/restore/doctor, backup receipt before every migration, Alembic advisory lock, historical upgrade-matrix tests, config-file upgrader, N backup generations. Operational durability for the owner's real data, explicitly NOT a multi-user refactor | ACCEPTED |
| 079 | 2026-08-01 | v2.36 THE CAPABILITY LADDER (owner: 'I despise multiple versions — ONE version with levels of capability; users should easily mirror our setup into their own cloud supplier; and no local version for me on a sailboat would be a mistake'): ADR-019 positioning reframed — one product, rungs never forks: (1) local palace, (2) bring-your-own-cloud via the MIRROR PRINCIPLE (deploy = reference recipe; owner hard-coding is v0.1 implementation state, not identity; per-install targets + ownership manifest = named future packet; support boundary stands — self-operated, no SaaS by accident), (3) replicas + sync = ADR-011 blue-water (M5), first customer the owner's own founding sailing example (P1.3.1); read-only offline cache noted as the cheap intermediate rung (urgent only once local models arrive — offline kills the broker before the palace). All three owner motivations recorded: continuity, product coherence, sovereignty | ACCEPTED |
| 080 | 2026-08-02 | v2.37 OWNER REVIEW DIRECTIVES: (1) B.6 rule 10 REAL CALLS — the running product always hits the real broker; no mock-model modes or dev-server conveniences as defaults, ever; stubs are rule-7 regression fixtures only, never SOP/judge evidence ('a demo that works against a fake is a fake that demos'); spend the pennies — the ledger prices it, the breaker bounds it. (2) MULTIMODALITY named as future surface in ADR-019 territory — text end-to-end today; broker/policy already multimodal-ready; rungs: input passthrough (M3-era candidate) → output rendering → multimodal memory (horizon, embedding-space implications). (3) Owner manual review of the M2B rack: theme improving, still boxy, some clunky non-intuitive phrasing — captured to the vision + notes channel for M2C/M2K polish | ACCEPTED |
| 081 | 2026-08-02 | v2.38 OWNER SCREENSHOT FINDINGS: (1) FIXTURE ISOLATION law (B.6 r10 extension) — the owner unknowingly transmitted to a leftover H5 scenario daemon: fixture servers must bind distinct ports, render unmistakable FIXTURE banners, die with their session, and run SOPs in isolated browser profiles (fixture threads polluted the owner's real catalog); reachability is the standard. Gate's own miss recorded: a four-keyword grep declared 'no mock anywhere' — narrowly true of product source, wrong where it mattered. (2) B.6 rule 11 ACCOUNTING FAILS OPEN — agent_runtime.py:229 fails turns when spend receipts can't persist (fail-closed accounting in the chat path); ruled backwards per Invariant 9: turn completes, basis=estimated line queued, reconciliation surfaces drift. (3) MULTIMODALITY rung 1 (image input passthrough) upgraded CANDIDATE → COMMITTED, ships with M3 harness parity beside browser/CLI tooling. M2O minted to implement (1)+(2) | ACCEPTED |
| 082 | 2026-08-02 | v2.39 THE JOURNAL LAW (owner ruling): history is MANDATORY — the daemon does not run without a writable conversation journal; conversations without history are unsupported by design (precedent: Claude Code and Codex keep complete session history on disk, always). Hard stop only after an ignored early warning (doctor + startup disk checks, M2N); THRESHOLD PROMPT — total on-disk footprint crossing a configured share of system storage raises a DECK card (the one surface permitted to demand; preventing data loss earns the interruption) with the Vitals resources gauge as the always-on view. Doctrine sealed: FAIL OPEN ON ANYTHING RECONCILABLE (spend receipts, B.6 r11), FAIL CLOSED ON ANYTHING UNRECOVERABLE (history) | ACCEPTED |
| 083 | 2026-08-03 | v2.40 MODULE SCOPE LAW (owner: 'visualizers switch natively to the current chat — global or current; current ones all switch together'): ADR-023 clause 5 — every rack module carries a GLOBAL|CURRENT scope toggle; CURRENT binds to the shared selection bus (ADR-018 cl.2's one-selection law generalized from faces to the whole rack; navigation anywhere turns all CURRENT modules as one stage); GLOBAL pins wide as an ambient wall. Motivation: context coherence. Toggle = presentation state persisting with layout sets, never a registry parameter; manifests declare default scope; existing modules adopt at next touch | ACCEPTED |
| 084 | 2026-08-03 | v2.41 THE ACCURACY SCORE (owner articulation ratifying v2.32 in percentage form: 'our new hyperparameters map to 98% correct injection across the backtest while the previous generation did 97% — or the update does not happen'): Vitals gains the memory system's performance as a living time series — per-generation held-out agreement %, published from the learner's existing replay manifests; chronological holdout noted as the anti-memorization discipline (score = predicts your NEXT clicks, not explains your old ones). No mechanism change — the owner's envisioned optimization IS the shipped M2F design; this makes its score a first-class visible metric | ACCEPTED |
| 085 | 2026-08-03 | v2.42 THE INJECTION CONSOLE (owner: 'the ghost gate is just a visualization and input module showing memory injection score — this is super important'): ADR-009 item 4 expanded from hyperparameter console into the four-layer scoring module — READ (per-candidate decomposition summing exactly to the score, τ/budget/near-miss geometry, score history, v2.41 accuracy series), CONTROL (unchanged versioned-insert law + PROPOSED manifests + one-tap), WHAT-IF (preview re-rank, never persisted; commit = versioned insert), AUDITION (challenger counterfactual overlay on live gates, pure consumer, incumbent governs until activation). Owner's memory validated: bars/console/trace were law; the module unification, what-if, and audition are the new law. M2P minted (audition + what-if, deps M2K); M2K keeps console core + accuracy series | ACCEPTED |
| 086 | 2026-08-03 | v2.43 INJECTION CONSOLE COMPLETED (owner riff): (1) THE SCORE GRAPH — two series (live sawtooth of the active generation dipping per override + per-generation backtest points), annotated; knobs labeled/normalized as decimals; (2) what-if RENAMED SIMULATE — instant re-rank + DEEP one-click backtest of arbitrary values (small spine endpoint over M2F's existing replay engine; owner confirmed the engine half already shipped); (3) FORCE allowed with honest delta + journal — ratified as THE EXPLORATION LEVER (owner: 'a way to get into a new local maxima'; convex inner fit, but forcing changes what is shown → changes future training evidence → system-level basin jump; next optimize climbs from there); (4) console DEFAULT in factory set, docked compact; (5) THE LANDSCAPE ships M3 COLOCATED with the curator visualization — one memory world, two territories (contents beside terrain), descent trail included; M2P ships 2D slices interim | ACCEPTED |
| 087 | 2026-08-03 | v2.44 INFORMED FORCE (owner): force is enabled only after deep simulate has scored the EXACT tentative set — the confirmation IS the score, never a dialog; any knob change stales the score and disarms force until re-simulated. Never blocked, never blind | ACCEPTED |
| 088 | 2026-08-03 | v2.45 TEST MOTIVATION LAW (owner: 'there should be a reason each test exists'; gate measurement: 450 tests, zero docstrings, ~12 citations): B.6 rule 12 — every test carries a motivation docstring with a citation token (law made executable cites its statute); mechanical pre-commit enforcement with a grandfather baseline the M2Q sweep retires; unmotivatable tests are discoveries (unwritten law → completion, or nothing → journaled deletion); the INVERSE INDEX generates the law-coverage report (zero-defender sections highlighted). M2Q minted: the motivation sweep + checker + coverage report | ACCEPTED |
| 089 | 2026-08-03 | v2.46 CORE-FIRST CHECK (owner: 'are we working flashy features rather than finishing core builds?'): audit verdict — one hit, one pin, defense elsewhere. (1) CONTEXT BARS (P2.2, viz item 1, promised in the founding M2 line) was NEVER MINTED in the wave split — gate's dropped ball, found by the owner; M2R minted now. (2) MOVEMENT COUPLING pinned for M3: the first parity packet ships location + movement law + f_loc activation WITH fs/shell, never after (an agent gets hands and a place to stand in the same breath). (3) Remaining board reordered CORE-FIRST: M2L, M2M, M2N, M2O, M2R, M2Q before M2P (simulate/audition polish claims last) | ACCEPTED |
| 090 | 2026-08-03 | v2.47 IDENTITY RATIFIED (owner, after a considered pivot question): the name STAYS NOCTURNE — value-naming beats mechanism-naming; the serpent's search space is owned (Cardano) and its tech connotation reads pejorative; and the mythology is richer with both (nocturne the practice, ouroboros the attainment). THE MARK: an ouroboros curled around the crescent moon — neon line work, NEO-NOIR (vision §21) — for the D3 branding wave. The Ouroboros remains N1, the self-hosting milestone. The serpent and the Chrysopoeia share Cleopatra's manuscript; the logo knows | ACCEPTED |
| 091 | 2026-08-03 | v2.48 OWNER SIDE-NOTE TRIO: (1) THE GARDEN NEVER SHOWS (ADR-019, twin of never-ships): no ADR/packet/amendment ids or build reasoning in customer-facing surfaces — 'don't sell your customer your org chart'; next UI packet (M2R) sweeps for leaks. (2) BROWSER DEFAULT = the OpenAI/Codex chrome extension PORT (owner override of the gate's Playwright lean; Playwright demoted to headless-automation fallback) — dossier + seeds updated. (3) TOOLSET ADOPTION DOCTRINE: never write the standard tools from scratch — the M3 survey ADOPTS an existing open-source agent toolset as the base (candidates named by owner: Code Puppy, OpenCode, Hermes; survey adds OpenHands/aider/pydantic-ai natives), modified under our law ('we'll modify them, but at least we'll have a standard list'); specing every tool from scratch is explicitly rejected | ACCEPTED |
| 092 | 2026-08-04 | v2.49 F015 RESOLVED — INFORMED RESTORE (owner: 'a popup that says do you want to roll back these memories, with a list'): `nocturne restore` is SIDE-BY-SIDE, never in-place — fresh volume, restore + verify there, former volume preserved as a rollback generation, a failed restore never touches the live palace. THE ROLLBACK MANIFEST: before switching, the two live volumes are DIFFED and the owner confirms against a NAMED LIST — memories that would be lost (born after backup), edits reverted, pins undone, event counts — the v2.44 informed pattern generalized: simulate before force, manifest before restore; never blocked, never blind. Owner-cloud restore remains a HUMAN Cloud SQL operation (PITR; mirror principle keeps cloud surgery human until fleet lifecycle is a chosen product). M2N unblocked | ACCEPTED |
| 093 | 2026-08-05 | v2.50 OWNER SPIN RULINGS (live gate day): (1) THE UPDATE PROMPT — up's version handshake: local palace behind auto-migrates (standing); remote palace behind gets one plain sentence + offered deploy (receipt-first per M2N); declining = visible notice, never a 503 wall; app updates ride pipx once publishing un-defers ('there could be an update prompt like other repos' — now contract). (2) PLAIN SECRET NAMES — exact vendor names for third-party secrets, plain-function names for generated ones ('call it exactly what it is'); 'bearer' banned from customer surfaces; init prompt is fix #1. Both ride the next harness FIXER/packet | ACCEPTED |
| 094 | 2026-08-05 | v2.51 OWNER-CLOUD CREDENTIAL ALIGNMENT GRANT + PLAIN PLAN LANGUAGE (owner, live gate day): (1) single-use grant — the deployer may perform a ONE-TIME CREDENTIAL RESET on the owner foundation: pre-migration backup receipt FIRST (M2N), then mint a fresh database password, update the database user, rewrite the spine-database-url secret (values never printed), roll the service, run migrations 0002→0009, remote verification — after which the deployer OWNS its managed credentials permanently (reset chosen over adoption: verified custody beats unverifiable hand-me-downs). Third appearance of the honest-refusal family (F011 deploy authority, F015 restore semantics, now credential custody) — each stopped rather than gambled. (2) PLAIN PLAN LANGUAGE: deploy output is rung-2 CUSTOMER surface — 'NOOP'→'OK — unchanged', 'BLOCKED'→'needs your decision', and refusal lines state the situation and the remedy in one plain sentence; the cascade defect (migrations declared incompatible before alembic_version is read when credentials disagree — the D3-erratum signature) is fixed so blocks report their true single cause | ACCEPTED |
| 095 | 2026-08-05 | v2.52 PLAIN LANGUAGE REFINED (owner: 'if NOOP is industry standard keep it — no inventing jargon for jargon's sake'): the v2.51 plan-language ruling is CALIBRATED BY AUDIENCE — OPERATOR surfaces (deploy plans, doctor output) keep industry-standard vocabulary (NOOP, dry-run, migration — the Puppet/Terraform lineage); CONSUMER surfaces keep the read-aloud rule. The ban is on INVENTED jargon (our coinages, garden terms) on any customer surface, and on REMEDY-FREE REFUSALS anywhere: every blocked/refused line states the situation AND the next action in one plain sentence. M2T's language work narrows to the refusal sentences + the cascade defect; NOOP survives | ACCEPTED |
| 096 | 2026-08-05 | v2.53 F016 RESOLVED — FRESH GRANT FOR THE CORRECTED RESET: D.2 094's attempt failed on a gcloud flag defect AFTER the receipt, BEFORE any credential changed (secret v1 sole enabled; palace untouched; defect fixed + tested in-session — honest block, grant consumed as written). New single-use grant: same scope and sequence as D.2 094, using the corrected reset. BACKUP RULING (bright line, permanent): EVERY mutation attempt begins with its OWN fresh receipt — prior receipts never carry forward, however recent; receipts are cheap, ambiguity isn't. M2T unblocked | ACCEPTED |
| 097 | 2026-08-06 | v2.54 F017 RESOLVED — GRANT FOR THE PROVEN ONE-STEP PATH + RECEIPT MARKS CONSUMPTION (owner, live gate day): (1) D.2 096's approved run reached the literal `nocturne up → y`, then failed BEFORE backup or any cloud mutation — an editable Spine checkout lacked the wheel-only deploy resources (fixed: spine f060cf6 materializes the same strict allowlist from wheel or canonical checkout; harness 3d1b6ca adds the durable one-time custody receipt). Gate-verified read-only proof: revision 00004-vs2 unchanged, secret v1 sole enabled, no new tags, no on-demand backup, no custody receipt. Discovery credited: the ORIGINAL credential AUTHENTICATES — the D.2 094 block was a gcloud classifier defect (built-in Postgres user omits `type`), never bad custody; the granted reset still runs ONCE (custody receipt makes it permanent) because verified ownership beats a working hand-me-down. (2) CONSUMPTION RECALIBRATED (permanent): a grant is consumed by MINTING ITS FRESH RECEIPT — which by D.2 096's bright line precedes every mutation — so success or any post-receipt failure consumes; a run that PROVABLY fails before its receipt has touched nothing and leaves the grant INTACT for in-session repair and retry, re-proving read-only ground before each retry. No replay is possible (mutation ⇒ receipt ⇒ consumed); no owner round-trip is burned on a local defect. Evidence: F016 failed post-receipt (correctly consumed under both rules); F017 failed pre-receipt (a free retry under this rule — the round-trip it cost is the motivation). (3) Fresh single-use grant: the literal `nocturne up → y` owner path — fresh verified backup receipt, corrected reset, secret rewrite, image, rollout, migrations 0002→0009, remote verification — consumed per the recalibrated rule. M2T unblocked | ACCEPTED |
| 098 | 2026-08-06 | v2.55 F018 RECORDED + F019 RESOLVED — SCOPE LAW + MUTATION-LAST ORDERING (owner ruling via the gate; gate-verified live): (1) HISTORICAL RECORD — F018 (D.2 097's attempt consumed post-receipt after reset/secret-v2/custody succeeded but Docker isolation hid Buildx, stranding the palace on disabled secret v1) was resolved by DIRECT OWNER GRANT in FLAGS: one post-alignment completion attempt, reset/secret NOOP under custody. That attempt succeeded through image 0.1.0, revision 00005-t64 on spine-database-url:latest, migrations 0002→0009 — then its own verifier false-negatived (re-used an active label expecting the hard-duplicate band; spine's C.4 label-conflict answer was CORRECT; verifier fixed 0ddc7e8 with regression proof; F019). (2) THE SCOPE LAW (permanent): the grant family (F011, F015-F019) governs INFRASTRUCTURE mutations — credentials, secrets, images, service rollouts, migrations, backups. APPLICATION-DATA-PLANE operations (memory CRUD via the public API, vitals reads, verification labels, typed round trips) are ordinary product operation and are NEVER grant-gated — otherwise every user chat is an unlawful mutation; the honest-refusal family protects custody of the FOUNDATION, not the data plane the product exists to serve. A run whose infra stages are all NOOP takes no receipt and needs no grant. F019's requested verification-only attempt is therefore ORDINARY OPERATION: next session runs `nocturne deploy` (all infra NOOP) + corrected typed verification + Vitals + verification label, then closes M2T. (3) MUTATION-LAST ORDERING (permanent, F018's lesson): within any granted infra sequence, every locally-fallible non-mutating stage (build, push, preflights) MUST complete before the first service-affecting mutation — build-first would have left the palace untouched by F018's failure. (4) GATE VERIFICATION (recorded evidence): independent probes — health 200 with schema_version 0009, /v1/vitals 200, DB-backed reads 200, traffic 100% to 00005-t64, secret v1 DISABLED yet DB live (proves v2 adoption); live spend-receipt contract test PASSED against production. Two live-contract failures are STALE FIXTURE-ERA EXPECTATIONS (hardcoded h2-contract-embedding-1536 vs production's real openai/text-embedding-3-small) — the mirror of B.6 r10: a test that only passes against the fake; M2U minted. M2T unblocked for verification-only closure | ACCEPTED |
| 099 | 2026-08-07 | v2.56 THE RELEASE-LIFECYCLE REVIEW (owner directive + gate walk of init/up/open/doctor/backup/restore/deploy after the M2T-M2V saga; motivation: ALL future releases = one or two commands and menus, for the owner and every user): (1) NO-DOWNGRADES LAW — `up`'s update prompt currently fires on ANY schema mismatch and always names the PALACE as stale; when the app is the older side (multi-machine owner, stale checkout), it would offer a DOWNGRADE. Permanent: reverse skew is detected as its own case, the voice names the app as stale ("this app is older than your Palace — update the app"), and deploy NEVER downgrades a palace. (2) DOCTOR-PARITY LAW — doctor reported healthy while `up` was 503-refusing (M2V): permanent, doctor probes every dependency `up` requires (web assets/factory, port, toolchain by rung, remote reachability); 'doctor healthy + up refuses' is itself a defect class. (3) VERSION-BUMP GUARD — deploy's image stage returns EXACT on tag existence alone; changed source + forgotten version bump = twenty NOOPs and changes that silently never ship (immutable repo would also refuse a re-push). Permanent: deploy detects tag-exists-but-packaged-source-differs and refuses with the plain remedy (bump the version); the release ritual owns version bumps. (4) MINTED — M2W (release-lifecycle hardening: reverse-skew voice + refusal, doctor parity, version-bump guard, open/port-conflict/adopt-existing remedy voices, rung-2 `nocturne backup`); D3 charge addendum (release ritual: version bump + changelog + `nocturne update` app self-update via the PyPI channel — the palace update prompt (v2.50, built) covers the palace side; D3's channel covers the app side; together they close the loop the owner named: one command, one menu, every future release) | ACCEPTED |
| 100 | 2026-08-09 | v2.57 THE PRE-GATE RULINGS (owner working session over the full pre-gate scout, report 077; 39 PASS / 12 FAIL / 7 NEEDS-TASTE): (1) BACKGROUND RETRAIN LAW — retraining is a BACKGROUND act on WORK-BASED triggers (owner doctrine, D.2 051 family: work done, never wall-clock): once the 25-signal floor is met, every 25 new graded owner signals since the last generation auto-runs the advisory retrain and posts the PROPOSED generation with its backtest score (config: retrain_signal_stride, default 25). The manual lever is ONE command/button, 'FORCE RETRAIN', for the 1-in-100 impatient case — same act, on demand. The Injection Console's FORCE (v2.44) is a DIFFERENT instrument — it force-SETS owner-chosen values (the exploration lever); retrain FITS values from data. UI copy must disambiguate ('Force values' vs 'Force retrain'); duplicate-function buttons are banned. ACTIVATION remains the owner's one tap (advisory lock; the v2.13 earned-autonomy graduation path remains future). (2) THE LONG PROBLEM METRIC — continuity is measured in COMPACTIONS SURVIVED, not days: 'compaction is the context-window entropy the memory palace is fighting' (owner). Until compaction machinery exists, thread boundaries are the entropy events; Tier 5 gate items rewritten accordingly; extraction-at-compaction (seeds) is this law's M3 twin. (3) PACKET-EMBEDDED AUTHORITY (standing practice): a packet's charge carries every authority it needs inline — provisional-taste grants, specific infra grants — so an owner one-liner suffices; platform consent walls for infra mutations remain (one live approval sentence). (4) PROVISIONAL TASTE (temporary, expires when the owner clears M2X): within M2Z3/M2Z4 only, builders make UX/composition decisions without pre-approval, constrained by NATES_VISION, each journaled in DECISIONS.md tagged PROVISIONAL-TASTE, all subject to owner overrule at the gate. Never delegated regardless: scorer activation, FORCE-values enactment, signal synthesis, infra beyond enumerated grants. (5) RELEASE GRANT (single-use, embedded in M2Z5): bump Spine to 0.1.1, changelog, image build/push, rollout, remote verification — receipt-marks-consumption per D.2 097; AND the provenance hash is scoped to SHIPPED source only (docs-only commits must not demand releases). (6) M2Z1 (journal rehydration + fail-closed preflight, F030), M2Z2 (guided split, F027), M2Z3 (project context + artificial paths, F028), M2Z4 (the learning path, F029), M2Z5 (release 0.1.1, F031) minted; M2Y5 deferred behind M2Z5 (owner: minor) | ACCEPTED |
| 101 | 2026-08-09 | v2.58 THE MEMORY SHARE + COMPACTION MEMORY DUTY (owner design session; motivation: recent history is supremely important AND so are long-term memories — the balance must be explicit, visible, and learned): (1) THE MEMORY SHARE — the v2.31 injection token budget becomes memory_context_share, n% OF THE ACTIVE MODEL'S CONTEXT WINDOW (model-portable by construction since the window follows the model, A-020; default 10%, rhyming with the ADR-021 attention principle; builder journals if this materially shifts today's effective budget). The share is a CEILING, NEVER A QUOTA — τ still decides entry; cold-open-empty remains correct; nobody pads to fill an allocation. The remainder of the window is ordinary recency: latest messages, tool calls, system — untouched. (2) VISIBLE + EDITABLE — the share is a registry parameter with its knob in the Injection Console (informed-force law v2.44 applies to manual changes); Context Bars displays allocation vs actual. (3) LEARNED IN THE LOOP, NOT A STANDALONE TUNER — the BUDGET-CUT BAND (memories above τ but cut by the share; distinct from near-misses, which failed τ) is logged as first-class outcomes; the replay/backtest grades the boundary both directions (budget-cut members that replay marks keep-worthy → share too small; lowest-ranked injected members earning removals → share too large); n joins the Chrysopoeia hyperparameter vector under the same backtest, scoreboard objective, and PROPOSED/owner-activation consent surface. (4) COMPACTION MEMORY DUTY (M3, lands with compaction machinery): the summarizing agent performs THREE-WAY TRIAGE — still-live content stays in the summary; important-but-not-live content becomes a memory through the ORDINARY C.6 autonomous channel (direct save, admit-then-curate, origin marked 'compaction' for lineage + scoreboard hygiene, never-save-secrets carries); everything else persists in the journal regardless (v2.39 — duty is timeliness and organization, never rescue). This SUPERSEDES the separate extraction-pipeline-at-compaction seed: the summarizer IS the extractor at that moment — one agent, one pass. (5) Implementation rides M3 planning (share knob + band logging need the learner loop and registry; compaction duty needs compaction); agent tests added to the standing checklist now, [DEFER-M3] tagged | ACCEPTED |
| 102 | 2026-08-09 | v2.59 SYMPHONY'S PLANNING ARCHITECTURE (owner riff, ratified as ADR-017's M3 design intent; motivation: build the harness FASTER, and this build itself is the evidence base): (1) THE THREE-JUDGE MINIMUM — every symphony's plan session emits judge charters for at least: the MOTIVATION judge (holds the whole why; builders hold slices), the IMPLEMENTATION judge (sensibleness/efficiency of the how), and the PERFORMANCE judge — NEW, owner-directed: it judges against PRECALCULATED METRICS agreed at plan time (suite runtimes, token spend, latency budgets, accuracy scores — baselines written into the plan artifact before any builder runs), and those metrics persist in a CROSS-SYMPHONY LEDGER (Vitals/ADR-024 family) so the bar rises over time. Users may add judges; none may be removed. FAIL → feedback persisted AS NEW PACKETS; UNANIMITY terminates; spend-wall + same-failure-twice brakes stand (seeds item 11 now law-shaped). (2) THE RECIPE CARD (owner-supplied model — the ratatouille schedule): the plan artifact is a STAGED DEPENDENCY TABLE — rows are work items (beads/packets), early columns are per-row independent prep (parallel, gated only by their own row: mise en place), MERGE CELLS are join barriers (a stage opens only when every feeding row has arrived), and every stage carries a DONENESS CRITERION — an observable state ('until translucent'), never a timer. Judges evaluate AT MERGE POINTS, not only at the end (taste as you cook); the final gate is 'season to taste' — owner/judge unanimity. Search sub-agents may fire within any stage. Beads remain the dependency DAG; the recipe card is its stage-structured, legible-at-a-glance schedule — and the CONDUCTOR VIEW renders it live (rows × stages, cells lighting as agents claim them; rack module family, honest encodings). Full ADR-017 formalization at M3 planning | ACCEPTED |
| 103 | 2026-08-09 | v2.60 THE LOCATION FENCE + RECIPE-CARD PRACTICE (owner: 'the location tenant is critical... deterministic enforcement of being able to edit files only in that location. This is a major tenant of the build and half the reason the Ant Farm visualization is useful'): (1) THE LOCATION FENCE (M3 movement law, design intent ACCEPTED now): every agent has a location at all times (CWD + heat-decayed working set, ADR-006); an agent may WRITE/EDIT files ONLY within its current location's subtree, enforced DETERMINISTICALLY AT THE TOOL LAYER — the harness refuses out-of-fence writes; model goodwill is never the mechanism. Movement is an explicit, logged act (presence events; f_loc reads the same location — one truth). Reads default unfenced (config may tighten). THE UNIFICATION: the fence is D.2 065's exclusion-set law made PHYSICAL — exclusions express file-surface conflicts by gate judgment today; disjoint locations make conflict-freedom PROVABLE, so parallel agents scale without a human deciding who collides. The Ant Farm is the fence made visible: where every agent stands IS what every agent may touch. (2) RATATOUILLE CRITERIA enters the glossary as the working name for doneness criteria (owner delight is a legitimate naming force; v2.52 satisfied — it names OUR concept, not fake industry vocabulary). (3) MANUAL APPROXIMATION (practice, effective immediately): while Symphony is unbuilt, the relay PRACTICES it — the gate authors a RECIPE CARD per wave (garden/notes/recipe-cards.md): rows, stages, merge cells, ratatouille criteria, and declared exclusions; the owner fires PARALLEL relay sessions into any open cells; claims stay safe under the dormant-until-now D.2 065 machinery (claim commits + exclusion sets). Sequential firing is no longer the default — the card says what may run together. Evidence from this very wave: M2Y6 ∥ M2Z1 ∥ M2Z2 were independent rows (two landed same-day); M2Z2→Z3 share the save path and Z3→Z4 share the web SPA — genuinely sequenced, the card makes the WHY legible | ACCEPTED |
| 105 | 2026-08-10 | v2.61 M2ZA COMPLETION GRANT (owner via gate): F033's fix required building the annotation surface itself (spine migration 0012, append-only annotation API, evidence projection, canonical deploy-verifier identity — report 091; the gate's 'data-plane only' charge assumption was wrong, the block honest). SINGLE-USE GRANT embedded in M2ZA: one authorized 0.1.2 rollout — fresh verified backup receipt first, image build/push, service rollout, migrations through 0012, remote verification, mutation-last ordering, receipt-marks-consumption (D.2 097); credentials NOOP under custody. THEN, as ordinary data-plane operation: annotate the three legacy verification events, verify the scoreboard renders the honest 1/25 baseline, close M2ZA DONE. M2Z9's contract decoupling remains next-sprint and rides the release after | ACCEPTED |
| 106 | 2026-08-10 | v2.62 THE PACKET TEMPLATE (owner, gate day; essential motivation verbatim: 'I never want to be afraid to fire off a volley of parallelizable agents' — fearless over-firing requires every packet to carry its own safety case): every packet minted from this day carries, AT MINT TIME, in its PLAN charge: (1) MOTIVATION — the problem-tree node it serves plus one sentence of why (B.6 r12's statute discipline extended from tests to the work itself; a packet without a why is banned exactly as a law without a row is a wish); (2) RECIPE — deps (ordering), TOUCHED SURFACES enumerated at file/subsystem level (the F032 lesson made mandatory), exclusions DERIVED from those surfaces, and an explicit PARALLEL verdict (OPEN or serial-with-reason) so the recipe card is GENERATED from packets rather than authored beside them — the volley becomes safe by construction, not by gate vigilance; (3) AUTHORITY — every grant the packet needs embedded inline (D.2 100(3) made universal); (4) exit criteria citing statutes, as already practiced. The gate retrofits all currently-open packets; agents flag template-less packets as gate defects rather than claiming them | ACCEPTED |
| 107 | 2026-08-11 | v2.63 THE REFERENCE PLATE (owner, direct session; essential motivation verbatim: 'I want to figure out a way to have it emulate this picture as absolutely as possible… pulling exact color samples out and using those in the same ratios as the picture'): the owner's supplied 90s-cel-anime chrome image IS COBALT-SERAPH's canonical ground truth, committed at notes/plates/cobalt-seraph-plate.png (sha256 40bfc4414de3fe5d252060dc806cf5c795180d6fcd20aae37ac059a69498e069); the claiming builder copies it verbatim into the harness web tree and verifies the hash before deriving anything. (1) DERIVED, NEVER AUTHORED: theme tokens are generated by a deterministic extractor — OKLab k-means with pinned k+seed, a chroma-outlier pass for rare identity accents that clustering absorbs (gold, glint, iris blue — gate-run evidence proved gold vanishes at k=12), declared in-material gradient rails (sky, chrome, warm ramps), minimal-lightness contrast repair recorded raw→worn — procedural law (D.2 042) extended to aesthetics: same plate → same tokens; the standing suite re-runs extraction and requires a byte-identical no-op. (2) THE RATIO LAW: every token carries its plate AREA SHARE and the worn skin assigns UI area classes by those shares (gate-run: darks ~56% → grounds/surfaces, silver/whites ~16% → ink/linework, warms ~13% → interaction/emphasis, coral 1.37% → the one danger color, chroma outliers rarest → focus glints/peak moments). (3) THE EMULATION CHECK: B.6 r7 screenshots of shipped grounds are palette-projected and their area distribution must sit within a pinned distance of the plate's — 'look like the picture' is machine-checkable, not taste; bound calibrated on the first honest green run, then pinned. (4) The owner rack WEARS plate-derived COBALT-SERAPH; NEO-NOIR persists as a preserved named token set (ADR-018 cl.7 amended; per-theme dataviz validation and one-danger-color law unchanged; fonts and data encodings theme-independent). Full technique menu + gate-run extraction table: notes/cobalt-seraph-reference-plate.md. M2UX4 minted serial after the UX lane (⊗ M2UX1-3, same SPA css) | ACCEPTED |
| 108 | 2026-08-11 | v2.64 THE SERAPH DIRECTIVE (owner second look, same session; verbatim: 'I am specifically interested in making the UI look most like the figure in the bottom of the image — liquid and sharp and different' + 'the same sense of humanity, alienness, surrealness, beauty, technology, majesty, scale, sky… a truly cool implementation of how to import this feeling'): (1) ACCENT PASSES join the D.2 107 extractor — chroma-outlier (gold #ad8659→#db9969, 0.061%, the plate's rarest color; iris #2b58ba; coral peak #cd352b) and specular (shine is a base→peak PAIR #7f879a/#a2afbd→#ebecec/#fffffd, 1.22% area, census 119 true glints — the measured sparkle ceiling); (2) LIQUID CHROME: every chrome part shares ONE viewport-fixed vertical gradient built from the sky+steel rails, so a module's frame reflects its altitude and dragging slides the reflection under it (mercury behavior, zero animation code, testable via computed background geometry); (3) SHARP: cel edges — 1px linework #574f56/#a2afbd/#fffffd, no blur or drop shadow on interactive surfaces, module controls silent until hover/focus (the seraph is featureless until approached); (4) THE TWO SKIES: a ≤8%-opacity full-viewport fixed wash samples the plate vertically — starfield night above, day cobalt below, amber horizon #412829/#3c2828 as the Vitals strip's top border (the ground the owner stands on is spend truth); the wash IS the chrome reflection source; (5) ONTOLOGY MAPPING (theme-level style, never a data re-encoding): warm=the human, chrome=the machine, cobalt=the shared world, white/silver=meaning, gold=the earned (≤0.1% painted area); machine-touching-human affordances are chrome-on-warm — the plate's central gesture; (6) THE WING RULE: at most one hero gesture per view, dark share IS the majesty, empty states get one cel-sharp #eaecec sweep, never apologetic gray text; (7) THE GLINT BUDGET: 4-point glints only at peak moments, specular area ≤ the plate's 1.2%; (8) ATMOSPHERIC DEPTH: recede by desaturate+cool haze, never by shadow; (9) the extractor emits a PART→MATERIAL→TOKEN map for the M2UX3 module template and M2UX4 asserts it via conformance + per-family area budgets in the emulation check. M2UX4 retrofitted in place (still TODO, deps/exclusions unchanged). Full mechanism table: notes/cobalt-seraph-reference-plate.md §second look | ACCEPTED |
| 109 | 2026-08-11 | v2.65 THE SHINE FLOOR (owner, third look, same session; verbatim: 'The chrome needs to be liquid and shiny and white — all of this feels muted and matte'): the D.2 108 chrome pair was MEAN-sampled and the mean of a mirror is gray — measured truth: the seraph's achromatic mass is 73% near-black (median L=0.176 #100f15) yet reads as white chrome because the luminance histogram is violently bimodal — the top 15% jumps #888d9b→#a1aebd→#bfcbd5→#dbe5ee→#eff8fa (p99.8, icy cool) with almost no midtone. LAWS: (1) mean-sampling banned for chrome; chrome tokens are luminance-PERCENTILE ramps with BANDED stops taken from the measured percentiles (p2 #090807, p50 #100f15, p70 #302529, p85 #888d9b, p93 #a1aebd, p97 #bfcbd5, p99 #dbe5ee, p99.8 #eff8fa); (2) THE MISSING MIDDLE is the shine — matte IS midtones; a rendered chrome part's luminance histogram must be bimodal (dark seams + white blaze, thin middle), asserted as a variance/bimodality check in the emulation suite; (3) the white end reaches L≥0.97 icy-cool and always sits adjacent to near-black seams — adjacency makes the blaze; (4) the worn chrome base flips from steel #7f879a (now transition-band only) to the bright-silver class #dbe5ee/#eff8fa with near-black seams; liquid chrome headers render the banded ramp UNDER the viewport-fixed two-sky reflection (D.2 108 unchanged). M2UX4 charge updated in place | ACCEPTED |
| 110 | 2026-08-11 | v2.66 THE RIM LAW (owner, fourth look, same session; verbatim: 'that kind of chrome for the borders not buttons… that isn't aesthetic at all'): the D.2 109 ramp was being applied as FILLS (headers, chips) — but the seraph wears chrome as RIM LIGHT on a dark body; the bright fill in the plate was always the sky behind the figure, never the figure itself. LAWS: (1) chrome is an EDGE material — module borders, frames, focus rings, resize edges, separators; the banded percentile ramp runs ALONG the stroke (border-image/rim, 1-2px at rest, blaze widens slightly on hover/focus), and the D.2 108 viewport-fixed reflection slides ON THE RIM when a module moves; (2) chrome never fills a button, header, chip, or any slab — fills stay dark cel (ground/surface families) with silver/white linework text; (3) TEXT NEVER SITS ON CHROME; (4) the D.2 109 bimodality check samples along the stroke for rims; area budgets unchanged (rims are naturally thin — the plate's own chrome share). Part→material map corrected in place: header = dark cel + 1px chrome top rim; frame/border = liquid chrome rim (quiet at rest, blaze on focus); drag/resize = rim blaze + glint on approach, never a filled chip | ACCEPTED |
| 111 | 2026-08-11 | v2.67 THE AUDITION CLAUSE (owner, fifth look, same session; verbatim: 'honestly a little better i like the current one better than what you are pitching right now'): the gate's HTML sketches were competing against the real M2B skin and losing — a category error, not a verdict on the derivation; B.6 r7's real-render law extends to TASTE: no theme is ruled on from mockups, only from the real rack. (1) D.2 107(4) is SOFTENED: M2UX4 builds plate-derived COBALT-SERAPH as a complete, switchable token set BESIDE NEO-NOIR; NEO-NOIR remains worn until the owner rules. (2) M2UX4's exit gains THE AUDITION: same views rendered in both themes on the live rack, one-keystroke switch, screenshots as evidence; the owner's side-by-side ruling decides the worn skin (and may keep NEO-NOIR). (3) THE BLEND PATH is legal law, not defeat: what the current skin does right — near-black grounds (#03070c is already the plate's night) and ELECTRIC ink (#38d7ff cyan energy) — may be preserved with the electricity re-sourced from the plate's own live wires (iris #2b58ba/#294cbd, specular #eff8fa/#fffffd); a blended candidate may enter the audition as a third contestant. All D.2 108-110 mechanism law (liquid rim chrome, two skies, ontology, shine floor) applies to whichever skin is worn. The gate stops pitching sketches | ACCEPTED |
| 112 | 2026-08-11 | v2.68 THE AUDITION LOOP + THE TWO FACES (owner, same session; protocol verbatim: 'Maybe you generate 4 things, and physically look at them, and then I tell you which of the samples I like and we do that for several iteration loops'): the loop ran five rounds same-day on candidates generated from the REAL harness stylesheets (literal maps + override blocks, real-class markup, local render, gate inspects and repairs before the owner sees). RULINGS extracted from picks: (1) round 1 → BLEND (keep the current skin's electric-ink energy, hues re-sourced from the plate); (2) round 2 → BOTH METALS + mid-round voice 'less red' (red is danger-only) and 'more that golden or silver'; (3) round 3 rebuked ('I was hoping for more differences') → THE VARIANCE LAW: mutation radius stays WIDE until the owner narrows; (4) round 4 → NIGHT FACE = SERAPH DRESSED + owner names Day Sky as light-mode base; (5) round 5 → DAY FACE = GOLD LINES. THE TWO FACES: the plate's upper panel is night, lower panel is day — one image, both modes; frozen exact maps/CSS in notes/cobalt-seraph-audition.md + notes/audition/ kit; both are M2UX4's starting candidates, switchable beside NEO-NOIR, real-rack audition final (D.2 111). SEAM-LEAK SWEEP is packet duty: the loop discovered the :root seam does not govern the app — 268 hard-coded literals, the #dad8d2/#c9dce4 user-text pair, the rgba(7,18,27,α) glass family; every color literal migrates into the token seam and computed-style checks (user text, composer glass, module borders resolve to tokens at every breakpoint) join the standing suite | ACCEPTED |
| 113 | 2026-08-11 | v2.69 THE THREE-THEME RULING (owner, session close; verbatim: 'let's leave the current color scheme in place and add these two as alternatives please in the packet'): NEO-NOIR remains the WORN DEFAULT — the D.2 111 pending worn-skin audition is RESOLVED in place, no replacement decision remains open; M2UX4 ships THREE themes through the tightened token seam: NEO-NOIR (default, byte-identical to today after the seam sweep — the sweep must not shift the worn skin), SERAPH DRESSED (night alternate), GOLD LINES (day alternate), selectable via an ordinary persistent control on the real rack; per-theme law still applies to the alternates (dataviz six checks, one danger color, D.2 108-110 mechanisms where each face wears them); exit: three themes live-switchable + seam sweep green + NEO-NOIR pixel-stable | ACCEPTED |
| 114 | 2026-08-11 | v2.70 THE PLATE PRESS (owner, same session; verbatim: 'It would be a really cool feature if we could automate this process — a feature where we could feed in a picture and then the color palette is extracted and makes a new colorway for the harness. we don't need to have all these intermediate steps of iteration but we can apply them to the rules'): the five-round audition was the RESEARCH; its laws are the RECIPE; the recipe becomes a FEATURE. Any image the owner presses becomes a named colorway via the ratified pipeline, fully deterministic, no LLM in the loop: (1) plate intake (file picker on the theme control; sha256 pins identity; same plate → same colorway); (2) derivation per D.2 107–109 — OKLab k-means with pinned seed, chroma-outlier + specular accent passes, luminance-percentile chrome ramps (never means), area shares; (3) ROLE ASSIGNMENT by the ratio law's algorithm (darkest large cluster → ground; lightest high-share → ink; highest-chroma → accent; nearest warm-red → the one danger color; rarest high-chroma → the earned) with minimal-lightness contrast repair recorded raw→worn; (4) VALIDATION fail-closed with plain remedy — dataviz six checks, contrast floors, one danger color; a plate that cannot yield a lawful colorway is refused with the failing pair named; (5) the colorway lands as DATA in the theme switcher beside NEO-NOIR and the two faces — tokens only, never code with surface access (ADR-018 cl.7), persisted locally, removable. The two faces remain curated law; pressed colorways are the owner's own. M2UX5 minted deps M2UX4 | ACCEPTED |
| 112b | 2026-08-11 | v2.71 THE LIFECYCLE MATRIX + ONE MIND (owner, gate day, after the prompt/guard dead-end: 'this CI/CD pipeline needs to be uber robust — it seems like any time we are working on it something breaks'): the root pattern across F016/F018/F019/F031, M2V, M2CI, and today's dead-end is UNENUMERATED STATE COMBINATIONS — each incident was a lifecycle state (app released/dev-drifted x palace current/behind/ahead/legacy x guard pass/block x journal writable/not x port free/held x assets present/absent x clean-room/host) whose voice and action nobody had defined. Permanent law: (1) THE MATRIX — the lifecycle decision table is ENUMERATED and TABLE-TESTED in the standing suite and in clean-room CI; a state combination without a defined, tested voice+action is a defect even before a user hits it; (2) THE INCIDENT RITUAL — every future lifecycle incident adds its exact state as a matrix row BEFORE its fix may close (regression discipline at the design-table level, not the bug level); (3) ONE MIND — a prompt may never offer an action a downstream guard would refuse: `up` pre-runs the deploy preflight and speaks the DEV-GROUND VOICE on unreleased source ('your app includes unreleased changes; your Palace is compatible; Nocturne will start normally'). M2LC minted (fire-now, parallel-OPEN); M2REL rescoped to the 0.1.3 release alone | ACCEPTED |
| 113b | 2026-08-12 | v2.72 F037 RESOLVED + EVIDENCE CAPTURE LAW + VISIBILITY DOCTRINE (owner ruling: 'I'm fine with hiding garden as it's just a build environment, but I want Nocturne in general to be available to people'): (1) VISIBILITY — nocturne-garden is PRIVATE (flipped 2026-08-12; the garden never shows, now literally); nocturne-harness and nocturne-spine remain PUBLIC by design — the product is for people, the quick-setup path is the point. (2) THE FOUR HISTORICAL SCREENSHOTS are OWNER-ACCEPTED as benign (gate inspected them: the exposed memory text is the owner's motivation-philosophy note — no PII, no credentials, no business content; precedent: the dev-key acceptance). No history purge; the option remains open to the owner. (3) THE EVIDENCE CAPTURE LAW (permanent; the real fix): committed verification evidence comes ONLY from fixture/verification identities — screenshots or transcripts of the OWNER'S rack are NEVER committed to any repo; the M2ST4 canon enforces mechanically (fixture banners/verification identity detectable in evidence). Motivation: today's exposure was benign; the same workflow against the owner's real Long Build palace would not be — the law fixes the trajectory, not the instance. (4) M2UX4 unblocked: reset for a fresh closeout recording tip 4854aee with fixture-only evidence verified; M2UX5 unblocks behind it | ACCEPTED |
| 114b | 2026-08-16 | v2.74 THE SYMPHONY ENACTMENT (owner: 'you have my blessing to fire away' — gate recommendations stand as rulings, owner may veto any): (1) ENACTED AS LAW: planning rulings R1-R23, acceptance bar S1-S4, the primitive alignment charter (14 alignments, 3 blocked misalignments, 3 vision adapters, independent replaceability), the four-wave Symphony plan (notes/symphony-plan.md), the tree-rooted component registry (RECIPE surfaces name registry components), and gap resolutions G1-G7, G9-G10, G12-G18, G20 as drafted in the trace. (2) OWNER-DELEGATED RULINGS NOW FIXED: R22 budgets (3 attempts, $10 wall, 3 rounds, depth 2, 4 children, 30 min); G11 — the winner's memory batch routes to the PALACE QUEUE (judged context attached; consent surface intact, no fast lane) and LOSERS' memories TOMBSTONE as attempt lineage (never injectable, never discarded — provenance doctrine); G19 — owner interventions classify as (a) clarification/logged follow-up, (b) attempt cancellation within T2, (c) charter/scope change, and class (c) FORKS the symphony (charters are immutable per deliberation; changing one IS a new deliberation; the old run's lineage stands append-only); R21 spatial linking blessed (frames within layers, global as the one escape, layers are rooms, portals on the YAGNI clock — supersedes GLOBAL/CURRENT at the module family's build). (3) ORPHANS PLACED: the device chain -> Wave 2 module family; memories-as-indexes/fetch_episode -> the long-thread/curator lane; ADR-016 rollback bar = a REQUIREMENT ON the 3D module wave (generators take as_of from day one; the scrubber UI may phase). (4) OWED GATE WORK: the editor pass folding A-020..A-048 and textually reconciling ADR-017/018 with this enactment. (5) SYM1-SYM6 minted; Card 6: SYM1 parallel SYM4 | ACCEPTED |
| 115 | 2026-08-17 | v2.75 THE GRIMOIRE THEMES + THE CONJURATION LAW (owner; verbatim: 'wizard mode should have runes and scripts and appearing and disappearing. Technomancer should be similar but more neon and cyber punk'): two AUTHORED built-ins join the lore family (Nocturne, the ouroboros, Chrysopoeia, the Athanor, the Cauldron) — no reference plate, so the press law is untriggered; per-theme validation (dataviz six checks, one danger color, vision §8 invariants) is universal. (1) THE CONJURATION LAW — new theme law for animated motifs, binding on every theme: motion is DECORATIVE ONLY, never on data channels or reading surfaces (a rune may frame a module; it may never carry or obscure a value); conjurations fire on STATE CHANGES (mount, hover, focus, peak moments) and settle to rest; ambient loops live only in empty states and backgrounds at whisper opacity; prefers-reduced-motion collapses every conjuration to its static rest state — suite-asserted, not best-effort; glyph layers are Unicode/typographic content in motif stylesheets (tokens/materials/keyframes; never code with surface access, ADR-018 cl.7). (2) WIZARD MODE (the scriptorium): candlelit manuscript world — ink-black/umber grounds, candleflame-amber accent, verdigris secondary, silver-leaf linework, and RUBRIC RED as the one danger color (rubrication is the manuscript's own red-mark practice); motifs: the RUNE RING (module mount inscribes an alchemical-glyph ring that fades), MARGINALIA (staggered hover glyphs in module margins), the BREATHING SCRIPT (empty states: faint drifting script lines); glyph set: the Unicode alchemical block (U+1F700) + planetary/metal sigils — Cleopatra's manuscript made visible (D.2 090); the earned stays distinct from the amber body as bright gold-leaf. (3) TECHNOMANCER (the sigil circuit): the same magic on neon — near-black violet grounds, phosphor-green accent, ultraviolet secondary (deliberately distinct from NEO-NOIR's cyan/pink), one neon-red danger; rune rings render as circuit-sigils; conjuration is GLITCH-IN/DEREZ-OUT (steps/clip reveals); scanline shimmer; empty-state glyph rain at whisper opacity. (4) Palettes are STARTING VALUES — the owner may run the audition loop (D.2 112 of 2026-08-11) on the real rack at claim. (5) NUMBERING ERRATUM: rows 112–114 each appear twice (the 2026-08-11 gate-day sequence and later sessions); the log is append-only so renumbering is illegal — disambiguate by date; textual repair joins the owed editor pass. M2UX6 minted deps M2UX4 (DONE — dependency-ready) | ACCEPTED |
| 116 | 2026-08-17 | v2.76 THE RUNE CRANK: GRIMOIRE SAMPLES BLESSED (owner, after viewing live conjuring samples; verbatim: 'I like it crank up the runes and symbols a bit more in different locations maybe 50% more. then finalize it.'): the samples were generated from the CURRENT production stylesheets through the real --seam-* token layer (per-family hue transform of the neo-noir seam block) with CSS-only conjurations, viewed live in motion. RULINGS: (1) density +50% and MORE LOCATIONS — wizard: rune ring on every module head (16 glyphs), marginalia in module right margins, the breathing script on TWO axes of empty space (horizontal baseline + vertical column), a margin rune beside each message label, the composer-edge rune row, topbar sigil trio; technomancer: 13-sigil circuit rings on module heads, module-bottom braille hexlines, TWO glyph-rain columns (phosphor + ultraviolet), composer circuit row, topbar sigil cluster, scanline shimmer; (2) the kit at notes/audition-grimoire/ (generator + both theme CSS + samples) is the REFERENCE IMPLEMENTATION — the builder implements from it: its generated :root blocks are the starting token sets and its motif CSS is the motif spec; (3) M2UX6 briefs are FINAL; audition rounds waived (owner may reopen); (4) unchanged: the conjuration law — the demo's continuous loops become state-change conjurations in product, reduced-motion collapses to static rest, motion never touches data channels or reading surfaces. One repair recorded for the builder's benefit: CSS unicode escapes must survive the toolchain intact (\\1F701 class glyphs render as raw hex text when mangled — the demo caught and fixed exactly this) | ACCEPTED |
| 117 | 2026-08-17 | v2.77 THE ELDER SCRIPTS (owner, viewing the cranked samples; verbatim: 'can we use the transformer script the ones that are appearing here are a bit normal'): familiar symbols (planetary marks, geometric shapes, braille) read as keyboard characters, not magic — the glyph sets swap to genuinely alien scripts. WIZARD MODE speaks ELDER FUTHARK (ᚠ ᚱ ᚦ ᛟ …, the literal runes) + OGHAM (᚛ᚁᚂᚃ᚜ feather-marked) + GLAGOLITIC (ⰰ ⱚ, the occult curves), with the alchemical sigils retained only as rare accents (🜍 ☿ — the Chrysopoeia's own signs stay closest to the work); TECHNOMANCER speaks an angular MACHINE-SCRIPT mix of CARIAN (𐊐 𐊚 𐊇) + OLD TURKIC (𐰇 𐰔 𐰦) + TIFINAGH (ⵣ ⵥ ⴲ) — the transformer-alphabet feel achieved entirely from ancient human scripts; no franchise alphabet is copied, ever. Implementation law: motif glyphs are LITERAL UTF-8 characters in the stylesheets, never CSS unicode escapes — this kills the D.2 116 mangling class outright. Verified rendering live on the owner's platform before blessing. Kit re-frozen at notes/audition-grimoire/ as the reference implementation; M2UX6 briefs remain FINAL | ACCEPTED |
| 118 | 2026-08-17 | v2.78 THE SIGIL FORGE (owner, naming the hands with charts supplied; verbatim: 'This tolkein should be the wizard' + 'The transformer should be the technomancer'): the owner's reference charts are the Tengwar alphabet (Tolkien-estate-protected letterforms) and a fan-refined Cybertronian alphabet (franchise-associated designs) — the gate's counsel, recorded: Nocturne ships publicly (PyPI `nocturne-ai`, the capability ladder's blue-water ambitions), so VERBATIM COPYING OF EITHER ALPHABET IS REFUSED under D.2 117's no-franchise law; the FEEL is style and style is free. THE SIGIL FORGE delivers it: original glyphs GENERATED procedurally — the procedural law (D.2 042: same data → same geometry) extended to script itself. TWO HANDS, one forge: THE ELVISH HAND for WIZARD MODE (curved stems with gentle S-bows, attached lobes, descender hooks, occasional low loops, floating tehta-style dots/tildes above; fine 1.7 stroke, round caps — reads as flowing calligraphy without reproducing any tengwa) and THE MACHINE HAND for TECHNOMANCER (chamfered seeded walks on a 3×4 grid with detached stubs/notch accents; heavy 3.4 uniform stroke, square caps, miter joins — reads as angular machine-script without reproducing any Cybertronian glyph). Sigils are seeded and deterministic (same seed → same sigil), emitted as SVG data-URI strips consumed by motif CSS as background materials — data, never code (ADR-018 cl.7); repeat-x/repeat-y strips keep every conjuration animation. Verified live on the owner's platform. D.2 117's Unicode elder scripts remain the legal fallback set. M3-era hook noted, decorative-only per the conjuration law: sigils may later seed from REAL ids (module names, thread ids) so the script quietly encodes true provenance. sigil_forge.py joins the frozen kit at notes/audition-grimoire/; M2UX6 briefs FINAL | ACCEPTED |
| 119 | 2026-08-17 | v2.79 GRAMMAR, NOT GLYPHS (owner, sixth look; verbatim: 'Can we not copy them and just change them enough to not impinge on and create our own scripts? That way we have something cool on our own?' + 'Technomancer honestly already looks cool. but the wizard is a bit lacking'): the gate's counsel, recorded as doctrine — there is NO percent-different safe harbor in derivative work; a script that BEGINS from reference letterforms and mutates remains derivative in the record, exactly the fact pattern a publicly-distributed product must avoid; the lawful path that also yields 'something cool on our own' copies the GRAMMAR — featural anatomy, pen logic, construction rules are unprotectable ideas — and constructs original letterforms from it. ENACTED: (1) THE ELVISH HAND v2.1 — a STABLE 24-glyph constructed alphabet (versioned seed nocturne-elvish-v2; the same 24 glyphs forever), each glyph assembled featurally: stem kind (tall/short/descender/full, always S-curved — signature) × gapped lobes (attach with a visible gap — signature) × optional doubled stem/crossbar/terminal flick × tehta family above (dot/double/triangle/tilde/arc); CALLIGRAPHIC PEN PRESSURE via two stroke weights (2.6 downstrokes, 1.3 curves); strips sequence glyphs frequency-weighted with repeats so the script reads as writing, not noise. (2) THE MACHINE HAND: the owner-blessed v1 chamfered-walk algorithm is FROZEN VERBATIM ('technomancer honestly already looks cool'). (3) THE COLLISION RULE (conjuration-law corollary, caught live): decorative strips must clear every reading surface — head rings cap at 7-7.5rem and offset clear of module meta text. Kit re-frozen at notes/audition-grimoire/; M2UX6 briefs FINAL | ACCEPTED |
| 120 | 2026-08-18 | v2.80 THE FIXTURE CURTAIN (owner sighting, verbatim: 'I just saw another "regression" fixture which wasn't the real app opening I thought that we'd aligned to not make those'): the D.2 111 real-render alignment becomes statute for all machine verification — (1) no fixture ever opens on the owner's screen: verification browser runs are headless, always; scenario/fixture servers bind only for the check's lifetime and never auto-open; (2) every fixture-served page wears an unmistakable in-page FIXTURE banner (fixture name + packet id) so a stray render can never pass as the product; (3) the only window that opens unbidden on the owner's machine is the real app on real data via its own startup — verification invocations of the product always pass --no-open; (4) suite-asserted where testable. Provoked by F052 (SYM13's scenario app, port 8873, presenting the SPA shell on a canned graph) and F053 (the M3R release agent's out-of-surface repair of verification/sym13/browser_check.mjs — competent content retained, the scope breach named). M3FX minted for remediation; the M3S pre-review scout is rechained behind it and its checklist inherits the curtain | ACCEPTED |
| 121 | 2026-08-18 | v2.81 THE SCOUT'S WORD (owner, verbatim: 'I think that the findings are that a single scouts report should not be taken as gospel out of context unless it is operating a specific test we've aligned on'): B.6 rule 13 — scout reports are EVIDENCE, not verdict. ALIGNED items (a test the owner and gate aligned on: ratified checklist item, golden proof, suite) yield findings; DERIVED items (scout-designed under rule 9) yield CLAIMS that become findings only after the gate's mechanism check against the code path or owner alignment; claims are presented in neutral words without severity; any claim naming a core-law violation (memory fence, isolation, injection, spend, consent) is never presented before its mechanism check. Scouts label every item's class. Rule 12 (test motivation) gains its body stub. Provocations: F054 (Recipe proven only against its fixture; the released endpoint 503s) and F056 (the rescout's derived 'isolation leak' — the spine's candidate fence `project_key IS NULL OR = thread project_key` makes cross-project injection impossible and the scorer has no thread feature; the real defect is the Rack's project control not persisting its binding; gate's overreach in relaying it acknowledged). Owner directive seeded alongside: THE THREAD INDEX for relevance (owner: 'a continuous conversation, thread, that isn't ended has a unique id and that can be stored with the memory giving a new index') — memories record their origin thread id; `_thread_feature` mirrors `_project_feature` one level finer (same thread 1.0 / thread-less 0.5 / other 0.0); locality ladder project → location → thread; never a fence; notes/m3-planning-seeds.md | ACCEPTED |
| 122 | 2026-08-18 | v2.82 editor note — D.2 NUMBERING COLLISION RESOLVED BY SUFFIX (the erratum row 115(5) flagged to the owed editor pass): rows 112–114 were written twice by two concurrent gate sessions — the 2026-08-11 theme sequence (112 THE AUDITION LOOP + THE TWO FACES v2.68, 113 THE THREE-THEME RULING v2.69, 114 THE PLATE PRESS v2.70) and the later-written set (v2.71 THE LIFECYCLE MATRIX + ONE MIND of 2026-08-11, v2.72 F037 RESOLVED + EVIDENCE CAPTURE LAW + VISIBILITY DOCTRINE of 2026-08-12, v2.74 THE SYMPHONY ENACTMENT of 2026-08-16). The log is append-only and other documents cite these numbers, so NO ROW IS RENUMBERED: the first-written set keeps 112/113/114 and the later-written set now carries the suffixed ids 112b/113b/114b; both sets stand verbatim. Citation key for readers: theme law (the audition loop, the two faces, the plate press; GLOSSARY; the M2UX packets) → 112/113/114; the lifecycle matrix and its incident ritual (M2LC, matrix-row tests, the plan archive's 'D.2 112') → 112b; F037, the evidence capture law and repo visibility → 113b; the Symphony enactment (R1-R23, R22, G11, G19, R21; A-059's 'D.2 114 G6/G11') → 114b. D.3's header lineage is suffixed to match; nothing else is touched | ACCEPTED |
| 123 | 2026-08-18 | v2.82 EDITOR PASS IV (content-preserving, mirroring 041/055/066 — the M3 planning enactment's owed gate work per 114b(4)): (1) enacted amendments A-020..A-048 folded verbatim into the sections they amend, each site marked '(A-0NN, v2.82)' — token-cost policy A-020/A-021/A-025/A-026 → C.5 (A-020 clauses (a)/(c) marked superseded by A-021); near-miss veto A-022 and autonomous re-scoring A-030 → C.4; the two-stage gate A-023 → C.6; memory.panel.update A-024 → C.7; the ledger seam A-027 and broker reconciliation A-037 → ADR-024; Vitals A-028/A-029/A-044, Context Bars A-039, graph/console/simulation A-035/A-047/A-048 → ADR-009; the learner A-031 and citation v1 A-036 → ADR-005; queue mechanics A-032 → ADR-021; seeds A-033 and the M2N owner-lifecycle contracts A-041..A-046 → ADR-019; registry controls A-034 → ADR-023; fixture identity + receipt queue A-038 and test motivation A-040 → B.6 (rule 12's body moved from the end of D.4 to its rule per A-040; the v2.81 stub retired); C.2 gains a schema-extension pointer list; every AMENDMENTS.md entry stamped with its fold site; (2) the 112–114 collision resolved by suffix (row 122); (3) ADR-017 and ADR-018 gain 'Reconciliation (editor pass IV, 2026-08-18)' subsections stating which clauses the Symphony enactment (114b; D.2 102/103; A-059), R10 (the Cube dissolves into six independent liquid 3D modules with hardware tiers; the Athanor and the Cauldron join the family) and R21 (spatial linking: proximity frames within layers, GLOBAL as the one escape, layers are rooms, portals on the YAGNI clock) supersede — original clauses retained; ADR-023 clause 5 and §1.0's Escher-view entry gain pointers; one tension flagged for the owner (ADR-018's rejected 'literal always-3D rendering' vs R10's 'literally-3D asset'); (4) §2's P-nodes gain brief '(components: …)' pointers from notes/component-registry.md (114b(1)); (5) the v2.81 header paragraph moves to D.3's header lineage. No law deleted; no meaning changed | ACCEPTED |
| 124 | 2026-08-18 | v2.83 THE ENGINE LAW + THE FUNCTION LAW (owner, verbatim: 'I think the real thing is I want it rendered via video game engine not some sub par graphical creation. Also, to be super clear we are not to ever make visualizations that aren't based on real data transforms information. Great visualizations are as much function as form.'): (1) the 3D module family renders through a real game engine adopted dependency-first (candidates named in M3GE: Godot 4 web export, PlayCanvas, Babylon.js — the packet evaluates under R14 against real palace data at the hardware tiers inside the module sandbox), never hand-rolled graphics; ADR-018's 'always-3D rejected' clause superseded — R10 governs (closes the editor-pass IV tension, row 123). (2) every visual variable on every data surface binds to a real measured quantity with provenance; decoration stays confined to empty states/backgrounds per the conjuration law (116); unbindable visuals are defects. The Cauldron's founding principle ('every pixel encodes a real variable') becomes universal law | ACCEPTED |
| 125 | 2026-08-21 | v2.84 THE PRECEDENT LAW (owner, verbatim: 'How are we missing these old specs over and over I want to understand the issue and create an institutional fix maybe better search logic?'). DIAGNOSIS (gate, measured): record ≈ 30k lines across SPEC 4.7k, AMENDMENTS 0.7k + archive 1.9k, FLAGS 1.0k, notes 8.1k (23 files), reports 10.2k (168 files), DECISIONS 2.9k + 1.1k; boot reads ≈ 3k; 16 notes carry design decisions that are neither law nor boot reading — the Cube note's v6 stack choice (three.js + r3f + TSL) and the D.2 081 fixture-banner law were both missed by the gate when writing M3GE and v2.80. THE FIX: (1) PRECEDENT field required on every packet and amendment, produced by a deterministic whole-record search (garden/bin/precedent), cited or 'none found; searched: …'; refusal law extends to PRECEDENT-less packets; the gate is bound. (2) NOTES PROPOSE, SPEC DECIDES: note-only decisions bind nothing until promoted; standing-items headers + notes/INDEX.md in the boot reading. (3) ADOPT THE PRODUCT: the garden ingested into a dedicated palace principal (ADR-019 seed ingestion) as the semantic second stage of the precedent tool — the relay dogfoods the memory palace over its own growing record. (4) Retroactive promotions: Cube v6 stack → pending ADR-008 amendment (owner re-measure ruling open: three.js measured beside PlayCanvas on the living-systems requirement); v2.80 annotated as extending D.2 081. M3PR minted | ACCEPTED |
| 126 | 2026-08-21 | v2.85 THE ANCESTRY CLAUSE + THE ARCHIVE RETROFIT (owner, verbatim: 'We should think through a small process change for creating packets that mandates looking for old packets to be the permanent fix and we should do a one time update where we go through all the old packets and update and integrate them into our modern formats. All those original ideas should be motivated and are extremely central and important.'): (1) the PRECEDENT field gains a mandatory ancestry half — ancestor packets named BY ID from the plan archive, reports, and board history, with inherited constraints carried forward or explicitly released; folded into the one PRECEDENT ritual so minting stays one motion, not a second ceremony. (2) M3AR minted, chained after M3PR (shared surfaces: the archive file, the notes index, the palace ingestion): every pre-template charge retrofitted to MOTIVATION/RECIPE/AUTHORITY/ANCESTRY in place in the archive; each retrofitted charge re-ingested as a well-formed palace memory; the component registry gains any missing founding links; never-built or dropped ideas surface in notes/dormant-ideas.md for the owner's review — the founding ideas are load-bearing, and the sweep makes them retrievable rather than archaeological | ACCEPTED |
| 127 | 2026-08-24 | v2.86 THE SIMPLE SEARCH (owner, verbatim: 'It doesn't need to be that complex... just look through old records for keywords LOL. I just want to add that to standard spec creation which you do.'): precedent = grep, ranked by authority class, plus the PRECEDENT/ANCESTRY habit in the gate's own spec-creation — the permanent fix is the HABIT, the tool is one small script. v2.84 clause 3 (garden-in-the-palace semantic stage) struck to the dormant ledger — protection-budget law applied to the gate's own machinery (the owner's overbuilding pattern, D.2-era, caught again). M3PR descoped: deliver bin/precedent + notes standing-items headers + INDEX; the ingestion script already written ships NOWHERE (journaled residue, not wired). M3AR exit amended: no palace re-ingest | ACCEPTED |
| 128 | 2026-08-24 | v2.87 THE SEED PACKET LAW (owner, verbatim: 'I'm also thinking that in the future we just mint incomplete packets from now on whenever a new idea is riffed that way we never lose it and we can always flesh it out later when we get to the incomplete packets.'): the permanent form of the idea audit — (1) at riff time the gate mints a SEED: the owner's words verbatim, origin cite, a number in the seed rack (notes/dormant-ideas.md; board carries one pointer line, honoring the boot diet); (2) seeds are exempt from the template (D.2 106) and precedent (D.2 125) laws until FLESHED — fleshing produces the full charge with the seed as ancestry; (3) seeds are never claimable and invisible to dispatch; (4) a seed leaves the rack only as a fleshed packet, an owner-declined tombstone (kept, marked), or a proven integration. Retroactive: audit items 1-46 are the founding rack. Motivation: the transcript audit proved riffed ideas die in conversation unless captured at birth — the rack is the idea's journal law | ACCEPTED |
| 129 | 2026-08-24 | v2.88 THE PLANNER'S HARNESS (owner: 'How is this going to be enforced on you or other planner/riffing managers going forward?'): enforcement is refusal machinery, not vigilance — (1) SEED-FIRST-RESPOND-SECOND binds every planning/riffing session; (2) builders refuse feature packets whose ANCESTRY names neither an SD seed nor a direct owner sentence (F050 class extended; the rack becomes the only path from idea to board); (3) planning handoffs carry a mandatory SEEDS MINTED line so silent idea-loss is visible as a missing field; (4) THE STANDING DIETS make the boot diet self-cleaning: one-entry header (prior entry -> D.3 in the same bump commit), DONE board rows -> board archive at wave close, resolved flags -> flags archive at stamp time. Same-day evidence: the header had regrown to 6.3k chars and the board carried 300+ DONE lines within three days of the manual sweep — manual diets decay, standing diets do not | ACCEPTED |
| 130 | 2026-08-24 | v2.89 THE FOUR RULINGS (owner, verbatim: '1. We can have black box score changes as an outcome of an optimization loop as long as the features we are optimizing the scalars for are understandable 2. use Three.js 3. Don't remember ensemble keep symphony 4. I'd prefer to keep it simple pick the better one.'): (1) transparency law scoped to the FEATURE layer — features are named understandable scalars with per-injection provenance forever; the weight-setting optimizer may be opaque (JEPA/solver candidates lawful under SD-029); (2) C1 closed — ADR-008's three.js + react-three-fiber + TSL stack STANDS; Decision 094 overruled; nebula re-render is Wave 5's first act; the law/build split healed on the law's side; (3) SD-035 tombstoned, owner-declined — symphony remains a property of the work; (4) the gate's delegated pick: the ITERATIVE BUILD TEST is the north-star acceptance test (measurable, repeatable, round 1 already run); SD-012 Ouroboros tombstoned as recoverable lineage. Wave 5's remaining gate: the Cauldron's name only | ACCEPTED |
| 131 | 2026-08-24 | v2.90 THE OWNER'S REVIEW (owner, verbatim: 'let's reset every packet to incomplete and I want to manually review every one before approval to add to a wave and the implementation plan'): the per-packet approval gate — seeds are incomplete until individually owner-reviewed; gate staging is proposal only; approval admits a packet to a wave; the wave still fires only on the recipe-card blessing. Same review: spend-rate policy + role model-policies pushed back for deeper fleshing; SD-047 minted (owner riff, verbatim: 'Are we actually running PI terminals with the fully pi feature set as threads: tools, skills, etc or are we doing something else.... we need to talk about boundaries and interfacing. This dove tales with the stack which is just a bunch of pi terminals.') — the runtime-vs-toolset boundary question; PRECEDENT: harness Decision 079 already rejected replacing our loop once (M3E era); ADR-001 (pydantic-ai runtime) and ADR-013 (the seam) govern until the owner re-rules | ACCEPTED |
| 132 | 2026-08-24 | v2.91 THE TOOL LAYER RULING (owner: 'I'm aligned with your recommendations'; SD-047 RULED): loop pydantic-ai (ADR-001/Decision 079 reaffirmed), broker ours, tool layer swaps to pydantic-ai-harness capabilities fenced in-process — the founding location-native premise stops being a guest patch (.mjs shims in PI's process) and becomes native; PI's RPC/Node/139MB/download retire at the swap; CONDITIONAL on M3PV proving coverage; golden tripwire = the zero-regression bar. The swap packet requires M3PV evidence + the owner's final go (v2.90 per-packet review honored) | ACCEPTED |
| 133 | 2026-08-24 | v2.92 THE SHARE RULING (owner, verbatim: 'the memory line and context window allocation to memories to be dynamically trained based off human feedback can be built, but we do not have enough human feedback yet for it to be turned on. leave it fixed for now. have it auto turn on after 100 feedbacks are added.'): SD-002 APPROVED with conditions — machinery built now, share + τ FIXED at current values until the 100-authentic-feedback floor (M2Y1/M2ZA hygiene applies), then both dimensions auto-join the work-based retrain. TRIGGERS, precisely: (ROOM ↑) budget_cut events on above-τ memories whose retention the replay shows matching owner dispositions — valuable memories were cut for space; (ROOM ↓) marginal injected memories going uncited or owner-removed — the room's occupants stop earning rent; ceiling-not-quota, min/max bounds, unused room returns to chat. (LINE) τ moves only via the binary replay — fewest disagreements with recorded add-backs/removals wins, cheaper at tie. Gate interpretation flagged: generations still land PROPOSED for the owner's tap (existing activation law) unless the owner rules full-auto. SD-002 is the first Wave 3 approval | ACCEPTED |
| 134 | 2026-08-24 | v2.93 PIN OVERFLOW + FORCE SEMANTICS (owner, verbatim: 'Is there the ability to pin memories into the context window in excess of the allocated memory window? that seems like it would be the process to increase context window. The score needed to incorporate the memory should increase if we manually removing lower scored memories etc. All of this needs to work with general hyperparameter optimization as well... Similarly to the hyperparameter force we can manually force it to a location but the algorithm will began moving that again immediately after.'): the two hands doctrine — PIN is the per-memory force (always injects, exempt from the share, never budget-cut; persistent pinned overflow is logged replay evidence FOR a larger share; overflow honest in Context Bars), FORCE is the parameter hand (informed-force v2.44: simulate-then-force, versioned generation, learner resumes immediately — starting point never lock); removals raise τ through the replay as always; share + τ + weights train as ONE loop per v2.58 — no standalone tuners. SD-002's fleshing carries all four clauses | ACCEPTED |
| 135 | 2026-08-24 | v2.94 THE ONE FORCE PATTERN + ONE CONSOLE (owner, verbatim: 'make sure those force or manual set functions all have the same pattern the user can set them but then the algorithm begins automating them again. Let's make sure these inputs and visualizations are in the same module as the existing hyper parameters.'): the universal manual-override contract — set → versioned generation → learner resumes immediately; informed-force wherever a backtest exists; never a lock, never a bespoke pattern. The share's inputs and visualizations join the INJECTION CONSOLE beside τ, weights, half-lives, simulate, force, and audition — one console forever; Context Bars displays, the console controls. Generalizes v2.44; extends v2.42-2.43; M2Z4's duplicate-button ban applies | ACCEPTED |
| 136 | 2026-08-24 | v2.95 THE SWAP GO + THE SHARE FIRES (owner sentence above): the v2.91 conditional resolves — owner overrides the M3PV HOLD knowingly; swap charge = upgrade pydantic-ai core (lockstep, golden tripwire green before and after), adopt pydantic-ai-harness capabilities for the seven tools + skills capability, close the grep-context and skill-resource gaps with shims on OUR side (never patches in foreign code — the fence and shims are in-process, the founding location-native premise realized), retire _pi/Node/download at green. M3MS (the share, per D.2 133-135) fires beside it. Both owner-approved per v2.90; this sentence is the wave-fire for the pair | ACCEPTED |
| 137 | 2026-08-24 | v2.96 THE GATE'S CHARTER (owner, verbatim: 'we need to make sure that this process for orchestrator management, you, is getting sufficiently codified'): garden/GATE.md is enacted as the orchestrator's constitution — ten sections gathering every law and hard-won ritual the gate runs by (boot + ground verification incl. the colima env truth; owner boundaries incl. the supervision throttle; seed-first-respond-second; the mint ritual w/ precedent+ancestry; the launch ritual w/ the stdin, one-turn, death-certification, and shared-checkout laws; the handoff audit; rule-13/mechanism-check/findings-freeze honesty rituals; the one-motion pen ritual; the standing diets; the failure-mode ledger). Succession law: repo-sufficient, memory-optional. GATE.md joins the gate's boot reading, stays under ~200 lines, yields to SPEC | ACCEPTED |
| 138 | 2026-08-25 | v2.97 THE ATTUNEMENT LAW (owner, verbatim: 'if set to global then it works across all tabs and modules within tabs. If not global then it's proximity. The proximity across tabs can be calculated by pythagorian theorum with tabs being +1 unit distance depending on their sequence in the menu bar then coordinates in the grid being further. If there is a tie pick a random one. if not global the module should show which thread name it is currently attuned to. If attuned to a stack it should show which stack.'): GLOBAL | ATTUNED replaces GLOBAL | CURRENT — proximity is the link: nearest context source by Euclidean distance over grid coordinates with +1 unit per tab-sequence step (treat tab index as a third axis; exact composition confirmed at fleshing); tie → random then sticky-until-layout-change, journaled; attunement DISPLAYED by real name (thread or stack; label law); no sources → honest 'unattuned'. Dragging a module near a different thread RE-ATTUNES it — placement is the binding, the channel-picker UI dies unbuilt (X8 superseded), and the ant-farm/cockpit intuition (visualizers oriented around what they sit beside) becomes product law. SD-007 is design-RULED, awaiting the owner's fire approval | ACCEPTED |
| 139 | 2026-08-25 | v2.98 THE DECK RULINGS + 0.1.6 (owner, verbatim: 'claude code does it an sometimes its nice... like a proposed user response? Not sure. See how they do it. yes advance upon enter but we should be able to scroll through the queue.' + 'yes to 0.1.6' + 'Make sure that each thing that isn't set to global displays what information it is displaying.'): draft-tweak-and-fire = the PROPOSED USER RESPONSE pattern, investigated from Claude Code's implementation at fleshing and adopted; advance-on-enter + scrollable queue; the attunement badge is hard law on every non-global module; the 0.1.6 release fires (M3R2, sentence embedded, receipt-marks-consumption) carrying origin_thread_id schema/spelling + the share machinery — also the F057 remedy-(b): after 0.1.6 the live palace serves the allocation field and current source injects again (remedy-(a) tolerance remains flagged open, recommended, unapproved). SD-046 → M3DK fired ∥ M3R2 (harness SPA vs spine release — disjoint) | ACCEPTED |
| 140 | 2026-08-28 | v2.99 THE DISCOVERY LAW (owner, verbatim: 'I'd prefer that we codify that the manager/orchestrator (you) do discovery not the implementation agents. discovery is hard and I want to align it with you before complete a packet. This is a general rule.'): discovery belongs to the gate — performed by the gate, aligned with the owner, THEN the packet fleshes; builders build from settled findings only; 'investigate/see how X does it' clauses in build charges are defects (the M3DK clause was the provoking instance, reclaimed same-day); gate-run verification packets (M3PV pattern) stay lawful. GATE.md §3a added | ACCEPTED |
| 141 | 2026-08-28 | v2.100 THE EXACT FENCE (owner, verbatim: 'b sounds right - exact for edits, bash keeps subtree'; provoked by the owner's spot check 'An agent in our harness cannot modify a file that it is not in the exact directory of' vs the built subtree check `target.is_relative_to(cwd)` — a drift from the founding amendment's literal words, enacted as subtree in D.2 103 without a conscious exact-vs-subtree ruling): edit/write demand target.parent == cwd (resolved, symlink-safe; refusal names the move remedy); bash keeps the subtree sandbox; reads free; grep/find/ls are reads. Prompt gains the sharp movement law ('you must move to a file's directory to modify it'). Golden tripwire re-goldened intentionally for the new refusals. M3EF approved by this sentence; serialized after M3DK (shared pydantic adapter surface) | ACCEPTED |
| 142 | 2026-08-31 | v2.101 THE NAMES LAW (owner, verbatim: 'We should stop refering to packets by the alphanumeric names to me. I don't have any refrence to what that is, you're welcome and should keep that nomenclature to help while your orgainize but they don't mean anything to me.'): owner-facing speech names features in plain words, never ids; ids remain internal bookkeeping and at most faint reference metadata on owner surfaces; extends the garden-never-shows and jargon laws to the gate's own mouth; GATE.md communication clause added; the wave card stays name-led with ids demoted to fine print | ACCEPTED |
| 143 | 2026-08-31 | v2.102 OWNER-FIRED MODE (owner, verbatim: 'I really want to cook through these waves now and get to build in my new harness rather than here. Let's get packets ironed out and i will fire them in codex from now on manually using our one liner.s'): the owner fires, the fire is the approval, auditability returns to the owner's hands; the gate fleshes/queues/audits; one-liners embed the work-to-completion instruction; gate-fired stays lawful for delegated mechanical work. GATE.md launch section amended | ACCEPTED |
| 144 | 2026-08-31 | v2.103 THE TUNING RULES (owner, verbatim: '1. Let's move from 25 to compaction on any thread. Limited to main threads not sub agents.' + the equivalence/neutrality questions + 'we lock and only allow one optimization to happen at once' + 'the backtest material so one threads history doesn't overpower it... a max size in the back test for all optimizations' + 'These optimizations should run in parrallel to the real time not blocking... use the current version not wait' + 'we need to define a good schema for storing state and scoring across optimization runs like we did for financials'): seven rules — compaction-trigger (main threads only; stride interim), embedding-band generation matching with novel-neutral + fewer-at-tie anti-padding, τ/share/size coupled in the one fit, the optimization lock, the balanced capped corpus, non-blocking incumbency, and the optimization ledger (queued as its own packet). The owner's Q4 answered: no conflict found between threshold and allotment law — one coupling (size×share share the same budget) which the one-fit law already governs | ACCEPTED |
| 146 | 2026-08-31 | v2.104 THE PURE RENDER + INTEGRATION ORDER (owner, verbatim: 'we have something that looks cool but is also functional. There shouldn't be changes made to the visualization if there aren't in the underlying data or actual processes running. Right? Also, let's make sure that we are baking this into our true integration plan not coming up with new things out of order.'): the render contract — view = f(event stream, queries), pure; no event, no motion; replay-deterministic; ambient decor only in conjuration zones; the framework agents build on IS the existing plugin three-surface contract (event stream / query+as_of / selection bus, the founding cube-note doctrine) with this purity clause bolted to its face. Sequencing: the look packet touches existing surfaces only; staged visual-wave modules adopt the grammars at their own builds from the one reference note | ACCEPTED |
| 147 | 2026-09-01 | v2.105 THE FRESHEST PALACE + THE PASS GALLERY (owner, verbatim: 'This whole contract thing can we make it simpler. I just want to always use the freshest one that works... Anyway yes fix these please. Also, make sure the scout is coming back with screen shots of all the features working!'): standing release grant — contract-bumping landings auto-release through the standing ritual as ordinary operation (guard-gated, receipt-backed, owner notified after); `up` auto-updates a behind palace with one plain line; per-release sentences retired; credentials/IAM/billing stay granted. Scouts owe a screenshot per PASS (the gallery), delivered to the owner by the gate. F060 (front door), F061 (release), F062 (404 leak) approved for repair; F061's remedy is the first release under the standing grant | ACCEPTED |
| 148 | 2026-09-01 | v2.106 THE HEARTBEAT (owner, verbatim: 'I am a bit concerned that even the ability for the harness to submit prompts and get responses broke through this... that super core functionality that should have been caught somewhere along the lines right?'): the founding warning ('code-level green is a liar') was answered with EPISODIC scouts, never a continuous check; six landings shipped with green suites and a dead front door, and the gate's audits (suites only) passed all six. Now law: the packaged-app core-loop walk (fresh thread → first prompt → gate → answer → receipt → journal) runs in every relevant handoff's exit ground, in CI, and in every gate audit; red heartbeat = red ground, Boot Sequence step 2 includes it. M3FP delivers the heartbeat as a standing suite member; GATE.md §0/§5 amended | ACCEPTED |
| 149 | 2026-09-01 | v2.107 EVERY FEATURE GETS A WALK (owner, verbatim: 'I think every feature pack from now on should have at least a minor "walk" please dont' really jargonify things just speak as specifically as possible.'): a feature packet's exit includes the builder opening the real packaged app in a real browser, using the feature as a person would, and attaching a screenshot of it working, named by the feature, in the report. Not done without it. Template EXIT clause amended | ACCEPTED |
| 150 | 2026-09-02 | v2.108 THE AXIOMS (owner-authored working axioms 0–12, garden/AXIOMS.md; boot STEP 0; frozen verbatim into harness/ and spine/ CLAUDE.md + AGENTS.md — enforcement is the mechanically-loaded file, not a one-hop pointer). Rulings: axiom 9 amended — pushing remote main is allowed in this project, never force-push; axiom 7c restated — document problem-solving concisely, never the superfluous or easily inferable. Seeded: SD-058 (the axioms as the starter AGENTS.md `nocturne init` drops in a new project) | ACCEPTED |
| 151 | 2026-09-08 | v2.109 THE CORE LOOP IS THE GATE'S (owner, verbatim: 'you all should be able to just make basic prompt response work with the pydantic prebuilt stuff. I don't want to have to keep approving these one offs'): core-loop fixes are fleshed by the gate without an approval round and fired by the owner like every packet (corrected same day — the packet paradigm stands); scaffolding around the framework is deleted, not patched, when it is the cause (F068 finalize invariant, F072 snapshot gate on the composer). GATE §1 amended | ACCEPTED |
| 152 | 2026-09-08 | v2.110 A GUARD CITES ITS INCIDENT (owner, verbatim: 'It looks like we have a problem overbuilding guards'): the gate's census found 391 hand-rolled raises in harness/src (390 with messages, 2 quoted in any test; 179 in the core-loop files) plus 26 disabled-until gates and 26 throws in the web — and the week's four core breaks (F068 finalize invariant, F072 snapshot gate, F075 one-request bound, the module-bridge channel) were all guards we invented. Law: a core-loop guard names its incident or wall in one line beside it, or is deleted; kept guards carry a test quoting their message; 'fail-closed' is for walls only. B.6 rule 14. The diet: M3GD sweeps the core-loop files; the boundary files follow after the second walk | ACCEPTED |
| 153 | 2026-09-09 | v2.111 NO AGENT MEMORIES UNTIL COMPACTION (owner ruling, verbatim in SD-060): creation moments = main-thread compaction, thread close, explicit /remember; mid-turn save tool removed; sub-agents never; sub-agent returns distilled + capped so they never force the main thread to compact. Clarifies v2.58/D.2 101 (compaction memory duty was additive; now it is THE event) and the Aug 31 optimizer-at-compaction ruling (v2.103). The Deck's scroll-past-approves applies only to compaction-born candidates. M3CM minted: the harness's own compaction (fill-percentage trigger against model metadata, the three-way triage, extraction at compaction) carries this law | ACCEPTED |
| 154 | 2026-09-12 | v2.112 THE FEATURE LEDGER (owner, verbatim: 'I want the features to be the thing that the scout checks every time, to make sure that all the old features work with the new ones'; and: 'It's really important that we have the right terminology and aren't inventing constraints that weren't directly described by us or agreed to by me'): the ledger (notes/feature-ledger.md, 194 rows from every owner message and the founding thread; 116 built, 32 partial, 33 not built, 5 horizon, 5 dropped, 3 unverified) replaces hand-written scout scripts; every label was checked against the owner's verbatim (124 of 196 tightened), 25 rows marked as constitution commitments; the second walk (M3RW) becomes 'run the ledger'. GATE §5/§6 amended | ACCEPTED |
| 155 | 2026-09-13 | v2.113 THE RULES LEDGER: every rule mapped to its enforcement (test/lint/canon/code/gate/none) with evidence; 19 testable rules lacked any check; the map found rule-vs-code conflicts (a top_k cap beside the threshold; spend reconciliation on a clock; a public tombstone PATCH; the ensemble term surviving in the glossary and ADR-012; the cube rule vs the modules ruling) and rule-vs-rule questions for the owner (deprecated memories deleted vs never hard-deleted). M3RC minted: the rules machine. GATE §5 amended | ACCEPTED |

## D.3 Resolved-question index (where each folded)

OQ-1 block placement → ADR-002 · OQ-2 offline mode → ADR-003 · OQ-3 mid-thread
live reads → ADR-004 · OQ-4 curator autonomy (earned per verdict class) →
ADR-022 · OQ-15 per-message re-scoring (first-gate once, then autonomous;
v2.31) → ADR-005 · OQ-6 presence transport → ADR-006 · OQ-7 durable
execution → ADR-001 · OQ-8 framework → ADR-001 · OQ-9 web command center →
ADR-008 · OQ-10 M1 slice → B.3 · OQ-11 identity → ADR-008 · OQ-13 pure relay →
ADR-008 · OQ-16 providers/models → C.5 · OQ-14 repo/product names → 1.0
vernacular (v1.10) · OQ-17 roots alpha → selection focus, not EV
(ADR-018, 2026-07-21)


### Header lineage moved here 2026-08-18 (boot-diet law; content verbatim)

**Version 2.106** (2026-09-01) — THE HEARTBEAT (owner: 'even the ability for the harness to submit prompts and get responses broke through this... that super core functionality that should have been caught somewhere along the lines right?' — right): a STANDING AUTOMATED WALK of the core loop in the PACKAGED app through a real headless browser — fresh thread → first prompt → gate opens → answer streams → receipt lands → journal line written — is the heartbeat; it runs (a) in the exit ground of every packet touching web, daemon, runtime, or toolset, (b) in clean-room CI, and (c) in every gate audit. Green suites with a red heartbeat are RED GROUND — the Boot Sequence's ground verification includes it. Scouts remain the episodic human-style walk; the heartbeat is continuous. Gate confession recorded: six landings passed gate audits on suites alone with the front door dead. The front-door fix delivers the heartbeat's first form.

**Version 2.105** (2026-09-01) — THE FRESHEST PALACE + THE PASS GALLERY (owner: 'This whole contract thing can we make it simpler. I just want to always use the freshest one that works' and 'make sure the scout is coming back with screen shots of all the features working!'): (1) A STANDING RELEASE GRANT replaces per-release sentences — when landed work bumps the palace contract, the release runs through the standing ritual (verified backup receipt → immutable image → rollout → migrations → remote verification) as ORDINARY OPERATION, the owner notified after, the guard the only gate; `nocturne up` updates a behind palace automatically with one plain line, no prompt; the breaker, backups, no-downgrades, and receipts remain the safety; the owner never reads a contract number again — 'freshest that works' is the whole policy. Scope law amended accordingly (releases under the standing grant are data-plane-class operations; credentials/IAM/billing remain granted). (2) THE PASS GALLERY: every scout PASS ships its screenshot of the feature working, and the gate hands the owner the gallery, not just the failures. Fixes A/B/C approved ('yes fix these please'): the front door, release 0.1.9 (the first automatic-class release, still carrying this sentence), quiet the 404.

**Version 2.104** (2026-08-31) — THE PURE RENDER (owner: 'There shouldn't be changes made to the visualization if there aren't in the underlying data or actual processes running. Right?' — right, made law): every visualization is a PURE PROJECTION of the plugin read surfaces (the event stream and queries — ADR-018's founding contract): a visual change exists only because a data or process event exists; static reality renders still; wall-clock animation of data marks is forbidden; the same event history replays to the same visuals (a testable determinism class, camera aside); ambient decoration stays confined to the conjuration law's sanctioned zones. INTEGRATION ORDER (owner: 'baking this into our true integration plan not coming up with new things out of order'): the look derivation constrains itself to EXISTING surfaces now; the deeper grammars (roots, creature, Athanor, tiers) land WITH their staged visual-wave modules, all deriving from the one reference note — no new visual subsystems outside the staged waves.

**Version 2.103** (2026-08-31) — THE TUNING RULES (owner, the live learning design session): (1) THE TRIGGER moves from the 25-disposition stride to COMPACTION ON ANY MAIN THREAD — sub-agent compactions never trigger; the stride remains the interim heartbeat only until compaction lands, then retires. (2) GENERATION MATCHING: instruction-backtest memories match across generations by embedding similarity using the existing dedup bands, never exact wording; NOVEL memories (absent from the backtest) score NEUTRAL — and cheaper-at-tie extends to creation: at equal backtest score, the generation producing FEWER/SMALLER memories wins (the anti-padding guard). (3) τ, the share, and the size cap are COUPLED, not conflicting — all train in the one fit; legacy absolute budgets stay replay-inputs-only. (4) THE OPTIMIZATION LOCK: one optimization at a time, advisory-locked (the migration-lock pattern). (5) THE BALANCED CORPUS: backtest material is stratified so no single thread's history overpowers — a mix across history — with a MAX SIZE for every optimization's corpus. (6) NON-BLOCKING (codifying built posture): optimizations run parallel to real time; live work uses the incumbent, never waits. (7) THE OPTIMIZATION LEDGER: every optimization run recorded in an append-only normal form across ALL loops — the financial-schema discipline applied to learning (packet queued).

**Version 2.102** (2026-08-31) — OWNER-FIRED MODE (owner: 'Let's get packets ironed out and i will fire them in codex from now on manually using our one liners'): the gate MINTS, FLESHES, and QUEUES; the OWNER FIRES — pasting the one-liner into Codex is simultaneously the per-packet approval (v2.90 satisfied by the act itself) and the launch; the chain of custody lives in the owner's own hands and the app's own UI. The gate's remaining launch duties: hand ready packets to the owner as plain-named queue entries each carrying its one-liner (completion instruction embedded to reduce stalls), audit every handoff, certify deaths, and resume stalled sessions only at the owner's word or with an explicit note. Gate-fired launches remain lawful for mechanical work (fixers, scouts) when the owner delegates them.

**Version 2.101** (2026-08-31) — THE NAMES LAW (owner: 'We should stop referring to packets by the alphanumeric names to me... you're welcome and should keep that nomenclature to help while you organize but they don't mean anything to me'): to the owner, every packet is its FEATURE NAME in plain words — 'the memory share,' 'the exact fence,' 'the spend table' — never its id. Ids (M3xx, SD-xxx, F0xx) are the gate's internal bookkeeping — board, reports, precedent searches — and may appear on owner surfaces only as faint reference metadata, never as the name. Extends v2.48 (the garden never shows — the owner is the first customer) and v2.52 (jargon law). Binds the gate's chat, the wave card, findings, and every summary.

**Version 2.100** (2026-08-28) — THE EXACT FENCE (owner: 'b sounds right - exact for edits, bash keeps subtree'; healing the drift the spot check caught): file-modification tools (edit, write) require the agent's location to BE the file's exact directory — resolved target.parent == cwd, symlink-safe, refusal names the remedy ('move to <dir> first'); bash keeps its subtree sandbox (builds write across the tree); reads stay free. D.2 103's subtree wording is amended for file tools; the founding words ('to work on a file the agent must move to that directory') are restored as literal law. The system prompt states the movement law sharply. Consequence embraced: every deliberate modification yields a movement event — richer presence trails, more f_loc signal, per-directory prompt refresh, finer parallel safety. M3EF minted; fires after M3DK (shared adapter surface). Version numbering continues 2.100+ — 3.0 stays reserved for the next editor-pass milestone.

**Version 2.99** (2026-08-28) — THE DISCOVERY LAW (owner: 'the manager/orchestrator (you) do discovery not the implementation agents. discovery is hard and I want to align it with you before complete a packet. This is a general rule.'): DISCOVERY — investigations, pattern research, dependency evaluations, mechanism checks, design comparisons — is the GATE'S work, never a builder's. The gate performs the discovery, ALIGNS THE FINDING WITH THE OWNER, and only then does the packet complete its fleshing; builders receive settled findings in the charge and build. Gate-run verification packets (the M3PV pattern) remain lawful; a build charge containing an 'investigate/evaluate/see how X does it' clause is a defect. Retroactive: M3DK's investigation clause is reclaimed — the gate performed the Claude Code proposed-response discovery and the charge now carries the settled pattern. GATE.md gains the section.

**Version 2.98** (2026-08-25) — THE DECK RULINGS + 0.1.6 (owner): (1) DRAFT REPLIES follow the pattern the owner likes in Claude Code — a PROPOSED USER RESPONSE, plausibly emitted as part of the model's own turn; the fleshing INVESTIGATES how Claude Code surfaces proposed responses and adopts the best of it (adopt-don't-invent applies to interaction patterns too); tweak-then-fire, journaled. (2) THE QUEUE: advance upon enter — firing a reply immediately fronts the longest-waiting card — AND the queue is freely scrollable (browsing never blocks the flow). (3) ATTUNEMENT confirmed as intended; re-emphasized as hard law: EVERY non-global module displays exactly what information it is showing (thread name, stack name — the honest badge is not optional chrome). (4) 'yes to 0.1.6' — the release sentence: spine's accumulated state (M3TI origin_thread_id schema + spelling alignment, M3MS share machinery) ships to the palace under the standing release ritual. SD-046 approved and fired as M3DK.

**Version 2.97** (2026-08-25) — THE ATTUNEMENT LAW (owner, ruling SD-007's design): module relation is a two-state law — GLOBAL works across all layers and modules; NOT-GLOBAL is ATTUNED BY PROXIMITY: the module attunes to the NEAREST context source (a thread module, or a stack), distance computed Pythagorean over grid coordinates with layer/tab separation contributing +1 unit per step of menu-bar sequence (Euclidean over (x, y, tabIndex) — exact formula confirmed at fleshing); ties pick RANDOM then stay sticky until the layout changes (journaled pick); a non-global module DISPLAYS its attunement honestly — the thread's name, or the stack's name when attuned to a stack; zero sources renders an honest 'unattuned' state. SUPERSEDES: v2.40's CURRENT follow-the-focus semantics and the gate's channel-picker proposal (X8) — placement is the binding; no picker UI. The Deck and stacks are SOURCES, not consumers.

**Version 2.96** (2026-08-24) — THE GATE'S CHARTER (owner: 'we need to make sure that this process for orchestrator management, you, is getting sufficiently codified'): the orchestrator's entire process — boot, owner boundaries, seed/mint/launch/handoff/scout/pen rituals, standing diets, known failure modes, succession — is codified in garden/GATE.md, the gate's own PLAN.md. Gate sessions are BOUND by it; succession law: everything binding lives in the repo, session memory is convenience never authority; a fresh orchestrator boots from GATE.md and loses nothing. GATE.md obeys the diets (~200 lines) and yields to SPEC on conflict.

**Version 2.95** (2026-08-24) — THE SWAP GO + THE SHARE FIRES (owner: 'I am fine if you've got all those details kicking that one off with the pydantic toolset, skills, etc use instead of pi'): (1) the owner gives the tool-layer swap its final go WITH EYES OPEN over the M3PV HOLD — the pydantic-ai core upgrade is accepted, the two named gaps (grep context lines; full skill-resource access) are closed by thin shims on OUR side of the seam, and the golden-behavior tripwire is the absolute zero-regression bar; PI's RPC process, Node runtime, 139MB vendoring, and init-time download retire when the swap lands green; skills arrive via the pydantic-ai-harness skills capability (the skills MODULE remains SD-033, unreviewed). (2) SD-002 the memory share fires as fleshed by D.2 133-135 (floor, triggers, pins, one force pattern, one console). M3MS + M3TS minted and fired in parallel — disjoint surfaces (spine/scorer/console vs harness toolset).

**Version 2.94** (2026-08-24) — THE ONE FORCE PATTERN + ONE CONSOLE (owner): (1) EVERY tunable parameter in the product exposes the identical manual-set contract — the owner sets it, the change lands as a versioned generation with provenance, and the algorithm resumes automating it immediately; a manual set is a starting point, never a lock; wherever a backtest exists, informed-force applies (simulate first, v2.44). No parameter anywhere may invent a different manual-override pattern. (2) ONE CONSOLE: the memory share and every force/manual-set input and its visualization live in the SAME module as the existing hyperparameters — the Injection Console (v2.42-2.44 layers) — never a second console; Context Bars keeps its honest display of the memory block against the share, but controls live only in the console (duplicate-function surfaces stay banned per the M2Z4 naming law).

**Version 2.93** (2026-08-24) — PIN OVERFLOW + FORCE SEMANTICS (owner, refining the share ruling): (1) PINNED memories always inject and MAY EXCEED the memory share — a pin is the owner's hand and is never budget-cut; persistent pinned overflow is logged and counts as replay evidence toward a larger share ('that seems like it would be the process to increase the context window'); overflow renders honestly in Context Bars. (2) Manual removals of lower-scored injected memories are exactly the signal that RAISES the line via the replay — reaffirmed. (3) ONE LOOP: share, τ, and feature weights optimize together inside the ordinary retrain — no standalone tuners (v2.58 reaffirmed; 'all of this needs to work with general hyperparameter optimization'). (4) SHARE FORCE follows informed-force semantics (v2.44): the owner may force the share to a value after a deep-simulate score; the force lands as a versioned generation; the algorithm resumes proposing from there immediately — a force is a starting point, never a lock.

**Version 2.92** (2026-08-24) — THE SHARE RULING (owner, approving SD-002 with conditions): the memory LINE (τ) and the memory ROOM (the context-window share) become dynamically trainable off human feedback — BUILT NOW, HELD FIXED until 100 authentic human feedback signals exist (the same authenticity floor discipline as retrain: owner signals only, verification hygiene-excluded), then the share/τ dimensions AUTO-JOIN the ordinary retrain loop. Gate's interpretation, flagged for the owner: 'auto turn on' admits the dimensions into training at the floor; each resulting generation still rides the existing PROPOSED → owner-tap activation flow (one consent surface, unchanged) — correct me if full-auto activation was meant. TRIGGER SEMANTICS (D.2 133): the ROOM grows only on evidence that above-the-line memories were cut purely for space and the replay shows keeping them would have matched the owner's recorded dispositions; it shrinks when marginal occupants go uncited/removed; it is a CEILING — unused room always returns to the conversation; bounded min/max. The LINE moves only through the replay: the generation that best agrees with recorded add-backs and removals wins; cheaper at tie (smaller share, higher τ) per standing law. Every change is a versioned generation with visible provenance.

**Version 2.91** (2026-08-24) — THE TOOL LAYER RULING (owner: 'I'm aligned with your recommendations', closing SD-047): (1) the LOOP stays pydantic-ai — ADR-001 and Decision 079 reaffirmed; the memory gate, journal, and receipts live in our loop forever; (2) the BROKER stays ours — policies, receipts, KV-sticky, OFF mode; no provider layer displaces it; (3) the TOOL LAYER swaps to pydantic-ai's official harness capabilities wrapped by the location fence IN-PROCESS — retiring the PI RPC process, the Node runtime, the 139MB vendoring, and the init-time release download — CONDITIONAL on a verification session proving capability coverage (edit semantics, grep behavior, bash discipline, skills); the golden tripwire suite is the zero-regression bar for the swap, exactly its founding purpose. M3PV minted (the verification); the swap packet fleshes only on M3PV's evidence + the owner's final go.

**Version 2.90** (2026-08-24) — THE OWNER'S REVIEW (owner, after reviewing the wave card: 'let's reset every packet to incomplete and I want to manually review every one before approval to add to a wave and the implementation plan'): every seed is INCOMPLETE until the owner has personally reviewed it — individually, not wave-level; the gate's staging (Card 7 / the wave card) is PROPOSAL ONLY; a packet enters a wave and the implementation plan only through the owner's per-packet approval, then the recipe-card blessing fires the wave as before. Riding rulings: spend-rate + role model-policies PUSHED BACK for deeper fleshing (owner: 'should be pushed back and fleshed out more'); SD-047 minted (the PI boundary question).

**Version 2.89** (2026-08-24) — THE FOUR RULINGS (owner, closing the coherence pass): (1) TRANSPARENT FEATURES, FREE OPTIMIZER — the transparency law's scope is fixed at the feature layer: every feature stays a named, understandable scalar with visible per-injection provenance, while the optimization loop that SETS the weights may be black-box ('as long as the features we are optimizing the scalars for are understandable'); SD-029 proceeds under this constraint. (2) C1 RESOLVED: USE THREE.JS — ADR-008's resolved stack stands; harness Decision 094 (PlayCanvas) is overruled by the owner; the engine law (v2.83) is satisfied by Three.js per the owner's word; the Palace Nebula re-renders on three.js + r3f + TSL as Wave 5's first act, PlayCanvas exiting at the swap; M3GE's measurements remain honest record. (3) SD-035 ensemble rung TOMBSTONED ('keep symphony') — no-mode-switch law untouched. (4) THE ITERATIVE BUILD TEST IS THE NORTH STAR (owner delegated the pick; the gate picks the measurable one) — SD-012 Ouroboros tombstoned as lineage, recoverable.

**Version 2.88** (2026-08-24) — THE PLANNER'S HARNESS (owner: 'How is this going to be enforced on you or other planner/riffing managers going forward?'): (1) seed-first-respond-second binds EVERY planning/riffing session, gate or otherwise; (2) builders REFUSE a feature packet whose ANCESTRY names neither a seed (SD-###) nor a direct owner sentence — F050 refusal class extended; (3) every planning handoff ends with a SEEDS MINTED line (ids or 'none'); (4) THE STANDING DIETS — dead text never rides boot: at every version bump this header keeps ONE entry (the prior entry moves to D.3 in the same commit); board rows move to notes/board-archive.md at DONE; resolved flags move to notes/flags-resolved-archive.md at resolution stamp. Enforced by the laws' own refusal machinery plus the gate audit.

Prior v2.86 (2026-08-24) — THE SIMPLE SEARCH (owner, descoping v2.84 clause 3: 'It doesn't need to be that complex... just look through old records for keywords LOL. I just want to add that to standard spec creation which you do.'): the precedent ritual is a DETERMINISTIC KEYWORD SEARCH over the record plus the gate's standing habit — nothing more; v2.84's ADOPT-THE-PRODUCT clause (garden ingested into a palace principal as a semantic second stage) is STRUCK from the law and moved to the dormant ledger as an optional future dogfood; M3PR descoped to bin/precedent + the notes standing-items index; M3AR's exit drops the re-ingest stage (D.2 127). Prior v2.85 (2026-08-21) — THE ANCESTRY CLAUSE + THE ARCHIVE RETROFIT (owner: 'a small process change for creating packets that mandates looking for old packets to be the permanent fix and… a one time update where we go through all the old packets and update and integrate them into our modern formats. All those original ideas should be motivated and are extremely central and important.'): (1) ANCESTRY — the PRECEDENT field (v2.84) must name ANCESTOR PACKETS BY ID: every mint searches the plan archive, reports/, and board history for prior packets on the same surface or idea and cites them (or 'no ancestors; searched: <terms>'); a new packet on old ground inherits its ancestors' still-binding constraints or says why not. (2) RETROFIT — one-time sweep (M3AR): every pre-template packet charge in the archive and the pre-archive era is retrofitted into the modern template (MOTIVATION with its P-node; RECIPE as-built; AUTHORITY as-was; ANCESTRY links), so the founding ideas are motivated, indexed, palace-ingested, and findable; ideas that were never built surface in a DORMANT IDEAS list for the owner — original ideas are central, not disposable (D.2 126). Prior v2.84 (2026-08-21) — THE PRECEDENT LAW (owner: 'How are we missing these old specs over and over I want to understand the issue and create an institutional fix maybe better search logic?'; diagnosis: the record is ~30k lines in seven places, boot reads ~10%, and 16 notes hold design decisions that bind nobody and trigger no search): (1) PRECEDENT is a required field of every packet and every amendment — the output of a deterministic search of the WHOLE record (SPEC incl. ADRs and D.2; AMENDMENTS + archive; FLAGS; GLOSSARY; notes/; reports/; both DECISIONS.md) for the surface's key terms, citing what the record already says or 'none found; searched: <terms>'; agents refuse PRECEDENT-less packets exactly as they refuse template-less ones (F050 class); the gate is bound. (2) NOTES PROPOSE, SPEC DECIDES — a design decision recorded only in notes/ does not bind until promoted to an ADR clause or D.2 row; every note carries a standing-items header (proposed / enacted→where / superseded→by what); notes/INDEX.md lists them and joins the boot reading. (3) ADOPT THE PRODUCT — the garden record is ingested into a dedicated palace principal as the semantic layer over the deterministic floor; the precedent tool queries grep first, palace second; the relay becomes the product's first long-running customer. (4) Retroactive: the Cube note's v6 stack decision (three.js + react-three-fiber + TSL, 2026-07-20) was an orphan — now a pending ADR-008 amendment awaiting the owner's re-measure ruling; v2.80 should have cited D.2 081 (fixture banners were already law). M3PR minted (D.2 125). Prior v2.83 (2026-08-18) — THE ENGINE LAW + THE FUNCTION LAW (owner, resolving the ADR-018/R10 tension the editor pass flagged: 'I want it rendered via video game engine not some sub par graphical creation. Also, to be super clear we are not to ever make visualizations that aren't based on real data transforms information. Great visualizations are as much function as form.'): (1) ENGINE — the 3D module family (the six liquid palace modules, the Athanor, the Cauldron, the device chain) is rendered by a real video game engine adopted dependency-first (R14), never by hand-rolled graphics; the engine runs inside the module sandbox (ADR-018 cl.7 / ADR-023) on the hardware tiers (R10); ADR-018's old rejection of 'always-3D rendering' is SUPERSEDED — R10 governs. (2) FUNCTION — no visualization may exist that is not a transform of real data: every visual variable (position, color, shape, motion, density) binds to a real measured quantity with its provenance; decorative-only visuals are forbidden on any data surface (the conjuration law D.2 116 already confines decoration to empty states and backgrounds); a visualization whose bindings cannot be named is a defect, not a style. M3GE minted: the engine pick + a real-data spike (D.2 124). Prior v2.82 (2026-08-18) — EDITOR PASS IV (content-preserving consolidation, mirroring v2.0/v2.12/v2.23; D.2 123): enacted amendments A-020..A-048 were folded verbatim into the sections they amend (the token-cost policy into C.5; the gate, panel and re-scoring contracts into C.4/C.6/C.7; the M2 surfaces into ADR-005/009/019/021/023/024; fixture and test-motivation law into B.6, with rule 12's body restored from D.4), the D.2 112–114 numbering collision was resolved by suffixing the later-written rows 112b/113b/114b (row 122), ADR-017 and ADR-018 were reconciled with the Symphony enactment, R10 and R21 by appended Reconciliation subsections, and §2's P-nodes gained component pointers from the registry. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

Prior v2.81 (2026-08-18) — THE SCOUT'S WORD (owner, after the F056 mechanism review: 'a single scouts report should not be taken as gospel out of context unless it is operating a specific test we've aligned on'): B.6 rule 13 — a scout's report is evidence, not verdict; items are ALIGNED (an owner/gate-aligned test — verdict is a finding) or DERIVED (scout-designed under rule 9 — verdict is a CLAIM until the gate mechanism-checks it against code or the owner aligns the test; claims reach the owner in neutral words with no severity; core-law claims never reach the owner before their mechanism check). B.6 rule 12 (test motivation, D.2 088) gains its missing body stub. F056 corrected on the record: a Rack project-control binding defect, not a memory-law defect — the injection fence makes cross-project injection impossible and the scorer has no thread feature (D.2 121). Prior v2.80 (2026-08-18) — THE FIXTURE CURTAIN (owner, at an unexpected window: 'I just saw another "regression" fixture which wasn't the real app opening I thought that we'd aligned to not make those'): the D.2 111 real-render alignment becomes statute for ALL machine verification, not just design pitches — (1) NO FIXTURE EVER OPENS ON THE OWNER'S SCREEN: verification browser runs are headless, always; scenario/fixture servers bind only for the check's own lifetime and never auto-open a window; (2) any fixture-served page MUST wear an unmistakable in-page FIXTURE banner (name + packet id) so a stray render can never be mistaken for the product; (3) the only window that ever opens unbidden on the owner's machine is the real app on real data via its own startup; verification invocations of the product always pass --no-open; (4) suite-asserted where testable. Flags F052 (the sighting) + F053 (M3R's out-of-surface fixture repair, content retained, class named). M3FX minted; M3S scout checklist inherits the curtain (D.2 120). Prior v2.79 (2026-08-17) — GRAMMAR, NOT GLYPHS (owner: 'can we not copy them and just change them enough… create our own scripts? That way we have something cool on our own'): counsel recorded — there is no percent-different safe harbor; a script derived from reference letterforms stays derivative; the lawful and better path COPIES THE GRAMMAR (featural anatomy, pen logic, construction rules — free ideas) and constructs original alphabets. THE ELVISH HAND v2.1: a STABLE 24-glyph featural alphabet (stem kinds × gapped lobes × crossbars × tehtar) with calligraphic PEN PRESSURE (thick downstrokes / thin curves) and frequency-weighted sequencing so strips read as real writing; THE MACHINE HAND stays the owner-blessed v1 unchanged; collision rule enforced — rings clear all reading surfaces; kit re-frozen; M2UX6 FINAL (D.2 119). Prior v2.78 (2026-08-17) — THE SIGIL FORGE (owner named the hands: 'This tolkein should be the wizard… The transformer should be the technomancer', supplying Tengwar and Cybertronian charts): verbatim franchise letterforms refused (Tolkien-estate and Hasbro-associated designs; Nocturne distributes publicly; D.2 117's no-franchise law holds) — instead the scripts are FORGED: original glyphs generated procedurally, the procedural law extended to script itself; THE ELVISH HAND (curved stems, bows, loops, descender hooks, floating tehta-style marks, fine round stroke) writes for WIZARD MODE and THE MACHINE HAND (heavy uniform strokes, 45° chamfers, notched stubs, square caps on a 3×4 grid) writes for TECHNOMANCER; seeded deterministic, emitted as SVG data-URI strips in motif CSS (materials, never code); verified live on the owner's platform; kit re-frozen with sigil_forge.py; M2UX6 briefs FINAL (D.2 118). Prior v2.77 (2026-08-17) — THE ELDER SCRIPTS (owner, at the cranked samples: 'the ones that are appearing here are a bit normal'): the grimoire glyph sets swap from familiar symbols to genuinely alien scripts — WIZARD MODE speaks Elder Futhark + Ogham + Glagolitic with rare alchemical accents; TECHNOMANCER speaks an angular machine-script of Carian + Old Turkic + Tifinagh (the transformer-alphabet feel evoked from ancient scripts — no franchise alphabet is ever copied); motif glyphs are literal UTF-8 in the stylesheets, never CSS escapes (kills the mangling class); kit re-frozen at notes/audition-grimoire/; briefs remain FINAL (D.2 117). Prior v2.76 (2026-08-17) — THE RUNE CRANK: GRIMOIRE SAMPLES BLESSED (owner, live samples viewed: 'I like it — crank up the runes and symbols a bit more in different locations maybe 50% more. then finalize it'): rune/sigil density +50% with enumerated locations (module heads, module margins, two empty-state script axes, message margins, composer edge, topbar sigils; technomancer adds a second rain column, module hexlines, composer circuit row); the demo kit — generated from the PRODUCTION stylesheets through the real seam — is frozen at notes/audition-grimoire/ as the reference implementation; M2UX6 briefs are FINAL, audition rounds waived (owner may reopen); demo loops become state-change conjurations in product per the conjuration law (D.2 116). Prior v2.75 (2026-08-17) — THE GRIMOIRE THEMES + THE CONJURATION LAW (owner: wizard mode 'runes and scripts and appearing and disappearing', technomancer 'similar but more neon and cyber punk'): two authored built-in themes join the switcher (no reference plate — the press law is untriggered; per-theme validation is universal); THE CONJURATION LAW governs animated motifs in any theme: motion is decorative only — NEVER on data channels or reading surfaces; conjurations fire on state changes and settle; ambient loops only in empty states/backgrounds at whisper opacity; prefers-reduced-motion yields static runes (suite-asserted); glyph layers are Unicode content in motif CSS — tokens/materials/keyframes, never code (ADR-018 cl.7); M2UX6 minted deps M2UX4; D.2 numbering collision 112–114 flagged to the owed editor pass (D.2 115). Prior v2.74 (2026-08-16) — THE SYMPHONY ENACTMENT (owner blessing: 'fire away'): planning rulings R1-R23, the acceptance bar, the primitive alignment charter, the four-wave plan, and the gap rulings become law (D.2 114b — written as 'D.2 next row' at the time, suffixed v2.82); the full ADR-017/018 textual reconciliation is owed gate editor work. Prior v2.73 (2026-08-13) — THE BOOT DIET (owner: consolidate the total reading): the version-lineage paragraph moves to Appendix D.3 (it duplicated D.2); PLAN charges of completed packets move to notes/plan-archive-completed.md (content preserved; PLAN 1497→403 lines). Motivation: every session's boot cost is round-trips over dead text; the law a runner must read is the law that still binds. Full version lineage: D.2 (rows) and D.3 (narrative).

## D.4 Parked ideas (not scope; preserved thinking)

- **Plan-mode compaction** (original proposal sheet): on compaction, save
  context → strongest available model summarizes → summarizer asks the user
  1–4 clarifying questions before the thread continues. Interesting,
  unproven; revisit when compaction (M3) has real usage. Implementation
  chassis when it comes: the ADR-013 seam over pydantic-ai ProcessHistory
  (+ harness summarization batteries).
- **Memory Graph lasso/bulk actions:** revisit with M3 maintenance workflows.
- **Gate near-miss count/e-greedy exploration:** deliberately not designed;
  the near-miss mechanism already provides mild exploration.
- **Session-affinity feature (f_sess):** present in the founding memory-index
  list; excised 2026-07-07 as a conscious divergence (near-always zero at the
  only moment scoring occurs). `thread_origin` metadata is preserved on every
  unit. Resurrection condition: M2 logs show resumed-thread patterns where
  session affinity would have changed rankings.
  *(B.6 rule 12's body, which v2.48 had appended to this bullet, was moved
  to its rule in editor pass IV — A-040, v2.82. No normative test law lives
  in D.4.)*




## D.3 — Version lineage narrative (moved from the header, 2026-08-13)

F037 RESOLVED + THE EVIDENCE CAPTURE LAW + REPO VISIBILITY DOCTRINE: the garden is private (build environment); the product repos are public by design (NOCTURNE is for people); committed evidence comes ONLY from fixture/verification identities — owner-rack captures are never committed; the four historical screenshots are owner-accepted as benign (D.2 117-era ruling; the row itself is D.2 113b after the v2.82 suffix). Prior v2.71 (2026-08-11) — THE LIFECYCLE MATRIX (owner: 'this CI/CD pipeline needs to be uber robust — any time we are working on it something breaks'): the update/deploy/launch decision table is enumerated and table-tested — every state combination has a defined voice and action; every lifecycle incident becomes a matrix row before its fix closes; ONE MIND law: the prompt never offers what the guard refuses (D.2 112b — suffixed v2.82). Prior v2.70 (2026-08-11) — THE PLATE PRESS (owner: 'a feature where we could feed in a picture and the color palette is extracted and makes a new colorway for the harness… we can apply the iteration to the rules'): the manual audition loop's learnings become an automated product feature — any image pressed through the ratified derivation pipeline (D.2 107–110 as executable code: extraction, accent passes, percentile chrome, role assignment by the ratio law, contrast repair, per-theme validation) yields a named COLORWAY that joins the theme switcher; colorways are DATA (tokens), never code (ADR-018 cl.7 sandbox law) — user-authorable theming realized; validation fail-closed with plain remedy; M2UX5 minted deps M2UX4 (D.2 114). Prior v2.69 (2026-08-11) — THE THREE-THEME RULING (owner close: 'leave the current color scheme in place and add these two as alternatives'): NEO-NOIR remains the worn default — no pending worn-skin audition; the two faces ship as named ALTERNATES in an ordinary theme switcher (SERAPH DRESSED night, GOLD LINES day), selectable on the real rack, choice persistent; M2UX4's exit is three selectable themes + the seam sweep, not a replacement ruling (D.2 113). Prior v2.68 (2026-08-11) — THE AUDITION LOOP + THE TWO FACES (owner protocol: 'generate 4 things, physically look at them, then I tell you which I like — several iteration loops'): five rounds run same-day on real-stylesheet candidates; rulings extracted from picks — red is danger-only, both metals (gold chips + silver rims), VARIANCE LAW (mutation radius stays wide until the owner narrows; round-3 convergence rebuked); winners frozen as M2UX4's starting candidates: NIGHT = SERAPH DRESSED (blend palette, gold+silver, two-sky wash, amber horizon, chrome rims) and DAY = GOLD LINES (cobalt-on-cloud, gilded linework) — the plate's own two panels are the two faces; the seam-leak sweep becomes packet duty (268 literals + #dad8d2/#c9dce4 + rgba glass discovered; every color literal migrates into the token seam, computed-style checks at every breakpoint); real-rack audition still final per D.2 111 (D.2 112). Prior v2.67 (2026-08-11) — THE AUDITION CLAUSE (owner taste signal: 'i like the current one better than what you are pitching'): gate sketches are NOT judging grounds — B.6 r7 extended to taste: theme rulings happen on the REAL rack only; D.2 107(4) softened — M2UX4 builds cobalt-seraph as a complete SWITCHABLE token set beside NEO-NOIR, which REMAINS WORN until the owner rules; exit gains a side-by-side audition (same views, both themes, live switch + screenshots); the blend path is legal — the derivation may keep the current skin's electric-ink strategy with the electricity re-sourced from the plate (iris #2b58ba, specular white) (D.2 111). Prior v2.66 (2026-08-11) — THE RIM LAW (owner: 'that kind of chrome for the borders not buttons — that isn't aesthetic'): chrome is an EDGE material — module borders, frames, focus rings, resize edges, separators — the D.2 109 banded ramp runs ALONG the stroke (1-2px rim; the blaze widens slightly on hover/focus; the viewport-fixed reflection slides on the rim when a module moves); chrome NEVER fills a button, header, chip, or slab; fills stay dark cel with silver/white linework text; TEXT NEVER SITS ON CHROME; the seraph wears chrome as rim light on a dark body — the bright fill was always the sky behind it; part→material map corrected in place (D.2 110). Prior v2.65 (2026-08-11) — THE SHINE FLOOR (owner: 'the chrome needs to be liquid and shiny and white — all of this feels muted and matte'): mean-sampling is BANNED for chrome — chrome never looks like its mean (the seraph is 73% near-black yet reads white); chrome tokens are luminance-PERCENTILE ramps with banded stops (measured: #090807/#100f15 dark mass → thin #302529 transition → #888d9b/#bfcbd5/#dbe5ee/#eff8fa blaze); THE MISSING MIDDLE is the shine — a chrome part's rendered luminance histogram must be bimodal (dark seams + white blaze, thin midtones; mid-centered variance = matte = defect, asserted in the emulation check); the white end reaches L≥0.97 icy-cool and sits adjacent to near-black seams (adjacency makes the blaze); the worn chrome base flips from steel #7f879a to the bright-silver class (D.2 109). Prior v2.64 (2026-08-11) — THE SERAPH DIRECTIVE (owner second look: 'making the UI look most like the figure in the bottom of the image — liquid and sharp and different'; 'the same sense of humanity, alienness, surrealness, beauty, technology, majesty, scale, sky'): the extractor gains the CHROMA-OUTLIER and SPECULAR passes (gold #ad8659→#db9969 at 0.061%, iris #2b58ba, coral peak #cd352b, shine as a base→peak PAIR #7f879a→#fffffd; glint census 119); LIQUID CHROME — all chrome parts share one viewport-fixed two-sky gradient so reflections slide when modules move; SHARP — cel edges, no blur/drop-shadow on interactive surfaces, controls silent until approached; THE TWO SKIES light model (night above, day cobalt below, amber horizon at the Vitals strip); ONTOLOGY MAPPING (warm=human, chrome=machine, cobalt=world, white=meaning, gold=the earned ≤0.1%; chrome-on-warm marks machine-touches-human); THE WING RULE (one hero gesture per view; dark share is the majesty); THE GLINT BUDGET (specular ≤ plate's 1.2%); ATMOSPHERIC DEPTH (recede by haze, never shadow); part→material→token map generated by the extractor and asserted by M2UX3's conformance test; M2UX4 retrofitted (D.2 108). Prior v2.63 (2026-08-11) — THE REFERENCE PLATE (owner: 'emulate this picture as absolutely as possible… pulling exact color samples out and using those in the same ratios'): COBALT-SERAPH gains a committed canonical reference plate; theme tokens with AREA SHARES are DERIVED by a deterministic extractor, never authored (procedural law extended to aesthetics: same plate → same tokens, regeneration is a byte-identical no-op); the RATIO LAW maps token shares to UI area budget; the EMULATION CHECK bounds screenshot-vs-plate palette-area distance; the owner rack wears the plate-derived theme, NEO-NOIR preserved as a named token set; M2UX4 minted serial after the UX lane (D.2 107). Prior v2.62 (2026-08-10) — THE PACKET TEMPLATE (owner: 'I never want to be afraid to fire off a volley of parallelizable agents'): every packet minted from now carries MOTIVATION (problem-tree node + the why), RECIPE (deps, touched surfaces at file/subsystem level, exclusions, parallel verdict), and AUTHORITY (embedded grants) at mint time — recipe cards are generated from packets (D.2 106). Prior v2.61 (2026-08-10) — M2ZA COMPLETION GRANT: single-use 0.1.2 rollout (ships migration 0012 + annotation API + canonical deploy identity), then the data-plane annotation and honest-scoreboard verification close the packet (D.2 105). Prior v2.60 (2026-08-09) — THE LOCATION FENCE + RECIPE-CARD PRACTICE (owner): deterministic tool-layer write enforcement — an agent WRITES only within its current location's subtree; movement is an explicit logged act; the fence is the exclusion-set law made PHYSICAL (disjoint locations = provably conflict-free parallelism) and the Ant Farm is the fence made visible; 'ratatouille criteria' named; manual approximation effective now: the gate authors a recipe card per wave, the owner fires parallel sessions into open cells under the D.2 065 exclusion machinery (D.2 103). Prior v2.59: SYMPHONY'S PLANNING ARCHITECTURE PINNED (owner): the THREE-JUDGE MINIMUM (motivation, implementation, PERFORMANCE — the third judging against PRECALCULATED METRICS agreed at plan time, persisted in a cross-symphony ledger so they improve over time) + THE RECIPE CARD: the plan artifact is a staged dependency table (rows=work items, prep stages parallel per-row, MERGE CELLS as join barriers, per-stage DONENESS criteria — observable states, never timers), judges evaluate at merge points, the conductor view renders the card live (D.2 102). Prior v2.58: THE MEMORY SHARE + COMPACTION MEMORY DUTY (owner design session): the injection token budget becomes n% of the active model's context window (visible, editable, Console-governed), auto-tuned INSIDE the Chrysopoeia loop via the budget-cut band counterfactual — never a standalone tuner; at compaction the summarizing agent carries three-way triage memory duty through the ordinary C.6 channel (D.2 101). Prior v2.57: THE PRE-GATE RULINGS (owner working session over report 077): BACKGROUND RETRAIN law (retrain is a background act on work-based triggers; one manual 'Force retrain' lever; the Console's FORCE-values stays a distinct instrument — no duplicate buttons); THE LONG PROBLEM measured in COMPACTIONS ('compaction is the context-window entropy the memory palace is fighting'); PACKET-EMBEDDED AUTHORITY practice; provisional-taste grant (expires at M2X clear); release grant for 0.1.1 with shipped-source-only provenance; M2Z1-M2Z5 minted (D.2 100). Prior v2.56: THE RELEASE-LIFECYCLE REVIEW (owner directive: every future release must be one or two commands and menus): NO-DOWNGRADES law (reverse skew names the app as the stale side and never offers a downgrade); DOCTOR-PARITY law (doctor probes every dependency `up` needs — 'doctor healthy, up refuses' is itself a defect); VERSION-BUMP GUARD (deploy refuses tag-exists-but-source-differs with the bump remedy); M2W minted; D3 charge gains the release ritual + `nocturne update` (D.2 099). Prior v2.55: F018/F019 closed out: THE INFRASTRUCTURE/DATA-PLANE SCOPE LAW (grants govern infrastructure mutations only; application-data-plane operations are ordinary product operation, never grant-gated) + MUTATION-LAST ORDERING (locally-fallible stages precede the first service-affecting mutation); F018's direct owner grant recorded; M2 palace deploy gate-verified live (D.2 098). Prior v2.54: F017 resolved: fresh grant for the proven `nocturne up → y` path + RECEIPT MARKS CONSUMPTION recalibration: pre-receipt failures leave grants intact for in-session retry (D.2 097). Prior v2.53: F016 resolved: fresh grant, corrected reset, one-receipt-per-attempt bright line (D.2 096). Prior v2.52: plain language calibrated by audience: standard ops vocabulary stays, invented jargon and remedy-free refusals banned (D.2 095). Prior v2.51: credential alignment grant (reset, single-use) + plain plan language (D.2 094). Prior v2.50: the update prompt + plain secret names (D.2 093). Prior v2.49: F015 resolved: informed restore — side-by-side + the rollback manifest (D.2 092). Prior v2.48: the garden never shows; Codex-extension browser default; adopt-an-OSS-toolset doctrine (D.2 091). Prior v2.47: identity ratified: NOCTURNE stays; the mark is the ouroboros around the moon (D.2 090). Prior v2.46: core-first check: M2R Context Bars minted (dropped-ball recovery), movement coupling pinned, board reordered core-first (D.2 089). Prior v2.45: TEST MOTIVATION LAW (B.6 r12): every test cites its statute; M2Q sweep + law-coverage report minted (D.2 088). Prior v2.44: informed force: simulate-before-force, score bound to exact values (D.2 087). Prior v2.43: Injection Console completed: score graph, SIMULATE (instant+deep), FORCE as exploration lever, default-compact, the Landscape colocated with curators at M3 (D.2 086). Prior v2.42: THE INJECTION CONSOLE: four-layer scoring module (read/control/what-if/audition); M2P minted (D.2 085). Prior v2.41: THE ACCURACY SCORE: memory performance as a per-generation Vitals time series (D.2 084). Prior v2.40: module scope law: GLOBAL|CURRENT toggles on every rack module, selection-bus coherence (D.2 083). Prior v2.39: THE JOURNAL LAW: history mandatory, fail-closed ratified, disk-threshold Deck card; reconcilable-vs-unrecoverable doctrine sealed (D.2 082). Prior v2.38: fixture isolation, accounting fails open (B.6 r11), image input committed; M2O minted (D.2 081). Prior v2.37: real-calls doctrine (B.6 r10); multimodality named future surface; owner rack review captured (D.2 080). Prior v2.36: THE CAPABILITY LADDER: one product, rungs never forks; the mirror principle; blue-water's first customer named (D.2 079). Prior v2.35: packaging review: ADR-019 v0.1 positioning (local = public path, deploy = owner-only), D3 evidence erratum, M2N lifecycle hardening minted (D.2 078). Prior v2.34: wave-2 clarity pass + mint: learner authority, extraction trigger, reconciliation scope, the missing re-scoring packet (D.2 077). Prior v2.33: live thread parameters: dispositions are binary locks both directions; f_sess resurrected into the replay pipeline (D.2 076). Prior v2.32: THE SCOREBOARD: binary override-rate replay acceptance; agenda item 1 closed (D.2 075). Prior v2.31: first-principles level-set: selection law = τ+budget (k is config), OQ-15 RESOLVED (first-gate once, then autonomous per-message re-scoring) (D.2 074). Prior v2.30: install name: PyPI `nocturne-ai`, command `nocturne` (D.2 073). Prior v2.29: M2 WAVE 1 opened pre-planning: M2A-M2E + M1C minted; scoreboard-dependent work waits for planning proper (D.2 072). Prior v2.28: M1 JUDGED PASS; ADR statuses normalized per the superseding verdict (D.2 071). Prior v2.27: motivation-in-place for /model resolution points (D.2 070). Prior v2.26: F012 RESOLVED: model RESOLUTION POINTS (never drift), /model command, J1 rewritten lawful; J re-opens for a Claude Code judge (D.2 069). Prior v2.25: UNIFIED QUEUE LAW: birthplace routing, deck flashcards with passive approval on visibility, Palace queue module for corpus-born proposals (D.2 068). Prior v2.24: THE GLOSSARY: garden/GLOSSARY.md, read at boot STEP 1; coinage duty on the human gate, proposal duty on agents (D.2 067). Prior v2.23: EDITOR PASS III: ADR-023 Plugin System extracted, ADR-024 Cost Domain consolidated, P2.5 + P4.1/P4.2 (D.2 066). Prior v2.22: PACKET EXCLUSION LAW: symmetric conflict sets on the board; concurrent relay sessions become legal + safe, dormant until used (D.2 065). Prior v2.21: RELAY UNBLOCK: F011 deploy grant (single-use, d41b286); hold rescoped to gate J only; sprint H5→I1 queued (D.2 064). Prior v2.20: contributor skill sharpened: self-contained + motivation-first + data-catalog heart, both plugin classes equal (D.2 063). Prior v2.19: plugin refinements: local freedom/documented edges (general project rule), resize law (grid units), contributor skill (D.2 062). Prior v2.18: PLUGINS ALL THE WAY DOWN: ADR-018 cl.8-10 — ground-up composition, control plugins + parameter registry, authoring ease + reference plugins (D.2 061). Prior v2.17: SPEND DASHBOARD: Vitals as Ableton-style bottom strip, dollar-true lanes (D.2 060). Prior v2.16: H5 scout consult: near-miss "never" veto (resolves F007 deadlock); no silent scope broadening (F008) (D.2 059). Prior v2.15: B.6 rule 9 SCOUT-BEFORE-OWNER (relay self-routes to scout at human gates) + ADR-022 split law (semantic, lineaged, never summarized) (D.2 058). Prior v2.14: flow-walkthrough closures: reinforcement in every mode, candidate invisibility, rejection-as-signal, anti-nag law, pin overflow (D.2 057). Prior v2.13: OQ-4 RESOLVED (curator autonomy earned per verdict class); injection-pressure trigger defined (was "80%-budget"); status normalization → J's charge (D.2 056). Prior v2.12: EDITOR PASS II (content-preserving): ADR-022 THE CURATORS extracted from ADR-004 accretions; problem tree gains P1.5/P1.6/P2.4; memory lifecycle map (D.2 055). Prior v2.11: PALACE VITALS gauges (lifecycle rates/hr, token spend by category/hr, counters) + ADR-021 lifecycle closures C1-C6 (D.2 054). Prior v2.10: ADR-021 MEMORY WRITE LAW: admit-then-curate, fire-and-forget saves, reinforcement coalescing, 10% attention budget (principle), Symphony staging + judge promotion, origin_agent lineage id (D.2 053). Prior v2.9: CURATOR ARCHITECTURE: deterministic diagnostics → Palace Health Report → LLM verdicts → deterministic write tools (D.2 052). Prior v2.8: CURATOR DOCTRINE: write-count trigger, palace-anchored, surgeons (root-cause, minimal intervention), slop removal, versioned curation SOPs (D.2 051). Prior v2.7: gate-day data + v0.2 proposal adoptions: hybrid candidate retrieval, keywords mandate, training-data hygiene, curator consolidation taxonomy + typed edges, promotion blend, candidate status (D.2 050). Prior v2.6: OQ-17 resolved (roots alpha = selection focus); ADR-019 seed ingestion; nocturne-* remotes + splash repo (D.2 049). Prior v2.5: B.6 rule 8 agent walkthroughs (D.2 048). Prior v2.4: NOCTURNE christened; ADR-019 onboarding; ADR-020 shared Palaces (D.2 046–047). Prior v2.3: broker-routed embeddings (D.2 044). Prior v2.2: themes (D.2 043). Prior v2.1: procedural law (D.2 042). Prior v2.0: EDITOR PASS: content-preserving consolidation of the v1.5–v1.15 organic growth. New/amended law: ADR-012 mode scale, ADR-015 walls, ADR-016 tree, ADR-017 Symphony, ADR-018 Cube+plugins+stack, ADR-007→index, ADR-008 stack resolved; enacted amendments A-001–A-017 folded into Part C (AMENDMENTS.md remains the historical record); D.1 refreshed. Full version lineage: Appendix D.2. Prior v1.4 (2026-07-07) was reorganized from the v0.x iteration transcript;

**v2.107 (2026-09-01) narrative, folded from the header:** EVERY FEATURE GETS A WALK (owner: 'every feature pack from now on should have at least a minor walk'): before a feature packet is DONE, the builder opens the real packaged app in a real browser, uses the new feature the way a person would, and attaches a screenshot of it working, named by the feature, in the handoff report. No screenshot of it working, not done. The heartbeat (v2.106) covers the core loop on every relevant packet; the walk covers the feature itself. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

**v2.108 (2026-09-02) narrative, folded from the header:** THE AXIOMS (owner: 'These are some axioms I think are important where should we put them?'): the team's twelve working axioms (absolute honesty; good faith; plain explanation; evidence over assumption; do only what is asked; more code is entropy; surgical edits; documentation is sacred, distilled, proposed, and records problem-solving only; step by step; narrow commits; no dead branches; prefer SQL; read the docs in your path) live in garden/AXIOMS.md, read at boot STEP 0, and are frozen verbatim into every ground-rules file because those are the only files an agent is mechanically handed. Two rulings: pushing remote main is allowed in this project; documentation covers problem-solving concisely, never the easily inferable. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

**v2.109 (2026-09-08) narrative, folded from the header:** THE CORE LOOP IS THE GATE'S (owner: 'I don't want to have to keep approving these one offs'): core-loop breakage (prompt → answer → send again, tools, gate, remember) is fleshed by the gate without an approval round; the owner fires every packet as before (the packet paradigm stands — corrected the same day: 'I want to keep the same packet implementation paradigm'). Standing order in every such fixer: when the break is in our own scaffolding around pydantic-ai, delete the scaffolding in favor of the framework's path; never patch the guard. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

**v2.110 (2026-09-08) narrative, folded from the header:** A GUARD CITES ITS INCIDENT (owner: 'It looks like we have a problem overbuilding guards'; the gate's census: 391 hand-rolled raises in the harness runtime, 2 exercised by any test): every raise, gate, limit or reconciliation check in the core loop names, in one line beside it, the real failure it prevents — a recorded incident, a flag, or a WALL (credentials, money, the owner's attention, writes to the palace or the owner's files). No incident, no guard: delete it and let the framework's own error surface. Kept guards get a test that quotes their message. 'Fail-closed' applies at walls only. B.6 gains rule 14. Enacted on the owner firing the guard diet (M3GD). Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

**v2.111 (2026-09-09) narrative, folded from the header:** NO AGENT MEMORIES UNTIL COMPACTION (owner: 'This is a more clean rule let's implement that. No agent memories until compaction. we need to be careful what sub agents come back to not overwhelm the main agent and force compaction on subagent stuff'): memory is made at exactly three moments — a MAIN thread's compaction (the summarizer's three-way triage is the creation event), thread close, and the owner's explicit /remember; the agent's mid-turn save tool is removed; sub-agents never make memories (symphony promotion of a winner stays the one exception); queue and Deck candidates arrive only after those moments, never per turn. Sub-agent returns are distilled and capped (the conductor's typed distillate is the pattern) so sub-agent bulk never forces the main thread's compaction; the compaction trigger reads the main thread's own fill. Requires the harness's own compaction, which has no packet yet — M3CM minted. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).

**v2.112 (2026-09-12) narrative, folded from the header:** THE FEATURE LEDGER (owner: 'I want the features to be the thing that the scout checks every time'): notes/feature-ledger.md is the standing checklist — one row per feature carrying the owner's own words where it was aligned, my one-line label stating only what he said, its status (built · partial · not built · horizon · dropped · unverified), and the walk a scout runs. Every scout runs every built/partial row and reports PASS/FAIL per row with a screenshot; a feature packet is not DONE until its row has a walk the scout can run. Labels use GLOSSARY.md terms and add no constraint the owner did not state; rows resting on the constitution say so. Full version lineage: Appendix D.2 (rows) and D.3 (narrative).
