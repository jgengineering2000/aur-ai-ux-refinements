// SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
//! aur-gate — Unified multi-call admission gate for system workloads and compiler processes.
//!
//! Replaces separate copies of rustc-gate, cc-gate, cxx-gate, and project-local gates with a single
//! high-performance multi-call binary.
//!
//! PERSONALITIES (selected via argv[0] or first subcommand):
//!   rustc-gate <rustc> <args...>
//!       Compiler admission gate for Cargo (sets $RUSTC_WRAPPER).
//!   cc-gate / cxx-gate <args...>
//!       C/C++ compiler admission gate for build scripts and CMake (sets $CC / $CXX).
//!   aur-gate <headroom|quiescent> <action> <target> [--demand 2C,1.5G,26MB] -- <cmd...>
//!       Supervised job gate for long-running tasks, benchmarks, and tests.
//!   aur-gate sysinfo [--interval <ms>]
//!       Streams live machine metrics and threshold values.

mod resources;

use resources::{
    admit, env_num, log_event, memory_mb, runnable, cores, swap_mb, disk_mb,
    AdmitSpec, CpuSampler, Demand, Mode, Policy, SchedClass,
};
use std::fmt::Write as _;
use std::io::Write as _;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicI32, Ordering};

extern "C" {
    fn signal(signum: i32, handler: extern "C" fn(i32)) -> usize;
    fn kill(pid: i32, sig: i32) -> i32;
}
const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

static CHILD_PID: AtomicI32 = AtomicI32::new(0);

extern "C" fn forward_signal(sig: i32) {
    let pid = CHILD_PID.load(Ordering::SeqCst);
    if pid > 0 {
        unsafe { kill(pid, sig) };
    }
}

/// Does this rustc invocation produce an executable or shared library object (link step)?
fn is_link_invocation(args: &[std::ffi::OsString]) -> bool {
    let mut is_explicit_lib = false;
    let mut is_explicit_bin = false;
    let mut has_emit_link = false;
    let mut has_emit_metadata_only = false;

    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        let s = arg.to_string_lossy();
        if s == "--test" {
            return true;
        }
        if s == "--crate-type" {
            if let Some(val) = iter.next() {
                let v = val.to_string_lossy();
                if v.contains("bin") || v.contains("cdylib") || v.contains("dylib") {
                    is_explicit_bin = true;
                } else if v.contains("lib")
                    || v.contains("rlib")
                    || v.contains("proc-macro")
                    || v.contains("staticlib")
                {
                    is_explicit_lib = true;
                }
            }
        } else if let Some(v) = s.strip_prefix("--crate-type=") {
            if v.contains("bin") || v.contains("cdylib") || v.contains("dylib") {
                is_explicit_bin = true;
            } else if v.contains("lib")
                || v.contains("rlib")
                || v.contains("proc-macro")
                || v.contains("staticlib")
            {
                is_explicit_lib = true;
            }
        } else if let Some(v) = s.strip_prefix("--emit=") {
            if v.contains("link") {
                has_emit_link = true;
            }
            if v == "metadata" || v == "dep-info,metadata" {
                has_emit_metadata_only = true;
            }
        }
    }

    if has_emit_metadata_only {
        return false;
    }
    if is_explicit_bin {
        return true;
    }
    if is_explicit_lib {
        return false;
    }
    has_emit_link
}

/// Does this compiler invocation produce an object/binary (not just a probe like -E or --version)?
fn produces_output(args: &[std::ffi::OsString]) -> bool {
    args.iter().any(|a| {
        let s = a.to_string_lossy();
        s == "-c" || s.starts_with("-o")
    })
}

#[allow(dead_code)]
fn resolve_compiler(name: &str) -> Option<PathBuf> {
    let p = Path::new(name);
    if p.is_absolute() || name.contains('/') {
        return p.canonicalize().ok();
    }
    std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path)
            .map(|dir| dir.join(name))
            .find(|c| c.is_file())
            .and_then(|c| c.canonicalize().ok())
    })
}

fn get_compiler_var(suffix: &str) -> Option<String> {
    if let Ok(val) = std::env::var(format!("AUR_GATE_{suffix}")) {
        if !val.trim().is_empty() {
            return Some(val);
        }
    }
    for (k, v) in std::env::vars() {
        if k.ends_with(&format!("_GATE_{suffix}")) && !v.trim().is_empty() {
            return Some(v);
        }
    }
    None
}

fn run_rustc_gate(args: impl Iterator<Item = std::ffi::OsString>) {
    let mut args = args;
    let Some(rustc) = args.next() else {
        eprintln!("rustc-gate: no rustc given (cargo passes it as argv[1])");
        std::process::exit(2);
    };
    let mut rest: Vec<_> = args.collect();
    let crate_name = rest
        .iter()
        .position(|a| a == "--crate-name")
        .and_then(|i| rest.get(i + 1))
        .map_or_else(|| "unit".into(), |s| s.to_string_lossy().into_owned());

    let is_link = is_link_invocation(&rest);

    if is_link {
        let link_threads = env_num("AUR_GATE_LINK_THREADS", 4);
        rest.push(format!("-Cllvm-args=-threads={link_threads}").into());
        rest.push(format!("-Clink-arg=-Wl,--thinlto-jobs={link_threads}").into());
    }

    let policy = Policy::for_invocation(&rest);

    let demand = if is_link {
        Demand {
            cores: env_num("AUR_GATE_LINK_CORES", 4),
            ram_mb: env_num("AUR_GATE_LINK_MB", 3500),
            disk_mb: 0,
            defaulted: false,
        }
    } else {
        Demand {
            cores: env_num("AUR_GATE_JOB_CORES", 1),
            ram_mb: env_num("AUR_GATE_JOB_MB", 1500),
            disk_mb: 0,
            defaulted: false,
        }
    };

    let verb = if is_link { "link" } else { "build" };
    let settle_ms = if is_link {
        env_num("AUR_GATE_LINK_SETTLE_MS", 500)
    } else {
        env_num("AUR_GATE_JOB_SETTLE_MS", 250)
    };

    let spec = AdmitSpec {
        who: "rustc-gate",
        verb,
        target: &crate_name,
        demand,
        settle_ms,
        mode: Mode::Headroom,
        class: SchedClass::from_env(),
        allow_off: true,
    };
    admit(&spec, &policy);

    let err = Command::new(&rustc).args(&rest).exec();
    eprintln!(
        "rustc-gate: failed to exec {}: {err}",
        rustc.to_string_lossy()
    );
    std::process::exit(1);
}

fn run_cc_gate(caller_name: &str, args: impl Iterator<Item = std::ffi::OsString>) {
    let cxx = caller_name.contains("cxx");
    let (real, fallback, who) = if cxx {
        (
            get_compiler_var("CXX"),
            "c++",
            "cxx-gate",
        )
    } else {
        (
            get_compiler_var("CC"),
            "cc",
            "cc-gate",
        )
    };

    let real = real.unwrap_or_else(|| fallback.to_string());
    let rest: Vec<_> = args.collect();

    if !produces_output(&rest) {
        let err = Command::new(&real).args(&rest).exec();
        eprintln!("{who}: failed to exec {real}: {err}");
        std::process::exit(1);
    }

    let policy = Policy::from_env();
    let demand = Demand {
        cores: 1,
        ram_mb: env_num("AUR_GATE_CC_MB", 470),
        disk_mb: 0,
        defaulted: false,
    };

    let target = rest
        .iter()
        .position(|a| a == "-o")
        .and_then(|i| rest.get(i + 1))
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "unit".into());

    let spec = AdmitSpec {
        who,
        verb: "compile",
        target: &target,
        demand,
        settle_ms: env_num("AUR_GATE_CC_SETTLE_MS", 100),
        mode: Mode::Headroom,
        class: SchedClass::from_env(),
        allow_off: true,
    };
    admit(&spec, &policy);

    let err = Command::new(&real).args(&rest).exec();
    eprintln!("{who}: failed to exec {real}: {err}");
    std::process::exit(1);
}

fn run_supervised(argv: &[String]) {
    let Some(split) = argv.iter().position(|a| a == "--") else {
        eprintln!("usage: aur-gate <headroom|quiescent> <action> <target> [--demand 2C,1.5G,26MB] -- <cmd…>");
        std::process::exit(2);
    };
    let head = &argv[..split];
    let cmd = &argv[split + 1..];
    if cmd.is_empty() {
        eprintln!("aur-gate: nothing to run after --");
        std::process::exit(2);
    }

    let mode = match head.first().map(String::as_str) {
        Some("quiescent") => Mode::Quiescent,
        _ => Mode::Headroom,
    };
    let action = head.get(1).cloned().unwrap_or_else(|| "run".into());
    let target = head.get(2).cloned().unwrap_or_else(|| "job".into());
    let demand = head
        .iter()
        .position(|a| a == "--demand")
        .and_then(|i| head.get(i + 1))
        .map(|s| Demand::parse(s))
        .unwrap_or_default();

    let policy = Policy::from_env();
    let class = SchedClass::from_env();

    let spec = AdmitSpec {
        who: "aur-gate",
        verb: &action,
        target: &target,
        demand,
        settle_ms: env_num("AUR_GATE_JOB_SETTLE_MS", 250),
        mode,
        class,
        allow_off: false,
    };
    admit(&spec, &policy);

    unsafe {
        signal(SIGINT, forward_signal);
        signal(SIGTERM, forward_signal);
    }

    let t0 = std::time::Instant::now();
    log_event("aur-gate", "Info", &format!("start {action} {target} cmd={:?}", cmd));

    let mut child = match Command::new(&cmd[0]).args(&cmd[1..]).spawn() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("aur-gate: cannot start {}: {e}", cmd[0]);
            std::process::exit(1);
        }
    };
    CHILD_PID.store(child.id() as i32, Ordering::SeqCst);

    let status = child.wait().expect("aur-gate: failed to wait on child");
    let dur = t0.elapsed();
    log_event(
        "aur-gate",
        "Info",
        &format!("done {action} {target} exit={:?} dur={:.2}s", status.code(), dur.as_secs_f64()),
    );

    std::process::exit(status.code().unwrap_or(1));
}

fn run_sysinfo(args: &[String]) {
    let mut interval_ms = 3000u64;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--interval" => {
                if let Some(v) = args.get(i + 1) {
                    if let Ok(n) = v.parse::<u64>() {
                        interval_ms = n.max(50);
                    }
                    i += 2;
                } else {
                    eprintln!("option `--interval' requires a value in milliseconds");
                    std::process::exit(2);
                }
            }
            "--help" | "-h" => {
                println!("usage: aur-gate sysinfo [--interval <ms>]");
                std::process::exit(0);
            }
            other => {
                eprintln!("unknown option `{other}' for mode `sysinfo'");
                std::process::exit(2);
            }
        }
    }
    let interval = std::time::Duration::from_millis(interval_ms);
    let policy = Policy::from_env();
    let mut cpu = CpuSampler::new();
    cpu.tick();
    loop {
        std::thread::sleep(interval);
        let mut line = String::with_capacity(512);
        if let Some(p) = cpu.tick() {
            let _ = write!(
                line,
                "sy={:.1} us={:.1} ni={:.1} id={:.1} wa={:.1} ",
                p.system, p.user, p.nice, p.idle, p.iowait
            );
        }
        if let Some((avail, total)) = memory_mb() {
            let _ = write!(line, "mem_avail_mb={avail} mem_total_mb={total} ");
        }
        if let Some((savail, stotal)) = swap_mb() {
            let _ = write!(line, "sw_avail_mb={savail} sw_total_mb={stotal} ");
        }
        if let Some((dfree, dtotal)) = disk_mb(&policy.disk.path) {
            let _ = write!(line, "st_free_mb={dfree} st_total_mb={dtotal} ");
        }
        if let Some(r) = runnable() {
            let _ = write!(line, "runnable={r} ");
        }
        let _ = write!(line, "cores={} ", cores());
        let _ = write!(
            line,
            "min_idle_pct={:.1} max_iowait_pct={:.1} reserve_mb={} free_cores={} disk_reserve_mb={} disk_reserve_pct={:.1} q_min_idle_pct={:.1} q_min_free_ram_pct={:.1} q_max_iowait_pct={:.1}",
            policy.work.min_idle_pct,
            policy.work.max_iowait_pct,
            policy.reserve_mb,
            policy.free_cores,
            policy.disk.reserve_mb(),
            policy.disk.reserve_pct,
            90.0,
            20.0,
            0.3
        );
        println!("{line}");
        let _ = std::io::stdout().flush();
    }
}

const USAGE: &str = "\
usage: aur-gate <personality | subcommand> [args...]

Multi-call admission control gate for compilation, tests, and system workloads.

PERSONALITIES (invoked via symlink name or first subcommand):
  rustc-gate <rustc> <args...>
      Cargo RUSTC_WRAPPER: paces compilations against live RAM, core, and CPU headroom.
  cc-gate / cxx-gate <args...>
      Compiler wrapper (CC/CXX): paces C/C++ builds without supervising micro-probes.
  aur-gate <headroom|quiescent> <action> <target> [--demand <C,M,G>] -- <cmd...>
      Supervises long-running jobs, forwarding signals and logging durable telemetry.
  sysinfo [--interval <ms>]
      Streams live host readings and current policy thresholds.

OPTIONS:
  -h, --help    Print this help text and exit zero (AUR Principle 14).
";

fn main() {
    let argv0 = std::env::args_os().next().unwrap_or_default();
    let bin_name = Path::new(&argv0)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();

    if bin_name.contains("rustc-gate") {
        run_rustc_gate(std::env::args_os().skip(1));
        return;
    }

    if bin_name.contains("cc-gate") || bin_name.contains("cxx-gate") {
        run_cc_gate(&bin_name, std::env::args_os().skip(1));
        return;
    }

    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.is_empty() {
        eprintln!("aur-gate: no command or personality given");
        eprintln!("{USAGE}");
        std::process::exit(2);
    }

    match args[0].as_str() {
        "--help" | "-h" => {
            print!("{USAGE}");
            std::process::exit(0);
        }
        "sysinfo" => {
            run_sysinfo(&args[1..]);
        }
        "rustc" => {
            run_rustc_gate(std::env::args_os().skip(2));
        }
        "cc" => {
            run_cc_gate("cc-gate", std::env::args_os().skip(2));
        }
        "cxx" => {
            run_cc_gate("cxx-gate", std::env::args_os().skip(2));
        }
        "run" => {
            run_supervised(&args[1..]);
        }
        _ => {
            // Default: supervised gate (e.g. `aur-gate headroom build foo -- cargo build`)
            run_supervised(&args);
        }
    }
}
