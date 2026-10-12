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
}

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
    let mut count = 0i32;

    while bpos < nread as isize && count < max_entries {
        let d = &*(proc_buf.as_ptr().offset(bpos) as *const LinuxDirent64);
        let reclen = d.d_reclen as isize;
        if d.d_type == DT_DIR && bpos + 19 < nread as isize {
            let name_bytes = &proc_buf[(bpos + 19) as usize..(bpos + reclen) as usize];
            if !name_bytes.is_empty() && name_bytes[0].is_ascii_digit() {
                if let Some(nul) = name_bytes.iter().position(|&b| b == 0) {
                    if let Ok(pid_str) = std::str::from_utf8(&name_bytes[..nul]) {
                        if let Ok(pid) = pid_str.parse::<i32>() {
                            if pid > 0 {
                                let mut stat_path = [0u8; 32];
                                let mut n = 0;
                                let pid_b = pid_str.as_bytes();
                                stat_path[..pid_b.len()].copy_from_slice(pid_b);
                                n += pid_b.len();
                                let sfx = b"/stat\0";
                                stat_path[n..n + 6].copy_from_slice(sfx);

                                let sfd = openat(proc_fd, stat_path.as_ptr() as *const c_char, O_RDONLY | O_CLOEXEC);
                                if sfd >= 0 {
                                    let mut stat_buf = [0u8; 1024];
                                    let sn = read(sfd, stat_buf.as_mut_ptr() as *mut c_void, stat_buf.len() - 1);
                                    close(sfd);
                                    if sn > 0 {
                                        let sslice = &stat_buf[..sn as usize];
                                        if let Some(open_p) = sslice.iter().position(|&b| b == b'(') {
                                            if let Some(close_p) = sslice.iter().rposition(|&b| b == b')') {
                                                if close_p > open_p {
                                                    let comm_bytes = &sslice[open_p + 1..close_p];
                                                    let rest = &sslice[close_p + 1..];

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

                                                        let entry = &mut *out_stats.offset(count as isize);
                                                        entry.pid = pid;
                                                        entry.nice = nice;
                                                        entry.ticks = utime + stime;
                                                        entry.rss_bytes = rss_pages * page_size;

                                                        let copy_len = comm_bytes.len().min(63);
                                                        entry.comm[..copy_len].copy_from_slice(&comm_bytes[..copy_len]);
                                                        entry.comm[copy_len] = 0;

                                                        count += 1;
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        bpos += reclen;
    }

    close(proc_fd);
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
