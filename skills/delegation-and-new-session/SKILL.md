---
name: delegation-and-new-session
description: Read BEFORE closing a session, recommending or composing a new-session kickoff, or spawning any delegate/subagent — the master-supervisor architecture (thin top-model supervisor, nested delegates), the enforced context economy (supervisor Bash guard, slim delegate agent, compaction window, reply card, token-usage measurement), the model ladder, phase-boundary-equals-session-boundary, delegate briefs, and the escalation rule for the unexpected.
---

# Delegation & New-Session Architecture

## The supervisor shape (owner ruling, 2026-09-03)

A working session that carries real scope is structured as a **master
supervisor with nested delegates**, not as one model doing everything:

- **Supervisor: the heaviest available model, effort 3+, kept THIN.** It holds
  the frame, decomposes the work, writes and dispatches delegate briefs,
  verifies deliverables, and — the reason it exists — **judges the
  unexpected**. It does not grind mechanical work itself.
- **Delegates do the work, nested as needed.** A phase-level delegate may
  itself spawn narrower ones. Deliverables land in the tree (files, commits),
  never only in chat (loss-proof progress); the chat report is a summary.

Why this shape: the unexpected is exactly where a cheap model fails silently
and confidently, and where an expensive one earns its price. Buying the top
model's judgment *thin at the top* costs a fraction of running it *thick at
the bottom*, and beats the inverse arrangement (cheap supervisor, expensive
workers) on both axes: the supervisor sees every anomaly first, and the
workers' tokens are the bulk of the spend.

## The model ladder

- **Supervisor:** heaviest model, effort 3+, thinking on. Thin by
  construction — if the supervisor's own context is filling with work
  product, a delegation was skipped.
- **The default delegate is the mid model (e.g. Sonnet), effort 2–3,
  thinking off** — locked-plan mechanical work, driving crystallised tools
  (PROCESS §1). Owner ruling 2026-09-05: Sonnet is the default, Opus is
  the escalation rung, not the default.
- **The second-tier model (e.g. Opus), effort ~4, thinking on, for a step
  only** — where a Sonnet result has failed verification once (the
  escalation rule below), or where the supervisor judges up front that the
  step genuinely needs thought (design, integration, re-creating a lost
  tool from evidence). The judgement is made per step, never per session.
- **Rote extraction, reporting, distillation:** the lightest model.
- **Escalation rule:** a delegate whose output fails verification ONCE is
  escalated one rung, not retried at the same rung with a longer prompt.
  Anything outside the brief goes UP to the supervisor; a delegate never
  improvises past its brief — improvisation is the supervisor's monopoly.

## Context economy — enforced by the harness, not remembered (owner 2026-10-12)

**Every call re-sends the whole context**, so a session costs its context size
times its call count — quadratic in its length — and a large context also costs
focus: the rules at its top are read less as it grows. Measured on one
project, 2026-10-01..12: 17.16B tokens re-read; **delegates 72%** of it; **growth inside
a transcript ~77%**; every session started at 79k tokens and every delegate at
66k; one delegate ran 2,294 calls and opened 984 images for 1.29B. That project
already had a delegate card, a session-start budget check and a token nudge
after every Bash call — all advice, and contexts still reached 967k. So each
rule below is a mechanism, installed by AUR's installer (token-economy
component):

| Rule | Mechanism |
|---|---|
| The main thread judges; delegates execute | `supervisor-bash-guard` (PreToolUse Bash): the main thread runs cheap reads + git only; subagents are never restricted. Per project: `.claude/supervisor-allow.txt` (exact prefixes of cheap status tools). Opt out per session: launch with `AUR_SUPERVISOR_BASH=off`. |
| Delegates start small | the `delegate` agent — Sonnet, `omitClaudeMd`, five tools, reads the project's `.claude/DELEGATE_BRIEF.md`. Measured start 15.9k vs 66k. Spawn it, not `general-purpose`. |
| No context grows unbounded | `CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000`. Task state lives in files (brief, plan, progress) so a compaction loses nothing needed; a SessionStart(`compact`) hook says to re-read them. |
| The owner's presentation rules stay in view | the reply card, injected at the END of the context on every genuine owner message (not on task notifications) and after each compaction. |
| Images are judged by sheet, not by stream | a review sheet driven by a full-resolution diff against the previous best — a large image opened whole is downscaled before the model sees it, so fine regressions hide in it anyway. Claude Code caps the displayed long edge at 2000 px (measured 2026-10-12; the Read result states original vs displayed size), so a page meant to be seen 1:1 stays within 2000 px. |

**Measured, never assumed:** the shared root's `token-usage.py` (`--compare
A..B C..D`) reports re-reads per owner message and per commit, starting size
and peak per transcript, main vs delegate, images opened. Acceptance for a
project adopting this: delegate start p50 ≤ 35k; no peak above the window plus
a compaction margin; re-reads per owner message and per commit ≥ 40% below its
baseline; presentation-rule compliance no lower than before.

**Never wire the same hook at user and project level** — both fire, and the
card arrives twice. A project's own settings carry only what is the project's.

## Sessions

- **Phase boundary = session boundary.** Each phase gets a clean session.
  Inside a phase, the compaction window bounds growth, and is safe because
  task state lives in files. Across a phase boundary, compaction is the wrong
  tool: its summary carries the finished phase's residue into the next one,
  so close and start clean. Never start a delegation near the session's
  token limits.
- **The kickoff prompt is the interface.** Complete, self-contained,
  ready-to-paste, re-emitted in full whenever anything changes (never "see
  above" / "with one amendment"). Open decisions are asked BEFORE the
  prompt is emitted or folded INSIDE it as its first item — never listed
  beside it.
- **Every kickoff names its delegation architecture**: which model
  supervises at what effort, which delegates carry which phases, and the
  escalation rule. A kickoff that assigns the work but not the architecture
  leaves the next session to run everything at whatever it woke up as —
  usually either too expensive or too weak.

## Decisions inside a delegation

- **Strike foreclosed options before surfacing any fork.** Before putting an
  A/B/C to the owner, delete every option a standing ruling — including the
  brief's own constraints — already forbids. **If one option remains, it is
  work: take it and notify.** Paid for on 2026-09-03: a supervisor's first
  turn posed a three-way merge-location decision in which one option
  discarded the history the method exists to preserve and another required
  seizing a second session's uncommitted files, both foreclosed by the very
  brief it was executing; the only open option then sat blocked on the
  owner's attention.
- **Authorized work starts immediately.** "Say the word and I will start"
  re-asks a go the kickoff already gave; run the authorized steps while any
  genuine question is in flight, never behind it.
- **Brief facts are leads, not ground truth.** The delegate re-grounds the
  brief against the real tree before acting and reports corrections — that
  part of the same turn was done right, and the corrections stood.

## Completion signals inside a delegation

- **A delegate that launches a job through a monitor and yields does not
  wake on it.** A completion notification is delivered to whoever holds the
  turn, and a delegate that has returned holds nothing — so the supervisor
  owns every completion signal for work a delegate started, and a brief that
  says "launch the build and report when it finishes" asks for something the
  delegate cannot do. Either the delegate waits on the job in the foreground
  inside its own turn, or the supervisor launches it and waits. Paid for on
  2026-09-03: a build launched by a delegate through a monitor finished with
  nobody listening, and the supervisor found it by polling. This is
  `ai_common.md` P11's completion-signal corollary — the signal belongs to
  whoever waits — seen from the delegation side.
- **A content defect is never a policy fork.** A guard refusal, a home path
  in a brief, a personal path in a board row: each is a defect in content the
  delegate produced, and the fix is correct content. Presenting one to the
  owner as a decision ("the guard blocks this — bypass it?") escalates a
  mistake as a fork whose other branch the brief already forbids, so the only
  option left is work (strike foreclosed options, above). The paid instance is
  under Delegate briefs below.

## Delegate briefs

**A brief is tracked content, so the ceiling binds it**: name worktrees by
branch and paths relative to the project root, never an absolute path. Paid
for on 2026-09-03 — two briefs carrying the ladder worktree's absolute path
were refused by the content guard, and the delegate then presented the refusal
as a policy fork instead of fixing the content.

One brief per phase, **in the repo**, compact and self-contained — never
re-derived per spawn (PROCESS §1: the second derivation is the signal to
crystallise). The brief states: the deliverable (files/commits), the
verification the supervisor will run (a crystallised script where one
exists), the boundaries (what is out of scope, what must not be touched),
and the escalation trigger. The supervisor verifies against the real
artifact (P9), never against the delegate's report of it.

**Every brief declares its expectation on the health model, and a brief
without one is refused** (owner 2026-09-18, P11's corollary). A delegate is
a spawn like any other, so before its first long step the brief states what
"sane" looks like on the machine monitor while that step runs — the class
that must be carrying load and within how many seconds (rustc within 20 s of
a build), the process that must be present during a batch (the renderer
during a corpus), the log that must grow, the RSS that is nominal — and the
delegate ticks the monitor every 15 s or better against that expectation,
reporting `state`/`wchan` of the blocked pid the tick it fails. Where a
watcher process exists, the brief arms it with the same declaration
(`tools/procstats_watch.py expect --job <label> …`, then
`tools/procstats_arm.sh <label>`) and the supervisor reads its alerts; where
it does not, the one-line tick is the floor. A brief that says only "build it
and report" has asked for the 11-minute stall that was paid for the day this
rule was written.
