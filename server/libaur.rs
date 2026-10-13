// SPDX-License-Identifier: Apache-2.0 OR LGPL-2.1-or-later
// libaur.rs — Pure Rust native accelerators for AUR doc/console server
//
// Unifies and replaces the C implementations of fduser and perfstat:
// 1. fduser_find_writers / fduser_has_writer / fduser_has_writers / fileholder_has_writer:
//    Single-pass reverse-PID Linux descriptor discovery matching (st_dev, st_ino)
//    directly via SYS_getdents64 and fstatat with sub-millisecond early exit.
// 2. aur_get_proc_stats:
//    Zero-allocation in-process extraction of PID, comm, nice, CPU ticks, and RSS bytes
//    from /proc/<pid>/stat directly into the ctypes AurProcStat struct buffer.

use std::os::raw::{c_char, c_int, c_long, c_void};
use std::sync::Mutex;
use std::time::Instant;

const PROC_BUF_SIZE: usize = 65536;
const FD_BUF_SIZE: usize = 8192;

const O_RDONLY: i32 = 0;
const O_WRONLY: i32 = 1;
const O_RDWR: i32 = 2;
const O_ACCMODE: u32 = 3;
const O_DIRECTORY: i32 = 0x10000;
const O_CLOEXEC: i32 = 0x80000;

const SYS_GETDENTS64: c_long = 217;
const DT_DIR: u8 = 4;
const _SC_PAGESIZE: c_int = 30;
const _SC_CLK_TCK: c_int = 2;

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct AurTopGroup {
    pub rank: [u8; 8],
    pub count: i32,
    pub name: [u8; 64],
    pub nice: [u8; 32],
    pub load: f64,
    pub cputime_s: f64,
    pub rss: u64,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Stat {
    pub st_dev: u64,
    pub st_ino: u64,
    pub st_nlink: u64,
    pub st_mode: u32,
    pub st_uid: u32,
    pub st_gid: u32,
    pub __pad0: u32,
    pub st_rdev: u64,
    pub st_size: i64,
    pub st_blksize: i64,
    pub st_blocks: i64,
    pub st_atime: i64,
    pub st_atime_nsec: i64,
    pub st_mtime: i64,
    pub st_mtime_nsec: i64,
    pub st_ctime: i64,
    pub st_ctime_nsec: i64,
    pub __unused: [i64; 3],
}

#[repr(C)]
struct LinuxDirent64 {
    d_ino: u64,
    d_off: i64,
    d_reclen: u16,
    d_type: u8,
}

#[repr(C)]
pub struct AurProcStat {
    pub pid: i32,
    pub comm: [u8; 64],
    pub nice: i32,
    pub ticks: u64,
    pub rss_bytes: u64,
}

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
    fn open(path: *const c_char, flags: c_int, ...) -> c_int;
    fn openat(dirfd: c_int, path: *const c_char, flags: c_int, ...) -> c_int;
    fn close(fd: c_int) -> c_int;
    fn syscall(number: c_long, ...) -> c_long;
    fn fstat(fd: c_int, buf: *mut Stat) -> c_int;
    fn stat(path: *const c_char, buf: *mut Stat) -> c_int;
    fn fstatat(dirfd: c_int, path: *const c_char, buf: *mut Stat, flags: c_int) -> c_int;
    fn getuid() -> u32;
    fn sysconf(name: c_int) -> c_long;
    fn read(fd: c_int, buf: *mut c_void, count: usize) -> isize;
    fn lseek(fd: c_int, offset: i64, whence: c_int) -> i64;
    fn statvfs(path: *const c_char, buf: *mut StatVfs) -> c_int;
}

const SEEK_SET: c_int = 0;

fn write_i32(mut val: i32, out: &mut [u8; 16]) -> usize {
    if val == 0 {
        out[0] = b'0';
        return 1;
    }
    let neg = val < 0;
    if neg {
        val = -val;
    }
    let mut tmp = [0u8; 16];
    let mut len = 0;
    while val > 0 {
        tmp[len] = b'0' + (val % 10) as u8;
        val /= 10;
        len += 1;
    }
    let mut idx = 0;
    if neg {
        out[idx] = b'-';
        idx += 1;
    }
    for i in (0..len).rev() {
        out[idx] = tmp[i];
        idx += 1;
    }
    idx
}

fn parse_u64(b: &[u8]) -> Option<u64> {
    if b.is_empty() {
        return None;
    }
    let mut val = 0u64;
    for &c in b {
        if c.is_ascii_digit() {
            val = val.checked_mul(10)?.checked_add((c - b'0') as u64)?;
        } else {
            return None;
        }
    }
    Some(val)
}

fn parse_i32(b: &[u8]) -> Option<i32> {
    if b.is_empty() {
        return None;
    }
    let (neg, digits) = if b[0] == b'-' {
        (true, &b[1..])
    } else {
        (false, b)
    };
    if digits.is_empty() {
        return None;
    }
    let mut val = 0i32;
    for &c in digits {
        if c.is_ascii_digit() {
            val = val.checked_mul(10)?.checked_add((c - b'0') as i32)?;
        } else {
            return None;
        }
    }
    Some(if neg { -val } else { val })
}

fn parse_octal(b: &[u8]) -> Option<u32> {
    let mut val = 0u32;
    let mut seen = false;
    for &c in b {
        if (b'0'..=b'7').contains(&c) {
            val = (val << 3) | ((c - b'0') as u32);
            seen = true;
        } else {
            break;
        }
    }
    if seen {
        Some(val)
    } else {
        None
    }
}

unsafe fn is_fd_writable(pid: i32, fd_num: i32) -> bool {
    let mut path_buf = [0u8; 64];
    let mut n = 0;
    let pfx = b"/proc/\0";
    path_buf[..6].copy_from_slice(&pfx[..6]);
    n += 6;

    let mut tmp_buf = [0u8; 16];
    let pid_len = write_i32(pid, &mut tmp_buf);
    path_buf[n..n + pid_len].copy_from_slice(&tmp_buf[..pid_len]);
    n += pid_len;

    let mid = b"/fdinfo/\0";
    path_buf[n..n + 8].copy_from_slice(&mid[..8]);
    n += 8;

    let fd_len = write_i32(fd_num, &mut tmp_buf);
    path_buf[n..n + fd_len].copy_from_slice(&tmp_buf[..fd_len]);
    n += fd_len;
    path_buf[n] = 0;

    let fd = open(path_buf.as_ptr() as *const c_char, O_RDONLY | O_CLOEXEC);
    if fd < 0 {
        return false;
    }
    let mut buf = [0u8; 512];
    let r = read(fd, buf.as_mut_ptr() as *mut c_void, buf.len() - 1);
    close(fd);
    if r <= 0 {
        return false;
    }
    let slice = &buf[..r as usize];
    for line in slice.split(|&b| b == b'\n') {
        if line.starts_with(b"flags:\t") {
            let rest = &line[7..];
            let octal_str = match rest.iter().position(|&b| b != b' ' && b != b'\t') {
                Some(idx) => &rest[idx..],
                None => continue,
            };
            if let Some(flags) = parse_octal(octal_str) {
                let acc = flags & O_ACCMODE;
                return acc == (O_WRONLY as u32) || acc == (O_RDWR as u32);
            }
        }
    }
    false
}

struct PidFdEntry {
    pid: i32,
    fd: c_int,
}

static PROC_FD_CACHE: Mutex<Vec<PidFdEntry>> = Mutex::new(Vec::new());

fn parse_proc_stat_fields(
    stat_buf: &[u8],
    page_size: u64,
    out_comm: &mut [u8; 64],
    out_comm_len: &mut usize,
    out_nice: &mut i32,
    out_ticks: &mut u64,
    out_rss_bytes: &mut u64,
) -> bool {
    if let Some(open_p) = stat_buf.iter().position(|&b| b == b'(') {
        if let Some(close_p) = stat_buf.iter().rposition(|&b| b == b')') {
            if close_p > open_p {
                let comm_bytes = &stat_buf[open_p + 1..close_p];
                let rest = &stat_buf[close_p + 1..];

                let mut toks = rest.split(|&b| b == b' ').filter(|s| !s.is_empty());
                if let (Some(_state), Some(_ppid), Some(_pgrp), Some(_session),
                        Some(_tty), Some(_tpgid), Some(_flags), Some(_minflt),
                        Some(_cminflt), Some(_majflt), Some(_cmajflt),
                        Some(utime_b), Some(stime_b), Some(_cutime), Some(_cstime),
                        Some(_prio), Some(nice_b), Some(_threads), Some(_itreal),
                        Some(_start), Some(_vsize), Some(rss_b)) = (
                    toks.next(), toks.next(), toks.next(), toks.next(),
                    toks.next(), toks.next(), toks.next(), toks.next(),
                    toks.next(), toks.next(), toks.next(),
                    toks.next(), toks.next(), toks.next(), toks.next(),
                    toks.next(), toks.next(), toks.next(), toks.next(),
                    toks.next(), toks.next(), toks.next(),
                ) {
                    let utime = parse_u64(utime_b).unwrap_or(0);
                    let stime = parse_u64(stime_b).unwrap_or(0);
                    let nice = parse_i32(nice_b).unwrap_or(0);
                    let rss_pages = parse_u64(rss_b).unwrap_or(0);

                    *out_nice = nice;
                    *out_ticks = utime + stime;
                    *out_rss_bytes = rss_pages * page_size;

                    let copy_len = comm_bytes.len().min(63);
                    out_comm[..copy_len].copy_from_slice(&comm_bytes[..copy_len]);
                    out_comm[copy_len] = 0;
                    *out_comm_len = copy_len;
                    return true;
                }
            }
        }
    }
    false
}

unsafe fn parse_proc_stat(
    pid: i32,
    stat_buf: &[u8],
    page_size: u64,
    entry: &mut AurProcStat,
) -> bool {
    entry.pid = pid;
    let mut len = 0;
    parse_proc_stat_fields(
        stat_buf,
        page_size,
        &mut entry.comm,
        &mut len,
        &mut entry.nice,
        &mut entry.ticks,
        &mut entry.rss_bytes,
    )
}

#[no_mangle]
pub unsafe extern "C" fn aur_get_proc_stats(
    out_stats: *mut AurProcStat,
    max_entries: i32,
) -> i32 {
    if out_stats.is_null() || max_entries <= 0 {
        return 0;
    }
    let proc_fd = open(b"/proc\0".as_ptr() as *const c_char, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if proc_fd < 0 {
        return -1;
    }

    static mut CACHED_PAGE_SIZE: u64 = 0;
    let page_size = if CACHED_PAGE_SIZE != 0 {
        CACHED_PAGE_SIZE
    } else {
        let ps = sysconf(_SC_PAGESIZE);
        let ps = if ps > 0 { ps as u64 } else { 4096 };
        CACHED_PAGE_SIZE = ps;
        ps
    };

    let mut proc_buf = [0u8; PROC_BUF_SIZE];
    let nread = syscall(SYS_GETDENTS64, proc_fd, proc_buf.as_mut_ptr(), PROC_BUF_SIZE);
    let mut bpos = 0isize;

    let mut current_pids = Vec::with_capacity(1024);
    while bpos < nread as isize {
        let d = &*(proc_buf.as_ptr().offset(bpos) as *const LinuxDirent64);
        let reclen = d.d_reclen as isize;
        if d.d_type == DT_DIR && bpos + 19 < nread as isize {
            let name_bytes = &proc_buf[(bpos + 19) as usize..(bpos + reclen) as usize];
            if !name_bytes.is_empty() && name_bytes[0].is_ascii_digit() {
                if let Some(nul) = name_bytes.iter().position(|&b| b == 0) {
                    if let Some(pid) = parse_i32(&name_bytes[..nul]) {
                        if pid > 0 {
                            current_pids.push(pid);
                        }
                    }
                }
            }
        }
        bpos += reclen;
    }

    let mut cache = match PROC_FD_CACHE.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };

    let mut next_cache = Vec::with_capacity(current_pids.len());
    let mut stat_buf = [0u8; 1024];
    let mut count = 0i32;

    for pid in current_pids {
        if count >= max_entries {
            break;
        }

        let mut fd = -1;
        if let Ok(idx) = cache.binary_search_by_key(&pid, |e| e.pid) {
            fd = cache[idx].fd;
        }

        let mut valid = false;
        if fd >= 0 {
            if lseek(fd, 0, SEEK_SET) >= 0 {
                let sn = read(fd, stat_buf.as_mut_ptr() as *mut c_void, stat_buf.len() - 1);
                if sn > 0 {
                    let entry = &mut *out_stats.offset(count as isize);
                    if parse_proc_stat(pid, &stat_buf[..sn as usize], page_size, entry) {
                        count += 1;
                        valid = true;
                    }
                }
            }
            if !valid {
                close(fd);
                fd = -1;
            }
        }

        if fd < 0 {
            let mut stat_path = [0u8; 32];
            let mut tmp_buf = [0u8; 16];
            let pid_len = write_i32(pid, &mut tmp_buf);
            stat_path[..pid_len].copy_from_slice(&tmp_buf[..pid_len]);
            let sfx = b"/stat\0";
            stat_path[pid_len..pid_len + 6].copy_from_slice(sfx);

            let sfd = openat(proc_fd, stat_path.as_ptr() as *const c_char, O_RDONLY | O_CLOEXEC);
            if sfd >= 0 {
                let sn = read(sfd, stat_buf.as_mut_ptr() as *mut c_void, stat_buf.len() - 1);
                if sn > 0 {
                    let entry = &mut *out_stats.offset(count as isize);
                    if parse_proc_stat(pid, &stat_buf[..sn as usize], page_size, entry) {
                        count += 1;
                        fd = sfd;
                        valid = true;
                    } else {
                        close(sfd);
                    }
                } else {
                    close(sfd);
                }
            }
        }

        if valid && fd >= 0 {
            next_cache.push(PidFdEntry { pid, fd });
        }
    }

    close(proc_fd);

    // Evict and close any fds that were in cache but are NOT in next_cache
    next_cache.sort_unstable_by_key(|e| e.pid);
    for old in cache.iter() {
        if next_cache.binary_search_by_key(&old.pid, |e| e.pid).is_err() {
            close(old.fd);
        }
    }

    *cache = next_cache;
    count
}

#[no_mangle]
pub unsafe extern "C" fn fduser_find_writers(
    paths: *const *const c_char,
    num_paths: i32,
    out_pids: *mut i32,
) -> i32 {
    if paths.is_null() || num_paths <= 0 || out_pids.is_null() {
        return 0;
    }
    std::ptr::write_bytes(out_pids, 0, num_paths as usize);

    struct Target {
        dev: u64,
        ino: u64,
        path_idx: usize,
        matched: bool,
    }

    let mut targets = Vec::with_capacity(num_paths as usize);
    for i in 0..num_paths as usize {
        let p = *paths.add(i);
        if !p.is_null() {
            let mut st: Stat = std::mem::zeroed();
            if stat(p, &mut st) == 0 {
                targets.push(Target {
                    dev: st.st_dev,
                    ino: st.st_ino,
                    path_idx: i,
                    matched: false,
                });
            }
        }
    }

    if targets.is_empty() {
        return 0;
    }

    let proc_fd = open(b"/proc\0".as_ptr() as *const c_char, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if proc_fd < 0 {
        return -1;
    }

    let my_uid = getuid();
    let mut proc_buf = [0u8; PROC_BUF_SIZE];
    let nread = syscall(SYS_GETDENTS64, proc_fd, proc_buf.as_mut_ptr(), PROC_BUF_SIZE);
    let mut bpos = 0isize;

    let mut pids = Vec::with_capacity(1024);
    while bpos < nread as isize {
        let d = &*(proc_buf.as_ptr().offset(bpos) as *const LinuxDirent64);
        let reclen = d.d_reclen as isize;
        if d.d_type == DT_DIR && bpos + 19 < nread as isize {
            let name_bytes = &proc_buf[(bpos + 19) as usize..(bpos + reclen) as usize];
            if !name_bytes.is_empty() && name_bytes[0].is_ascii_digit() {
                if let Some(nul) = name_bytes.iter().position(|&b| b == 0) {
                    if let Ok(s) = std::str::from_utf8(&name_bytes[..nul]) {
                        if let Ok(pid) = s.parse::<i32>() {
                            pids.push(pid);
                        }
                    }
                }
            }
        }
        bpos += reclen;
    }

    // Sort descending (newest / largest PID first)
    pids.sort_unstable_by(|a, b| b.cmp(a));

    let mut satisfied_count = 0i32;
    let total_targets = targets.len();
    let mut fd_buf = [0u8; FD_BUF_SIZE];

    'proc_loop: for &pid in &pids {
        let mut fd_dir_path = [0u8; 32];
        let mut tmp_buf = [0u8; 16];
        let pid_len = write_i32(pid, &mut tmp_buf);
        fd_dir_path[..pid_len].copy_from_slice(&tmp_buf[..pid_len]);
        let sfx = b"/fd\0";
        fd_dir_path[pid_len..pid_len + 4].copy_from_slice(sfx);

        let fd_dir_fd = openat(proc_fd, fd_dir_path.as_ptr() as *const c_char, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if fd_dir_fd >= 0 {
            let mut dir_st: Stat = std::mem::zeroed();
            if fstat(fd_dir_fd, &mut dir_st) != 0 || dir_st.st_uid != my_uid {
                close(fd_dir_fd);
                continue;
            }

            let fd_nread = syscall(SYS_GETDENTS64, fd_dir_fd, fd_buf.as_mut_ptr(), FD_BUF_SIZE);
            let mut fd_bpos = 0isize;

            while fd_bpos < fd_nread as isize {
                let fd_d = &*(fd_buf.as_ptr().offset(fd_bpos) as *const LinuxDirent64);
                let fd_reclen = fd_d.d_reclen as isize;
                if fd_bpos + 19 < fd_nread as isize {
                    let fd_name = &fd_buf[(fd_bpos + 19) as usize..(fd_bpos + fd_reclen) as usize];
                    if !fd_name.is_empty() && fd_name[0] != b'.' {
                        let mut st: Stat = std::mem::zeroed();
                        if fstatat(fd_dir_fd, fd_name.as_ptr() as *const c_char, &mut st, 0) == 0 {
                            for target in &mut targets {
                                if !target.matched && st.st_ino == target.ino && st.st_dev == target.dev {
                                    if let Some(nul) = fd_name.iter().position(|&b| b == 0) {
                                        if let Ok(fd_str) = std::str::from_utf8(&fd_name[..nul]) {
                                            if let Ok(fd_num) = fd_str.parse::<i32>() {
                                                if is_fd_writable(pid, fd_num) {
                                                    target.matched = true;
                                                    *out_pids.add(target.path_idx) = pid;
                                                    satisfied_count += 1;
                                                    if satisfied_count as usize == total_targets {
                                                        close(fd_dir_fd);
                                                        break 'proc_loop;
                                                    }
                                                    break;
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                fd_bpos += fd_reclen;
            }
            close(fd_dir_fd);
        }
    }

    close(proc_fd);
    satisfied_count
}

#[no_mangle]
pub unsafe extern "C" fn fduser_has_writers(
    paths: *const *const c_char,
    num_paths: i32,
    out_matched: *mut i32,
) -> i32 {
    if paths.is_null() || num_paths <= 0 {
        return 0;
    }
    let mut pids = vec![0i32; num_paths as usize];
    let res = fduser_find_writers(paths, num_paths, pids.as_mut_ptr());
    if !out_matched.is_null() {
        *out_matched = if res >= 0 { res } else { 0 };
    }
    res
}

#[no_mangle]
pub unsafe extern "C" fn fduser_has_writer(path: *const c_char) -> i32 {
    if path.is_null() {
        return 0;
    }
    let paths = [path];
    let mut pids = [0i32; 1];
    let res = fduser_find_writers(paths.as_ptr(), 1, pids.as_mut_ptr());
    if res > 0 {
        1
    } else if res < 0 {
        -1
    } else {
        0
    }
}

#[no_mangle]
pub unsafe extern "C" fn fileholder_has_writer(path: *const c_char) -> i32 {
    fduser_has_writer(path)
}

// ── AUR System Info Sampler ──────────────────────────────────────────────────

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct AurSysInfo {
    pub sy: f64,
    pub us: f64,
    pub ni: f64,
    pub id: f64,
    pub wa: f64,
    pub mem_avail_mb: u64,
    pub mem_total_mb: u64,
    pub sw_avail_mb: u64,
    pub sw_total_mb: u64,
    pub st_free_mb: u64,
    pub st_total_mb: u64,
    pub runnable: u64,
    pub cores: u64,
}

#[derive(Clone, Copy)]
struct CpuJiffies {
    user: u64,
    nice: u64,
    system: u64,
    idle: u64,
    iowait: u64,
    total: u64,
}

static CPU_PREV: Mutex<Option<CpuJiffies>> = Mutex::new(None);

fn read_file_to_buf(path: *const c_char, buf: &mut [u8]) -> usize {
    let fd = unsafe { open(path, O_RDONLY | O_CLOEXEC) };
    if fd < 0 {
        return 0;
    }
    let n = unsafe { read(fd, buf.as_mut_ptr() as *mut c_void, buf.len()) };
    unsafe { close(fd) };
    if n > 0 { n as usize } else { 0 }
}

fn parse_kb_val(mut slice: &[u8]) -> Option<u64> {
    while !slice.is_empty() && (slice[0] == b' ' || slice[0] == b'\t') {
        slice = &slice[1..];
    }
    let mut end = 0;
    while end < slice.len() && slice[end].is_ascii_digit() {
        end += 1;
    }
    if end == 0 {
        return None;
    }
    parse_u64(&slice[..end])
}

fn sample_meminfo(info: &mut AurSysInfo) {
    let mut buf = [0u8; 4096];
    let n = read_file_to_buf(b"/proc/meminfo\0".as_ptr() as *const c_char, &mut buf);
    if n == 0 {
        return;
    }
    let data = &buf[..n];
    let mut pos = 0;
    while pos < data.len() {
        let line_start = pos;
        while pos < data.len() && data[pos] != b'\n' {
            pos += 1;
        }
        let line = &data[line_start..pos];
        if pos < data.len() {
            pos += 1;
        }
        if line.starts_with(b"MemAvailable:") {
            if let Some(val) = parse_kb_val(&line[13..]) {
                info.mem_avail_mb = val / 1024;
            }
        } else if line.starts_with(b"MemTotal:") {
            if let Some(val) = parse_kb_val(&line[9..]) {
                info.mem_total_mb = val / 1024;
            }
        } else if line.starts_with(b"SwapFree:") {
            if let Some(val) = parse_kb_val(&line[9..]) {
                info.sw_avail_mb = val / 1024;
            }
        } else if line.starts_with(b"SwapTotal:") {
            if let Some(val) = parse_kb_val(&line[10..]) {
                info.sw_total_mb = val / 1024;
            }
        }
    }
}

fn sample_loadavg(info: &mut AurSysInfo) {
    let mut buf = [0u8; 128];
    let n = read_file_to_buf(b"/proc/loadavg\0".as_ptr() as *const c_char, &mut buf);
    if n == 0 {
        return;
    }
    let data = &buf[..n];
    let mut token_idx = 0;
    let mut pos = 0;
    while pos < data.len() {
        while pos < data.len() && (data[pos] == b' ' || data[pos] == b'\t') {
            pos += 1;
        }
        if pos >= data.len() {
            break;
        }
        let tok_start = pos;
        while pos < data.len() && data[pos] != b' ' && data[pos] != b'\t' && data[pos] != b'\n' {
            pos += 1;
        }
        let tok = &data[tok_start..pos];
        token_idx += 1;
        if token_idx == 4 {
            if let Some(slash) = tok.iter().position(|&b| b == b'/') {
                if let Some(val) = parse_u64(&tok[..slash]) {
                    info.runnable = val;
                }
            }
            break;
        }
    }
}

fn read_cpu_jiffies() -> Option<CpuJiffies> {
    let mut buf = [0u8; 1024];
    let n = read_file_to_buf(b"/proc/stat\0".as_ptr() as *const c_char, &mut buf);
    if n == 0 {
        return None;
    }
    let data = &buf[..n];
    if !data.starts_with(b"cpu ") && !data.starts_with(b"cpu\t") {
        return None;
    }
    let mut pos = 3;
    while pos < data.len() && (data[pos] == b' ' || data[pos] == b'\t') {
        pos += 1;
    }
    let mut vals = [0u64; 10];
    let mut val_count = 0;
    while pos < data.len() && data[pos] != b'\n' && val_count < 10 {
        while pos < data.len() && (data[pos] == b' ' || data[pos] == b'\t') {
            pos += 1;
        }
        if pos >= data.len() || data[pos] == b'\n' {
            break;
        }
        let start = pos;
        while pos < data.len() && data[pos].is_ascii_digit() {
            pos += 1;
        }
        if let Some(v) = parse_u64(&data[start..pos]) {
            vals[val_count] = v;
            val_count += 1;
        }
    }
    if val_count < 5 {
        return None;
    }
    let user = vals[0];
    let nice = vals[1];
    let system = vals[2];
    let idle = vals[3];
    let iowait = vals[4];
    let total: u64 = vals[..val_count].iter().sum();
    Some(CpuJiffies {
        user,
        nice,
        system,
        idle,
        iowait,
        total,
    })
}

fn sample_cpu(info: &mut AurSysInfo) {
    let cur = match read_cpu_jiffies() {
        Some(j) => j,
        None => return,
    };
    let mut lock = match CPU_PREV.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    };
    let prev = match *lock {
        Some(p) => p,
        None => {
            std::thread::sleep(std::time::Duration::from_millis(50));
            if let Some(second) = read_cpu_jiffies() {
                *lock = Some(second);
                let dt = second.total.saturating_sub(cur.total);
                if dt > 0 {
                    let share = |c: u64, p: u64| (c.saturating_sub(p) as f64) * 100.0 / dt as f64;
                    info.us = share(second.user, cur.user);
                    info.ni = share(second.nice, cur.nice);
                    info.sy = share(second.system, cur.system);
                    info.id = share(second.idle, cur.idle);
                    info.wa = share(second.iowait, cur.iowait);
                }
            }
            return;
        }
    };
    let dt = cur.total.saturating_sub(prev.total);
    if dt > 0 {
        let share = |c: u64, p: u64| (c.saturating_sub(p) as f64) * 100.0 / dt as f64;
        info.us = share(cur.user, prev.user);
        info.ni = share(cur.nice, prev.nice);
        info.sy = share(cur.system, prev.system);
        info.id = share(cur.idle, prev.idle);
        info.wa = share(cur.iowait, prev.iowait);
        *lock = Some(cur);
    }
}

fn sample_disk(disk_path: *const c_char, info: &mut AurSysInfo) {
    let p = if disk_path.is_null() {
        b".\0".as_ptr() as *const c_char
    } else {
        disk_path
    };
    let mut st: StatVfs = unsafe { std::mem::zeroed() };
    if unsafe { statvfs(p, &mut st) } == 0 {
        let mb = 1024 * 1024;
        info.st_free_mb = st.f_bavail.saturating_mul(st.f_frsize) / mb;
        info.st_total_mb = st.f_blocks.saturating_mul(st.f_frsize) / mb;
    }
}

#[no_mangle]
pub unsafe extern "C" fn aur_sysinfo_sample(disk_path: *const c_char, out: *mut AurSysInfo) -> c_int {
    if out.is_null() {
        return -1;
    }
    let mut info = AurSysInfo::default();
    info.cores = std::thread::available_parallelism().map_or(1, |n| n.get() as u64);
    sample_meminfo(&mut info);
    sample_loadavg(&mut info);
    sample_disk(disk_path, &mut info);
    sample_cpu(&mut info);
    *out = info;
    0
}

fn resolve_python_name(pid: i32, out: &mut [u8; 64], out_len: &mut usize) -> bool {
    let mut path_buf = [0u8; 32];
    let mut tmp_buf = [0u8; 16];
    let pid_len = write_i32(pid, &mut tmp_buf);
    let pfx = b"/proc/";
    path_buf[..6].copy_from_slice(pfx);
    path_buf[6..6 + pid_len].copy_from_slice(&tmp_buf[..pid_len]);
    let sfx = b"/cmdline\0";
    path_buf[6 + pid_len..6 + pid_len + 9].copy_from_slice(sfx);

    let mut cmd_buf = [0u8; 1024];
    let fd = unsafe { open(path_buf.as_ptr() as *const c_char, O_RDONLY | O_CLOEXEC) };
    if fd < 0 {
        return false;
    }
    let n = unsafe { read(fd, cmd_buf.as_mut_ptr() as *mut c_void, cmd_buf.len() - 1) };
    unsafe { close(fd) };
    if n <= 0 {
        return false;
    }
    let slice = &cmd_buf[..n as usize];
    let args: Vec<&[u8]> = slice.split(|&b| b == 0).filter(|s| !s.is_empty()).collect();
    if args.is_empty() {
        return false;
    }
    let mut i = 1;
    while i < args.len() {
        let arg = args[i];
        if arg == b"-m" && i + 1 < args.len() {
            let mod_b = args[i + 1];
            let len = mod_b.len().min(60);
            out[..len].copy_from_slice(&mod_b[..len]);
            if !mod_b.ends_with(b".py") && len + 3 < 64 {
                out[len..len + 3].copy_from_slice(b".py");
                out[len + 3] = 0;
                *out_len = len + 3;
                return true;
            }
            out[len] = 0;
            *out_len = len;
            return true;
        } else if arg.starts_with(b"-m") && arg.len() > 2 {
            let mod_b = &arg[2..];
            let len = mod_b.len().min(60);
            out[..len].copy_from_slice(&mod_b[..len]);
            if !mod_b.ends_with(b".py") && len + 3 < 64 {
                out[len..len + 3].copy_from_slice(b".py");
                out[len + 3] = 0;
                *out_len = len + 3;
                return true;
            }
            out[len] = 0;
            *out_len = len;
            return true;
        } else if arg == b"-c" {
            return false;
        } else if (arg == b"-W" || arg == b"-X" || arg == b"--check-hash-based-pycs") && i + 1 < args.len() {
            i += 2;
            continue;
        } else if arg.starts_with(b"-") {
            i += 1;
            continue;
        } else {
            let base = if let Some(slash) = arg.iter().rposition(|&b| b == b'/') {
                &arg[slash + 1..]
            } else {
                arg
            };
            if !base.is_empty() {
                let len = base.len().min(63);
                out[..len].copy_from_slice(&base[..len]);
                out[len] = 0;
                *out_len = len;
                return true;
            }
        }
        i += 1;
    }
    false
}

struct ProcessRecord {
    pid: i32,
    fd: c_int,
    comm: [u8; 64],
    comm_len: usize,
    nice: i32,
    cur_ticks: u64,
    prev_ticks: u64,
    cur_time: Instant,
    prev_time: Instant,
    rss_bytes: u64,
    load: f64,
    is_active: bool,
    dormant_polls: u32,
}

impl Drop for ProcessRecord {
    fn drop(&mut self) {
        if self.fd >= 0 {
            unsafe { close(self.fd) };
            self.fd = -1;
        }
    }
}

struct TopGroupState {
    records: Vec<ProcessRecord>,
    last_full_scan: Option<Instant>,
    poll_count: u64,
    page_size: u64,
    clk_tck: f64,
}

static TOP_STATE: Mutex<Option<TopGroupState>> = Mutex::new(None);

fn format_nices(nices: &mut [i32], out: &mut [u8; 32]) -> usize {
    if nices.is_empty() {
        out[0] = b'0';
        out[1] = 0;
        return 1;
    }
    nices.sort_unstable();
    let mut idx = 0;
    for (i, &n) in nices.iter().enumerate() {
        if i > 0 && idx < 30 {
            out[idx] = b',';
            idx += 1;
        }
        if n > 0 && idx < 30 {
            out[idx] = b'+';
            idx += 1;
        }
        let mut num_buf = [0u8; 16];
        let num_len = write_i32(n, &mut num_buf);
        for j in 0..num_len {
            if idx < 31 {
                out[idx] = num_buf[j];
                idx += 1;
            }
        }
    }
    out[idx] = 0;
    idx
}

pub fn native_get_top_process_groups(out_groups: &mut [AurTopGroup]) -> usize {
    let mut guard = match TOP_STATE.lock() {
        Ok(g) => g,
        Err(p) => p.into_inner(),
    };
    let state = guard.get_or_insert_with(|| {
        let ps = unsafe { sysconf(_SC_PAGESIZE) };
        let page_size = if ps > 0 { ps as u64 } else { 4096 };
        let ct = unsafe { sysconf(_SC_CLK_TCK) };
        let clk_tck = if ct > 0 { ct as f64 } else { 100.0 };
        TopGroupState {
            records: Vec::with_capacity(1024),
            last_full_scan: None,
            poll_count: 0,
            page_size,
            clk_tck,
        }
    });

    let now = Instant::now();
    let need_scan = state.records.is_empty()
        || state.last_full_scan.map_or(true, |t| now.duration_since(t).as_secs() >= 10);

    if need_scan {
        let proc_fd = unsafe { open(b"/proc\0".as_ptr() as *const c_char, O_RDONLY | O_DIRECTORY | O_CLOEXEC) };
        if proc_fd >= 0 {
            let mut proc_buf = [0u8; PROC_BUF_SIZE];
            let nread = unsafe { syscall(SYS_GETDENTS64, proc_fd, proc_buf.as_mut_ptr(), PROC_BUF_SIZE) };
            let mut bpos = 0isize;
            let mut current_pids = Vec::with_capacity(1024);
            while bpos < nread as isize {
                let d = unsafe { &*(proc_buf.as_ptr().offset(bpos) as *const LinuxDirent64) };
                let reclen = d.d_reclen as isize;
                if d.d_type == DT_DIR && bpos + 19 < nread as isize {
                    let name_bytes = &proc_buf[(bpos + 19) as usize..(bpos + reclen) as usize];
                    if !name_bytes.is_empty() && name_bytes[0].is_ascii_digit() {
                        if let Some(nul) = name_bytes.iter().position(|&b| b == 0) {
                            if let Some(pid) = parse_i32(&name_bytes[..nul]) {
                                if pid > 0 {
                                    current_pids.push(pid);
                                }
                            }
                        }
                    }
                }
                bpos += reclen;
            }

            let mut stat_buf = [0u8; 1024];
            let mut new_records = Vec::new();
            for pid in current_pids {
                if state.records.binary_search_by_key(&pid, |e| e.pid).is_err() {
                    let mut stat_path = [0u8; 32];
                    let mut tmp_buf = [0u8; 16];
                    let pid_len = write_i32(pid, &mut tmp_buf);
                    stat_path[..pid_len].copy_from_slice(&tmp_buf[..pid_len]);
                    let sfx = b"/stat\0";
                    stat_path[pid_len..pid_len + 6].copy_from_slice(sfx);

                    let sfd = unsafe { openat(proc_fd, stat_path.as_ptr() as *const c_char, O_RDONLY | O_CLOEXEC) };
                    if sfd >= 0 {
                        let sn = unsafe { read(sfd, stat_buf.as_mut_ptr() as *mut c_void, stat_buf.len() - 1) };
                        if sn > 0 {
                            let mut comm = [0u8; 64];
                            let mut comm_len = 0;
                            let mut nice = 0;
                            let mut ticks = 0;
                            let mut rss_bytes = 0;
                            if parse_proc_stat_fields(
                                &stat_buf[..sn as usize],
                                state.page_size,
                                &mut comm,
                                &mut comm_len,
                                &mut nice,
                                &mut ticks,
                                &mut rss_bytes,
                            ) {
                                if comm.starts_with(b"python") {
                                    let mut py_name = [0u8; 64];
                                    let mut py_len = 0;
                                    if resolve_python_name(pid, &mut py_name, &mut py_len) {
                                        comm = py_name;
                                        comm_len = py_len;
                                    }
                                }
                                let is_active = rss_bytes >= 20 * 1024 * 1024;
                                new_records.push(ProcessRecord {
                                    pid,
                                    fd: sfd,
                                    comm,
                                    comm_len,
                                    nice,
                                    cur_ticks: ticks,
                                    prev_ticks: ticks,
                                    cur_time: now,
                                    prev_time: now,
                                    rss_bytes,
                                    load: 0.0,
                                    is_active,
                                    dormant_polls: 0,
                                });
                            } else {
                                unsafe { close(sfd) };
                            }
                        } else {
                            unsafe { close(sfd) };
                        }
                    }
                }
            }
            unsafe { close(proc_fd) };
            if !new_records.is_empty() {
                state.records.extend(new_records);
                state.records.sort_unstable_by_key(|e| e.pid);
            }
            state.last_full_scan = Some(now);
        }
    }

    let is_full_probe = state.poll_count % 5 == 0;
    state.poll_count += 1;

    let mut stat_buf = [0u8; 1024];
    for record in &mut state.records {
        if !is_full_probe && !record.is_active && record.rss_bytes < 50 * 1024 * 1024 {
            continue;
        }

        if unsafe { lseek(record.fd, 0, SEEK_SET) } >= 0 {
            let sn = unsafe { read(record.fd, stat_buf.as_mut_ptr() as *mut c_void, stat_buf.len() - 1) };
            if sn > 0 {
                let mut dummy_comm = [0u8; 64];
                let mut dummy_len = 0;
                let mut nice = 0;
                let mut ticks = 0;
                let mut rss_bytes = 0;
                if parse_proc_stat_fields(
                    &stat_buf[..sn as usize],
                    state.page_size,
                    &mut dummy_comm,
                    &mut dummy_len,
                    &mut nice,
                    &mut ticks,
                    &mut rss_bytes,
                ) {
                    record.nice = nice;
                    record.rss_bytes = rss_bytes;
                    let dt = now.duration_since(record.cur_time).as_secs_f64();
                    if dt > 0.05 {
                        let delta_ticks = ticks.saturating_sub(record.cur_ticks);
                        record.load = ((delta_ticks as f64) / state.clk_tck) / dt * 100.0;
                        record.prev_ticks = record.cur_ticks;
                        record.prev_time = record.cur_time;
                        record.cur_ticks = ticks;
                        record.cur_time = now;
                    }

                    if record.load > 0.0 || record.rss_bytes >= 20 * 1024 * 1024 {
                        record.is_active = true;
                        record.dormant_polls = 0;
                    } else {
                        record.dormant_polls += 1;
                        if record.dormant_polls >= 2 {
                            record.is_active = false;
                        }
                    }
                    continue;
                }
            }
        }
        // Exited or unreadable
        unsafe { close(record.fd) };
        record.fd = -1;
    }

    // Evict exited
    state.records.retain(|r| r.fd >= 0);

    // Group live records by comm
    struct GroupItem {
        name: [u8; 64],
        name_len: usize,
        count: i32,
        nices: [i32; 8],
        nice_count: usize,
        load: f64,
        ticks: u64,
        rss: u64,
    }

    let mut groups: Vec<GroupItem> = Vec::with_capacity(64);
    for r in &state.records {
        if r.rss_bytes == 0 && r.cur_ticks == 0 && r.load == 0.0 {
            continue;
        }
        let comm_slice = &r.comm[..r.comm_len];
        if let Some(g) = groups.iter_mut().find(|g| &g.name[..g.name_len] == comm_slice) {
            g.count += 1;
            g.load += r.load;
            g.ticks += r.cur_ticks;
            g.rss += r.rss_bytes;
            if g.nice_count < 8 && !g.nices[..g.nice_count].contains(&r.nice) {
                g.nices[g.nice_count] = r.nice;
                g.nice_count += 1;
            }
        } else {
            let mut name = [0u8; 64];
            name[..r.comm_len].copy_from_slice(comm_slice);
            let mut nices = [0i32; 8];
            nices[0] = r.nice;
            groups.push(GroupItem {
                name,
                name_len: r.comm_len,
                count: 1,
                nices,
                nice_count: 1,
                load: r.load,
                ticks: r.cur_ticks,
                rss: r.rss_bytes,
            });
        }
    }

    if groups.is_empty() {
        return 0;
    }

    let mut cpu_order: Vec<usize> = (0..groups.len()).collect();
    cpu_order.sort_unstable_by(|&a, &b| {
        let ga = &groups[a];
        let gb = &groups[b];
        gb.load.partial_cmp(&ga.load).unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| gb.ticks.cmp(&ga.ticks))
    });

    let mut rss_order: Vec<usize> = (0..groups.len()).collect();
    rss_order.sort_unstable_by(|&a, &b| {
        groups[b].rss.cmp(&groups[a].rss)
    });

    let g1 = cpu_order.get(0).copied();
    let g2 = cpu_order.get(1).copied();
    let top_rss = rss_order.get(0).copied();

    let (g3, tag3) = if let Some(rss_idx) = top_rss {
        if Some(rss_idx) != g1 && Some(rss_idx) != g2 {
            (Some(rss_idx), b"rss\0")
        } else {
            (cpu_order.get(2).copied(), b"cpu\0")
        }
    } else {
        (cpu_order.get(2).copied(), b"cpu\0")
    };

    let selected: &[(Option<usize>, &[u8])] = &[
        (g1, b"cpu\0"),
        (g2, b"cpu\0"),
        (g3, tag3),
    ];

    let mut written = 0;
    for &(opt_idx, tag) in selected {
        if written >= out_groups.len() {
            break;
        }
        if let Some(idx) = opt_idx {
            let g = &mut groups[idx];
            let out = &mut out_groups[written];
            let tag_len = tag.iter().position(|&b| b == 0).unwrap_or(tag.len());
            out.rank[..tag_len].copy_from_slice(&tag[..tag_len]);
            out.rank[tag_len] = 0;
            out.count = g.count;
            out.name = g.name;
            format_nices(&mut g.nices[..g.nice_count], &mut out.nice);
            out.load = (g.load * 10.0).round() / 10.0;
            out.cputime_s = (g.ticks as f64) / state.clk_tck;
            out.rss = g.rss;
            written += 1;
        }
    }
    written
}

#[no_mangle]
pub unsafe extern "C" fn aur_get_top_process_groups(
    out_groups: *mut AurTopGroup,
    max_groups: i32,
) -> i32 {
    if out_groups.is_null() || max_groups <= 0 {
        return 0;
    }
    let slice = std::slice::from_raw_parts_mut(out_groups, max_groups as usize);
    native_get_top_process_groups(slice) as i32
}


