---
name: logging-discipline
description: Read BEFORE designing or adding logging, a logger, verbosity levels, log rotation, or log output anywhere in any language — BEFORE demoting or promoting the level of an existing log site, or diagnosing a runaway/oversized log — and BEFORE diagnosing an error, warning, race, hang, incomplete task, unknown state, silently-dead child process or resource, or progress ambiguity whose investigation reveals missing observability. The full field contract, type-injection requirements, rotation cascade, scope-boundary instrumentation, and sink rules that AUR P10 anchors.
---

# Logging Discipline — the P10 recipe

AUR P10 states the principle (every error path logs, with full context; types runtime-switchable; never a null sink). This skill defines the log type structure, bitmask evaluation model, centralized configuration, timestamp format, duplicate throttling, sink routing and rotation, and the instrumentation placement rules — which code points must log, at which type, and what a scope boundary must carry so that hot paths stay diagnosable without being perturbed.

## Design Philosophy — Log Types, Not Severity Levels

MTI uses a **bitmask-based log type** model, not the traditional hierarchical severity model found in most logging frameworks. Each log type is an independent bit that can be enabled or disabled individually. This means:

- Setting `LOGLEVEL=5` enables Error (1) + Debug (4) **without** enabling Info (2).
- Setting `LOGLEVEL=3` enables Error (1) + Info (2) **without** enabling Debug (4).
- A message is printed if `(type_bit & LOGLEVEL) != 0`.

This is a deliberate rejection of the hierarchical pattern (where enabling "debug" implicitly enables everything above it). Each type serves a different audience and purpose, and operators should be able to mix and match exactly the output they need.

## Log Types (Bitmask)

Four log types are defined, each occupying one bit:

| Type  | Bit | Value | Audience  | Aliases          | Description |
|-------|-----|-------|-----------|------------------|-------------|
| Error | 0   | 1     | Everyone  | Warn, Warning    | Something failed or is not working as expected. Includes warnings — a separate Warning level is differentiation without real value. |
| Info  | 1   | 2     | User      | Status, Verbose  | Stateful/Status information. Infrequent but detailed: stats on startup/shutdown of a connection, Nautilus window status, directory opens. INFO and STATUS are interchangeable aliases for the same log type and bit. |
| Debug | 2   | 4     | Developer | *(none)*         | Location-sensitive information: opening a new file, reading metadata, tracking fine-grained control flow. |
| Trace | 3   | 8     | Developer | *(none)*         | High-verbosity fast-path details that would normally drown the system but are needed when debugging hot paths. |

Note: INFO/STATUS/VERBOSE are **aliases** — they share the same bit and are interchangeable. DEBUG and TRACE are **semantically distinct** developer tools occupying separate bits: Debug targets control-flow granularity, Trace targets hot-path verbosity. They are never aliases of each other.

### Audience Classification

- **User-digestible** (potentially visible to end users): Error, Info (aliases: Status, Verbose)
- **Developer tools** (diagnostic only, semantically distinct from each other): Debug, Trace

### Common Composite Values

| Value | Meaning                    | Types Enabled            |
|-------|----------------------------|--------------------------|
| 0     | Silent                     | None                     |
| 1     | Errors only (default)      | Error                    |
| 3     | Errors + Info              | Error, Info              |
| 5     | Errors + Debug (no Info)   | Error, Debug             |
| 7     | Errors + Info + Debug      | Error, Info, Debug       |
| 15    | All types enabled          | Error, Info, Debug, Trace|

### String Parsing (cumulative masks)

When parsing named log levels from config or CLI, each name maps to a **cumulative** mask that includes all lower types in its audience tier:

- `ERROR`, `WARN`, `WARNING` → `1`
- `INFO`, `STATUS`, `VERBOSE` → `3` (Error + Info)
- `DEBUG` → `7` (Error + Info + Debug)
- `TRACE`, `ALL`, `TRUE`, `ON` → `15` (all types)
- `NONE`, `FALSE`, `OFF` → `0`
- Raw numeric strings (e.g., `"5"`) are used as-is

## Bitmask Evaluation

A log message with type bit $T$ is printed if $(T \mathbin{\&} \text{LOGLEVEL}) \neq 0$.

**Do NOT use threshold/comparison checks** like `severity <= loglevel`. Always use bitwise AND.

### Performance & Cost Gating

In compiled languages like Rust, log macros must check the enabled log level bit *before* evaluating any arguments or formatting strings. For example:
```rust
macro_rules! dlog {
    ($($a:tt)*) => {
        if $crate::log::is_level_enabled("Debug") {
            $crate::log::debug(&format!($($a)*))
        }
    };
}
```
This pattern ensures that when the corresponding log type is disabled:
1. **No string formatting** occurs.
2. **No heap allocations** are performed.
3. **No function call overhead** is incurred.

The runtime cost drops to a single fast CPU atomic bitwise check (typically 1–2 ns). This makes it safe to instrument code liberally (even in embedded environments) without introducing latency or memory pressure. Instrumentation should only be avoided or restricted in performance-dependent hot paths (e.g. raw encoder/decoder inner loops) where even nanosecond-level checks can accumulate.

**Gate the emission, never the accounting.** Counters and accumulators that feed a scope-boundary summary — item counts, min/max/percentile timings, byte totals, resident-memory samples — are maintained **unconditionally**. That is a few integer operations per item: nanoseconds, no allocation, no lock. Only the format-and-write *at the boundary* sits behind the level check. If the accounting itself were gated, the aggregate would be missing exactly when an operator enables the type mid-problem, forcing a restart to reproduce or a fall back to hot-path Trace — the outcome this model exists to avoid.

**Gating makes instrumentation cheap to CARRY, not cheap to ENABLE.** The 1–2 ns check is the *disabled* cost. The enabled cost of a high-frequency type is a format, a throttle lock, and a write per event, which on a latency-governed path is a first-order change to the behaviour under observation. This asymmetry is why category assignment (below) is a correctness decision, not a taste decision.

## Centralized Configuration (`~/.config/mti/mti.conf`)

Log levels are configured via a centralized INI-style config file at `~/.config/mti/mti.conf`. This avoids the need for environment variables in `.bashrc` (which don't propagate to graphical desktop applications or systemd user services).

### Format

```ini
[global]
LOGLEVEL=7

[mtid]
LOGLEVEL=7

[mti-fuse]
LOGLEVEL=3

[trackmetad]
LOGLEVEL=3

[mti-sidecar]
LOGLEVEL=7

[mti-columns]
LOGLEVEL=3
```

### Resolution Order (highest priority first)

1. **Environment variable** `MTI_LOGLEVEL` — overrides everything (useful for one-off debugging)
2. **Process-specific section** in `mti.conf` (e.g., `[mtid]`) — matched against the binary name from `current_exe()` (Rust) or a hardcoded component name (Python)
3. **`[global]` section** in `mti.conf` — fallback default
4. **Hardcoded default** — `1` (Error only)

### Section Naming

Section names must match the **executable binary name**, not the host process:
- `[mtid]`, `[mti-fuse]`, `[trackmetad]`, `[mti-sidecar]` — Rust binaries
- `[mti-columns]` — the Nautilus Python extension (NOT `[nautilus]`, since it is not Nautilus)

## Field and Timestamp Contract

Every log line must be self-explanatory and contain full context:
- **Timestamp Format**: Exactly `YYYY-MM-DD_HH:MM:SS.ms` (e.g. `2026-07-14_01:28:08.123`).
- **Context fields**: `[Level]`, `[PID:nnn]`, `[Thread:name/ThreadId(n)]`, `[component]`
- **State**: Variable values, identifiers, sizes, and loop/iteration counters.

### Log Line Format

```
[2026-07-14_01:28:08.123] [Error] [PID:254656] [Thread:main/ThreadId(1)] [mtid] Something failed
```

## Duplicate Throttling (Deduplication)

To prevent log-spam and write bottlenecks:
- Consecutive identical log messages (same level and message text) are suppressed within a 5-second window.
- When the message text or level changes, or the 5-second window expires, a summary line `Last message repeated N times` is printed immediately prior to the next message.

## Target Sink Routing

### Per-Process Log Files

Each daemon/process writes to its own log file under `~/.local/share/mti/`:
- `mtid.log`, `mti-fuse.log`, `trackmetad.log`, `mti-sidecar.log`, `mti-columns.log`

The log filename is derived from the binary/component name, **not** the host process. The Nautilus extension log is `mti-columns.log`, not `nautilus.log`.

### Routing

- **File (Mandatory)**: Appended to the process-specific log file. Parent directories are auto-created.
- **Console (Optional/stderr)**: Written to locked standard error (enabled by default, can be disabled by setting `MTI_LOG_CONSOLE=0` or `MTI_LOG_CONSOLE=false`).

Note the interaction with service managers: a process run under systemd whose unit does not set `StandardError=` has its console sink captured by the journal, so every line is stored **twice**. Either set the unit's stream disposition deliberately or disable the console sink for that component — silently paying double is not a third option.

### Rotation

**Every sink rotates.** An unbounded log file is a defect: it consumes the disk it was meant to diagnose, and past a certain size it stops being readable by the tools and the humans that need it.

**Baseline trigger — startup.** Every process rotates its own log during initialisation, before its first line is written. Each run's log then becomes a self-contained artifact: "what did this process do on this run" needs no date arithmetic inside a shared file, and a crashed run's evidence is not diluted by the runs that follow it.

**Optional second trigger — running size cap.** A long-lived process may additionally rotate mid-run when the live file crosses a configured size (close, rotate, reopen), reusing the identical cascade. Startup rotation is mandatory; the size cap is a per-component decision.

**The cascade** (depth configurable — trim it in production):

```
exe.log       →  exe.log.0        previous run, kept UNCOMPRESSED (opens instantly)
exe.log.0     →  exe.log.1.xz     compressed on the way out
exe.log.N.xz  →  exe.log.N+1.xz
exe.log.9.xz  →  unlink
```

**Compression is `xz -3 -T2`, in a detached double-forked process.** Not gzip: `-3` buys roughly 3–4× gzip's ratio on log text, and `-T2` keeps the wall time short without taking the machine. Not a thread: a double-forked, reparented process (fork → setsid → `execv`) outlives the component that spawned it, so a daemon that rotates and exits promptly still gets its predecessor compressed instead of stranding it. Startup MUST NOT block on compression — a large `.log.0` would otherwise stall initialisation for seconds on exactly the runs where something has gone wrong.

**The shift is synchronous; only the compress is detached — no lock needed.** Shifting the numbered generations (a handful of `rename`/`remove` calls, microseconds) runs inline at startup before the first log line is written; only the actual compression of the one generation that needs it is forked off. This is safe without a lock because the resource this protects — one log file per component — has exactly one writer at a time: MTI runs a single instance per component, so no second process is ever racing this cascade for the same file. A shared multi-writer log target would need the serialisation an `flock`-based worker once provided; a per-component single-instance log does not, and carrying that machinery here was over-generalizing from a constraint that doesn't hold.

**Never lose a generation to a failed compression — for free, not by construction.** Plain `xz <file>` is fail-safe on its own: it writes `file.xz` and unlinks `file` only on success, so a killed compressor (SIGKILL, reboot, ENOSPC) simply leaves the raw file in place. No temp sibling, no rename-into-place dance, and no startup sweep for interrupted artifacts are needed — the next cascade ages a leftover raw generation exactly like a compressed one, because the shift moves `exe.log.N` and `exe.log.N.xz` alike.

**One implementation, shared.** Rotation lives in the sink layer, so every component inherits it identically. A component that hand-rolls its own rotation, or opts out, is the drift this rule exists to prevent.

**Rotation cannot log where it happens.** It runs inside the sink's own initialisation, so a log call there re-enters that initialisation on the same thread and deadlocks. Queue the report — rotation performed, size, depth, interrupted-artifact findings — and drain it through the sink on the first line written, so it lands at the head of the new generation ahead of the component's startup block. Silently skipping the report instead violates "the instrument monitors itself".

## Level Control

- **Startup**: Initialized by calling `init_from_env()` (Rust) or `load_config()` (Python) which reads `~/.config/mti/mti.conf` then checks `MTI_LOGLEVEL`.
- **Runtime**: Level changes can be made dynamically:
  - **Daemons**: Via the command FIFO socket (`loglevel:<val>`), AI Chat command (`/loglevel <val>`), or by sending `SIGUSR2` (toggles between Error=1 and Trace=8 verbosity).
  - **Non-Daemons**: Standard command utilities keep the `-v` / `--verbose` CLI options, which map to level `7` (Error + Info + Debug).
- **Storage**: A single atomic variable: `LOGLEVEL` (Rust `AtomicU8`) or `loglevel` (Python module global).

## Startup, Exit, and Closure Logging

Startup and shutdown are two ends of one contract. A logger that mandates only the opening half produces logs whose last word on any resource is permanently optimistic: it records that a thing was acquired and never that it went away. **Every external-influence boundary a process crosses — syscall, filesystem, IPC, subprocess, socket, lock — must be trackable through the log, with startup, exit, and failure/closure mandatory.**

### Startup block (mandatory)

Every daemon must emit a structured startup block on launch that includes:
- All command-line arguments
- All parsed configuration settings
- All `MTI_*` environment variables
- The resolved log level value

This ensures that crash investigations always have the initial state context.

### Exit and closure (mandatory, symmetric)

Every process logs its own termination — normal return, signal, and panic paths alike — with the exit cause, the exit code or signal, total uptime, and the disposition of anything it still owned. The teardown P8 requires on every exit path is *logged* on every exit path; a panic hook that runs teardown silently has done half the job.

Every **owned external resource** logs its release: child processes, mounts, sockets, FIFOs, locks, devices, database handles. Acquire and release are a pair. A codebase whose acquires are instrumented and whose releases are not cannot answer "what does this process own right now, and since when?" from its log — which is the first question every lifecycle investigation asks.

### Unsolicited closure — the watcher requirement

Call-site logging can only report events some thread asked for. A resource that dies on its own — a child killed by the kernel, a mount yanked, a peer closing a socket, a lock broken, a device removed — is **nobody's call site**, and is therefore invisible to every rule above unless something is explicitly watching.

Every owned singleton resource therefore has a **supervisor observing its liveness**, which logs the transition nobody requested, at Error:

```
[Error] [sidecar] [faces] OWNED CHILD DIED UNEXPECTEDLY pid=135070 role=face-engine
        status=killed(SIGKILL) alive_for=0.004s since_ready=0.004s
        expected=alive-until-shutdown — engine is now cold; next request will respawn
```

The supervisor also flips whatever readiness flag the resource backs, so state and log agree. Absence of an expected event is never observable by accident — only by design. This is AUR P11 ("silence is not success") stated at the logging layer, and P8's "own the resource lifecycle at the owner" given its observability half.

### The instrument monitors itself

The logging subsystem applies these rules to its own operation: a rotation performed, a sink crossing a size threshold, a sink that cannot be opened, a level change. A log file that grows without anything ever remarking on it is the same blind spot as a dead child nothing reported.

## Instrumentation Placement Rules

The mechanism sections above define HOW to log. This section defines WHERE — which code points must emit log statements and at which type. A logger without instrumentation is useless; these rules ensure that every user action, scope boundary, external-influence decision point, failure path, and state transition is observable.

**Frequency determines category.** How often a site fires decides its type before any other consideration: per-item-in-a-loop is Trace, a scope boundary is Debug, a user-visible outcome is Info, a happy-path deviation is Error. This is not a stylistic preference — because enabling a type is not free (see Performance & Cost Gating), putting high-frequency content at an operator-facing type makes that level unusable and putting low-frequency context at Trace makes it unreachable without perturbing the system.

### Info — User-Initiated Action Summary

Every user-initiated action must emit **at least one Info-level log line** summarizing what happened. This is the action's receipt — if an operator runs with `LOGLEVEL=3` (Error+Info), they should see a chronological record of every action the system performed and its outcome.

**Placement**: After the action completes (or fails), on the main/initiating code path.

**Content**: Action name, target (e.g. file path), and outcome (success, failure reason, or key result metric like elapsed time or output path).

```
[Info] [sidecar] Enhance completed for /photos/a.jpg → /photos/a_enhance.jpg (12.3s)
[Info] [sidecar] Enhance failed for /photos/a.jpg: fast_pass.sh exited with status 1
[Info] [sidecar] Regen (file) completed for /photos/a.jpg
```

### Debug — Scope Boundary: Entry and EVERY Exit

A scope that encloses repeated or complex work — a queue drain, a batch, a directory walk, a request handler, a subprocess interaction — emits Debug at its **entry** and at **every one of its exits**. These boundaries are low-frequency by construction (one pair per queue, not one per item), which is exactly what makes them affordable to carry rich content. Surround high-frequency behaviour with strong low-frequency diagnostics.

**Entry** is the action's black-box recorder: after argument resolution, before the first side-effecting call (spawn, file write, DB update, FIFO write, D-Bus call). If a crash or hang occurs mid-scope, this is the last evidence of what was attempted. It carries every resolved variable that influences behaviour:
- **Target**: file path, directory, hash
- **Configuration**: mode flags, quality tier, budget, autoclean state, resolved input path (if different from the user-selected path, e.g. recovery composite)
- **Initiator**: button click, remote `ACTION:` dispatch, FIFO command, CLI
- **Computed values**: output path, resolved script path, subprocess arguments
- **Entry state for the accumulators**: queue depth, resident memory, start time

**Every exit** — success, early return, error, budget exhaustion, cancellation, shutdown — carries two things the entry line cannot:

1. **Which exit fired.** An `exit_trigger=` field naming the branch actually taken. "drained" versus "budget-exhausted" versus "shutdown" versus "error" is the difference between a tuning problem and a correctness problem, and no other field recovers it.
2. **The accumulators.** Everything counted across the scope: items processed against items offered, per-category breakdown, successes/failures/deferrals, total elapsed, per-item p50/p95/max, bytes moved, resident memory at exit **and the delta from entry**.

Multiple exits mean multiple boundary lines — one per return path. A single summary line at the bottom of the function is wrong, because the paths that matter most are the ones that never reach it.

```
[Debug] [mti-fuse] gen-queue ENTER dir="/photos/2024" depth=142 target=256px budget=90ms rss=58.1MB
[Debug] [mti-fuse] gen-queue EXIT exit_trigger=budget-exhausted processed=120/142 tiers={1:98,2a:22}
        failed=0 deferred=22 elapsed=1.84s p50=9.1ms p95=14.2ms max=41.0ms bytes=3.1MB rss=61.4MB (+3.3MB)
[Debug] [sidecar] run_manual_cleanup ENTER path="/photos/a.jpg" input="/photos/.mti/recovery/a_g5composite.jpg"
        output="/photos/a_g5composite_enhance.jpg" script="fast_pass.sh"
```

**This pair is the primary diagnostic surface for hot-path work.** It is not a summary of what Trace would have told you — it is what you are meant to read *instead of* enabling Trace. Design it to be sufficient on its own.

### Error — Happy-Path Deviation

Any point where the expected (happy) path cannot proceed must emit **at least one Error-level log line** with enough information to understand WHY it failed. This includes:
- Subprocess spawn failures (with the OS error)
- Non-zero exit codes from child processes (with the status code and, when available, stderr)
- File/DB/IPC operations that return errors
- Unexpected state (missing parent directory, no file stem, null path)

**Placement**: At the point of failure, before any early return or fallback.

**Content**: The operation that failed, the error value, and enough context to identify the specific call site (function name, target path).

```
[Error] [sidecar] run_manual_cleanup: failed to spawn fast_pass.sh: No such file or directory (os error 2)
[Error] [sidecar] run_manual_cleanup: fast_pass.sh exited with status 127 for /photos/a.jpg
[Error] [sidecar] run_recover: db.clear_fuse_thumbnail failed for md5=abc123: database is locked
```

### Trace — Last Resort, Because It Perturbs What It Measures

Trace is reserved for **hot paths** — code running per-item in a tight loop, per-frame, or per-event at high frequency (per-thumbnail serve, per-FUSE op, per-pixel decode). Anything firing at that frequency belongs at Trace and nowhere else.

**Trace is a last resort, not a fallback.** Enabling it does not observe a hot path — it *deforms* it. A format, a throttle lock, and a write per event is a first-order cost on any latency-governed path; at a millisecond-scale budget and tens of events per second it changes the timings, the ordering, and sometimes the bug itself. **"Just turn on Trace" is therefore never a valid answer to a hot-path question**, and a design that depends on it has misplaced its instrumentation. The Debug scope boundary must stand on its own; Trace is what you reach for only when the boundary accumulators prove something is wrong and cannot say what.

Consequences:
- Trace lines are NEVER added speculatively — only for a demonstrated need (a proven bug, a regression, unexplained behaviour on that path).
- A hot-path type is never on by default, and never left on after the investigation that justified it.
- Whatever a Trace line would have summarised belongs in the enclosing scope's accumulators, where it costs an integer add instead of a write.
- Demoting a site to Trace is only complete once the enclosing boundary carries the aggregate that site was informally providing. Demotion without that step trades spam for blindness.

**Placement**: Only inside loops, callbacks, or handlers firing at high frequency, where Debug logging would cause backpressure or log flooding.

**Anti-pattern**: Do NOT use Trace for one-shot user actions, button clicks, or subprocess lifecycle events. Those are Debug or Info.

### Summary: Decision Table

| Situation | Type | Example |
|-----------|------|---------|
| User clicked a button / action dispatched | Info (outcome) + Debug (context) | "Enhance completed", "run_manual_cleanup ENTER path=... input=..." |
| Queue / batch / handler **entry** | Debug | "gen-queue ENTER dir=... depth=142 budget=90ms rss=58.1MB" |
| Queue / batch / handler **exit — each return path** | Debug (+ Info if user-visible) | "gen-queue EXIT exit_trigger=budget-exhausted processed=120/142 elapsed=1.84s p95=14.2ms rss=+3.3MB" |
| Per-item loop iteration (thumbnails, directory walk, FUSE op) | Trace (last resort — prefer the enclosing scope's accumulators) | "serve thumbnail for hash=abc123" |
| Subprocess failed to spawn | Error | "failed to spawn fast_pass.sh: ..." |
| Subprocess exited non-zero | Error | "fast_pass.sh exited with status 127" |
| File/DB/IPC operation failed | Error | "FIFO write failed: broken pipe" |
| Unexpected null/missing state causing early return | Error | "path has no parent directory, aborting" |
| Owned resource acquired / released (child, mount, socket, lock, FIFO) | Info | "face engine warm and ready elapsed=812ms", "unmounted ~/.cache/thumbnails" |
| **Owned resource died without being asked** | **Error** (requires a supervisor — nobody's call site) | "OWNED CHILD DIED UNEXPECTEDLY pid=... role=face-engine status=killed(SIGKILL) alive_for=0.004s" |
| Daemon startup | Info (mandatory block) | CLI args, config, env vars |
| Daemon exit — normal, signal, or panic | Info (Error if abnormal) | "exit cause=SIGTERM uptime=4h12m owned={mount:released, children:2 reaped}" |
| Log sink rotated / crossed size threshold / unopenable | Info / Error | "rotated mti-fuse.log → .log.0 (729MB, gzip queued)" |
| Configuration change at runtime | Info | "Log level changed to 7" |

## Timing Fields — logs as a live perf sensor (P7 corollary)

INFO-level logs of discrete operations (a scan, a decode batch, a serve, a
flush, a startup phase) carry the operation's **elapsed duration** as a
standard field — `... done files=142 elapsed=1.84s` — so any run with Info
enabled doubles as a perf-regression sensor: a regression shows up in the
ordinary log stream immediately, in flight, not in a later dedicated benchmark.
Where a stable expectation exists (recorded baseline), a breach beyond the
±30% band is logged at Error level with both actual and expected values.
Timing fields follow the same context contract as every other field: the
identifier, counts, and phase that make the number attributable on its own.

**Aggregate at the boundary, never per item.** The Info receipt carries the
operation's headline duration; the *distribution* — p50/p95/max, per-category
counts, bytes, memory delta — lives on the Debug scope-boundary exit line. Per
item timing lines are Trace and are not a perf sensor at all, because enabling
them changes the timings they report. One boundary line with percentiles is a
strictly better regression detector than N unaggregated lines: a breach is one
number against a band, rather than something a reader must aggregate by eye.
