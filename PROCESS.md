# Process & Methodology — AUR Master

Methodology rules — *how* we audit, verify, and build (the principles — *what*
correct looks like — live in `ai_common.md`). Inherited by every project; a
project `PROCESS.md` adds workflow on top without duplicating this. When in
doubt: describes the artifact → principle; describes our actions → process.

## 1. Crystallise any repeated AI effort into a lightweight tool — don't re-derive it

Whenever the model's own work repeats a pattern — parsing, researching,
building, auditing, editing, ANYTHING — and a lightweight tool could shrink the
AI-token scope of the repetitions, writing that tool is **baseline behavior**:
self-triggered, never waiting for the owner to name it. Both repetition axes
count: **iterative** (the same shape recurs within one effort) and
**common-cause** (the same shape recurs across separate efforts/sessions — a
verification sequence hand-run before every landing, a delegate brief
re-derived per spawn, a boilerplate multi-touch-point edit stamped feature
after feature; no single session sees all N occurrences, so this axis is the
one that hides). Re-deriving burns expensive reasoning on a solved problem and
drifts between runs, so regressions look like noise. First time: solve by
hand, note the procedure. Second time the same shape appears: write the tool.
Thereafter: call it, reason only over its output, and sharpen the tool when it
misses.

This is token economy, not abstraction design: the tool is the most
straightforward, trivial generalization of the repetition actually observed —
a dumb script, a scaffold stamp, a checklist runner — never a speculative
framework, so it cannot collide with the no-false-abstraction rule (P3).
Intelligence is applied once, then called. **Never hand-mine machine output
with the reasoning model** — repetition (multi-line included), anomalies,
incomplete-call pairing, context/buffer capture, and counts belong in an
analyzer with stable indexed references; eyeballing a log in chat is the
signal to write it. That analytical case (trace mining, Valgrind/log/
query-plan/diff parsing) is one instance of this rule, not its boundary.

Recurring permission-prompted operations are the same trigger applied to
authorization friction: a bespoke, instance-specific invocation (exact PIDs,
exact paths) can never be pre-authorized, but the narrow, reviewed,
committed script wrapping it (exact-match census-then-act, self-reporting)
collapses to one stable command a permission rule can cover — write that
script instead of re-authorizing the bespoke form each time, and never widen
the rule beyond the reviewed script's own invocation.

## 2. Comprehensive audits fan out per subsystem and read end-to-end

One agent/pass per subsystem reading 100% of its files (sampling gives false
confidence); a single shared rubric (correctness, optimisation, hoisting, P10
error-handling, quality, lint baseline captured once); stable finding IDs with
severities (P1/P2/P3) and file:line fixes in one annotatable doc; the
consolidator re-verifies the most falsifiable P1 claims in source. Full method +
constraints: the **audit-methodology skill**. Every audit runs BOTH directions:
code→correctness ("is this site right?") AND spec→code coverage ("for each
behavior the design promises, point to the implementing code — or state 'none'");
the second traversal is the only one that finds documented designs never built.
For each central mutable structure, enumerate every writer and assert their
population/lifecycle models agree with each other and with the spec — a positive
check, not merely absence-of-violation.

## 3. Differential syscall-tracing is a standard audit pass

Operationalises the P9 corollary: across the defined process roster, trace the
real deployed process; flag per-item syscalls, incomplete calls, and latency
outliers; capture with fork-following, buffers, and per-call latency/timestamps.
Never start a session-fatal component to trace it. Run **both detection axes** —
the statistical miner-scan at scale AND an exhaustive AI read of a deliberately
tiny curated set (one work-item of each type, read 100% end-to-end across every
logfile) for the emergent patterns a scanner can't encode; the scan alone is half
the audit. Full capture flags + roster discipline + the two axes: the
**audit-methodology skill**.

## 4. Every fix lands with a test that fails first

Write the test, watch it fail for the right reason, fix, watch it pass — a fix
without a red→green reversal is unproven and a regression waiting to return.
Doubly required for audit findings: the missing coverage is part of the defect.
Beyond fail-first: every fix/feature also adds or enhances coverage of adjacent
reachable paths while the file is in hand, and every plan/work item states a
concrete Before (observable defect) → After (observable outcome) — the After is
the acceptance criterion the test encodes. A reachable path with no verification
artifact is unfinished: extend a harness or build a tool to drive it, against
deterministic fixtures AND real-world data. When a result surprises, isolate the
case end-to-end before blaming the code-under-test — the verifier may itself be
wrong, and a real corpus is what stress-tests its assumptions.

## 5. Dev tests run through a committed teardown-and-guard harness, assistant-driven

Five fixed stages: guard/census every shared or session-fatal resource (refuse on
a stray — the guard detects, never cleans; a stray is a P8 defect in the
component); shut the running instance down cleanly; hand-launch the installed
binary on the SAME canonical resource under the chosen tool, driven by standard
clients (P9); a trap that ALWAYS restores; mine output with the crystallised
script. The harness is the method — ad-hoc hand sequences are how guardrails get
skipped. Full stage detail: the **audit-methodology skill**. (MTI instances:
`tests/dev_instrument.sh`, `tests/syscall_audit.py`.)

## 6. Capture principles at the right altitude

When the owner states a general principle ("X should always…", a capability or
content rule), file it at the level its scope implies — universal →
`ai_common.md`; repeatable procedure → PROCESS; project fact → project
memory/spec — and do NOT demote it to a local task because it surfaced while
discussing one bug. A **task-shaped recipe** — guidance consulted only when a
specific task shape occurs (logging setup, an audit pass, a hazardous subsystem)
— files as a **skill** (AUR `skills/` symlinked global, or project skill if it
names project machinery), never as always-loaded text: the test is "would this
change behavior in a session that never touches its topic?" — if no,
always-loading it is Priority-2 waste. The skill's trigger lives in its
description; where a missed trigger is costly, an always-loaded principle keeps
a one-line "invoke the X skill before Y" anchor (P10→logging-discipline and
the doc-generation skill is the pattern; a project's memory-recall corpus is the
same shape — consult the **memory-hygiene skill** when setting one up or once
it starts feeling bloated, rather than re-deriving the fix each time it recurs).
Reiterating a principle correctly in chat is NOT capturing
it; the in-chat echo is exactly what masks the filing error. When unsure whether
something was dropped, re-read the transcript from the request forward — recall
is the thing that fails here.

## 7. Ground plans in HEAD, not in the audit doc

Before planning from an audit/recommendation/spec document, verify each item
against HEAD (`git log --since=<doc-date> -- <files>` plus reading the live
sections) — an audit is a snapshot whose items may already be shipped, and
planning from it re-litigates settled work (P5). Cite commit SHAs for "already
done"; only the genuinely-unexecuted residue enters the plan.

## 8. Long-running work presents a timed checklist — silence is not progress

Harnesses, builds, and any multi-stage job print their component checklist up
front with expected durations (a ±30% band around the last recorded run; a
single run seeds the expectation), flip each component to done-with-actual as
it completes, and flag a band breach live — the operator must never sit
watching an opaque spinner. The per-component timing record is persisted as
part of the test/build result (P7 corollary: performance is result content,
not metadata), and a breach is triaged immediately post-run like a red test.
Components with no baseline print an explicit "no expectation yet" marker
rather than silently omitting the estimate.

The measurement layer is itself crystallised tooling (§1), never inline
re-derivation: a shared Python metrics toolset that **measures, stores (with
run history), reviews, and presents** per-component durations *and*
success/failure outcomes across both builds and tests — one store, one parser,
one presenter consumed by every harness. The model reasons over the tool's
findings and trends; it never hand-mines timing or pass/fail output across
repeated runs — anything iterative or repeated where intelligence is spent on
multiple invocations of the same extraction is the signal to extend the tool.

## 9. Diverged trees normalize before they merge

When two long-lived trees must become one, no semantic reconciliation happens
until the trees share a shape. Three ordered steps, each strictly smaller in
judgment than the next: **(1) renames** — replay every rename one side made
onto the other, automated through tools (the refactor-rename discipline), never
by hand; **(2) non-semantic movement** — file and function placement, import
paths, formatting: mechanical, behaviour-preserving; **(3) the merge itself** —
the only step that requires thought, now operating on a conflict surface
reduced to the genuinely semantic residue.

Steps 1 and 2 are proven, not trusted: after normalization the tree must
**regenerate its approved reference artifact as a pixel-perfect (bit-identical
where the toolchain permits) match, and that proof is committed as the
baseline** before the merge begins. A normalization that cannot reproduce its
own approved output has changed behaviour and is a defect to fix, not a
variance to explain (P2, P7's behaviour-preserving-refactor corollary). Paid
for first on 2026-09-03: a two-branch pipeline merge whose ~2,000-line
conflict surface was mostly one crate rename.

## 10. A silent log is the first fix — never the reason for a probe

The log is read first (the SOP). When it cannot answer the question, the next
action is **the line that would have** — not a re-run, a probe, a constructed
test or a trace. Those answer this instance once, at the cost of reproducing
it; the line answers every future instance for free, and P17 says the fix must
carry it anyway. Order: (1) read the log; (2) name the fact it lacks and the
**exposure** that lost it — a spawn that logs no argv, a report computed and
dropped, a sink switched off with nothing beneath it; (3) land that line at
every site with the same exposure, found by grepping the *mechanism* (every
`.output()`, every `let _rep`, every sink switch), never the symptom (P3 hoist,
P17 class); (4) re-run the REAL scenario once and read the line; (5) only then
probe or trace, with the log now able to record what the probe finds. **The
checkable moment is typing a reproduction command**: if the log has not first
been made able to answer, that command is the wrong next step. Owner
2026-09-11, paid for the same day: a two-way re-run of an enhancer binary established
a routing defect that a spawn-argv line plus a produced-dimensions line would
have shown in the log already written — and will show in the next one.
