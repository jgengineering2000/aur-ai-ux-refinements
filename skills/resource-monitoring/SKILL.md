---
name: resource-monitoring
description: Read BEFORE modifying daemon discovery, log channel tracking, console stream liveness, procstats polling, or sysinfo in AUR server.py — the mtime-driven lifecycle model, active vs dormant state invariants, targeted descriptor tracking, close/exit rescan protocol, and waste hierarchy rules.
---

# AUR Resource & Daemon Monitoring (AUR P1/P2/P3/P9/P15/P16)

Scope boundary: `audit-methodology` owns planned audit passes; `process-introspection`
owns debugging stuck or refusing processes. This skill owns **runtime daemon discovery,
liveness tracking, log channel attribution, and system resource polling** within
`aur/server/server.py` and the doc server infrastructure.

---

## 1 · The Core Principle: Causal Precedence & Mtime-Driven Discovery

A daemon log file exists to record output. **The critical driver of monitoring is `mtime`
being updated.** We do not care who holds a file open until the file is actually written.

```
+-----------------------------------------------------------------------------+
|  WRONG (Inverted):                                                          |
|  Static Disk Files  --->  Exhaustive /proc Scan  --->  PID Discovery        |
|  (Repeated every 1s across all system FDs for quiet, unwritten files)       |
+-----------------------------------------------------------------------------+
|  RIGHT (Causal / Mtime-Driven):                                             |
|  mtime advances (write)  --->  Discovery Pass  --->  Targeted (PID, fd)     |
|  (Dormant files checked for mtime; /proc scanned ONLY upon active writes)    |
+-----------------------------------------------------------------------------+
```

### The Waste Hierarchy (AUR Principle 9)
- **The Negative Search Trap:** Proving that a file has *no* writer is an
  $O(\text{all system FDs})$ operation — inspecting all ~500 processes and ~5,000 open
  descriptors on the machine. Running that operation periodically on files that have
  not received any writes is pure waste.
- **Porting to C/Rust (`libaur.so`) was "making waste cheaper"** (200 ms $\to$ 20 ms).
- **Adding a coalescing timer was "sharing waste"** (1.5s interval).
- **The true architectural fix is ELIMINATION:** Dormant files that have not moved are
  **never** scanned in `/proc`.

---

## 2 · The State Machine & Lifecycle Transitions

Every candidate log file in `daemon_dir` occupies one of two states: **`DORMANT`** or
**`MONITORED`**.

```
  +--------------------------------------------------------------------------+
  |                                                                          |
  |                        [ DORMANT ]                                       |
  |             (Waiting for file mtime to change)                           |
  |             - No /proc scanning. Zero CPU overhead.                      |
  |             - Holds baseline_mtime (st_mtime_ns)                         |
  |                                                                          |
  +------------------------------------+-------------------------------------+
                                       |
                     mtime advances    |  (inotify IN_MODIFY or stat delta)
                     (file written)    |
                                       v
  +--------------------------------------------------------------------------+
  |                     [ DISCOVERY PASS ]                                   |
  |             - Query fduser_find_writers for this specific file           |
  |             - Early-exits in microseconds because writer is active       |
  +------------------------------------+-------------------------------------+
                                       |
              +------------------------+------------------------+
              | Writer found: (pid, fd)                         | No writer found:
              v                                                 | (one-shot exited)
  +---------------------------------------------------+         |
  |                   [ MONITORED ]                   |         |
  |   (Actively tracking known PID + specific fd)     |         |
  |   - Metric tick checks ONLY this (pid, fd):       |         |
  |       * kill(pid, 0) alive?                       |         |
  |       * /proc/{pid}/fd/{fd} matches file & write? |         |
  |   - Sample metrics via aur_get_proc_stats(pid)    |         |
  |   - Cost: ~15 µs per live process                 |         |
  +-------------------------+-------------------------+         |
                            |                                   |
              Process exits | or closes the monitored fd        |
                            v                                   |
  +---------------------------------------------------+         |
  |                 [ RESCAN PASS ]                   |         |
  |   - Stop looking at the closed PID                |         |
  |   - Scan to see if anyone else has it open:       |         |
  |       * Yes: Update monitored (pid, fd)           |         |
  |       * No: Update baseline_mtime                 |         |
  +-------------------------+-------------------------+         |
                            |                                   |
              No other      |                                   |
              writers open  +-----------------------------------+
                            |
                            v
               Back to waiting for mtime
                       [ DORMANT ]
```

### Invariant Table

| State | Conditions & Invariants | 1.0s Metric Tick Actions | Exit Condition |
|---|---|---|---|
| **`DORMANT`** | Zero monitored PIDs.<br>Holds `baseline_mtime`.<br>**Invariant:** NEVER scanned in `/proc` while `mtime == baseline_mtime`. | **Zero `/proc` work.**<br>Check if `mtime` advanced (inotify or fast `stat`).<br>Liveness reported as `false`; metrics empty. | `mtime > baseline_mtime`<br>$\to$ Trigger **Discovery Pass**. |
| **`MONITORED`** | Holds verified `(pid, target_fd)`.<br>Liveness reported as `true`.<br>**Invariant:** Polling touches ONLY this specific PID and fd. | 1. `kill(pid, 0)` check (~500 ns).<br>2. Check `/proc/{pid}/fd/{target_fd}` (~14 µs).<br>3. Read metrics via `aur_get_proc_stats`. | Monitored fd closed or PID exited<br>$\to$ Trigger **Rescan Pass**. |

---

## 3 · Detailed Transition Mechanics

### Phase 1: Dormancy (Zero-Cost Baseline)
- When a file has no active writers (such as a historical crash log or an idle service),
  it sits in `DORMANT` with its current `st_mtime_ns` recorded as `baseline_mtime`.
- As long as no write occurs, `mtime == baseline_mtime`.
- **No `/proc` scans occur.** The periodic 1-second `console_procstats()` tick bypasses
  all dormant files completely. Cost is $0.00\%$ CPU.

### Phase 2: Post-Write Discovery
- When an application writes to the log, `st_mtime` advances.
- The change is noticed either via an inotify event (`IN_MODIFY`) or when comparing
  `st_mtime` against `baseline_mtime`.
- **Now, and only now, do we look up the writer:**
  1. Call `fduser_find_writers` for this specific file.
  2. Because the application was just writing, the process and fd are actively open,
     allowing `libaur.so` to early-exit rapidly on the writer.
  3. **Writer found:** Record `(pid, target_fd)` and transition the file to `MONITORED`.
  4. **No writer found:** A short script ran, appended, and closed before the discovery
     pass. Update `baseline_mtime = current_mtime` and remain `DORMANT`.

### Phase 3: Targeted Descriptor Monitoring
- While in `MONITORED`, we know the exact `pid` and `target_fd`.
- On each periodic metric poll:
  1. Verify `os.kill(pid, 0)`.
  2. Verify `os.readlink(f"/proc/{pid}/fd/{target_fd}") == target_path` and descriptor
     flags contain write mode (`_fd_writable`).
  3. If valid, sample CPU and RSS metrics directly via `aur_get_proc_stats(pid)`.
- **Cost:** Exactly 1 `kill` syscall + 1 `readlink` + 1 `stat`. Total duration:
  **~15 microseconds**. Zero full-system `/proc` walking.

### Phase 4: Close / Exit and Final Rescan
- When the monitored process exits or closes `target_fd`:
  1. Stop tracking that PID.
  2. **Run an isolated rescan:** Check if any other process currently has this file open
     for writing (`fduser_find_writers`).
  3. **If another writer exists:** Adopt the new `(pid, target_fd)` and remain in `MONITORED`.
  4. **If no other writer exists:** Update `baseline_mtime = current_mtime` and transition
     back to `DORMANT`.
- Once back in `DORMANT`, no further scans occur until the file moves again.

---

## 4 · Real-World Edge Cases Handled Correctly

1. **Intermittent / Rotating Daemons:** An application that writes to a log once per day
   or once an hour sits in `DORMANT` for 23 hours and 59 minutes. It consumes zero CPU
   while idle. The moment it writes, it is discovered, monitored during execution, and
   cleanly retired to `DORMANT` when it closes its descriptor.
2. **Dead Crash Logs & One-Shots:** Crash logs from days ago (`*-crash.log`) will never have
   their `mtime` advance. Under this design, they are placed in `DORMANT` once and
   **never inspected in `/proc` ever again**.
3. **Multi-Writer Files:** If a secondary process or worker also writes to the log (e.g.
   separate stdout and stderr writers), the close-rescan pass explicitly checks whether
   anyone else has the file open before dropping monitoring, preserving the invariant that
   a file is only unmonitored when its last writer closes.
4. **Separation of Cadences:**
   - **Metrics Polling (1.0s):** Returns numeric readings only (`/procstats`). Touches
     only monitored PIDs. Must complete in $< 1\,\text{ms}$.
   - **Structural Cataloging (10.0s):** Rebuilds console groups and session lists
     (`/consoles`). Gathers static metadata.
   - **Log Streaming (`/console/read`):** Reads disk bytes directly from the file;
     independent of PID discovery.

---

## 5 · Performance Budget & Enforcement

- **Idle CPU Target:** `server.py` CPU usage must remain **$< 0.05\%$** during steady-state
  monitoring under `procstats-watch`. A reading of $\ge 0.5\%$ indicates an unmonitored file
  is leaking into periodic negative scans.
- **Syscall Footprint:** Periodic metric ticks must never call `opendir("/proc")` or
  `scandir("/proc")`. All metric gathering must be $O(\text{live PIDs})$ using direct
  descriptor paths (`/proc/{pid}/fd/{fd}`) and pure in-process C/Rust (`libaur.so`).

---

## 6 · Process Probing & Serialization Discipline

### Held-Descriptor Proc Stat Scanning (`aur_get_proc_stats`)
- **Problem:** Full process table sampling previously opened, read, and closed `/proc/<pid>/stat`
  for all ~560 PIDs every 2 seconds, consuming 1,700 syscalls and ~8.4 ms.
- **Invariant:** A Linux `/proc/<pid>/stat` file descriptor can be held open and rewound via
  `lseek(fd, 0, SEEK_SET)`. Subsequent `read()` calls return the latest CPU ticks and RSS pages.
  When the process terminates, `read()` returns an immediate error (`ESRCH`), cleanly signaling
  descriptor teardown.
- **Lifecycle:**
  1. `libaur.rs` maintains a sorted table `PROC_FD_CACHE` of `(pid, fd)`.
  2. For existing PIDs: rewinds via `lseek(0, SEEK_SET)` and reads without `openat` or `close`.
  3. For new PIDs: opens `/proc/<pid>/stat`, caches the descriptor, and parses metrics.
  4. For dead PIDs: closes descriptor and evicts from cache.
  5. Execution time dropped from **8.38 ms down to 4.2 ms** (2× faster, zero open/close churn).

### Zero-Allocation Serialization & HTTP Keep-Alive
- **Pre-Serialized Bytes Cache:** In `server.py`, `_PROCSTATS_CACHE` caches both Python dict
  (`data`) and pre-encoded UTF-8 bytes (`bytes`). Repeated requests within the 1.5s interval
  bypass `json.dumps()` and `.encode()` completely (serving cached bytes in $< 1\,\mu\text{s}$).
- **HTTP/1.1 Keep-Alive & TCP_NODELAY:** `Handler` runs with `protocol_version = "HTTP/1.1"`,
  sets `Content-Length`, and enables `TCP_NODELAY`. This eliminates TCP socket teardown,
  per-request thread destruction, and the 40ms delayed-ACK latency.
- **Client Connection Reuse:** Polling clients (such as `procstats_watch.py`) maintain a persistent
  `HTTPConnection` across 1.0s ticks, keeping one TCP connection and one server worker thread alive.

