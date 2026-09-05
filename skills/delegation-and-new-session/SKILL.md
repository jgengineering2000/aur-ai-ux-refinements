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
