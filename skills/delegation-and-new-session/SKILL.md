---
name: delegation-and-new-session
description: Read BEFORE closing a session, recommending or composing a new-session kickoff, or spawning any delegate/subagent — the master-supervisor architecture (thin top-model supervisor, nested delegates), the model ladder, phase-boundary-equals-session-boundary, delegate briefs, and the escalation rule for the unexpected.
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
- **Design / integration / anything requiring thought:** the second-tier
  model (e.g. Opus), effort ~4, thinking on.
- **Locked-plan mechanical work:** a mid model (e.g. Sonnet), effort 2–3,
  thinking off, driving crystallised tools (PROCESS §1).
- **Rote extraction, reporting, distillation:** the lightest model.
- **Escalation rule:** a delegate whose output fails verification ONCE is
  escalated one rung, not retried at the same rung with a longer prompt.
  Anything outside the brief goes UP to the supervisor; a delegate never
  improvises past its brief — improvisation is the supervisor's monopoly.

## Sessions

- **Phase boundary = session boundary.** Each phase gets a clean session;
  a bloated context is the signal to close, not to compact indefinitely.
  Never start a delegation near the session's token limits.
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
