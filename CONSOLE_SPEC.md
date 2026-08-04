# CONSOLE_SPEC — the Console Manager

The Console tab of the AUR doc server. It answers one question, continuously and
without being asked twice: **what is this machine doing right now, and what is
the work I started doing to it?**

Three parts, and none is optional. A job's own output says where the job is; it
cannot say what the job is costing the machine, and neither can say what is
running when no job of yours is. The **daemon list** is what the machine is
running, the **session console** is the work you started, and the **system row**
is what it is costing.

Status: normative where marked, owner-authored. Sections marked *open* are not
yet ruled and must not be implemented from inference.

---

## 1. The session console

**Normative.** One session, one console. Every command that ran in a session
appears in a **single continuous history in start order** — not a row of
per-command chips.

Per-stream chips are **removed, not demoted**. They were built to answer "keep
the output" and made the tab's real job worse: watching a build meant guessing
which of a growing row of buttons was the build, and a pane pinned to one
command showed nothing while another produced everything. A session that runs
two hundred commands produced two hundred buttons, which is not a monitoring
surface. There is no "isolate one command" affordance to preserve; filtering the
merged history is that affordance.

### 1.1 Command boundaries

The history is continuous, so the reader is told where one command begins and
ends. Two markers, and only these:

| Event | Marker |
|---|---|
| Command begins | `▶ <time>  <first non-empty line of its output>` |
| Command ends | `■ ended · <duration> · <size>` |

`<time>` is when the command **started** — the moment its output file was
linked, never its last-write time. A banner carrying two different clocks is
worse than one carrying none.

The first line stands in for the command text because the harness does not
record the command next to its task id. It is arbitrary program output on its
way to becoming UI, so it is neutralized **where it is built**: ANSI escapes
stripped, control characters collapsed, whitespace run together, truncated to
one line. A label sanitized only at the renderer is one consumer away from
corrupting the page, which has happened.

A boundary is drawn where the **source** changes, not per chunk — a command
writing steadily gets one banner, not one per poll.

### 1.2 Concurrency and cursors

Two commands running at once **interleave visibly**. Neither silently replaces
the other, and neither resets the reader's position.

The cursor is a byte offset **per command file, held by the client**. The server
keeps no per-reader state, so two browser tabs on one session cannot
desynchronize each other.

### 1.3 Retention

A foreground command's output file is appended to live and **unlinked the moment
the command exits**. Retention is therefore a second hard link, taken while the
command runs: the harness's unlink drops a link instead of the data, and because
the writer's descriptor still points at that inode, bytes written after the
unlink land in the spool too. No copy, no held descriptor, no read loop.

A file still carrying more than one link is a command that **has not exited**.
That is the end-of-command signal; a file that merely stopped growing is
indistinguishable from a slow command.

### 1.4 Stated gaps

Capture runs only while the server does. A command that both started and
finished while the server was down left no trace, and a session that began
earlier has a hole at its front. **The reader is told where that hole ends** — an
unstated gap reads as "nothing happened", which is the failure this whole
surface exists to prevent.

---

## 2. The daemon list

**Normative.** Above the sessions, one row per **daemon** — not per file.

A daemon writes on two channels and they are different things:

| Channel | What it is |
|---|---|
| `out` | what the daemon printed on stdout/stderr, redirected into a file by whoever launched it — a shell, a service unit, a test harness. The daemon does not know the file exists. |
| `log` | the file the daemon opened for itself and writes its levelled product log to. |

Both belong to one daemon, so they belong on one row, each opening its own pane.
Listing them as two unrelated rows sorted apart — which is what a file-keyed
list does — leaves the reader to work out that `<name>.log` and `<name>_err.log`
are the same program, and neither row can say which process wrote it.

### 2.1 Classification is by evidence, then by convention

**A descriptor number is evidence; a filename is a guess.** Where a process is
running, the kernel already knows the answer:

- Descriptors **1 and 2 are stdout and stderr by definition**, so a regular file
  on one of them is a captured stream *whatever it is named*.
- A `.log` held on any other descriptor is a log the process opened itself.
- Anything else held open — a database, a WAL, a lock — is not a channel. This
  is load-bearing rather than tidy: one observed daemon held ninety descriptors
  on its own database, which would have been the entire row.

This requires no product name, no path template and no cooperation from the
daemon. It is what `lsof` reads, without the subprocess.

**The descriptor must be writable.** Holding a log open proves ownership only if
the holder can write to it; without that test every reader becomes a daemon —
`tail -f` on a log would be listed as the process producing it, and so would the
doc server itself the moment a pane opened on one. The access mode comes from
the kernel's own `fdinfo` flags.

**Convention is the fallback, and only that.** A daemon that is not running has
no descriptors to read, so the filename is the only evidence left: `<stem>.log`
is the own log and `<stem>_err.log` — or `-err`, `.err`, `_stderr`, `_out`,
`_stdout` — is the captured stream, and both fold onto one row because they name
one daemon. Separators are normalized, because the two files frequently disagree
about them. A marker counts **only when a separator introduces it**, or
`layout.log` loses its tail and files itself under `lay`.

Evidence outranks convention wherever both apply: a file a process holds is
attributed to that process and to nothing else.

### 2.2 Liveness is per channel

A daemon writes its product log long after the shell that captured its stderr
exited. Each channel therefore carries its own liveness, and the row is live if
either is.

**Held-open beats every clock.** A log nothing has open is finished no matter
how recently it was touched, and a log held open is live no matter how long it
has been quiet.

An **unlinked** descriptor does not count. A writer that missed a rotation keeps
a handle on the deleted generation; attributing that process to the file of the
same name on disk claims output is flowing into a file nothing writes to.

### 2.3 Identity

The row is named for the daemon: the process's own name where one is running,
the normalized filename stem otherwise. Two live processes sharing a name get
separate rows — folding two processes onto one line is a lie no column can
correct.

### 2.4 One-shot artifacts are not streams

Files written once by a tool that then exits — crash reports and the like — live
in the same directory but are not daemons and never appear as one. Age cannot
filter them: crashes happen daily, so recent ones sit permanently inside any
window, and on a real directory they outnumbered the genuine logs by better than
twenty to one. They are matched by a **project-declared prefix**, which respects
a contract rather than guessing, counted separately from quiet daemons — the two
are different facts and a reader cannot trust a count that conflates them — and
they remain reachable through the archive.

---

## 3. The system row

**Normative.** One line beneath the daemon list, sampled continuously.

### 3.1 Fields

Left to right: `Sys` then

| Field | Carries |
|---|---|
| `sy` | system CPU share |
| `us` | **user** CPU share |
| `ni` | nice CPU share |
| `wa` | iowait CPU share |
| `mem` | available / total |
| `sw` | available / total |
| `st` | free |

The four CPU shares are `top`'s `%Cpu(s)` fields under `top`'s normalization —
percentages of total jiffies across all CPUs — so the row reads against a `top`
open beside it.

**Memory and swap report what is AVAILABLE, never what is used.** Available is
the number a scheduler admits on and the one that answers "can this machine take
more work". For memory that is the kernel's `MemAvailable`, never `MemFree`,
which excludes reclaimable cache and reads as near-exhaustion on a healthy
machine. Storage reports free, as `df` does.

Every share is a **delta across the sampling interval**, never a cumulative
counter. `/proc/stat` counts since boot: a single read reports the machine's
lifetime average and would call a box that has been hammered for ten seconds
"99 % idle" because it was idle all night.

A reading that cannot be taken is **omitted**, never reported as a zero it cannot
justify. The reader distinguishes absent from zero.

### 3.2 Thresholds

**Highlighting is against the admission thresholds the build system actually
applies**, streamed down beside the readings. Never a number typed into the
presentation layer — that would be a second definition of the policy, drifting
the moment a knob moved.

| Level | Meaning |
|---|---|
| amber | this field is why the gate would hold work back |
| blue | fine for work, not quiescent enough to **measure** on |

The second level is not decoration. A benchmark on a contended box does not
produce a weak number, it produces a wrong one, and a wrong one enters the
timing baselines.

Colour is not the only carrier: an amber field also goes semibold, so the row
survives a colour-blind reader.

### 3.3 Freshness

A row that keeps presenting a stale reading as current is the exact failure this
surface exists to stop. Three states, and the row says which it is in:

- **live** — sampling normally.
- **stale** — held past the freshness bound. Values still shown, flagged with
  their age, never presented as current.
- **unavailable** — the sampler is broken now and these numbers will never
  refresh. **The cause outranks the age**: naming what broke — an unbuilt or
  unconfigured sampler — is the reader's next step, where "stale 17s" is the
  symptom with the cause withheld.
  Any values still held ride along, so the reader can see *what* froze.

### 3.4 One authority

**Every number in this row, and every threshold it is judged against, comes from
one implementation.** A second set of numbers parsed out of `/proc` in the
presentation layer is a second definition of the machine's state.

That implementation is long-lived and streams, one process for the life of the
consumer. It is not a process per sample: the CPU figures are a delta between
two reads separated in time, which a short-lived invocation could only produce
by sleeping through the interval inside every poll.

The sampler **does not outlive the server it feeds**, on any exit path —
signalled, normal, or failed. An orphan sampling into a closed pipe is a process
nobody will reap; writing to that closed pipe is what ends it.

---

## 4. Scheduling

**Normative.** Interactive paths get out of the way of live users. The ladder is
set by *whether someone is actively waiting on the result*, not by what the work
costs.

Builds and background scans run at the bottom; daemons serving a live user run
near the top. Batch work runs niced without exception — headroom decides whether
work starts, niceness decides what it costs the person at the keyboard.

*Open:* the per-component tier values are owner-assigned and belong in the
project that defines the components, not here.

---

## 5. What a project supplies

**Normative.** The console is AUR infrastructure — one implementation, inherited.
A project does not fork it to change a path or a title; it declares what differs,
and a project that declares nothing still works.

| Setting | What it names |
|---|---|
| `project_name` | Label for the daemon group and the spec pages. Defaults to the project directory's own name. |
| `daemon_dir` | Where the project's daemons write. Defaults under the project name. |
| `registry` | Where console sessions register themselves. |
| `oneshot_prefixes` | Filename prefixes of one-shot artifacts (§2.4). Empty means the project has none. |
| `sysinfo_bin` | The single authority of §3.4. **Absent is a supported state** — the system row simply does not render. |
| `ai_context_exclude` | Directories under `.claude/` to keep out of the AI Context tab. |
| `header_links` | Documents pinned into the manifest header. |

Relative paths resolve against the **project root**, never the working
directory: the server is started from an editor task, a shell and a keybinding,
and none of them agree on a cwd.

This is a fixed set of values, deliberately **not** an extension mechanism.
Nothing today needs to add a tab or a route, and a plugin system with one
consumer has no second opinion to keep it honest.

---

## 6. Open — owner to author

- Filtering semantics for the merged history (scope, syntax, whether a filter
  hides or merely highlights).
- **The one-shot artifact table.** §2.4 keeps crash reports out of the daemon
  list and reachable through the archive, which is correct but not a home: on a
  real directory that is ~495 files and 121 MB behind one archive toggle. The
  agreed shape is a table of its own — binary and command, pid, trigger reason,
  time, size, age, with multi-select delete and click-to-open. Two rulings are
  already made and are not open: **retention is manual, never automatic**, and
  the reports are **text diagnostics, not core dumps**, so the surface must not
  imply a debugger. What remains open is where the table lives relative to the
  daemon list, and whether a project with no one-shot prefixes sees it at all.
