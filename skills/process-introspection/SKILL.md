---
name: process-introspection
description: Read BEFORE trying to get a stack trace, syscall trace, heap view or core dump out of a running, hung, or crashed process — and BEFORE concluding that a debugger "worked" or that a process "produced no core". Covers ptrace as the attach prerequisite, Yama ptrace_scope and its workarounds, PR_SET_PTRACER, the in-process backtrace interfaces, the /proc interfaces that need no attach at all, core-dump plumbing, and which tool to reach for given live / hung / crashed / already-gone.
---

# Getting Evidence Out of a Live Process (AUR P9/P11/P17/P20)

Scope boundary: `audit-methodology` owns the **planned** pass — the differential
syscall-tracing audit, the fan-out read, the guarded harness. This skill owns the
**obstructed** case: you have a process, you need to see inside it, and the
operating system is refusing. Those refusals are configuration, not code, so no
amount of reading the source finds them — which is why they burn hours per
occurrence until the toolkit is written down once.

## 1 · The failure mode this skill exists for: exit 0, captured nothing

Every tool in this area can fail while reporting success.

- A debugger refused an attach and **still exits 0**, having written a file that
  contains headers and no frames.
- A core-dump handler ran, consumed the core, and stored it somewhere the
  operator does not know to look — so "no core was produced" is indistinguishable
  from "a core was produced and taken".
- A backtrace succeeded structurally and resolved every frame to `??`, because a
  linker flag was missing. The trace is present. It says nothing.

**So never assert on exit status in this domain. Assert on content.** A capture
step checks for a frame line (`#0 `), a non-empty symbol, a thread count — the
specific artifact you came for. This is AUR P20 from the operator's side: a call
that answers on success and returns silently on refusal has broken its own
contract, and here the tools do it by design.

Corollary: **record which rung of the ladder produced the artifact.** "Captured
via in-process handler" versus "captured via privileged attach" is one word that
saves the whole investigation next time (P17).

## 2 · First: what `/proc` gives you with no attach at all

Reach here **before** any debugger. None of it requires ptrace, so none of it can
be refused by the mechanisms in §3–§6, and it answers most questions outright.

| Interface | Answers |
|---|---|
| `/proc/PID/status` | `State`, `Threads`, **`TracerPid`** (already traced, and by whom), `VmRSS`, signal masks |
| `/proc/PID/stat` | machine-readable state, scheduling, fault counts |
| `/proc/PID/wchan` | the kernel symbol a blocked task is waiting in — the whole diagnosis for `D` state |
| `/proc/PID/stack` | kernel-side stack (needs privilege); pairs with `wchan` |
| `/proc/PID/syscall` | the syscall in progress and its arguments |
| `/proc/PID/maps`, `smaps`, `smaps_rollup` | mappings, per-mapping RSS, growth locus |
| `/proc/PID/fd/`, `fdinfo/` | what is open, and offsets — who is on the other end |
| `/proc/PID/task/` | per-thread everything above; a hung *thread* hides behind a healthy process |
| `/proc/PID/limits` | the ceiling being hit, including core size |
| `/proc/PID/environ`, `cmdline`, `exe`, `cwd` | how it was started and from what binary |

**The state letter is the triage.** `R` is running — a busy loop or a shutdown path
ignoring a signal. `D` is blocked in the kernel and `wchan` names on what. `S`,
`Z`, `T` are sleeping, unreaped, stopped. A 0%-CPU blocked process and a spinning
one are different defects; do not begin with a debugger when one character
separates them.

**Capture this before reaping anything.** The evidence that distinguishes a hung
shutdown from a kernel-wedged task is destroyed by the kill (P8).

## 3 · ptrace is the prerequisite, and a live daemon fails it

Attaching, reading another process's memory, `gcore`, and most of what a debugger
does all pass a **`PTRACE_MODE_ATTACH` access-mode check**. That check consults
credentials, whether the target is dumpable, and any enabled security module.

The check is not specific to the `ptrace()` call — several unrelated kernel
interfaces perform it, which is why an operation that looks like a plain read can
fail for "debugger" reasons.

## 4 · Yama `ptrace_scope` — read it, never assume it

Read the value first; it is one file and it decides everything downstream:

```
cat /proc/sys/kernel/yama/ptrace_scope
```

| Value | Meaning | Consequence for attaching to a daemon |
|---|---|---|
| `0` | classic permissions | works |
| `1` | **restricted ptrace** (the common default) | you may only trace a **descendant** — or hold `CAP_SYS_PTRACE`, or be declared by the target (§5) |
| `2` | admin-only attach | `CAP_SYS_PTRACE` only |
| `3` | no attach, **and the value cannot be changed back** | nothing attaches; reboot to recover |

**At `1`, the common case is already lost.** A long-running service is nobody's
descendant — it was started by an init system, not by your shell — so an attach
from your terminal is refused even though both run as you. This is the single most
common obstacle in this whole area, and it presents as a mysterious permission
error or as §1's silent empty capture.

Two further traps: creating a **user namespace** effectively removes Yama's
protection at values `1` and `2`, so a result inside a container does not
generalize; and raising the value requires `CAP_SYS_PTRACE`, so tightening is easy
and loosening is not.

Temporarily relaxing it is a **system-wide** change affecting every process, so
prefer §5 or §6 when the target is yours to influence.

## 5 · `PR_SET_PTRACER` — the target declares who may attach

Meaningful only when Yama is at mode `1`. The target calls:

```c
prctl(PR_SET_PTRACER, pid);              /* that PID may attach, as if an ancestor */
prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY); /* Yama's restriction lifted for me */
prctl(PR_SET_PTRACER, 0);                /* clear the declaration */
```

Each call **replaces** the previous declaration — it is one slot, not a list.

**This is the right fix for a program you control**: put it in the crash-handler
setup so every future run is attachable, with no system-wide change and no
privilege.

**And it is the trap that costs an afternoon.** The declaration lives in the
running process, so it only helps processes started from a build that already had
the call. A service that has been up for days predates your edit, refuses the
attach exactly as before, and the capture yields a process-info file with no
stack. Symptom: the fix works on a fresh run and fails on the one you actually
need. **Check the target's start time against the build that added the call**
before believing the attach path is broken.

## 6 · Escalation, for a process you cannot restart

`CAP_SYS_PTRACE` bypasses Yama at modes `1` and `2`. Practically: run the debugger
under a privilege-escalation helper appropriate to the desktop or host
(`pkexec` with a graphical polkit agent, `sudo` on a terminal host).

Reserve it for what genuinely needs it — heap inspection, a full backtrace of an
already-running daemon that must not be bounced. It is the rung that always works
and the rung that costs a password prompt and a privileged debugger inside your
process, so it is last, not first.

## 7 · In-process backtraces — the interface that cannot be refused

The most reliable stack is the one the process takes of **itself**, because no
attach happens and therefore nothing in §3–§6 applies. A dump that requires an
attach is a dump you may not get; a self-dump always arrives. Prefer this as the
primary crash path and treat the debugger as the optional layer.

glibc's `<execinfo.h>`:

- `backtrace()` fills an array of return addresses.
- `backtrace_symbols()` returns strings — **calls `malloc`, so it is unusable in a
  signal handler**.
- `backtrace_symbols_fd()` writes straight to a file descriptor and does not call
  `malloc`. This is the signal-handler-safe one.

Four caveats that turn a working trace into a useless one:

1. **Symbol names need `-rdynamic`** (GNU linker). Without it, frames resolve to
   addresses. `static` function names are never exposed regardless.
2. **Omitted frame pointers** — implied by any nonzero optimization level — can
   violate the unwinder's assumptions. Build the crash path with frame pointers
   retained.
3. **Inlined functions have no frame** and tail calls replace one frame with
   another, so the trace is not a source-level call list.
4. **The `malloc`-free guarantee has a hole**: both functions live in libgcc,
   which is loaded *dynamically on first use*, and that load usually allocates.
   For a signal handler to be genuinely safe, **force libgcc to load during
   startup** by taking one throwaway backtrace before you need it.

Pair the trace with the `/proc` facts from §2 — captured in-process, they cost
nothing and make the stack interpretable.

## 8 · Core dumps: three independent switches, each silently sufficient to lose it

"No core was produced" is almost never one cause. Check all three:

1. **The size limit.** `ulimit -c` is commonly `0`, which means no core, ever, with
   no message. Per-process truth is in `/proc/PID/limits`.
2. **The destination.** `/proc/sys/kernel/core_pattern` may be a filename *or*, if
   it begins with `|`, a **pipe to a handler program** — a distribution crash
   reporter or the init system's collector. The kernel then produces a core and
   hands it to that program, which may compress it, file it, or discard it by
   policy. Nothing appears in the working directory, and the process did dump.
   **Read this file before concluding anything about cores.**
3. **Dumpability.** A process that changed credentials is not dumpable by default;
   `/proc/sys/fs/suid_dumpable` and `PR_SET_DUMPABLE` govern it.

Retrieval depends on switch 2: if a collector owns the core, use *that
collector's* query tool, and if the tool is not installed, the cores are being
taken and are unreadable. **Confirm the retrieval path exists before you need a
core**, not during the incident.

To get a core from a **healthy** process without killing it, `gcore` — which is an
attach, so §3–§6 all apply.

## 9 · Tool matrix — what each needs, what each yields

| Tool | Needs an attach? | Yields | Note |
|---|---|---|---|
| `/proc` reads (§2) | **no** | state, wchan, maps, fds, kernel stack | always available; start here |
| in-process backtrace (§7) | **no** | user-space stack at the moment of the fault | requires building it in |
| `strace` | yes | syscalls, arguments, return values, latency | `-f` for children, `-T`/`-tt` for timing, `-s` for buffers, `-p` to attach |
| `ltrace` | yes | library calls | fragile on heavily optimized or static binaries |
| `gdb` | yes | full stacks, all threads, heap, variables | `thread apply all bt` is the one command worth memorizing |
| `gcore` | yes | a core from a live process | the process keeps running |
| `perf` | varies | sampled profiles, on-CPU stacks | answers "what is it *doing*", not "where is it stuck" |
| elfutils/gdb stack helpers | yes | one quick stack, no interactive session | often **not installed**; check before scripting against them |
| the crash collector's query tool | no | previously captured cores | tied to `core_pattern` (§8) |

**Check availability before writing a procedure around a tool.** Several
quick-stack helpers are packaged separately and are commonly absent; a runbook
naming a missing tool fails at the worst moment.

## 10 · The ladder, by situation

**Process is live and healthy, you want to know what it is doing.**
`/proc` first (§2), then `strace -p` for syscall behaviour or `perf` for on-CPU
cost. Attach only once §4 is known.

**Process is hung, 0% CPU.** Do **not** start a debugger. `State` and `wchan`
(§2) name what it is blocked on, per thread under `task/`. Record it *before* any
kill — the reap destroys it.

**Process is hung, 100% CPU.** A profile beats a stack: `perf` finds the loop.
`strace` may show a tight failing syscall.

**Process crashed and you control the code.** The in-process handler (§7) should
already have produced the stack. If it did not, that is the defect to fix — before
investigating the crash.

**Process crashed and you do not control the code.** §8's three switches decide
whether a core exists and who has it.

**Process is already gone and nothing was captured.** There is nothing to
introspect; the finding is that the path was uninstrumented. Add the in-process
handler and the `/proc` snapshot, then reproduce (P11, P17).

## 11 · What to fix, not merely work around

An attach that must be escalated by hand every time, a core that a collector
silently eats, a stack full of `??` — each is a **standing** cost, not an
incident. The durable fixes, in preference order:

1. **The program declares its own attachability** (§5) and **dumps itself** (§7),
   so evidence arrives without privilege or configuration.
2. **The capture step asserts on content** (§1), so a refusal is loud.
3. **The artifact names the rung that produced it** (§1), so the next occurrence
   is a lookup rather than a re-derivation.

Ground each mechanism against the manual pages and the live system rather than
from recall — `ptrace_scope`, `core_pattern`, `ulimit -c` and tool availability
are all machine-specific, all one command to read, and all routinely remembered
wrongly (P9, P12, P15).
