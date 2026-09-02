# CONSOLE_SPEC — the Console Manager

The Console tab of the AUR doc server. It answers one question, continuously and
without being asked twice: **what is this machine doing right now, and what is
the work I started doing to it?**

Three questions, and none is optional. A job's own output says where the job is;
it cannot say what the job is costing the machine, and neither can say what is
running when no job of yours is.

| Surface | Answers |
|---|---|
| Daemon list (§3) | what this machine is running |
| Session console (§1) and transcript (§2) | the work you started, and the conversation that drove it |
| System row (§4) and spool row (§5) | what it is costing — the machine, and the disk |

Status: normative where marked, owner-authored. Sections marked *open* are not
yet ruled and must not be implemented from inference.

---

## Conventions

**Normative, and they bind every surface here.**

**One clock: local time, 24-hour, zero-padded.** Every timestamp the console,
the manifest, the transcript and the served log lines display reads the same
way. A page that renders one time as `3:45:12 PM` and the line beside it as
`15:45:12` makes the reader convert before they can compare, and comparing is
the entire activity. Locale-default formatting is therefore not used: it follows
the browser rather than the rest of the page, which is exactly how the two
disagreed.

Dates carry `YYYY-MM-DD`. A duration is a duration, never a clock time.

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

`<time>` is when the command **started** — the moment capture opened its output
file, never its last-write time. A banner carrying two different clocks is
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
the command exits**. Retention is therefore a **tail over a held descriptor**,
taken while the command runs: capture opens the task file the moment it appears,
holds the descriptor, and appends each new byte to a per-command spool file. The
deletion of the source name is the terminator — the only one the harness gives —
answered by draining to end-of-file, one grace re-read, and closing both files.
The writer's descriptor outlives the unlink, so the held one does too, and bytes
written after the unlink drain like any others.

**A second hard link is the one design this section must never return to.** It
was the original mechanism — one syscall, zero copies — and the AI harness now
forbids it: from 2.1.252 the harness **refuses to consume any task output whose
link count is not 1** and can kill the running command when the count rises,
while its error text wrongly blames another session's deletion. Every command
raced the linker, so captures died intermittently wherever a server ran (proven
2026-09-01). The nlink-1 check is an unconfigurable boundary on the harness's
side, so capture adapts: the source file's link count never changes while the
file exists. The accepted cost — one descriptor per live command, each byte
copied once — is watched by the spool row's alert thresholds (§5).

**Liveness is the held descriptor.** A command whose source descriptor capture
still holds has not exited; one it holds nothing for has. The signal is
in-process state, and that scope is correct: after a server restart nothing
holds a descriptor, and not-live is the true answer for every command that
predates the restart. A file that merely stopped growing remains
indistinguishable from a slow command, and is never treated as ended.

Spool files stay **per command**, never one merged log: concurrent commands
would interleave on disk, start/duration/size come free from each file's own
stat, and the §1.2 cursor protocol is keyed per command — the unified stream id
was already retired once for resetting readers. The single continuous history
of §1 is synthesized at read time, unchanged.

### 1.4 Stated gaps

Capture runs only while the server does. A command that both started and
finished while the server was down left no trace, and a session that began
earlier has a hole at its front. **The reader is told where that hole ends** — an
unstated gap reads as "nothing happened", which is the failure this whole
surface exists to prevent.

---

## 2. The transcript

**Normative.** A session's conversation, opened as a pane beside its console.

A transcript is roughly four-fifths tool traffic, so it is a **reduction**, never
the raw record: turns keep their text, a tool call becomes one line naming what
it acted on, and results are **carried but collapsed** — for shell commands they
duplicate the session console, but for file reads and edits this is the only copy
that exists anywhere.

### 2.1 Attribution is what the record carries, not the envelope it arrived in

Tool results come back inside `user` records. That is the API's turn structure,
not a claim about who spoke, and reading the envelope attributes them to the
reader. Measured on one real transcript: **857 such records against 378 the
person actually wrote** — more than two thirds of everything labelled "You" was
a tool answering.

A record carrying only results is the **tool**. A record the person typed is
theirs. A record carrying both is theirs, because their words exist nowhere else.

### 2.2 A result-only turn costs one line

Its attribution, its time and its result count fold onto the collapsed summary,
so the row *is* the turn. Spending two lines to say "who" and then "what" makes
a transcript double-spaced by exactly the traffic §2 exists to compress.

### 2.3 It is a log, and behaves like one

It opens at the **end** and follows new turns as they arrive, under the same
hold-position control as every other pane. Opening a thousand-record session at
its oldest turn answers a question nobody asked.

---

## 3. The daemon list

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

### 3.1 Classification is by evidence, then by convention

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

### 3.2 Liveness is per channel

A daemon writes its product log long after the shell that captured its stderr
exited. Each channel therefore carries its own liveness, and the row is live if
either is.

**Held-open beats every clock.** A log nothing has open is finished no matter
how recently it was touched, and a log held open is live no matter how long it
has been quiet.

An **unlinked** descriptor does not count. A writer that missed a rotation keeps
a handle on the deleted generation; attributing that process to the file of the
same name on disk claims output is flowing into a file nothing writes to.

### 3.3 Identity

The row is named for the daemon: the process's own name where one is running,
the normalized filename stem otherwise. Two live processes sharing a name get
separate rows — folding two processes onto one line is a lie no column can
correct.

### 3.4 One-shot artifacts are not streams

Files written once by a tool that then exits — crash reports and the like — live
in the same directory but are not daemons and never appear as one. Age cannot
filter them: crashes happen daily, so recent ones sit permanently inside any
window, and on a real directory they outnumbered the genuine logs by better than
twenty to one. They are matched by a **project-declared prefix**, which respects
a contract rather than guessing, counted separately from quiet daemons — the two
are different facts and a reader cannot trust a count that conflates them — and
they remain reachable through the archive.

---

## 4. The system row

**Normative.** One line beneath the daemon list, sampled continuously.

### 4.1 Fields

Left to right, unlabelled — a row of named fields does not need a word saying
it is the system row:

| Field | Carries |
|---|---|
| `us` | **user** CPU share |
| `ni` | nice CPU share |
| `sy` | system CPU share |
| `wa` | iowait CPU share |
| `mem` | available / total |
| `swp` | available / total |
| `stor` | free |

`sy` sits beside `wa` deliberately. **System time and iowait are the pair that
move together when a machine is subtly in trouble**, and they are leading
indicators — they turn before the capacity fields do. Reading them apart is what
lets a rising kernel share go unnoticed.

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

### 4.2 Thresholds

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

Which level each field is judged at:

| Field | Blue at | Amber at |
|---|---|---|
| `us`, `ni` | the quiescent idle bound | the headroom idle bound |
| `sy` | 14 % | 30 % |
| `wa` | the quiescent iowait bound | the headroom iowait bound |
| `mem`, `stor` | the quiescent free bound | the reserve |
| `swp` | any upward move — see below | — |

`us`, `ni` and `sy` are the three shares that **add up to system load**, so the
level for `us` and `ni` is read off the aggregate — via idle, which is what the
gates are specified on — and applied to the contributors. A reader seeing `us`
amber is being told the machine is loaded, not that user time crossed a line of
its own.

**`sy` and `wa` are special cases, judged on their own bounds.** The OS is
efficient with the CPU, so kernel time is overhead rather than work: if `sy` is
high the machine is in trouble no matter how much headroom the gates believe
they have. `wa` is the same argument for storage. These are the only thresholds
not streamed from the sampler, because they are not admission questions.

**`swp` latches.** A swap-in is an EVENT, not a level: by the time anyone looks,
the figure has often settled back and the row would read clean for a machine
that has just paged. So **any upward move turns it blue and it stays blue** until
aggregate CPU load falls below **5 %** — while load is still up, whatever caused
the paging has not finished.

### 4.3 Freshness

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

**The row never goes blank once it has a reading.** A line stands until a newer
one replaces it — not until a poll gets round to it, and not because the list
around it was rebuilt. A monitoring row that blinks out is read as something
breaking, at the exact moment the reader looks at it, and the absence of news is
not news: a response that carries no sample, and the window before the first
sample, both leave what is held alone. Two consequences follow. A dropped
request takes **one automatic retry** rather than waiting out the next beat,
since a restarting server is the ordinary case. And because the server can only
state its own freshness in an answer that *arrives*, a held row states the
client's side too: past the same bound, it says how long it has been since a
sample landed. Held is never presented as current — that is the same rule as
`stale`, applied to the half the server cannot see.

### 4.4 One authority

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

## 5. The spool row

**Normative.** One line beneath the system row: what capture is costing on disk.

Shown **always**, not only when breached — a limit that appears at the moment it
is crossed teaches a reader nothing about where they normally sit, so the first
time they see it they cannot tell "unusual" from "Tuesday".

| Field | Carries |
|---|---|
| `total` | spooled bytes against the alert bound |
| `cmds` | files, one per foreground command inside the retention window |
| `min/avg/max` | the distribution of those file sizes |

`cmds` counts **commands, not sessions**, which is why it reaches the hundreds in
an ordinary week without anything being wrong.

The distribution is three figures because **one number cannot say whether it is
an outlier**: a 185 K maximum reads alarming beside nothing and unremarkable
beside a 7 K mean. That judgement is the whole point of showing this
continuously — it is what makes a runaway command obvious *while it is still
writing*.

**Nothing here caps anything.** Capture continues past the threshold and the row
goes amber, because silently dropping output is the exact defect the spool was
built to fix. The operator decides.

---

## 6. The pane

**Normative.** Every stream on this page opens into the same pane — a session
console, a transcript, a daemon's log, its stderr, its crash channel. The pane's
behaviour is therefore one contract, not five: a reader who has learned to
search one of them has learned to search all of them.

### 6.1 The buffer is bounded in bytes, not lines

A line cap bounds nothing. The runaway case is a command emitting enormous
single lines — an `ltrace`, a hexdump, a minified blob — where two thousand
lines is hundreds of megabytes and the tab dies holding them. Bytes are what
the DOM actually costs, so bytes are what is capped.

| Pane state | Retained |
|---|---|
| Live tail — streaming, not held, not searching | 2 MB |
| Held, searching, or quiet | 5 MB |

The tighter cap is **conditional rather than absolute** because trimming is
irreversible: a buffer that shrank because a stream briefly went live would
destroy exactly what the reader had scrolled back to. The moment a reader
holds position or opens a search they *are* reading the history, and the full
cap applies.

**The window states its own edge.** Capture never drops a byte — the file keeps
everything — but a pane is a window onto that file, and a reader who scrolls to
the top and finds their output gone reads it as data loss. The boundary says so
in the pane, for the same reason §1.4 states a capture gap: an unstated
boundary reads as "nothing happened".

### 6.2 Search highlights; it never hides

A search over a log must not remove the lines around a hit. The surrounding
lines are most of why the hit was worth finding — a filter that hides them
answers "where does this string occur" while destroying "what was happening
when it did". So every match in the buffer is marked, the current one is marked
differently, and nothing is removed.

**Defaults are regex and upward**, because a console is searched for something
that has *already gone past*. A fresh search therefore opens on the **last**
match rather than the first.

**Opening a search turns Hold on**, and closing it does not turn Hold off. A
tailing pane would scroll the hit out from under the reader on the next line of
output; and having spent a search to reach a position, the reader does not lose
it by closing the box. Hold is released by the Hold button, or by dragging down
to the last line, and by nothing else.

| Control | Behaviour |
|---|---|
| `Search` | Opens the find row, focuses the field, latches Hold |
| `regex` / `plain` | Mode. Plain escapes the query; the label states which is live |
| `▲` / `▼` | Direction. Governs what "next" means for every repeat control |
| Count | `position/total`, `no match`, or `bad pattern` — tabular figures, so advancing the position never reflows the row |

**A button is drawn as a button and a field as a field.** The two are the only
kinds of thing in the row, and which is which is carried by the drawing —
a raised surface against an inset one — never by punctuation in a label. A
control that needs parentheses around its text to read as pressable is styled
wrongly; every button in a pane head is the same object and takes the same
styling, whichever row it sits in.

**A rejected pattern is reported as a rejected pattern.** A half-typed regex is
the normal state while someone is typing one, so it marks the field rather than
raising an error — but it must never render as `no match`, which is a *wrong*
answer to the question asked rather than a vaguer one.

**Repeats.** Three pairs, and they agree: `Enter`/`Shift+Enter`, `n`/`N`, and
`Ctrl+F`/`Ctrl+Shift+F`. The first of each steps in the direction shown, the
second against it, and none of them moves the direction control — a repeat that
flipped the button would change what the *next* repeat means. Stepping wraps
rather than stopping at the end.

**Committing a query ends the typing.** `Enter` — and `Ctrl+F` pressed while
still in the field, which is the same act asked for with a different key —
cancels any pending debounce, steps, and hands focus to the pane body. `n` and
`N` are *text* while the query field holds focus, so a search that left focus
there left the reader with a find row whose repeat keys typed into it. Focus
moves only on a search that landed: a `bad pattern` or a `no match` is a query
still being worked on, and taking focus off it would make the reader click back
to fix a typo. The query is not cleared, so `/`, `?` or a click return to
editing it.

**Keystrokes are a convenience, never the only route.** `Ctrl+F` opens the row
in plain mode when it is shut and steps when it is already open — a second
press is a repeat, not a re-open. `/` and `?` open in regex mode searching down
and up respectively. A pane becomes the keystroke target by being clicked. The
host that embeds this page may claim any of these before they reach the
document — `Ctrl+F` is a binding in most editors — so **every keystroke has a
control in the find row that does the same thing**. A swallowed key costs
convenience and never capability.

### 6.3 Cost

Search is bounded so that a query cannot cost more than the buffer it runs
over. The hit list is built once per query and then navigated, rather than
re-scanned per step; typing is debounced, so a five-character query walks the
buffer once and not five times; and the number of hits is capped, with the
count reporting `+` when it truncates.

**Matching and marking are separated, because they are not the same cost.**
Measured at the 5 MB cap — 5,242,925 bytes, 44,598 lines — finding every hit
costs 77 ms, wrapping 5,000 of them costs 1,234 ms, and unwrapping them again
on the next query costs a further 478 ms. Matching is 4% of the work and the
DOM is the rest, so a query **matches everything and marks what is being looked
at**: the current hit and the lines around it before the step returns, the
reader's neighbourhood on idle time, and nothing beyond a fixed radius from
where they are. Stepping and scrolling both re-centre that region, so it
follows the reader rather than sitting where the search began.

Two consequences are normative. **The count is complete from the first frame**
— it is read off the match list, never off the marks, because a total that grew
as highlights appeared would be a wrong answer rather than a partial one. And
**the marks resident are proportional to the view, not to the buffer**: the
second query on a full pane pays to remove only what the first one drew, which
is what keeps a repeat search flat as the pane fills.

Matches are **wrapped, never rewritten**: the text of a line is preserved
exactly, so the byte accounting stays true and Hide Headers keeps working on a
line that contains a hit. Unwrapping restores the text exactly, and re-merges
only the lines that carried a mark — the whole-buffer normalize it replaced was
a walk over 5 MB to repair seams in a few hundred nodes.

*Open:* output arriving after a hit list is built is not in it. `Enter`
rebuilds; `n`/`N` deliberately do not, since a list that rebuilt underneath a
reader stepping through it would move their place. Whether a live pane should
re-mark on arrival is not ruled.

---

## 7. Scheduling

**Normative.** Interactive paths get out of the way of live users. The ladder is
set by *whether someone is actively waiting on the result*, not by what the work
costs.

Builds and background scans run at the bottom; daemons serving a live user run
near the top. Batch work runs niced without exception — headroom decides whether
work starts, niceness decides what it costs the person at the keyboard.

*Open:* the per-component tier values are owner-assigned and belong in the
project that defines the components, not here.

---

## 8. What a project supplies

**Normative.** The console is AUR infrastructure — one implementation, inherited.
A project does not fork it to change a path or a title; it declares what differs,
and a project that declares nothing still works.

| Setting | What it names |
|---|---|
| `project_name` | Label for the daemon group and the spec pages. Defaults to the project directory's own name. |
| `daemon_dir` | Where the project's daemons write. Defaults under the project name. |
| `registry` | Where console sessions register themselves. |
| `oneshot_prefixes` | Filename prefixes of one-shot artifacts (§3.4). Empty means the project has none. |
| `sysinfo_bin` | The single authority of §4.4. **Absent is a supported state** — the system row simply does not render. |
| `ai_context_exclude` | Directories under `.claude/` to keep out of the AI Context tab. |
| `header_links` | Documents pinned into the manifest header. |

Relative paths resolve against the **project root**, never the working
directory: the server is started from an editor task, a shell and a keybinding,
and none of them agree on a cwd.

This is a fixed set of values, deliberately **not** an extension mechanism.
Nothing today needs to add a tab or a route, and a plugin system with one
consumer has no second opinion to keep it honest.

---

## 9. Open — owner to author

- Filtering semantics for the merged history (scope, syntax). The
  hides-or-highlights half is **no longer open**: §6.2 rules that a search
  highlights and never removes, and a filter that contradicted its own pane's
  search would be two vocabularies for one gesture. What remains open is
  whether the merged history wants a *filter* at all now that it has a search.
- **The one-shot artifact table.** §3.4 keeps crash reports out of the daemon
  list and reachable through the archive, which is correct but not a home: on a
  real directory that is ~495 files and 121 MB behind one archive toggle. The
  agreed shape is a table of its own — binary and command, pid, trigger reason,
  time, size, age, with multi-select delete and click-to-open. Two rulings are
  already made and are not open: **retention is manual, never automatic**, and
  the reports are **text diagnostics, not core dumps**, so the surface must not
  imply a debugger. What remains open is where the table lives relative to the
  daemon list, and whether a project with no one-shot prefixes sees it at all.
