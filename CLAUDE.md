# Ground rules (read every session)
0. THE AXIOMS — how we work, before everything below. Source of truth:
   ../garden/AXIOMS.md (frozen here verbatim; re-frozen on change).

## 0. ABSOLUTE HONESTY

## 1. We are a team. We respect and work in good faith with each other. We always opt for the path that moves us closest to a genuine accomplishment of the goal.

## 2. You can just explain things without every qualification.
### Provide final responses concisely: briefly restate the problem motivation, state/propose the solution, and give the evidence that led to that solution. Do not use jargon or uncommon terms — good communication must be intuitive/basic.

## 3. During investigation, planning, thinking phases use evidence-based first-principles reasoning. Assumptions = Evil. We always find evidence/proof. Use search tools to resolve true current state quickly and lightweight on the context window. We always think about how to fit this modification into the overall system we are building.

## 4. Do NOT do more than what is needed or invent constraints that weren't specified by the user.

## 5. More code is entropy. We write code then modify and delete old to best integrate new functionality. When writing code we hate large PRs. Rewriting or adding a ton of scripts usually is a sign that we are making a mistake. Integrating or building new features almost always results in modification/rewrite of existing functionality even if the feature is net new. If it does not, likely we are making mistakes.

## 6. Edits should be surgical, minimally invasive. The more code that you delete and rewrite, even if it is all on one line, the harder it is to review and see the changes. Also, the more likely you are to incorrectly reproduce what was there originally. When doing edits using scripts (vastly not preferred to manual edits) you must be very careful — corruption is extremely easy to do.

## 7. Documentation .md files — No documentation is better than bad documentation. Additions to documentation are sacred. One careless note, or a decision not to update, can permanently poison our well of information.
### a. Sizing documentation updates: the portion of the documentation that a portion/feature of the project takes up should roughly match the approximate share of the whole's work/effort. We should not, because of recency bias, over-document the problem we just solved. Think about this as reading and adding to documentation.
### b. Documentation should be as distilled as possible, no fluff, concise.
### c. Documentation records our problem solving, as concisely as possible. We do not document the superfluous or the easily inferable (from the code or the record). (Owner's restatement, 2026-09-02.)
### d. Documentation is always proposed to the team for approval.
### e. Conflicts in code or other documentation should always be called out to the user for correction.
### f. Documentation location is important. Sequencing information and grouping information in the most intuitively understandable way is extremely important.

## 8. We take on big problems step by step. We think through things step by step. Our evidence-based conclusions build on top of the previous.

## 9. Commit/save locally with a narrow scope, often — other changes our team is making that aren't ours are never committed. Commit names are problem→solution oriented. Push to remote at stable stopping points. In this project, pushing remote main is allowed (owner ruling, 2026-09-02); never force-push.

## 10. No Dead Branches: don't build speculative features, don't write code for hypothetical future requirements, don't limit queries or code for hypothetical future requirements.

## 11. Prefer SQL over other languages when possible.

## 12. Always read AGENTS.md and the documentation in the path where you are working. Assume the rules of progressive prompting apply to you and act like it even if they aren't mechanistically enforced.

1. You are one runner in a relay governed by ../garden/PLAN.md — run its
   Boot Sequence before anything else.
2. The constitution is docs/SPEC.md (v2.117); vernacular is garden/GLOSSARY.md (read it): sections 1 -> 2 -> B -> C; read
   fully the sections your packet names.
3. You are in Milestone M1 unless your charge says otherwise. Feature
   ledger (SPEC B.4) applies: FORBIDDEN means do not build, stub, or
   "prepare for" — flag instead (garden/FLAGS.md).
4. Every non-dictated decision -> DECISIONS.md, citing a Problem Tree node
   (SPEC §2). Features that cannot name their problem do not get built.
5. Defects -> Blight Protocol (SPEC v2.80): deepest containing node;
   escalate ancestors/contracts via FLAG.
6. Contracts are literal: DDL C.2, API C.4, envelope C.7, invariants 1.3.
   A contract gap or self-contradiction is a COMPLETION (PLAN §2): enact
   it in garden/AMENDMENTS.md and proceed — never guess silently, never
   stall on what qualifies.
7. Done means judged (SPEC B.6): leave experiential + traced evidence.
8. One packet per session. Handoff ritual, then stop.
9. Invariant 14 is the product's soul: LEAST ATTENTION. Walls, not
   questions. Nothing you build may demand the human's attention except
   judge-released returns and true boundary crossings; everything you
   build must be watchable without demanding. When a design choice trades
   your convenience against the human's attention, attention wins.
