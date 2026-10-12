// SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
//! Live machine readings, shared by every admission gate.
//!
//! One module so "how busy is this box" has a single answer, whatever is
//! asking — the compile wrapper, the test wrapper, or the benchmark wrapper.
//! They differ only in the thresholds they apply to these numbers, never in how
//! the numbers are obtained.

#![allow(dead_code)]

use std::io::Read;
use std::io::Write;

fn read(path: &str) -> Option<String> {
    let mut s = String::new();
    std::fs::File::open(path)
        .ok()?
        .read_to_string(&mut s)
        .ok()?;
    Some(s)
}

/// `MemAvailable` and `MemTotal`, in MB. MemAvailable rather than MemFree: it is
/// the kernel's own estimate of what can be handed out without swapping, which
/// is the question a gate is actually asking.
pub fn memory_mb() -> Option<(u64, u64)> {
    let s = read("/proc/meminfo")?;
    let mut avail = None;
    let mut total = None;
    for line in s.lines() {
        if let Some(r) = line.strip_prefix("MemAvailable:") {
            avail = r
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()
                .map(|k| k / 1024);
        } else if let Some(r) = line.strip_prefix("MemTotal:") {
            total = r
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()
                .map(|k| k / 1024);
        }
    }
    Some((avail?, total?))
}

/// `SwapFree` and `SwapTotal`, in MB.
///
/// Occupancy, deliberately, not an si/so rate. A rate answers "is it thrashing
/// right now"; occupancy answers "has this machine been under memory pressure",
/// which persists after the pressure passes and is the context a reader wants
/// beside iowait — swap that filled during a build is still telling you what
/// that build cost long after the paging stopped.
pub fn swap_mb() -> Option<(u64, u64)> {
    let s = read("/proc/meminfo")?;
    let mut avail = None;
    let mut total = None;
    for line in s.lines() {
        if let Some(r) = line.strip_prefix("SwapFree:") {
            avail = r
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()
                .map(|k| k / 1024);
        } else if let Some(r) = line.strip_prefix("SwapTotal:") {
            total = r
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()
                .map(|k| k / 1024);
        }
    }
    Some((avail?, total?))
}

/// Runnable tasks right now, from `/proc/loadavg`'s `running/total` field.
/// Instantaneous by design — the 1-minute average lags exactly when a build is
/// ramping, and would admit the whole ramp before reacting to any of it.
pub fn runnable() -> Option<u64> {
    read("/proc/loadavg")?
        .split_whitespace()
        .nth(3)?
        .split('/')
        .next()?
        .parse()
        .ok()
}

pub fn cores() -> u64 {
    std::thread::available_parallelism().map_or(1, |n| n.get() as u64)
}

/// Cumulative jiffies from `/proc/stat`'s aggregate `cpu` line.
///
/// Carries every field anything here reports plus the total they are shares of.
/// `user`, `system` and `nice` are kept rather than discarded because a monitor
/// wants the same numbers `top` prints, and re-parsing the same line elsewhere
/// to recover them would be a second definition of this machine's CPU state.
#[derive(Clone, Copy)]
struct CpuJiffies {
    user: u64,
    nice: u64,
    system: u64,
    idle: u64,
    iowait: u64,
    total: u64,
}

fn cpu_jiffies() -> Option<CpuJiffies> {
    let s = read("/proc/stat")?;
    let line = s.lines().find(|l| l.starts_with("cpu "))?;
    let v: Vec<u64> = line
        .split_whitespace()
        .skip(1)
        .filter_map(|f| f.parse().ok())
        .collect();
    if v.len() < 5 {
        return None;
    }
    // user nice system idle iowait irq softirq steal …
    Some(CpuJiffies {
        user: v[0],
        nice: v[1],
        system: v[2],
        idle: v[3],
        iowait: v[4],
        total: v.iter().sum(),
    })
}

/// CPU shares over one interval, in `top`'s normalization: percentages of total
/// jiffies across all CPUs, so the set sums to 100 and can be read against a
/// `top` open beside it.
#[derive(Clone, Copy)]
pub struct CpuPct {
    pub user: f64,
    pub system: f64,
    pub nice: f64,
    pub idle: f64,
    pub iowait: f64,
}

/// Holds the previous reading so a caller can take a delta WITHOUT sleeping.
///
/// The sleep used to live inside `cpu_idle_iowait_pct`, which welded *how to
/// sample* to *when to sample* and left only one usable shape: a process that
/// blocks for a window every time it wants a number. That is fine for a gate,
/// which runs once and exits, and impossible for a monitor sampling every few
/// seconds forever. Separating them costs nothing and both shapes fall out —
/// the one-shot below is this sampler with a sleep on the outside.
#[derive(Default)]
pub struct CpuSampler {
    prev: Option<CpuJiffies>,
}

impl CpuSampler {
    pub fn new() -> Self {
        CpuSampler { prev: None }
    }

    /// Shares since the previous tick, or `None` on the first call and whenever
    /// no jiffies have elapsed — never a zero it cannot justify.
    ///
    /// A DELTA, never the cumulative counter: `/proc/stat` counts since boot, so
    /// a single read reports the machine's lifetime average and would call a box
    /// that has been hammered for ten seconds "99 % idle" because it was idle
    /// all night.
    pub fn tick(&mut self) -> Option<CpuPct> {
        let cur = cpu_jiffies()?;
        let prev = self.prev.replace(cur)?;
        let dt = cur.total.checked_sub(prev.total)?;
        if dt == 0 {
            return None;
        }
        // Saturating rather than checked: a counter that appears to go backwards
        // (a CPU offlined between reads, a container's counters reset) yields a
        // zero share for that field rather than dropping the whole sample.
        let share = |c: u64, p: u64| (c.saturating_sub(p) as f64) * 100.0 / dt as f64;
        Some(CpuPct {
            user: share(cur.user, prev.user),
            system: share(cur.system, prev.system),
            nice: share(cur.nice, prev.nice),
            idle: share(cur.idle, prev.idle),
            iowait: share(cur.iowait, prev.iowait),
        })
    }
}

/// Percentage of CPU spent idle and in iowait over `window`.
///
/// The one-shot form every gate uses: seed the sampler, sleep, read. Callers
/// that already have a loop of their own should hold a [`CpuSampler`] instead
/// and tick it, rather than blocking a thread per reading.
pub fn cpu_idle_iowait_pct(window: std::time::Duration) -> Option<(f64, f64)> {
    let mut s = CpuSampler::new();
    s.tick();
    std::thread::sleep(window);
    s.tick().map(|p| (p.idle, p.iowait))
}

/// Is the machine quiescent enough to take a *measurement* on?
///
/// A benchmark run on a contended system does not produce a weak number, it
/// produces a wrong one — and a wrong number is worse than none, because it
/// enters the timing baselines and every later comparison inherits it. So this
/// bar is deliberately much stricter than the one for doing work, and callers
/// are expected to REFUSE rather than proceed when it cannot be met.
///
/// Owner's definition, 2026-07-31: ≥90 % aggregate CPU idle, ≥20 % RAM free,
/// <0.3 % iowait.
///
/// QUIESCENT throughout, never "quiet" — owner's ruling, because "quiet" reads
/// as a property of the OUTPUT (how much a job prints) rather than of the
/// MACHINE (how little it is doing). The CLI mode was renamed immediately; the
/// type, the function and the sample-count knob kept the rejected word for
/// another day, which is how a term that was explicitly ruled out survives one
/// layer below the surface everyone reads.
pub struct QuiescentSpec {
    pub min_idle_pct: f64,
    pub min_free_ram_pct: f64,
    pub max_iowait_pct: f64,
}

impl Default for QuiescentSpec {
    fn default() -> Self {
        QuiescentSpec {
            min_idle_pct: 90.0,
            min_free_ram_pct: 20.0,
            max_iowait_pct: 0.3,
        }
    }
}

/// One sample against the spec: `Ok(())` or the reason it failed, phrased so the
/// caller can log it verbatim (P10 — the line must say what happened, to what,
/// and why, without the running system).
pub fn check_quiescent(spec: &QuiescentSpec, window: std::time::Duration) -> Result<(), String> {
    let (idle, iowait) = cpu_idle_iowait_pct(window).ok_or_else(|| {
        "cannot read /proc/stat — refusing to assume the machine is quiescent".to_string()
    })?;
    let (avail, total) = memory_mb().ok_or_else(|| {
        "cannot read /proc/meminfo — refusing to assume the machine is quiescent".to_string()
    })?;
    let free_pct = (avail as f64) * 100.0 / total.max(1) as f64;

    if idle < spec.min_idle_pct {
        return Err(format!(
            "cpu idle {idle:.1}% < {:.0}% required",
            spec.min_idle_pct
        ));
    }
    if free_pct < spec.min_free_ram_pct {
        return Err(format!(
            "free ram {free_pct:.1}% ({avail} of {total} MB) < {:.0}% required",
            spec.min_free_ram_pct
        ));
    }
    if iowait > spec.max_iowait_pct {
        return Err(format!(
            "iowait {iowait:.2}% > {:.2}% allowed",
            spec.max_iowait_pct
        ));
    }
    Ok(())
}

/// Free space in MB on the filesystem holding `path`, via `statvfs(3)`.
///
/// Declared here rather than pulled from the `libc` crate: this package's
/// contract is no CRATE dependencies, so the launcher every binary execs
/// through builds instantly and runs on any baseline box. It already links
/// libc, as every Rust binary on this platform does, so declaring the one
/// symbol costs nothing and keeps the contract intact. The earlier version
/// shelled out to `df`, which was a subprocess per check and forced the disk
/// axis to be sampled far less often than the other two.
///
/// The struct layout is x86-64 Linux/glibc, which is the only target this
/// package builds for (its tier table is x86-64 throughout).
#[repr(C)]
struct StatVfs {
    f_bsize: u64,
    f_frsize: u64,
    f_blocks: u64,
    f_bfree: u64,
    f_bavail: u64,
    f_files: u64,
    f_ffree: u64,
    f_favail: u64,
    f_fsid: u64,
    f_flag: u64,
    f_namemax: u64,
    __spare: [i32; 6],
}

extern "C" {
    fn statvfs(path: *const u8, buf: *mut StatVfs) -> i32;
}

/// Available and TOTAL capacity, in MB — the pair, from one `statvfs`.
///
/// Both come from the same call because they are one reading of one filesystem:
/// taking them separately would let a reader see an available figure from one
/// instant against a total from another, and present the pair as if it were a
/// single measurement.
///
/// `f_bavail` rather than `f_bfree` — the blocks an unprivileged process may
/// actually use, which is what `df` reports and what a scheduler can admit work
/// against; `f_bfree` counts the root reserve nobody else can touch.
pub fn disk_mb(path: &str) -> Option<(u64, u64)> {
    let mut c = Vec::with_capacity(path.len() + 1);
    c.extend_from_slice(path.as_bytes());
    c.push(0);
    let mut st: StatVfs = unsafe { std::mem::zeroed() };
    // SAFETY: `c` is NUL-terminated and outlives the call; `st` is a correctly
    // sized zeroed struct that the kernel only writes.
    if unsafe { statvfs(c.as_ptr(), &mut st) } != 0 {
        return None;
    }
    let mb = 1024 * 1024;
    Some((
        st.f_bavail.saturating_mul(st.f_frsize) / mb,
        st.f_blocks.saturating_mul(st.f_frsize) / mb,
    ))
}

/// The disk axis of admission: WHERE free space is measured and HOW MUCH of it
/// must stay free once the job is running.
///
/// Measured on 2026-09-15 02:09: four sessions each admitted a build against
/// the previous policy — a 1 GB absolute reserve measured at rustc's cwd — and
/// `/` fell from 53 GB to 7 GB while every gate on the machine was, by its own
/// rule, right. Two things were wrong with the rule: the reserve was a fixed
/// number on a 722 GB disk (the RAM and core reserves had been percentages of
/// the machine since 07-31, for exactly the reason that the same policy has to
/// hold on 8 GB and on 64 GB), and the path was whatever directory cargo ran
/// rustc from rather than the tree the build was filling.
///
/// The path is INFERRED (AUR P16), never demanded: rustc's own `--out-dir` is
/// the deps directory this unit writes to; `CARGO_TARGET_DIR` is what make
/// exports for every other gate; the cwd is the rung that always exists.
/// `origin` records which rung answered, and the wait line prints it, so a
/// reserve measured against the wrong filesystem is a visible fact rather than
/// a puzzle (AUR P17: name the layer that acted).
pub struct DiskPolicy {
    pub path: String,
    pub origin: &'static str,
    /// Percentage of the filesystem's TOTAL capacity that must remain free
    /// after the job's own disk demand. `AUR_GATE_DISK_RESERVE_PCT`, default 5.
    ///
    /// Scope: every gate on every host, one number. Why inference cannot serve
    /// it: how much headroom the operator wants held for THEMSELVES — /tmp, the
    /// logs, the desktop's own writes, the next session's cold build — is not a
    /// property of the disk, and the disk cannot say what the person plans to
    /// put on it. 5 % is disk-guard.sh's 95 %-used warning line, so the gate
    /// starts waiting exactly where the guard starts shouting; on this host that
    /// is ~36 GB, which the 02:09 cascade would have hit at its second build.
    pub reserve_pct: f64,
    /// `AUR_GATE_DISK_RESERVE_MB`, when set: an absolute reserve that REPLACES
    /// the percentage — the override a wrong-1 %-of-the-time default needs.
    pub reserve_mb_override: Option<u64>,
}

/// The disk policy for one path, with the two knobs read the ONE way.
///
/// Hoisted out of `Policy::for_invocation` (2026-09-18) because the gate is no
/// longer the only caller: anything that writes a lot into a directory — the
/// enhance crate's stage-dump probes, which once filled 88 GB — has to stop at
/// the SAME floor the gate admits work against, and a second reading of
/// `AUR_GATE_DISK_RESERVE_PCT`/`_MB` elsewhere is exactly the fork that lets
/// the two disagree about where the floor is (AUR P3).
///
/// `origin` names the layer that chose `path`, so a reserve measured against
/// the wrong filesystem is a visible fact rather than a puzzle (AUR P17).
pub fn disk_policy_for(path: String, origin: &'static str) -> DiskPolicy {
    DiskPolicy::new(
        path,
        origin,
        env_pct("AUR_GATE_DISK_RESERVE_PCT", 5.0),
        std::env::var("AUR_GATE_DISK_RESERVE_MB")
            .ok()
            .and_then(|v| v.trim().parse().ok()),
    )
}

impl DiskPolicy {
    pub fn new(
        path: String,
        origin: &'static str,
        reserve_pct: f64,
        reserve_mb_override: Option<u64>,
    ) -> Self {
        DiskPolicy {
            path,
            origin,
            reserve_pct,
            reserve_mb_override,
        }
    }

    /// The reserve as an absolute figure: the override if given, else the
    /// percentage of the filesystem's total, read from the filesystem itself.
    /// The total is the one reading that does not move, so re-deriving it per
    /// call costs a `statvfs` and buys a policy that follows the path.
    pub fn reserve_mb(&self) -> u64 {
        if let Some(mb) = self.reserve_mb_override {
            return mb;
        }
        let total = disk_mb(&self.path).map_or(0, |(_, t)| t);
        (total as f64 * self.reserve_pct / 100.0) as u64
    }
}

/// Where the build is writing, and which rung of the ladder said so:
/// `--out-dir` from the compiler invocation, else `CARGO_TARGET_DIR` (passed
/// in, not read here, so a test can drive every rung without touching the
/// process environment), else the cwd.
pub fn infer_target_dir(
    args: &[std::ffi::OsString],
    cargo_target_dir: Option<String>,
) -> (String, &'static str) {
    if let Some(i) = args.iter().position(|a| a == "--out-dir") {
        if let Some(dir) = args.get(i + 1) {
            return (dir.to_string_lossy().into_owned(), "--out-dir");
        }
    }
    for a in args {
        if let Some(dir) = a.to_string_lossy().strip_prefix("--out-dir=") {
            return (dir.to_owned(), "--out-dir");
        }
    }
    if let Some(dir) = cargo_target_dir.filter(|d| !d.is_empty()) {
        return (dir, "CARGO_TARGET_DIR");
    }
    (".".into(), "cwd")
}

/// A byte count in MB rendered for a person: `6.8 GB`, `412 MB`.
pub fn human_mb(mb: u64) -> String {
    if mb >= 1024 {
        format!("{:.1} GB", mb as f64 / 1024.0)
    } else {
        format!("{mb} MB")
    }
}

/// What a job expects to consume. Supplied per invocation because demand is a
/// property of the JOB, not of the machine or the shell: a linker, a unit test
/// and a FUSE surface test that mounts and spawns a simulator have nothing in
/// common here, and one exported value covering all of them is wrong for most.
///
/// `defaulted` records which axes were guessed, so the event stream can say so
/// out loud — a default that reads like a measurement is worse than no estimate,
/// because it carries authority it has not earned.
#[derive(Debug, Clone, Copy)]
pub struct Demand {
    pub cores: u64,
    pub ram_mb: u64,
    pub disk_mb: u64,
    pub defaulted: bool,
}

impl Default for Demand {
    fn default() -> Self {
        Demand {
            cores: 1,
            ram_mb: 1024,
            disk_mb: 100,
            defaulted: true,
        }
    }
}

impl Demand {
    /// Parse `2C,1.5G,26MB` — ordered cores, RAM, disk. Any field may be
    /// omitted and takes its default; the order is what disambiguates RAM from
    /// disk, since both are byte quantities and a suffix alone cannot say which.
    pub fn parse(spec: &str) -> Demand {
        let mut d = Demand::default();
        let mut given = 0;
        for (i, raw) in spec.split(',').enumerate() {
            let t = raw.trim();
            if t.is_empty() {
                continue;
            }
            let num: String = t
                .chars()
                .take_while(|c| c.is_ascii_digit() || *c == '.')
                .collect();
            let Ok(v) = num.parse::<f64>() else { continue };
            let unit = t[num.len()..].trim().to_ascii_uppercase();
            given += 1;
            match i {
                0 => d.cores = v.ceil() as u64,
                1 => {
                    d.ram_mb = if unit.starts_with('G') {
                        (v * 1024.0) as u64
                    } else {
                        v as u64
                    }
                }
                2 => {
                    d.disk_mb = if unit.starts_with('G') {
                        (v * 1024.0) as u64
                    } else {
                        v as u64
                    }
                }
                _ => {}
            }
        }
        d.defaulted = given < 3;
        d
    }

    pub fn describe(&self) -> String {
        format!(
            "{}C {}M-ram {}M-disk",
            self.cores, self.ram_mb, self.disk_mb
        )
    }
}

/// The binding constraint given this job's demand, or `None` if there is room
/// on every axis. Headroom is what must remain AFTER the demand is met.
/// Admission bars for doing WORK (as opposed to taking a measurement).
pub struct WorkSpec {
    /// CPU that must remain idle once this job is running. Idle% measures real
    /// capacity, where a runnable count over-counts: a task blocked on I/O or a
    /// lock is scheduled but generating no load, so counting it refuses work
    /// the machine could actually do.
    pub min_idle_pct: f64,
    /// iowait ceiling. The axis most likely to bind in this project — thumbnail
    /// work is I/O-heavy, so a build competing with a FUSE scan shows low CPU
    /// and high iowait, which a CPU-and-memory check cannot see at all.
    pub max_iowait_pct: f64,
}

impl Default for WorkSpec {
    fn default() -> Self {
        WorkSpec {
            // 15% idle must survive the job. Owner's figure, 2026-07-31, from
            // the symptom rather than a model: the desktop lugged badly while a
            // build held the machine inside the older 8% bar. Combined with the
            // projected-demand subtraction below, this is the "<80% load plus
            // ~5% for the incoming job" rule — on a 20-core box one core is
            // that 5%.
            min_idle_pct: 15.0,
            // 0.8%, down from 3%. Owner ruling 2026-07-31 after measurement:
            // across 83 samples of a full Tier A build iowait never once
            // exceeded 0%, so a 3% ceiling was unreachable — a threshold that
            // cannot bind is not a safety margin, it is an axis that silently
            // does nothing. 0.8% sits above idle noise and well below the level
            // at which the disk has actually become the bottleneck.
            max_iowait_pct: 0.8,
        }
    }
}

/// How hard a process yields to the person at the keyboard — the whole project's
/// scheduling ladder, batch and non-batch alike.
///
/// THREE levels rather than one, because unprivileged niceness is a **one-way
/// ratchet**. `RLIMIT_NICE` is 0 here, so the ceiling `20 - rlim_cur` is 20 —
/// above every real nice value — and a process can raise its nice value but
/// never lower it again. Verified: a process sitting at nice 10 asking for 5
/// gets EPERM.
///
/// That single fact is why nothing upstream of a gate nices itself any more. A
/// spawner that nices its whole subtree has spent the range on everybody below
/// it: the leaf inherits the floor and cannot climb back. Leaving every spawner
/// at 0 keeps the full 0..19 available, and the gate, which is the last thing to
/// run before the work, picks the level. One-way travel is fine when it happens
/// once — but it does mean **a parent must sit at the highest priority any of
/// its children will ever need**, because the ladder can only be walked down.
///
/// The class is chosen by environment (`AUR_BATCH_CLASS`) and not per call site,
/// for the reason the ratchet implies: it must be INHERITED. `aur-gate` around a
/// stage and `rustc-gate` around every unit inside that stage have to agree, or
/// the inner gate asks for a level the outer one already made unreachable and
/// every compile reports a refusal it can do nothing about.
///
/// The axis is WHO IS WAITING, never how long the job takes (owner, 2026-08-02).
/// Duration is the tempting proxy and it is the wrong one: a six-tier `dist-pkg`
/// takes an hour and is still something a person typed and is waiting on, so the
/// whole build system — dev, prod and packaging alike — is `Normal`.
/// **A parent must sit at the highest priority any of its children will ever
/// need**, because the ladder can only be walked down. `daemon` serving at
/// `Serve` and forking generation workers that raise themselves to `Low` is
/// fine; the reverse is impossible.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchedClass {
    /// No active user at all: whenever-you-can work. Background corpus scans,
    /// characterisation, mining.
    Low,
    /// Somebody actively cares about the result. The default, and where the
    /// entire build system and test harness sit.
    Normal,
    /// Carries urgency, but is heavy enough that it must not be allowed to
    /// interfere with the desktop. `trackmetad` — users notice its work, and a
    /// burst of inotify updates must not lug the session.
    Priority,
    /// NOT batch. On the interactive path, serving a live user's application —
    /// `daemon` answering Nautilus, `daemon` answering the sidecar. It must beat
    /// every batch class outright and still yield to the desktop itself.
    ///
    /// Unreachable through `AUR_BATCH_CLASS`, deliberately: a daemon names this
    /// in code because nothing an admission gate admits is ever on the
    /// interactive path, and a build that could claim it would be able to
    /// outrank the thumbnailer it is compiling.
    Serve,
}

impl SchedClass {
    /// The class named by `AUR_BATCH_CLASS`, or `Normal`.
    ///
    /// An unrecognised name is reported and then treated as `Normal` rather than
    /// refusing: this is read inside `rustc-gate` and `cc-gate`, which cargo
    /// invokes hundreds of times per build, and aborting the build over a
    /// scheduling hint would trade a whole build for a typo. Reporting keeps the
    /// typo visible (P14's requirement is that the tool not silently
    /// *reinterpret*, and naming the token satisfies that).
    ///
    /// AN EMPTY VALUE IS UNSET, not a typo — and that distinction is load-bearing
    /// rather than pedantic. `export AUR_BATCH_CLASS` in a Makefile puts
    /// `AUR_BATCH_CLASS=` into every child's environment even when make has no
    /// value for it (verified 2026-08-02; an earlier probe that printed the
    /// variable was not decisive, because an unexported variable and an empty
    /// one both print as nothing). Treating that as a rejected token put a
    /// warning in front of every single compilation unit — hundreds per build,
    /// each one wrong. A variable set to nothing is how a shell says "no value";
    /// only a non-empty token nobody recognises is a mistake worth naming.
    pub fn from_env() -> SchedClass {
        match std::env::var("AUR_BATCH_CLASS") {
            Err(_) => SchedClass::Normal,
            Ok(raw) if raw.trim().is_empty() => SchedClass::Normal,
            Ok(raw) => SchedClass::parse(&raw).unwrap_or_else(|| {
                eprintln!(
                    "AUR_BATCH_CLASS=\"{raw}\" is not a batch class (low|normal|priority) — using normal"
                );
                SchedClass::Normal
            }),
        }
    }

    /// Parse a class name from the environment knob. `Serve` is absent on
    /// purpose — see its doc comment.
    pub fn parse(name: &str) -> Option<SchedClass> {
        match name.trim().to_ascii_lowercase().as_str() {
            "low" => Some(SchedClass::Low),
            "normal" => Some(SchedClass::Normal),
            "priority" => Some(SchedClass::Priority),
            _ => None,
        }
    }

    pub fn name(self) -> &'static str {
        match self {
            SchedClass::Low => "low",
            SchedClass::Normal => "normal",
            SchedClass::Priority => "priority",
            SchedClass::Serve => "serve",
        }
    }
}

/// Every admission threshold, its environment knob, and its default — in ONE
/// place.
///
/// Owner ruling, 2026-07-31: "these thresholds should only exist at a single
/// point or we are going to have trouble with maintenance." They were right, and
/// the drift had already started: `rustc-gate` and `aur-gate` each carried their
/// own copy of the RAM reserve ratio, the free-core ratio, the idle floor, the
/// iowait ceiling, the nice level, the report cadence, AND their own private
/// `env_num`/`env_pct` helpers. Eight duplicated constants across two files, so
/// every policy change was a two-file edit that nothing checked — and a
/// half-applied one would leave the compile gate and the test gate quietly
/// enforcing different policies, which is far worse than either value alone.
///
/// Defaults are proportions, not absolutes, so one policy holds on 8 GB and on
/// 64 GB. The absolute figures the owner gave (1 GB, 3 cores) were this host's
/// instance of the ratio, measured on 30.8 GiB and 22 cores.
pub struct Policy {
    pub reserve_mb: u64,
    pub free_cores: u64,
    pub disk: DiskPolicy,
    pub work: WorkSpec,
    /// Absolute nice values for the three batch classes, lowest priority first.
    /// Three knobs rather than one arithmetic base plus offsets: a derived level
    /// is a second definition of the same number, and §10 exists to stop exactly
    /// that.
    pub nice_low: i32,
    pub nice_normal: i32,
    pub nice_priority: i32,
    pub nice_serve: i32,
    pub report: std::time::Duration,
    pub poll: std::time::Duration,
    /// How long `cpu_idle_iowait_pct` samples for. Every caller must use the
    /// same window or two gates reading "idle%" would not mean the same thing.
    pub window: std::time::Duration,
}

/// Read a `u64` knob, falling back to `default`.
pub fn env_num(key: &str, default: u64) -> u64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

/// Read a percentage knob as `f64`. Deliberately not integer-parsed: the
/// quiescent bar wants `0.3` and the iowait ceiling `0.8`, and an integer parse
/// would floor both to `0` — a threshold that can never bind, failing silently
/// in the permissive direction.
pub fn env_pct(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.trim().parse().ok())
        .unwrap_or(default)
}

impl Policy {
    /// The policy for a gate that has no compiler invocation to read the target
    /// dir from: `CARGO_TARGET_DIR`, else the cwd (see `for_invocation`).
    pub fn from_env() -> Self {
        Self::for_invocation(&[])
    }

    /// The policy for a gate wrapping one compiler invocation `args`, whose
    /// `--out-dir` is the most precise answer to "which filesystem is this
    /// build filling". `AUR_GATE_DISK_PATH` overrides every inferred rung.
    pub fn for_invocation(args: &[std::ffi::OsString]) -> Self {
        let total_mb = memory_mb().map_or(32768, |(_, t)| t);
        let d = WorkSpec::default();
        let (path, origin) = match std::env::var("AUR_GATE_DISK_PATH") {
            Ok(p) if !p.is_empty() => (p, "AUR_GATE_DISK_PATH"),
            _ => infer_target_dir(args, std::env::var("CARGO_TARGET_DIR").ok()),
        };
        Policy {
            reserve_mb: env_num("AUR_GATE_RESERVE_MB", total_mb * 325 / 10000),
            free_cores: env_num("AUR_GATE_FREE_CORES", (cores() * 1364 / 10000).max(1)),
            disk: disk_policy_for(path, origin),
            work: WorkSpec {
                min_idle_pct: env_pct("AUR_GATE_MIN_IDLE_PCT", d.min_idle_pct),
                max_iowait_pct: env_pct("AUR_GATE_MAX_IOWAIT_PCT", d.max_iowait_pct),
            },
            // AUR_NICE_*, not AUR_GATE_NICE_*. Niceness is not a gate concern
            // that happens to be applied at a gate — it is the whole project's
            // batch policy, and the gates are merely the last process able to
            // set it. Two knob names for one policy is the split the
            // single-point rule exists to prevent.
            //
            // The values are the owner's, 2026-08-02. The ordering is what
            // carries the meaning, since with RLIMIT_NICE at 0 a job can only
            // ever be moved DOWN this list.
            //
            // These are the BATCH band. They are effective against everything
            // else in `app.slice` — which is where background daemons, the
            // build, the terminal and Nautilus all live, since `app.slice` does
            // not enable the cpu controller for its children and they therefore
            // share one CPU cgroup. They have NO effect on `gnome-shell`, which
            // sits in `session.slice`: two sibling cgroups at cpu.weight 100
            // apiece, and no nice value inside one changes what the other gets.
            // Protecting the compositor needs a cgroup weight, not a nice value
            // (verified against /sys/fs/cgroup, 2026-08-02).
            nice_low: env_num("AUR_NICE_LOW", 19) as i32,
            nice_normal: env_num("AUR_NICE_NORMAL", 16) as i32,
            nice_priority: env_num("AUR_NICE_PRIORITY", 11) as i32,
            // The non-batch level, and the only one above the batch band. Owner
            // 2026-08-02: the thumbnailer must "get out of the way of live users
            // like Nautilus itself, but still be responsive to the active
            // desktop" — so it sits between the desktop's 0 and the batch band's
            // 11. Note the gates never set this; it is claimed by the daemon
            // itself at startup.
            nice_serve: env_num("AUR_NICE_SERVE", 4) as i32,
            report: std::time::Duration::from_secs(env_num("AUR_GATE_REPORT_SECS", 10)),
            poll: std::time::Duration::from_millis(env_num("AUR_GATE_POLL_MS", 500)),
            window: std::time::Duration::from_millis(env_num("AUR_GATE_WINDOW_MS", 250)),
        }
    }

    /// The binding constraint for `demand` under this policy, or `None` to admit.
    /// Gates call THIS rather than assembling the argument list themselves —
    /// otherwise the seven-parameter call is one more thing to get inconsistently
    /// right in two places.
    pub fn blocker(&self, demand: &Demand) -> Option<String> {
        blocker_with_load(
            demand,
            self.reserve_mb,
            self.free_cores,
            &self.disk,
            &self.work,
            self.window,
        )
    }

    /// The absolute nice value for `class` under this policy.
    pub fn nice_for(&self, class: SchedClass) -> i32 {
        match class {
            SchedClass::Low => self.nice_low,
            SchedClass::Normal => self.nice_normal,
            SchedClass::Priority => self.nice_priority,
            SchedClass::Serve => self.nice_serve,
        }
    }
}

// `setpriority`/`getpriority`/`flock`/`futimens` are hand-declared rather than pulled from the
// libc crate: this crate is deliberately dependency-free so it builds instantly
// on a bare baseline target, and both are a stable part of the C ABI.
extern "C" {
    fn setpriority(which: i32, who: u32, prio: i32) -> i32;
    fn getpriority(which: i32, who: u32) -> i32;
    fn __errno_location() -> *mut i32;
    fn flock(fd: i32, operation: i32) -> i32;
    fn futimens(fd: i32, times: *const std::ffi::c_void) -> i32;
}
const PRIO_PROCESS: i32 = 0;
const LOCK_EX: i32 = 2;
const LOCK_UN: i32 = 8;

/// Serializes admission across concurrent compile gates via a touch file on tmpfs.
///
/// Follows the owner's touch-file pacing design:
/// 1. `flock(precedent.lock)`
/// 2. If `time - mtime < settle_ms`, sleep for the difference
/// 3. Caller waits until resource requirements are met
/// 4. `touch precedent.lock` (updating mtime via `futimens`)
/// 5. `funlock(precedent.lock)`
/// 6. Caller executes queued command
struct PrecedentGuard {
    lock_file: std::fs::File,
}

/// The tmpfs directory shared, short-lived admission-control state lives
/// under: `/dev/shm` when it is a writable directory, `/tmp` otherwise. Every
/// writer of such state — `PrecedentGuard`'s lock file, `GateState`'s per-pid
/// blocked-reason files — goes through this one function so the choice can
/// never fork between them (AUR P3); it was inline in `PrecedentGuard::acquire`
/// alone until `GateState` needed the identical answer.
fn tmpfs_base() -> &'static std::path::Path {
    let dir = std::path::Path::new("/dev/shm");
    if dir.is_dir() && std::fs::metadata(dir).is_ok_and(|m| !m.permissions().readonly()) {
        dir
    } else {
        std::path::Path::new("/tmp")
    }
}

impl PrecedentGuard {
    /// Private to this module — reachable only through `admit`, so a front
    /// end that wants to skip the lock cannot even name the type to try.
    ///
    /// `AUR_GATE_PRECEDENT_LOCK_TEST_OVERRIDE` exists ONLY for a test that
    /// deliberately holds a wait open (e.g. to prove `admit` does not touch
    /// the guard until the wait clears): without it, that test would flock
    /// the REAL machine-wide lock every `rustc-gate`/`cc-gate`/`aur-gate`
    /// paces against, and a leaked thread holding it open would hang every
    /// OTHER gate on the box — measured directly on 2026-10-01, where
    /// exactly that leaked the lock and SIGKILLed a sibling test mid-run.
    /// Production code never sets this, same carve-out as `gate_state_dir`'s
    /// own test override.
    fn acquire(settle_ms: u64) -> Option<Self> {
        if let Ok(dir) = std::env::var("AUR_GATE_PRECEDENT_LOCK_TEST_OVERRIDE") {
            let path = std::path::PathBuf::from(dir);
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            return Self::acquire_at(&path, settle_ms);
        }

        let base = tmpfs_base();
        let user = std::env::var("USER").unwrap_or_else(|_| "default".into());
        let lock_path = base.join(format!("aur-gate-precedent-{user}.lock"));
        // The real path may sit under `/dev/shm`; fall back to `/tmp` on the
        // same name if that directory is not writable here.
        Self::acquire_at(&lock_path, settle_ms).or_else(|| {
            let fallback =
                std::path::Path::new("/tmp").join(format!("aur-gate-precedent-{user}.lock"));
            Self::acquire_at(&fallback, settle_ms)
        })
    }

    /// The actual sequence against one resolved lock path: open (creating if
    /// needed), `flock(LOCK_EX)`, settle if touched within `settle_ms`.
    fn acquire_at(path: &std::path::Path, settle_ms: u64) -> Option<Self> {
        use std::os::unix::io::AsRawFd;
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .ok()?;

        // 1. flock ( precedent.lock )
        unsafe {
            flock(file.as_raw_fd(), LOCK_EX);
        }

        // 2. if mtime is time - mtime < settle_ms then sleep for the difference endif
        if let Ok(meta) = file.metadata() {
            if let Ok(mtime) = meta.modified() {
                if let Ok(elapsed) = std::time::SystemTime::now().duration_since(mtime) {
                    let settle = std::time::Duration::from_millis(settle_ms);
                    if let Some(diff) = settle.checked_sub(elapsed) {
                        std::thread::sleep(diff);
                    }
                }
            }
        }

        Some(PrecedentGuard { lock_file: file })
    }

    fn touch(&self) {
        // 4. touch precedent.lock
        use std::os::unix::io::AsRawFd;
        unsafe {
            futimens(self.lock_file.as_raw_fd(), std::ptr::null());
        }
    }
}

impl Drop for PrecedentGuard {
    fn drop(&mut self) {
        // 5. funlock ( precedent.lock )
        use std::os::unix::io::AsRawFd;
        unsafe {
            flock(self.lock_file.as_raw_fd(), LOCK_UN);
        }
    }
}

/// The directory every gate's blocked-state file lives under, inferred the
/// same way `PrecedentGuard` chooses its tmpfs — never configured, since the
/// answer is always derivable from the running system (AUR P16).
///
/// `AUR_GATE_STATE_DIR_TEST_OVERRIDE` exists ONLY for `gate_state.rs`'s
/// integration test: a real gate on the same machine writes into the shared
/// `aur-gate-<user>` directory concurrently with the test, and the test needs
/// an isolated directory to assert against without racing it. Production code
/// (the three gate binaries) never sets this variable and never reads it via
/// any other path.
pub fn gate_state_dir() -> std::path::PathBuf {
    if let Ok(dir) = std::env::var("AUR_GATE_STATE_DIR_TEST_OVERRIDE") {
        return std::path::PathBuf::from(dir);
    }
    let user = std::env::var("USER").unwrap_or_else(|_| "default".into());
    tmpfs_base().join(format!("aur-gate-{user}"))
}

fn json_escape(s: &str) -> String {
    use std::fmt::Write as _;
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out
}

fn json_unescape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut chars = s.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            match chars.next() {
                Some('"') => out.push('"'),
                Some('\\') => out.push('\\'),
                Some('n') => out.push('\n'),
                Some(other) => out.push(other),
                None => {}
            }
        } else {
            out.push(c);
        }
    }
    out
}

/// Splits one flat, single-level JSON object into its raw `key -> value`
/// tokens (string values unescaped, numeric values left as their literal
/// text) — sufficient for `GateState`'s fixed schema and nothing more; there
/// is no nesting in this file's own writer to parse back out.
fn parse_json_flat(text: &str) -> Option<std::collections::HashMap<String, String>> {
    let text = text.trim();
    let inner = text.strip_prefix('{')?.strip_suffix('}')?;
    let mut parts = Vec::new();
    let mut start = 0usize;
    let mut in_str = false;
    let mut esc = false;
    for (i, c) in inner.char_indices() {
        if esc {
            esc = false;
            continue;
        }
        match c {
            '\\' if in_str => esc = true,
            '"' => in_str = !in_str,
            ',' if !in_str => {
                parts.push(&inner[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    parts.push(&inner[start..]);

    let mut map = std::collections::HashMap::new();
    for part in parts {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        let mut kv = part.splitn(2, ':');
        let k = kv.next()?.trim().trim_matches('"');
        let v = kv.next()?.trim();
        let v = if v.len() >= 2 && v.starts_with('"') && v.ends_with('"') {
            json_unescape(&v[1..v.len() - 1])
        } else {
            v.to_string()
        };
        map.insert(k.to_string(), v);
    }
    Some(map)
}

/// One blocked gate's state, visible to anything reading `gate_state_dir()`
/// while this process waits — the file a monitor reads so "cargo idle" reads
/// instead as "rustc-gate pid N waiting to link k7_conditioning 34s, blocked
/// by disk 3.6 GB < reserve 36.9 GB" without opening this process's own
/// stderr (AUR P17).
///
/// Rewritten atomically (`<pid>.json.tmp` then `rename`) only on a window
/// where `blocked_by` or the rounded `waited_s` actually changed, so a long
/// stall on one unchanging reason costs no repeated tmpfs writes — the same
/// "only on change" discipline the gates already apply to their stderr
/// cadence. Nothing is written until the first `update()` call, so a demand
/// admitted immediately (the common case) never touches tmpfs at all.
///
/// Removed on `Drop` — normal admit, an early `?`-propagated error, or a
/// panic unwinding through it — so a live file always means a live wait. A
/// `kill -9`'d gate cannot run its own `Drop`, which is why every reader
/// (`read_gate_entries` here, and the AUR doc server's Python reader) prunes
/// by pid liveness rather than trusting the file's presence alone.
pub struct GateState {
    path: std::path::PathBuf,
    pid: u32,
    gate: String,
    verb: String,
    target: String,
    since: u64,
    demand: String,
    criteria: String,
    last: std::cell::RefCell<(String, u64)>,
    wrote: std::cell::Cell<bool>,
}

impl GateState {
    /// Begins tracking one blocked wait; call `update()` on every poll of the
    /// wait loop while `policy.blocker()`/`check_quiescent()` keeps returning
    /// a reason.
    pub fn new(gate: &str, verb: &str, target: &str, demand: &str, criteria: &str) -> Self {
        let pid = std::process::id();
        let path = gate_state_dir().join(format!("{pid}.json"));
        GateState {
            path,
            pid,
            gate: gate.to_string(),
            verb: verb.to_string(),
            target: target.to_string(),
            since: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_secs()),
            demand: demand.to_string(),
            criteria: criteria.to_string(),
            last: std::cell::RefCell::new((String::new(), u64::MAX)),
            wrote: std::cell::Cell::new(false),
        }
    }

    pub fn update(&self, waited_s: f64, blocked_by: &str) {
        let waited = waited_s.round().max(0.0) as u64;
        {
            let last = self.last.borrow();
            if last.0 == blocked_by && last.1 == waited {
                return;
            }
        }
        let Some(dir) = self.path.parent() else {
            return;
        };
        if std::fs::create_dir_all(dir).is_err() {
            return;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
        }
        let body = format!(
            "{{\"gate\":\"{}\",\"pid\":{},\"verb\":\"{}\",\"target\":\"{}\",\"since\":{},\
             \"waited_s\":{},\"blocked_by\":\"{}\",\"demand\":\"{}\",\"criteria\":\"{}\"}}\n",
            json_escape(&self.gate),
            self.pid,
            json_escape(&self.verb),
            json_escape(&self.target),
            self.since,
            waited,
            json_escape(blocked_by),
            json_escape(&self.demand),
            json_escape(&self.criteria),
        );
        let tmp = self.path.with_extension("json.tmp");
        if std::fs::write(&tmp, body).is_ok() && std::fs::rename(&tmp, &self.path).is_ok() {
            self.wrote.set(true);
            *self.last.borrow_mut() = (blocked_by.to_string(), waited);
        }
    }
}

impl Drop for GateState {
    fn drop(&mut self) {
        // Only ever wrote a file if `update()` actually landed one — an
        // instantly-admitted wait leaves nothing to journal or remove.
        //
        // Owner ruling, 2026-10-01: "If there's a logging gap that prevents
        // tracking what happened with the resource gate that in and of
        // itself is also a bug — always." This file was the ONLY record of
        // a wait that was ever blocked, and it vanished on every exit path —
        // exactly when a reader most needs it (a killed gate, the 02:09-style
        // cascade). The durable event log gets the final state BEFORE the
        // tmpfs file is removed, so the evidence survives the file that held
        // it.
        if self.wrote.get() {
            let (blocked_by, waited) = self.last.borrow().clone();
            log_event(
                &self.gate,
                "Info",
                &format!(
                    "wait-closed {} {} since={} waited={waited}s last_blocked_by=\"{blocked_by}\" demand={} criteria={}",
                    self.verb, self.target, self.since, self.demand, self.criteria
                ),
            );
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// How much of the machine must be free before `admit` lets a job start.
/// `Headroom` is "enough stays idle once this job is running" — the bar
/// every compile and link clears. `Quiescent` is the much stricter "the box
/// is doing nothing at all," which only a MEASUREMENT needs (see
/// `QuiescentSpec`'s doc comment). Only `aur-gate` offers `Quiescent`: a
/// compile or a link is work, never a benchmark taking a reading.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Headroom,
    Quiescent,
}

impl Mode {
    /// The name this mode is recorded under — in `GateState`'s `criteria`
    /// field and in the event log — so a reader never has to infer which
    /// bar a wait was held to.
    pub fn name(self) -> &'static str {
        match self {
            Mode::Headroom => "headroom",
            Mode::Quiescent => "quiescent",
        }
    }
}

/// Everything about ONE admission request that a caller supplies to
/// [`admit`]. `demand` is the only field meant to differ in SUBSTANCE
/// between `rustc-gate`, `cc-gate` and `aur-gate` — a compilation unit, a
/// translation unit and a FUSE surface test that mounts and spawns a
/// simulator have nothing in common on that axis, and everything in common
/// on every other field here. That is the whole reason this is one struct
/// and not three hand-assembled call sites (AUR P3).
pub struct AdmitSpec<'a> {
    /// The gate's own name — `"rustc-gate"`, `"cc-gate"`/`"cxx-gate"`, or
    /// `"aur-gate"` — the component every event-log line is attributed to.
    pub who: &'a str,
    /// What is about to happen: `"build"`, `"link"`, `"compile"`, or
    /// `aur-gate`'s own action (`"test"`, `"benchmark"`, …).
    pub verb: &'a str,
    /// What it is about to happen to: a crate name, a translation unit, a
    /// benchmark target.
    pub target: &'a str,
    pub demand: Demand,
    pub settle_ms: u64,
    pub mode: Mode,
    pub class: SchedClass,
    /// Whether `AUR_GATE_OFF=1` bypasses the wait for THIS call site.
    /// `rustc-gate`/`cc-gate` have always honoured it (hundreds of units per
    /// build, so an escape hatch matters); `aur-gate` never has — its own
    /// module doc never lists the knob, and it wraps a handful of whole
    /// stages rather than hundreds of units. Giving `aur-gate` the bypass
    /// now would be a CAPABILITY change, not a refactor — past this item's
    /// "structure and logging only" scope — so each caller states its own
    /// existing behaviour explicitly instead of silently inheriting a new
    /// one from the other two.
    pub allow_off: bool,
}

/// What a successful call to [`admit`] leaves the caller to act on.
/// Everything else — the precedent guard, the published wait state, the
/// event-log record — was fully handled inside `admit` and is already gone
/// by the time this comes back.
pub struct Admission {
    /// The nice value `admit` attempted to apply for `spec.class`.
    pub nice: i32,
    /// Whether it took. A refusal means something upstream already niced
    /// this process tree past the level requested (`set_priority`'s doc
    /// comment) — `admit` has already reported it (stderr + event log); this
    /// is for a caller that wants to act on it further.
    pub priority: Result<(), String>,
    pub waited_s: f64,
}

/// Admission control, start to finish, for one job: acquire the precedent
/// guard that paces concurrent gates, publish this wait's state, hold the
/// line until `spec.demand` clears `spec.mode`'s bar (reporting as it
/// holds), touch the guard, drop it, then apply `spec.class`'s scheduling
/// priority. EXACTLY `rustc-gate`'s sequence before this change — that was
/// the correct one, this is now the only one
/// (`.claude/briefs/gate-one-sequencer-2026-10-01/BRIEF.md`).
///
/// A front end that wants to skip a step cannot: `PrecedentGuard` is
/// private to this module, so nothing outside `admit` can even name the
/// type, and the wait loop lives here, not at any call site. Three
/// hand-assembled copies of this sequence is how `aur-gate` shipped without
/// ever taking the lock, and stacked unpaced admissions until the OOM
/// killer took the owner's editor on 2026-10-01.
pub fn admit(spec: &AdmitSpec, policy: &Policy) -> Admission {
    let waited_s = if spec.allow_off && std::env::var("AUR_GATE_OFF").is_ok() {
        log_event(
            spec.who,
            "Info",
            &format!(
                "skip {} {} AUR_GATE_OFF=1 demand={}",
                spec.verb,
                spec.target,
                spec.demand.describe()
            ),
        );
        0.0
    } else {
        admission_wait(spec, policy)
    };

    // SchedClass work yields to the desktop. Applied LAST, after admission —
    // on both the waited path and the `AUR_GATE_OFF` path, which is why it
    // lives here once rather than at the tail of each branch above. Niceness
    // is inherited across fork and exec, so setting it here covers whatever
    // the caller execs or spawns next without touching a single call site
    // downstream.
    let nice = policy.nice_for(spec.class);
    let priority = set_priority(nice);
    match priority {
        Ok(()) => log_event(
            spec.who,
            "Info",
            &format!(
                "class {} {} {} nice={nice}",
                spec.verb,
                spec.target,
                spec.class.name()
            ),
        ),
        Err(ref why) => log_event(
            spec.who,
            "Error",
            &format!(
                "batch class {} {} {} {why}",
                spec.verb,
                spec.target,
                spec.class.name()
            ),
        ),
    }

    Admission {
        nice,
        priority,
        waited_s,
    }
}

/// The wait itself: steps 1 through 5 of `admit`'s doc comment, minus the
/// scheduling class — which is common to both the waited and the
/// `AUR_GATE_OFF` path, so it lives in `admit` once rather than being
/// duplicated into this helper too.
fn admission_wait(spec: &AdmitSpec, policy: &Policy) -> f64 {
    // A default that reads like a measurement carries authority it has not
    // earned. Said up front, before the wait, not in a summary once the job
    // is already running — the operator needs to know which kind of number
    // they are looking at while the gate is still holding.
    if spec.demand.defaulted {
        log_event(
            spec.who,
            "Info",
            &format!(
                "note {} {} demand not fully specified — defaulting to {} (no precedent recorded)",
                spec.verb,
                spec.target,
                spec.demand.describe()
            ),
        );
    }

    // 1. flock(precedent.lock), settling if another gate touched it recently.
    let guard = PrecedentGuard::acquire(spec.settle_ms);
    let start = std::time::Instant::now();
    let mut last_report = std::time::Instant::now();
    // 2. Publish this wait's state before the first poll, not after — a
    // demand admitted instantly never touches tmpfs at all (GateState's own
    // doc comment), so publishing early costs nothing on the common path.
    let gate_state = GateState::new(
        spec.who,
        spec.verb,
        spec.target,
        &spec.demand.describe(),
        spec.mode.name(),
    );
    let need_streak = env_num("AUR_GATE_QUIESCENT_SAMPLES", 5);
    let mut streak = 0u64;

    // 3. The wait loop, reported on `policy.report`'s cadence. It never
    // times out: a gate that quietly proceeds has discarded the guarantee it
    // exists for, and one that refuses has discarded the work.
    loop {
        let blocker = match spec.mode {
            Mode::Quiescent => {
                // Sustained, not instantaneous: one lucky sample between two
                // bursts is exactly how a contended machine passes a
                // quiescence check. The 500ms window is `check_quiescent`'s
                // own sampling window, not `policy.window` — that field
                // governs the headroom path's CPU sample instead, and the
                // two are not interchangeable (`Policy`'s doc comment).
                match check_quiescent(
                    &QuiescentSpec::default(),
                    std::time::Duration::from_millis(500),
                ) {
                    Ok(()) => {
                        streak += 1;
                        if streak >= need_streak {
                            None
                        } else {
                            Some(format!(
                                "settling ({streak}/{need_streak} quiescent samples)"
                            ))
                        }
                    }
                    Err(why) => {
                        streak = 0;
                        Some(why)
                    }
                }
            }
            Mode::Headroom => policy.blocker(&spec.demand),
        };

        let why = match blocker {
            None => break,
            Some(why) => why,
        };

        let waited = start.elapsed().as_secs_f64();
        gate_state.update(waited, &why);
        if last_report.elapsed() >= policy.report {
            let msg = format!(
                "waiting to {} {} for {waited:.0}s. blocked by {why}",
                spec.verb, spec.target
            );
            // The promised response (logging-discipline): this line is owed
            // to whoever is watching the terminal the job is running in,
            // regardless of verbosity, so it goes to stderr unconditionally
            // — AND to the durable record, which is what reconstructs a wait
            // nobody was attached to watch. That gap is the actual incident:
            // `make build-dev`'s archive job waited inside a `setsid … &`
            // detached process, so nothing was ever reading its stderr.
            eprintln!("{msg}");
            log_event(spec.who, "Info", &msg);
            last_report = std::time::Instant::now();
        }
        // Headroom paces on `policy.poll` — "time between polls" is its
        // documented purpose and its name. Quiescent takes no extra sleep of
        // its own: the 500ms quiescence sample above already paces it.
        //
        // Found, not fixed (unification required picking one): `rustc-gate`
        // and `cc-gate` used to sleep `policy.window` here instead — a
        // field whose documented, single purpose is `cpu_idle_iowait_pct`'s
        // OWN sample duration inside `policy.blocker()`, not the loop's
        // pacing. `aur-gate`'s headroom path already used `policy.poll`,
        // the correctly-named field. Both env knobs (`AUR_GATE_POLL_MS`,
        // `AUR_GATE_WINDOW_MS`) keep working for their documented purpose;
        // only the loop's OWN pacing changes, for `rustc-gate`/`cc-gate`
        // only, from `window`'s 250ms default to `poll`'s 500ms default —
        // slower iteration, not a change to any admission threshold.
        if matches!(spec.mode, Mode::Headroom) {
            std::thread::sleep(policy.poll);
        }
    }

    // 4. touch precedent.lock — only once the wait has actually cleared.
    if let Some(ref g) = guard {
        g.touch();
    }
    // 5. funlock(precedent.lock).
    drop(guard);

    // Close the wait state BEFORE the final admit line, so the durable
    // record reads in the order a human expects: the wait, its closure (if
    // it was ever blocked), then the outcome.
    drop(gate_state);

    let waited_s = start.elapsed().as_secs_f64();
    log_event(
        spec.who,
        "Info",
        &format!(
            "admit {} {} mode={} demand={} waited={waited_s:.2}s{}",
            spec.verb,
            spec.target,
            spec.mode.name(),
            spec.demand.describe(),
            if spec.demand.defaulted {
                " (defaulted)"
            } else {
                ""
            }
        ),
    );
    waited_s
}

/// The durable record of what every gate admitted, refused, or waited on —
/// owner ruling, 2026-10-01: "If there's a logging gap that prevents
/// tracking what happened with the resource gate that in and of itself is
/// also a bug — always." Three gates used to hand-roll THREE different
/// answers to "where does this go" (`rustc-gate` wrote the file AND stderr
/// every poll, `cc-gate` wrote nothing at all, `aur-gate` wrote the file XOR
/// stderr) — the same ingredients-shared-but-sequence-not split `admit`
/// exists to close for admission, applied here to its logging.
///
/// Always writes the durable file (`event_log_path`) — unconditional, never
/// behind `AUR_GATE_EVENTS`'s presence. The incident this item exists to fix
/// (2026-10-01: a `setsid … &`-detached archive job stacked unpaced
/// admissions until the OOM killer took the owner's editor) had nobody
/// watching stderr at all; the file is the only sink that survives a run
/// nobody is attached to. Falls back to stderr ONLY when the file genuinely
/// cannot be opened (no `HOME`, permission denied) — a line is never simply
/// dropped.
///
/// `Error` also always reaches stderr (logging-discipline's console routing
/// rule): a refusal someone is watching their terminal for must not depend
/// on whether the file happened to open. Everything else stays file-only on
/// the happy path, by design — `rustc-gate` wraps every compilation unit in
/// a build, hundreds of times, and chattering to stderr there would drown
/// the compiler's own output (its module doc, "deliberately silent on the
/// happy path"). The one deliberate exception is the periodic wait report,
/// which the caller in `admission_wait` prints to stderr itself alongside
/// this call: it is a RESPONSE the operator was promised, not an ordinary
/// Info line (logging-discipline: "a line the operator was promised
/// regardless of verbosity... never a warning").
pub fn log_event(who: &str, level: &str, msg: &str) {
    let rendered = render_event(level, who, msg);
    let wrote = match event_log_path() {
        Some(path) => write_event_line(&path, &rendered),
        None => false,
    };
    if !wrote || level == "Error" {
        eprintln!("{rendered}");
    }
}

fn write_event_line(path: &std::path::Path, rendered: &str) -> bool {
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
    else {
        return false;
    };
    writeln!(f, "{rendered}").is_ok()
}

/// Where the durable admission/refusal record lives: `AUR_GATE_EVENTS` when
/// set — an override of the PATH only, never the on/off switch any more
/// (see `log_event`'s doc comment for why "nothing sets it" was the defect)
/// — else `~/.local/share/aur/aur-gate-events.log`, the same home every AUR
/// daemon's log lives under (`resolve_log_file_path`, `rust/src/lib.rs`).
///
/// ONE file shared by every gate binary, not one per binary: the precedent
/// lock these gates pace against (`PrecedentGuard::acquire`) is ALREADY one
/// file shared across `rustc-gate`/`cc-gate`/`aur-gate` by user, because the
/// question an operator asks is "what did admission do on this machine",
/// not "what did rustc-gate do alone" — splitting the record by binary would
/// make the two-gates-pacing-each-other demonstration unreconstructable from
/// either file alone.
///
/// Deliberately NOT under `tmpfs_base()`: that directory is for state that
/// is SUPPOSED to die with the machine (the precedent lock, the per-pid wait
/// files). A record of what a gate admitted yesterday has to survive a
/// reboot, so tmpfs is the wrong home for it — the owner ruling this answers
/// asked for the choice to be deliberate and said why.
///
/// `HOME` unset means no personal path can be derived (AUR P16/PII) — the
/// gate then has only stderr, same as a daemon with no `HOME`.
fn event_log_path() -> Option<std::path::PathBuf> {
    if let Ok(p) = std::env::var("AUR_GATE_EVENTS") {
        if !p.is_empty() {
            return Some(std::path::PathBuf::from(p));
        }
    }
    let home = std::env::var("HOME").ok()?;
    Some(
        std::path::Path::new(&home)
            .join(".local")
            .join("share")
            .join("aur")
            .join("aur-gate-events.log"),
    )
}

/// One fully-rendered event-log line: timestamp, level, pid, thread,
/// component, message — the field contract every AUR log line follows
/// (logging-discipline skill), applied here even though these are short-lived
/// CLI gates rather than daemons, because the line has to be self-explanatory
/// with nothing else running (P10: "the log line alone, without the running
/// system, must say what happened, to what, in what state, why").
fn render_event(level: &str, who: &str, msg: &str) -> String {
    let thread = std::thread::current();
    let thread_name = thread.name().unwrap_or("unnamed");
    let thread_id = format!("{:?}", thread.id());
    format!(
        "[{}] [{level}] [PID:{}] [Thread:{thread_name}/{thread_id}] [{who}] {msg}",
        timestamp(),
        std::process::id(),
    )
}

/// `struct tm`, x86-64 Linux/glibc layout — the same target-scoping already
/// documented on `StatVfs` above (this package builds for no other target).
#[repr(C)]
struct Tm {
    sec: i32,
    min: i32,
    hour: i32,
    mday: i32,
    mon: i32,
    year: i32,
    wday: i32,
    yday: i32,
    isdst: i32,
    gmtoff: i64,
    zone: *const u8,
}

extern "C" {
    fn localtime_r(t: *const i64, tm: *mut Tm) -> *mut Tm;
}

/// Local wall-clock timestamp, `YYYY-MM-DD_HH:MM:SS.mmm` — logging-discipline's
/// field contract. Hand-declared `localtime_r` rather than a date crate, for
/// the same reason as `statvfs` above: this package stays dependency-free so
/// every gate builds instantly, and `localtime_r` already carries the
/// system's own timezone/DST rules, which is more correct than this file
/// re-deriving them.
fn timestamp() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let millis = now.subsec_millis();
    let mut tm: Tm = unsafe { std::mem::zeroed() };
    // SAFETY: `secs` is a valid time_t and `tm` is a correctly sized, zeroed
    // struct that only this call writes into.
    unsafe {
        localtime_r(&secs, &mut tm);
    }
    format!(
        "{:04}-{:02}-{:02}_{:02}:{:02}:{:02}.{millis:03}",
        tm.year + 1900,
        tm.mon + 1,
        tm.mday,
        tm.hour,
        tm.min,
        tm.sec,
    )
}

/// One live blocked-gate entry, as read back off `gate_state_dir()`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GateEntry {
    pub gate: String,
    pub pid: u32,
    pub verb: String,
    pub target: String,
    pub since: u64,
    pub waited_s: u64,
    pub blocked_by: String,
    pub demand: String,
    pub criteria: String,
}

/// Every live blocked-gate entry under `dir`, pruning (deleting) any file
/// whose pid is no longer alive and skipping — logged, left in place — any
/// file that fails to parse. A `kill -9`'d gate leaves an orphan file behind
/// (it never ran `Drop`) and a concurrent rewrite can be caught mid-rename;
/// both are expected here, not defects in the reader.
///
/// This is the rule both sides agree to: the AUR doc server's Python reader
/// re-implements only this read, against a fixture pinned to the same schema
/// and the same pruning rule, so a monitor watching the manifest and a test
/// running `cargo test -p launcher` can never disagree about which entries
/// are live.
pub fn read_gate_entries(dir: &std::path::Path) -> Vec<GateEntry> {
    let mut out = Vec::new();
    let Ok(rd) = std::fs::read_dir(dir) else {
        return out;
    };
    for ent in rd.flatten() {
        let path = ent.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&path) else {
            continue;
        };
        let Some(map) = parse_json_flat(&text) else {
            eprintln!(
                "gate-state: malformed entry {}: not valid JSON, skipped",
                path.display()
            );
            continue;
        };
        let Some(pid) = map.get("pid").and_then(|v| v.parse::<u32>().ok()) else {
            eprintln!(
                "gate-state: malformed entry {}: missing or non-numeric pid, skipped",
                path.display()
            );
            continue;
        };
        let get = |k: &str| map.get(k).cloned().unwrap_or_default();
        if !std::path::Path::new(&format!("/proc/{pid}")).exists() {
            // The owning process is gone: its own Drop never ran (SIGKILL,
            // or the exact OOM-killer scenario this item exists to fix), so
            // this reader is the one place that reaps the file. AUR P8:
            // "report before you reap" — the evidence goes to the durable
            // record before the file that held it is gone for good.
            log_event(
                &get("gate"),
                "Error",
                &format!(
                    "orphaned-wait pid={pid} verb={} target={} since={} waited_s={} last_blocked_by=\"{}\" demand={} criteria={} — owning process is gone without reaping its own state; reaped here",
                    get("verb"),
                    get("target"),
                    map.get("since").and_then(|v| v.parse::<u64>().ok()).unwrap_or(0),
                    map.get("waited_s").and_then(|v| v.parse::<u64>().ok()).unwrap_or(0),
                    get("blocked_by"),
                    get("demand"),
                    get("criteria"),
                ),
            );
            let _ = std::fs::remove_file(&path);
            continue;
        }
        out.push(GateEntry {
            gate: get("gate"),
            pid,
            verb: get("verb"),
            target: get("target"),
            since: map.get("since").and_then(|v| v.parse().ok()).unwrap_or(0),
            waited_s: map
                .get("waited_s")
                .and_then(|v| v.parse().ok())
                .unwrap_or(0),
            blocked_by: get("blocked_by"),
            demand: get("demand"),
            criteria: get("criteria"),
        });
    }
    out
}

/// Active compiler processes currently running on the system (rustc, cc1, etc.),
/// and their unrealized RAM demand (expected demand minus current resident set size).
///
/// Under parallel builds, newly spawned compiler processes start up and parse code with low
/// RSS (<50 MB) before entering LLVM codegen where each allocates 1.0–1.5 GB. Reading raw
/// `MemAvailable` without accounting for running compilers would admit dozens of units before
/// the memory surge arrives. Counting active compiler units and deducting their unrealized
/// memory demand ensures the machine never over-admits past available physical RAM.
pub fn active_compiler_unrealized_ram_mb(assumed_job_mb: u64) -> (u64, u64) {
    let mut count = 0u64;
    let mut unrealized_mb = 0u64;
    let my_pid = std::process::id() as i32;

    if let Ok(entries) = std::fs::read_dir("/proc") {
        for entry in entries.flatten() {
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if !name_str.bytes().all(|b| b.is_ascii_digit()) {
                continue;
            }
            let Ok(pid) = name_str.parse::<i32>() else {
                continue;
            };
            if pid == my_pid {
                continue;
            }

            let comm_path = entry.path().join("comm");
            let Ok(comm) = std::fs::read_to_string(comm_path) else {
                continue;
            };
            let comm_trimmed = comm.trim();
            let is_compiler = comm_trimmed == "rustc"
                || comm_trimmed == "cc1"
                || comm_trimmed == "cc1plus"
                || comm_trimmed == "clang"
                || comm_trimmed == "rust-lld"
                || comm_trimmed == "ld.mold"
                || comm_trimmed == "ld";

            if is_compiler {
                count += 1;
                let rss_mb = if let Ok(statm) = std::fs::read_to_string(entry.path().join("statm"))
                {
                    statm
                        .split_whitespace()
                        .nth(1)
                        .and_then(|p| p.parse::<u64>().ok())
                        .map_or(0, |pages| pages * 4096 / (1024 * 1024))
                } else {
                    0
                };
                unrealized_mb += assumed_job_mb.saturating_sub(rss_mb);
            }
        }
    }
    (count, unrealized_mb)
}

/// This process's current nice value, or `None` if it could not be read.
///
/// `getpriority(2)` returns -1 both for failure and for the legitimate priority
/// -1, so errno is cleared first and consulted after — the one call in this file
/// where the ambiguity is real.
pub fn current_priority() -> Option<i32> {
    // SAFETY: getpriority(2) takes two plain ints and touches no memory;
    // __errno_location returns a valid pointer to this thread's errno.
    unsafe {
        *__errno_location() = 0;
        let p = getpriority(PRIO_PROCESS, 0);
        if p == -1 && *__errno_location() != 0 {
            None
        } else {
            Some(p)
        }
    }
}

/// Set this process's scheduling priority to `nice` ABSOLUTELY, so anything it
/// goes on to exec inherits that level.
///
/// Headroom alone does not keep a desktop responsive: the gate can hold the
/// machine at 80% and the compositor will still stutter, because the scheduler
/// weighs a compile exactly as heavily as the UI. Every job these gates admit is
/// batch work — no interactive media, no presentation deadline — so it should
/// always yield to whatever the person is actually looking at. Niceness is
/// inherited across fork and exec, which is why setting it here covers the whole
/// process tree without touching a single call site.
///
/// ABSOLUTE, via `setpriority(2)`, replacing the additive `nice(2)` this used to
/// call. The difference is not cosmetic: `nice(+16)` on a process already at 16
/// asks for 32 and lands at the 19 clamp, so the resulting level was a function
/// of the call chain rather than of the work. Every nesting made it worse, and
/// with the ratchet in [`SchedClass`] there is no way back down. `setpriority(16)`
/// lands at 16 from wherever it started, or says why it could not.
///
/// A refusal is REPORTED by every caller, never discarded. Both gates used to
/// throw away the old boolean, which made the one case that actually matters —
/// something upstream already niced us past the level we want, and the class we
/// think we are running at is fiction — completely invisible.
pub fn set_priority(nice: i32) -> Result<(), String> {
    let before = current_priority();
    // SAFETY: setpriority(2) takes three plain ints and touches no memory.
    let (rc, err) = unsafe {
        *__errno_location() = 0;
        let rc = setpriority(PRIO_PROCESS, 0, nice);
        (rc, *__errno_location())
    };
    if rc == 0 {
        return Ok(());
    }
    Err(match before {
        Some(cur) if cur > nice => format!(
            "refused: already at nice {cur}, asked for {nice} (errno {err}). \
             Unprivileged priority is one-way — something upstream niced this \
             process tree before the gate could set its class"
        ),
        Some(cur) => format!("refused: at nice {cur}, asked for {nice} (errno {err})"),
        None => format!(
            "refused: asked for {nice} (errno {err}), and the current priority could not be read"
        ),
    })
}

/// EVERY threshold currently blocking `demand` — not merely the first one found.
///
/// Owner requirement, 2026-07-31: a wait must say which threshold(s) are pending,
/// all on one line, and may stay silent about the ones that are fine.
///
/// The earlier version returned on the first failing axis, which turned one wait
/// into a sequence of discoveries: memory is over, you lower the job's demand,
/// and only then learn cores were over too. Worse, it made the *set* of active
/// constraints unobservable — an operator tuning a threshold could not tell
/// whether relaxing it would actually admit anything, or just expose the next
/// wall. Collecting means one report answers "what would have to change for this
/// to start?" completely.
///
/// Non-blocking axes are deliberately omitted rather than listed as OK: the line
/// exists to name what is wrong, and padding it with passing checks is how the
/// signal gets buried (the same reason the status block drops empty rows).
///
/// Every axis is now evaluated on every call, including the CPU sample — so this
/// always costs `window`. That is the price of a complete answer, and it is
/// already the poll interval's order of magnitude.
fn pending_thresholds(
    d: &Demand,
    reserve_mb: u64,
    free_cores: u64,
    disk: &DiskPolicy,
    spec: &WorkSpec,
    window: std::time::Duration,
) -> Vec<String> {
    let mut pending = Vec::new();

    let (compiler_count, unrealized_ram_mb) = active_compiler_unrealized_ram_mb(d.ram_mb);

    if let Some((avail, total)) = memory_mb() {
        let effective_avail = avail.saturating_sub(unrealized_ram_mb);
        if effective_avail < d.ram_mb + reserve_mb {
            let used = 100.0 - (avail as f64) * 100.0 / total.max(1) as f64;
            let note = if compiler_count > 0 && unrealized_ram_mb > 0 {
                format!(
                    " ({avail} MB free, {unrealized_ram_mb} MB promised across {compiler_count} active compilers; needs {} + {reserve_mb} reserved)",
                    d.ram_mb
                )
            } else {
                format!(
                    " ({avail} MB free; needs {} + {reserve_mb} reserved)",
                    d.ram_mb
                )
            };
            pending.push(format!("RAM {used:.0}% in use{note}"));
        }
    }

    let cap = cores().saturating_sub(free_cores + d.cores).max(1);
    if let Some(r) = runnable() {
        let effective_r = r.max(compiler_count);
        if effective_r > cap {
            let note = if compiler_count > r {
                format!(" ({compiler_count} compilers active)")
            } else {
                String::new()
            };
            pending.push(format!(
                "cores {effective_r} runnable{note} on {} (needs {}, {free_cores} kept free)",
                cores(),
                d.cores
            ));
        }
    }

    // Free space is read LIVE at every poll, never once at gate start: the
    // 02:09 cascade was four gates each admitted against a reading that three
    // other builds were consuming as it was taken. Only the total is stable.
    if let Some((free, total)) = disk_mb(&disk.path) {
        let reserve_mb = disk.reserve_mb();
        if free < d.disk_mb.saturating_add(reserve_mb) {
            let free_pct = free as f64 * 100.0 / total.max(1) as f64;
            let reserve_text = match disk.reserve_mb_override {
                Some(mb) => format!("{} reserved (AUR_GATE_DISK_RESERVE_MB)", human_mb(mb)),
                None => format!(
                    "{:.1}% reserved ({})",
                    disk.reserve_pct,
                    human_mb(reserve_mb)
                ),
            };
            pending.push(format!(
                "disk {} free of {} ({free_pct:.1}%) on {} [{}] — needs {} + {reserve_text}",
                human_mb(free),
                human_mb(total),
                disk.path,
                disk.origin,
                human_mb(d.disk_mb),
            ));
        }
    }

    if let Some((idle, iowait)) = cpu_idle_iowait_pct(window) {
        if iowait > spec.max_iowait_pct {
            pending.push(format!(
                "iowait {iowait:.1}% > {:.1}% allowed (disk-bound, not CPU-bound)",
                spec.max_iowait_pct
            ));
        }
        // Subtract what THIS job is about to add, exactly as the memory and core
        // axes do. Comparing raw idle against the floor would admit the job that
        // consumes precisely the headroom the floor was protecting — the same
        // error the demand argument exists to prevent everywhere else.
        let projected = d.cores as f64 * 100.0 / cores().max(1) as f64;
        if idle - projected < spec.min_idle_pct {
            pending.push(format!(
                "cpu idle {idle:.1}% − {projected:.1}% needed < {:.1}% floor",
                spec.min_idle_pct
            ));
        }
    }

    pending
}

/// Every blocking threshold joined into ONE line, or `None` to admit.
///
/// Two time constants on purpose. Idle% is the honest capacity measure but it
/// LAGS — it needs a sample window, so a burst of gates checking at once all see
/// the pre-burst figure and admit together, a thundering herd. The runnable
/// count has no such lag: a newly admitted rustc is runnable within
/// microseconds. Using both means capacity decides, and the fast signal stops
/// the herd.
pub fn blocker_with_load(
    d: &Demand,
    reserve_mb: u64,
    free_cores: u64,
    disk: &DiskPolicy,
    spec: &WorkSpec,
    window: std::time::Duration,
) -> Option<String> {
    let pending = pending_thresholds(d, reserve_mb, free_cores, disk, spec, window);
    if pending.is_empty() {
        None
    } else {
        Some(pending.join(" · "))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `admit()` end to end, against a Policy built in the test (not read
    /// from the environment) so both scenarios below are deterministic
    /// regardless of whatever else is running on this machine while the
    /// suite executes.
    /// Serializes every test that sets the gate's PROCESS-WIDE environment.
    ///
    /// `AUR_GATE_EVENTS`, `AUR_GATE_OFF` and the two test overrides are
    /// `std::env` variables, so two tests setting them in parallel do not get a
    /// variable each -- the last writer wins and the other test's gate writes
    /// land in the loser's file. That is a real failure with a plausible shape:
    /// `aur_gate_off_bypasses_only_the_wait_never_the_scheduling_class` read its
    /// own events file, found it empty, and reported "a bypass must still be
    /// reconstructable from the durable record" -- a correct assertion about a
    /// record another test had redirected away.
    ///
    /// Each test still uses its own pid-stamped paths; the collision is the
    /// variable, not the file. The guard is poison-tolerant because a panicking
    /// test must not then fail every other test in this module for a different
    /// reason than its own.
    fn gate_env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
        LOCK.get_or_init(|| std::sync::Mutex::new(()))
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

    fn open_policy_for_test(disk: DiskPolicy, report: std::time::Duration) -> Policy {
        Policy {
            reserve_mb: 0,
            free_cores: 0,
            disk,
            work: WorkSpec {
                min_idle_pct: 0.0,
                max_iowait_pct: 100.0,
            },
            nice_low: 19,
            nice_normal: 16,
            nice_priority: 11,
            nice_serve: 4,
            report,
            poll: std::time::Duration::from_millis(20),
            window: std::time::Duration::from_millis(5),
        }
    }

    /// §5.2 of the brief: `admit` must (a) still pace successive callers
    /// through the SAME precedent guard `rustc-gate`'s hand-written sequence
    /// used to, now reachable only through the public API, and (b) hold that
    /// guard across the whole wait and touch it only once the wait clears —
    /// so a still-blocked caller's wait state is observable while its
    /// ADMISSION is provably not yet recorded. One test, one temp event log
    /// and state dir, because `AUR_GATE_EVENTS` / the state-dir override are
    /// process-global and cargo runs a crate's tests on threads of one
    /// process (the discipline `gate_state.rs` already established).
    // `expect` on an `Option` is denied workspace-wide for PRODUCTION code
    // (a silent panic is the wrong failure mode for something that runs
    // inside a build); a test asserting a precondition before continuing is
    // exactly what the lint exists to distinguish from, and this file's
    // other test (`main.rs`) carries the identical carve-out.
    #[allow(clippy::expect_used)]
    #[test]
    fn admit_is_paced_and_holds_the_guard_across_a_blocked_wait() {
        let _env = gate_env_lock();
        let state_dir =
            std::env::temp_dir().join(format!("aur-admit-sequencing-state-{}", std::process::id()));
        let events_path = std::env::temp_dir().join(format!(
            "aur-admit-sequencing-events-{}.log",
            std::process::id()
        ));
        let lock_path = std::env::temp_dir().join(format!(
            "aur-admit-sequencing-precedent-{}.lock",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&state_dir);
        let _ = std::fs::remove_file(&events_path);
        let _ = std::fs::remove_file(&lock_path);
        std::env::set_var("AUR_GATE_STATE_DIR_TEST_OVERRIDE", &state_dir);
        std::env::set_var("AUR_GATE_EVENTS", &events_path);
        // CRITICAL: without this, `admit`'s blocked call below flocks the
        // REAL machine-wide precedent lock every live gate on this box paces
        // against, and the thread it runs on is deliberately leaked (never
        // joined) — a real run of exactly this gap hung and SIGKILLed a
        // sibling test, 2026-10-01.
        std::env::set_var("AUR_GATE_PRECEDENT_LOCK_TEST_OVERRIDE", &lock_path);

        // (0) The guard itself, directly: acquire, touch, drop, then
        // acquire again immediately — the second acquire must sleep out
        // most of `settle_ms`, proving the touch-then-settle contract
        // `admit` relies on. Folded into this one test (rather than kept
        // separate, as it was before this change) because it is the ONLY
        // other code in this file that can set
        // `AUR_GATE_PRECEDENT_LOCK_TEST_OVERRIDE`, and cargo runs a crate's
        // tests on threads of ONE process — two tests independently racing
        // that env var is the exact hazard `gate_state.rs` already
        // documented for its own override.
        let guard_settle_ms = 50;
        let t0 = std::time::Instant::now();
        let guard1 = PrecedentGuard::acquire(guard_settle_ms);
        assert!(guard1.is_some());
        if let Some(ref g) = guard1 {
            g.touch();
        }
        drop(guard1);
        let guard2 = PrecedentGuard::acquire(guard_settle_ms);
        assert!(guard2.is_some());
        assert!(
            t0.elapsed() >= std::time::Duration::from_millis(40),
            "expected at least ~{guard_settle_ms}ms elapsed across two paced \
             acquires, got {:?}",
            t0.elapsed()
        );
        if let Some(ref g) = guard2 {
            g.touch();
        }
        drop(guard2);

        // (a) Pacing: a trivially-satisfiable demand admits with zero wait
        // iterations, but `admit` still took the guard, touched it, and
        // released it — so a SECOND call right after must sleep out the
        // settle window exactly as step (0) proves at the `PrecedentGuard`
        // level, this time only through `admit`.
        let open_disk = DiskPolicy::new(".".into(), "cwd", 0.0, None);
        let policy = open_policy_for_test(open_disk, std::time::Duration::from_secs(10));
        let trivial = Demand {
            cores: 1,
            ram_mb: 1,
            disk_mb: 1,
            defaulted: false,
        };
        let spec = AdmitSpec {
            who: "test-admit-pacing",
            verb: "probe",
            target: "unit-a",
            demand: trivial,
            settle_ms: 50,
            mode: Mode::Headroom,
            class: SchedClass::Normal,
            allow_off: false,
        };
        let t0 = std::time::Instant::now();
        let a1 = admit(&spec, &policy);
        // Under an open policy the wait LOOP never iterates — but
        // `policy.blocker` still costs its one CPU sample (`pending_thresholds`'s
        // own doc comment: "every axis is evaluated on every call, including
        // the CPU sample"), so "admitted instantly" means well under the
        // report/settle timescale, not exactly zero wall-clock time.
        assert!(
            a1.waited_s < 0.5,
            "an open policy must admit without iterating the wait loop: {}",
            a1.waited_s
        );
        let a2 = admit(&spec, &policy);
        assert!(a2.waited_s < 0.5, "{}", a2.waited_s);
        assert!(
            t0.elapsed() >= std::time::Duration::from_millis(40),
            "two admits 50ms apart must be paced by the shared precedent \
             guard even though neither one waited on `policy.blocker`: {:?}",
            t0.elapsed()
        );

        let logged_after_a = std::fs::read_to_string(&events_path).unwrap_or_default();
        assert!(
            logged_after_a.contains("admit probe unit-a"),
            "the durable record must carry the admission with its verb and \
             target: {logged_after_a:?}"
        );
        assert!(
            logged_after_a.contains("mode=headroom") && logged_after_a.contains("demand="),
            "the admission line must name the mode and demand it cleared: \
             {logged_after_a:?}"
        );

        // (b) Ordering: a demand no filesystem can satisfy (100% of `/`
        // reserved, mirroring `gate_reporting.rs`'s established disk trick)
        // blocks `admit` forever by construction. Run it on a thread and
        // never join it — it is deliberately leaked, dying only when this
        // test binary exits; joining it would hang this test. Left running,
        // it keeps polling into THIS test's own temp paths (never unset
        // below) rather than the real user log, which is why the env
        // overrides are intentionally not cleaned up at the end of this
        // test.
        let blocked_disk = DiskPolicy::new("/".into(), "cwd", 100.0, None);
        let blocked_policy =
            open_policy_for_test(blocked_disk, std::time::Duration::from_millis(30));
        let blocked_spec = AdmitSpec {
            who: "test-admit-ordering",
            verb: "probe",
            target: "unit-b",
            demand: trivial,
            settle_ms: 0,
            mode: Mode::Headroom,
            class: SchedClass::Normal,
            allow_off: false,
        };
        std::thread::spawn(move || {
            let _ = admit(&blocked_spec, &blocked_policy);
        });

        // A few `poll` iterations (20ms) land well inside this window.
        std::thread::sleep(std::time::Duration::from_millis(150));

        let entries = read_gate_entries(&state_dir);
        let blocked = entries
            .iter()
            .find(|e| e.target == "unit-b")
            .expect("a still-blocked admit() must publish its wait state");
        assert!(
            !blocked.blocked_by.is_empty(),
            "the published state must name what is blocking it: {blocked:?}"
        );

        let logged_after_b = std::fs::read_to_string(&events_path).unwrap_or_default();
        assert!(
            logged_after_b.contains("blocked by"),
            "the durable record must show the wait: {logged_after_b:?}"
        );
        assert!(
            !logged_after_b.contains("admit probe unit-b"),
            "admit() must not journal an ADMISSION for a spec that is still \
             blocked — touch only happens once the wait clears: {logged_after_b:?}"
        );
    }

    /// `AUR_GATE_OFF=1` bypasses the wait for a caller that opted in
    /// (`allow_off: true`, exactly `rustc-gate`/`cc-gate`'s existing
    /// behaviour, now inside `admit` instead of hand-rolled per binary) —
    /// but the scheduling class is still applied, because it always was
    /// (both gates set nice UNCONDITIONALLY, outside the admission check).
    /// A spec with `allow_off: false` (`aur-gate`'s own, unchanged) ignores
    /// the variable entirely.
    #[test]
    fn aur_gate_off_bypasses_only_the_wait_never_the_scheduling_class() {
        let _env = gate_env_lock();
        let events_path =
            std::env::temp_dir().join(format!("aur-admit-off-events-{}.log", std::process::id()));
        let _ = std::fs::remove_file(&events_path);
        std::env::set_var("AUR_GATE_EVENTS", &events_path);
        std::env::set_var("AUR_GATE_OFF", "1");

        // A demand the open policy below would admit instantly anyway — the
        // bypass is observed through `waited_s` and the event log, not
        // through otherwise-unreachable blocking.
        let policy = open_policy_for_test(
            DiskPolicy::new(".".into(), "cwd", 0.0, None),
            std::time::Duration::from_secs(10),
        );
        let demand = Demand {
            cores: 1,
            ram_mb: 1,
            disk_mb: 1,
            defaulted: false,
        };

        let bypassed = admit(
            &AdmitSpec {
                who: "test-admit-off",
                verb: "probe",
                target: "bypassed",
                demand,
                settle_ms: 0,
                mode: Mode::Headroom,
                class: SchedClass::Normal,
                allow_off: true,
            },
            &policy,
        );
        assert!(
            bypassed.waited_s < f64::EPSILON,
            "AUR_GATE_OFF must skip the wait entirely, not merely shorten it: {}",
            bypassed.waited_s
        );
        assert_eq!(
            bypassed.nice, 16,
            "the scheduling class (nice_normal) must still have been \
             resolved and attempted under the bypass — both gates always \
             set it unconditionally, outside the admission check"
        );

        std::env::remove_var("AUR_GATE_OFF");
        let logged = std::fs::read_to_string(&events_path).unwrap_or_default();
        assert!(
            logged.contains("skip probe bypassed"),
            "a bypass must still be reconstructable from the durable record: {logged:?}"
        );
        let _ = std::fs::remove_file(&events_path);
    }

    #[test]
    fn test_active_compiler_unrealized_ram_mb() {
        let (count, unrealized_mb) = active_compiler_unrealized_ram_mb(1500);
        // On a live Linux system, read_dir("/proc") succeeds and returns non-negative values.
        // We verify calculation consistency: unrealized_mb <= count * 1500
        assert!(
            unrealized_mb <= count * 1500,
            "unrealized RAM ({unrealized_mb} MB) cannot exceed compiler count ({count}) * assumed demand"
        );
    }
}
