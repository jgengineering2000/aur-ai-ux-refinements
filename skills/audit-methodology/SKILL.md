---
name: audit-methodology
description: Read BEFORE running a codebase audit, a syscall/strace tracing pass, or any dev test against a live daemon/mount/shared resource — the AUR fan-out audit method, differential syscall-tracing pass, and the mandatory five-stage teardown-and-guard test harness.
---

# Audit, Tracing & Guarded-Test Methodology (AUR PROCESS §2/§3/§5)

## Comprehensive audits fan out per subsystem and read end-to-end (§2)

A real audit is **not** a pattern-driven sample. When the task is "audit the
codebase":

- **One agent (or pass) per subsystem**, each reading **all** its assigned files
  end to end — not greps, not excerpts. A sampling audit reports ~15% and gives
  false confidence; the fan-out reads 100%.
- **A single shared rubric** across all agents so findings are comparable:
  correctness, optimisation (buffer reuse, syscall/context consolidation — standing
  lenses on every audit), hoisting (within and across modules),
  exception/error-handling normalisation (against claude_master P10 — leveled
  output + full-context messages), code quality, and the mechanical lint baseline
  (captured once, up front, as ground truth).
- **Stable finding IDs and severities** (P1 correctness, P2 perf/hoist, P3
  quality), each with file:line and a concrete fix, consolidated into one
  annotatable document.
- **The highest-impact, most-falsifiable claims are re-verified directly in
  source** by the consolidator before being asserted as P1 — an agent's reasoning
  is a lead, the source is the proof.

Independent subsystems audit in parallel; only spawn sub-agents when the owner has
asked for the fan-out, and give each the safety constraints for its area (an
AT-SPI/session-fatal component is *read-only, never run*).

## Differential syscall-tracing is a standard audit pass (§3)

Across a **defined process roster** (every component we influence, not just the
GUI), trace the real deployed process and flag:

- **per-item syscalls** — anything scaling O(files/rows/requests) that should be
  O(1) or O(batches) (a per-file `stat` of a shared config/IPC file is the
  canonical catch);
- **incomplete calls** — an enter with no matching return (a hang/leak);
- **long-latency calls** — duration outliers.

Capture with **full fork/thread following, buffer contents, and per-call latency +
timestamps**. Crystallise this analysis into a tool (PROCESS §1) rather than
re-reasoning it each run. Tracing an already-running process is read-only and safe;
never *start* a session-fatal component to trace it — attach to the one already up.

### Two co-equal detection axes — the tool-scan is only half the audit

A crystallised miner encodes **known-shape** anomalies; it cannot flag the pattern
no one thought to encode. So every trace/log/valgrind-mining audit runs **both**
axes, weighted equally:

- **Axis 1 — statistical scan at scale.** A large corpus through the miner
  (`syscall_audit.py`, log/valgrind parsers) to catch known shapes at volume:
  per-item repeats, latency tails, redundant opens, incomplete calls.
- **Axis 2 — exhaustive AI read of a deliberately tiny curated set.** A set small
  enough (e.g. one work-item of each type — valid / other-type / missing /
  container) that the model reads **100% of every record end-to-end across every
  logfile** (strace + ltrace + callgrind + the app-level timing log for the *same*
  items), hunting the emergent / non-self-evident patterns and standout delays a
  scanner can't encode. The smallness **is** the design: a set this size is fully
  readable, and full reading — never sampling (§2: sampling gives false
  confidence) — is the entire point.

Neither substitutes for the other: Axis 1 finds what we already know to look for;
Axis 2 finds what we don't — and any pattern it surfaces that the miner missed is
folded back into the miner (§1). A syscall audit that ran only the scan is
incomplete.

## Dev tests run through a committed teardown-and-guard harness, assistant-driven (§5)

Until a project declares "production," the standard way to exercise the **real
deployed artifact** is a *committed* harness the assistant runs itself, five fixed
stages:

1. **Guard every shared/fragile resource first** — census the singleton
   mount/socket/lock/PID and any session-fatal shared subsystem, **refusing to
   proceed** on a stray. The guard **detects; it never implements cleanup** — a
   stray is a defect in the component's own lifecycle (P8), fixed in the
   daemon/tool itself, never in the harness.
2. **Shut the running instance down cleanly** so it releases its resources via its
   own lifecycle (P8).
3. **Hand-launch the installed binary** on the SAME canonical resource, under the
   chosen tool (strace/ltrace/valgrind/gdb — unprivileged, precisely because it is
   hand-launched), driven by the STANDARD client tools that are the real contract
   (P9).
4. **A trap that ALWAYS restores** the running instance, so a failed or interrupted
   run never strands the resource.
5. **Mine the output with the crystallised script** (§1/§3), then reason over
   findings.

The harness *is* the method; "a few commands by hand" is how a guardrail gets
skipped and a run strands a resource. MTI instances: `tests/dev_instrument.sh`,
`tests/atspi_guard.sh`, `tests/syscall_audit.py`.

## ptrace gotchas (MTI-learned)

setuid-fusermount → attach-after-mount; `pkexec sysctl kernel.yama.ptrace_scope=0`
when attach is refused (restore to 1 after).
