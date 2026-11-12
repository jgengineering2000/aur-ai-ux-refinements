---
name: delegation-and-new-session
description: Read BEFORE closing a session, recommending or composing a new-session kickoff, spawning any delegate/subagent or writing its brief, or putting a decision (an A/B/C fork) to the owner — and, inside a delegation, before waiting on a job, killing a process, or acting at a timer — the master-supervisor architecture (thin top-model supervisor, nested delegates), the enforced context economy (supervisor Bash guard, slim delegate agent, compaction window, reply card, token-usage measurement), the model ladder, phase-boundary-equals-session-boundary, delegate briefs, timers that pause and report, and the escalation rule for the unexpected.
---

# Delegation & New-Session Architecture

## The supervisor shape (owner ruling, 2026-09-03)

A working session that carries real scope is structured as a **master
supervisor with nested delegates**, not as one model doing everything:

- **Supervisor: the heaviest available model, effort 3+, kept THIN.** It holds
  the frame, decomposes the work, writes and dispatches delegate briefs,
  verifies deliverables, and — the reason it exists — **judges the
  unexpected**. It does not grind mechanical work itself.
- **Delegates do the work, nested as needed.** A delegate given a whole delegated task may
  itself spawn narrower ones. Deliverables land in the tree (files, commits),
  never only in chat (loss-proof progress); the chat report is a summary.
- **Report upward every 15 minutes or less** (owner 2026-10-28): "supervisor
  should sanity check for correctness and progress, and report (succinctly)
  upward every 15 minutes or less. That replaces the current restriction for
  approved work." What it replaced was a project's rule that nothing ran over
  15 minutes without the owner approving its time.

Why this shape: the unexpected is exactly where a cheap model fails silently
and confidently, and where an expensive one earns its price. Buying the top
model's judgment *thin at the top* costs a fraction of running it *thick at
the bottom*, and beats the inverse arrangement (cheap supervisor, expensive
workers) on both axes: the supervisor sees every anomaly first, and the
workers' tokens are the bulk of the spend.

## The model ladder

- **Supervisor:** heaviest model, effort 3+, extended thinking on. Thin by
  construction — if the supervisor's own context is filling with work
  product, a delegation was skipped.
- **The default delegate is the mid model (e.g. Sonnet), effort 2–3,
  extended thinking off** — locked-plan mechanical work, driving crystallised tools
  (PROCESS §1). Owner ruling 2026-09-05: Sonnet is the default, Opus is
  the escalation rung, not the default.
- **The second-tier model (e.g. Opus), effort ~4, extended thinking on, for a step
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

- **Phase boundary = session boundary, and a phase is the kickoff's whole
  scope** (owner 2026-10-17: "You're not doing supervisor right if you have
  to end the session every 30 minutes. Sonnet is perfectly capable of viewing
  and comparing multiple images and generating a guided report"). A part,
  increment or review inside the kickoff is not a phase. The supervisor
  stays thin by delegating, not by closing: viewing and comparing images,
  sweeps and reading code go to a mid-model worker that returns a guided
  report. A supervisor that must close before its kickoff is done has
  skipped a delegation. Inside a phase, the compaction window bounds growth,
  and is safe because task state lives in files. Across a phase boundary,
  compaction is the wrong tool: its summary carries the finished phase's
  residue into the next one, so close and start clean. Never start a
  delegation near the session's token limits.
- **The kickoff prompt is the interface.** Complete, self-contained,
  ready-to-paste, re-emitted in full whenever anything changes (never "see
  above" / "with one amendment"). Open decisions are asked BEFORE the
  prompt is emitted or folded INSIDE it as its first item — never listed
  beside it.
- **Every kickoff names its delegation architecture**: which model
  supervises at what effort, which delegates carry which delegated tasks, and the
  escalation rule. A kickoff that assigns the work but not the architecture
  leaves the next session to run everything at whatever it woke up as —
  usually either too expensive or too weak.

## A goal file, checked by itself (owner 2026-11-11: "NEVER diverge from this model")

A supervisor under load drifts off the owner's goal, and a rule it must remember lapses. So the goal is a file, and the checks run
without being remembered. For any project with a long-running goal:

- **The goal file (`GOAL.md`).** The owner's own words, each quote with its raw source (file and line, or transcript and line) and its
  date, taken from the raw message and never from a summary. Our commentary sits in a last section, marked as ours. Nothing else.
- **The checks at every worker start.** The brief check (the brief states its class, site and kind, its predicted result with a time,
  the comparison that will check it, and its landing gate; work that closes nothing names the run it unblocks; a brief that lists the
  items to be solved is refused), the pace check (predicted against actual since the first build line), and the prediction against the
  measurement after each landing, from the one predictor.
- **A fresh auditor at fixed checkpoints.** A delegate with a clean context and the goal file as its whole rubric judges the checks'
  output, the landings' diff stats, the active briefs and the supervisor's checkpoint log, and writes one verdict file (first line
  `verdict: on task` or `verdict: off task`, then evidence per clause, quoted). Checkpoints: choosing what to build next, a worker's timer
  report, a missed prediction, before a speed or tooling brief, before a question to the owner, a plan change from another session, and
  the closing kickoff. Log one line per checkpoint.
- **The block.** A `PreToolUse` hook on the spawn tool (`hooks/goal-gate.py`) refuses a goal brief when the brief check refuses it,
  when the newest verdict says off task and no re-plan (the reason, then the plan) is newer, or when the newest verdict is older than the
  newest commit or the last compaction. The auditor's and the checks' own cards are allowed. Every decision is logged with its route.
  A verdict that only appears in a report is one more line nothing acts on; the block is what makes it bind.

## Decisions put to the owner — supervisor and delegate alike

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

One brief per delegated task, **in the repo**, compact and self-contained — never
re-derived per spawn (PROCESS §1: the second derivation is the signal to
crystallise). The brief states: the deliverable (files/commits), the
verification the supervisor will run (a crystallised script where one
exists), the boundaries (what is out of scope, what must not be touched),
and the escalation trigger. The supervisor verifies against the real
artifact (P9), never against the delegate's report of it.

**Every brief carries a `thread:` line** (its thread id in the project's ledger, `threads.py` in the shared root).
The supervisor opens the thread when it writes the brief (`threads.py open ID --title ... --goal ... --done ...`);
the delegate checkpoints it, and **pauses** it (`threads.py pause`) rather than writing a RESUME MAP on its own,
so the map's location is recorded; the landing closes it with the SHA. A thread with no close is open, and
`threads.py status` (run at SessionStart) sweeps unmerged labs, dirty lab trees, RESUME MAP/HANDOFF lines, saved
patches and recent briefs, flagging any no thread accounts for as an orphan. Paused work is never lost silently.

**Before briefing a fix, search the history for prior attempts** —
`git log --grep=<topic>` and `git log -S<symbol>` (owner-accepted
2026-10-06): a brief once had a delegate build a harness relaunch the owner
had already ruled out, and the ruling sat in a commit nobody searched.

**Every run brief states its cost and the run's hard stop** (owner 2026-10-15, after
425 CPU-minutes went unbudgeted): expected **CPU-hours** (threads × wall,
never wall alone), the stop that ends a pathological run (the job's stop: it ends the job, never
the worker, who pauses and reports, below), and a report of
**mean / p90 / max per stage, never the median alone** — a median hid one
626 s picture that cost 96% of a 7.2-hour run. A pathological cost is a
defect to fix before scaling (P7), never a budget to fund with threads.

**A timer pauses and reports; it never quits** (owner 2026-10-28). Every
brief sets timers: one at each incremental checkpoint and one for the final
closure. When a timer fires, the delegate pauses at the next safe point and
ends its turn with a checkpoint report: what is done (committed or not),
what is left, the numbers so far, and what it would do next. It does not
abandon, skip or wrap up. The supervisor then makes one of three calls,
**continuing the same agent** (SendMessage, so the agent keeps its context):
correct course, extend with a new budget based on the report, or abort.
The rules that follow from this:

- **The clock is the logged one**: `date +%T` lines in the progress log,
  never the worker's estimate.
- **A brief says "pause and report" at a worker's stop, never "STOP" or
  "hard stop"**, which read as permission to quit. A run's hard stop (above)
  ends the job; the worker still pauses and reports.
- **A re-brief to a fresh worker is the last resort**, because it pays the
  cold start again and loses everything the first worker understood.

Paid for 2026-10-28, when the worker that did the brief's first part claimed its
12-minute stop at 3.8 logged minutes and skipped three parts. The parts went to
two new workers, each starting cold.

**Pictures cost transcript.** The supervisor opens only the sheet a decision
rests on (JPEG q85, at most 2000 px a side); viewing and comparing go to a
worker that returns a guided report. A viewer's call is a lead: the property
it judged is named in the rubric and drawn on the sheet so it can be checked.

**A worker kills only the processes it started, by pid** — never
`pkill -f`, whose pattern matches other sessions' work.

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
