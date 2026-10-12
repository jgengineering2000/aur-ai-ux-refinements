// fduser.c — High-performance single-pass discovery of PIDs holding files open
//
// Features:
// - Takes multiple file paths and checks them all in a single pass over /proc.
// - Canonical kernel identity matching (dev + ino) via fstatat (zero heap allocation).
// - Early-exit (-1 / --first): stops as soon as at least one match is found for EACH listed file.
// - Optional -q / --quiet: suppresses output, exits 0 if all targets matched, 1 otherwise.
// - Optional --fdinfo: reports open mode (r, w, rw), file position (pos), octal flags, mnt_id, and comm.
// - Optional -w / --writable: filters to only report processes holding the file for writing.
// - Optional --json: outputs structured JSON lines.
// - Static buffer output flushed via direct write(2) to bypass stdio locking and line-buffering overhead.
// - Single-threaded, zero extra background threads, zero external library dependencies.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <dirent.h>
#include <ctype.h>

#define PROC_BUF_SIZE 65536
#define FD_BUF_SIZE   8192
#define OUT_BUF_SIZE  8192

static int cmp_int_desc(const void *a, const void *b) {
    return (*(const int *)b - *(const int *)a);
}

struct linux_dirent64 {
    unsigned long long d_ino;
    long long          d_off;
    unsigned short     d_reclen;
    unsigned char      d_type;
    char               d_name[];
};

struct target_file {
    const char *path;
    dev_t dev;
    ino_t ino;
    int matched_count;
};

struct options {
    int fdinfo;
    int writable_only;
    int json;
    int all_users;
    int first_match_each;
    int quiet;
    int total_matches;
};

struct fd_info_data {
    long long pos;
    unsigned int flags;
    int mnt_id;
    const char *mode_str;
    int is_writable;
};

static char out_buf[OUT_BUF_SIZE];
static size_t out_len = 0;

static inline void flush_out(void) {
    if (out_len > 0) {
        ssize_t written = write(STDOUT_FILENO, out_buf, out_len);
        (void)written;
        out_len = 0;
    }
}

static inline void append_out(const char *str, size_t len) {
    if (out_len + len >= sizeof(out_buf)) {
        flush_out();
    }
    memcpy(out_buf + out_len, str, len);
    out_len += len;
}

static int read_fdinfo(int pid, int fd_num, struct fd_info_data *out) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/fdinfo/%d", pid, fd_num);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;

    char buf[512];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    close(fd);
    if (n <= 0) return -1;
    buf[n] = '\0';

    out->pos = 0;
    out->flags = 0;
    out->mnt_id = 0;

    char *p = buf;
    while (*p) {
        if (strncmp(p, "pos:\t", 5) == 0) {
            out->pos = strtoll(p + 5, NULL, 10);
        } else if (strncmp(p, "flags:\t", 7) == 0) {
            out->flags = (unsigned int)strtoul(p + 7, NULL, 8);
        } else if (strncmp(p, "mnt_id:\t", 8) == 0) {
            out->mnt_id = atoi(p + 8);
        }
        char *nl = strchr(p, '\n');
        if (!nl) break;
        p = nl + 1;
    }

    int acc = out->flags & O_ACCMODE;
    if (acc == O_RDONLY) {
        out->mode_str = "r";
        out->is_writable = 0;
    } else if (acc == O_WRONLY) {
        out->mode_str = "w";
        out->is_writable = 1;
    } else if (acc == O_RDWR) {
        out->mode_str = "rw";
        out->is_writable = 1;
    } else {
        out->mode_str = "?";
        out->is_writable = 0;
    }
    return 0;
}

#ifndef LIB_BUILD
static void read_comm(int pid, char *out, size_t max_len) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%d/comm", pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd >= 0) {
        ssize_t n = read(fd, out, max_len - 1);
        close(fd);
        if (n > 0) {
            if (out[n - 1] == '\n') n--;
            out[n] = '\0';
            return;
        }
    }
    snprintf(out, max_len, "%d", pid);
}
#endif

// ---------------------------------------------------------------------------
// Exported C API for shared library (ctypes) callers
// ---------------------------------------------------------------------------

// Sets out_pids[i] to the PID holding paths[i] open for write, or 0 if none.
// Returns count of paths with writers (>= 0), or -1 on error.
int fduser_find_writers(const char **paths, int num_paths, int *out_pids) {
    if (!paths || num_paths <= 0 || !out_pids) return 0;
    memset(out_pids, 0, num_paths * sizeof(int));

    struct target_file targets[num_paths];
    int valid_targets = 0;
    int target_to_path_idx[num_paths];

    for (int i = 0; i < num_paths; i++) {
        struct stat st;
        if (paths[i] && stat(paths[i], &st) == 0) {
            targets[valid_targets].path = paths[i];
            targets[valid_targets].dev = st.st_dev;
            targets[valid_targets].ino = st.st_ino;
            targets[valid_targets].matched_count = 0;
            target_to_path_idx[valid_targets] = i;
            valid_targets++;
        }
    }

    if (valid_targets == 0) {
        return 0;
    }

    int targets_satisfied = 0;
    int proc_fd = open("/proc", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (proc_fd < 0) {
        return -1;
    }

    uid_t my_uid = getuid();
    char proc_buf[PROC_BUF_SIZE];
    char fd_buf[FD_BUF_SIZE];

    long nread = syscall(SYS_getdents64, proc_fd, proc_buf, PROC_BUF_SIZE);
    long bpos = 0;

    int pids[4096];
    int num_pids = 0;

    while (bpos < nread) {
        struct linux_dirent64 *d = (struct linux_dirent64 *)(proc_buf + bpos);
        if (d->d_type == DT_DIR && isdigit((unsigned char)d->d_name[0])) {
            if (num_pids < 4096) {
                pids[num_pids++] = atoi(d->d_name);
            }
        }
        bpos += d->d_reclen;
    }

    // Process backward from largest to smallest PID
    qsort(pids, num_pids, sizeof(int), cmp_int_desc);

    for (int p_idx = 0; p_idx < num_pids; p_idx++) {
        int pid = pids[p_idx];

        char fd_dir_path[64];
        snprintf(fd_dir_path, sizeof(fd_dir_path), "%d/fd", pid);

        int fd_dir_fd = openat(proc_fd, fd_dir_path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
        if (fd_dir_fd >= 0) {
            struct stat dir_st;
            if (fstat(fd_dir_fd, &dir_st) != 0 || dir_st.st_uid != my_uid) {
                close(fd_dir_fd);
                continue;
            }

            long fd_nread = syscall(SYS_getdents64, fd_dir_fd, fd_buf, FD_BUF_SIZE);
            long fd_bpos = 0;

            while (fd_bpos < fd_nread) {
                struct linux_dirent64 *fd_d = (struct linux_dirent64 *)(fd_buf + fd_bpos);
                if (fd_d->d_name[0] != '.') {
                    struct stat st;
                    if (fstatat(fd_dir_fd, fd_d->d_name, &st, 0) == 0) {
                        for (int i = 0; i < valid_targets; i++) {
                            if (targets[i].matched_count == 0 &&
                                st.st_ino == targets[i].ino && st.st_dev == targets[i].dev) {
                                int fd_num = atoi(fd_d->d_name);
                                struct fd_info_data info = {0};
                                read_fdinfo(pid, fd_num, &info);
                                if (info.is_writable) {
                                    targets[i].matched_count = 1;
                                    out_pids[target_to_path_idx[i]] = pid;
                                    targets_satisfied++;
                                    if (targets_satisfied == valid_targets) {
                                        close(fd_dir_fd);
                                        goto done;
                                    }
                                    break;
                                }
                            }
                        }
                    }
                }
                fd_bpos += fd_d->d_reclen;
            }
            close(fd_dir_fd);
        }
    }

done:
    close(proc_fd);
    return targets_satisfied;
}

int fduser_has_writers(const char **paths, int num_paths, int *out_matched) {
    if (!paths || num_paths <= 0) return 0;
    int pids[num_paths];
    int res = fduser_find_writers(paths, num_paths, pids);
    if (out_matched) *out_matched = (res >= 0) ? res : 0;
    return res;
}

int fduser_has_writer(const char *path) {
    if (!path) return 0;
    const char *paths[1] = { path };
    int pids[1] = { 0 };
    int res = fduser_find_writers(paths, 1, pids);
    return (res > 0) ? 1 : (res < 0 ? -1 : 0);
}

// Backwards compatibility alias
int fileholder_has_writer(const char *path) {
    return fduser_has_writer(path);
}

#ifndef LIB_BUILD
static void print_usage(const char *prog) {
    printf("Usage: %s [OPTIONS] <file1> [file2 ...]\n"
           "Options:\n"
           "  -1, --first     Early exit as soon as at least one match is found for EACH listed file\n"
           "  -q, --quiet     Quiet mode: exit 0 if all listed files matched, 1 otherwise (implies -1)\n"
           "  -w, --writable  Only report holders with write access (O_WRONLY/O_RDWR)\n"
           "  --fdinfo        Read and display mode, pos, octal flags, and mnt_id from fdinfo\n"
           "  --json          Output results as JSON lines\n"
           "  --all-users     Scan all processes on the machine (default: current user only)\n"
           "  -h, --help      Display this help message and exit\n",
           prog);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 2;
    }

    struct options opts = {0};
    const char *paths[argc];
    int num_paths = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            print_usage(argv[0]);
            return 0;
        } else if (strcmp(argv[i], "--fdinfo") == 0) {
            opts.fdinfo = 1;
        } else if (strcmp(argv[i], "-w") == 0 || strcmp(argv[i], "--writable") == 0) {
            opts.writable_only = 1;
        } else if (strcmp(argv[i], "-1") == 0 || strcmp(argv[i], "--first") == 0) {
            opts.first_match_each = 1;
        } else if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            opts.quiet = 1;
            opts.first_match_each = 1;
        } else if (strcmp(argv[i], "--json") == 0) {
            opts.json = 1;
        } else if (strcmp(argv[i], "--all-users") == 0) {
            opts.all_users = 1;
        } else if (argv[i][0] == '-') {
            fprintf(stderr, "Unknown option: %s\n", argv[i]);
            return 2;
        } else {
            paths[num_paths++] = argv[i];
        }
    }

    if (num_paths == 0) {
        fprintf(stderr, "Error: No target paths specified.\n");
        return 2;
    }

    // 1. Stat all target files up front
    struct target_file targets[num_paths];
    int valid_targets = 0;
    for (int i = 0; i < num_paths; i++) {
        struct stat st;
        if (stat(paths[i], &st) == 0) {
            targets[valid_targets].path = paths[i];
            targets[valid_targets].dev = st.st_dev;
            targets[valid_targets].ino = st.st_ino;
            targets[valid_targets].matched_count = 0;
            valid_targets++;
        }
    }

    if (valid_targets == 0) {
        return 1; // None of the target files exist
    }

    int targets_satisfied = 0;

    int proc_fd = open("/proc", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (proc_fd < 0) {
        perror("open /proc");
        return 2;
    }

    uid_t my_uid = getuid();
    char proc_buf[PROC_BUF_SIZE];
    char fd_buf[FD_BUF_SIZE];
    char line_buf[512];

    long nread = syscall(SYS_getdents64, proc_fd, proc_buf, PROC_BUF_SIZE);
    long bpos = 0;

    int pids[4096];
    int num_pids = 0;

    while (bpos < nread) {
        struct linux_dirent64 *d = (struct linux_dirent64 *)(proc_buf + bpos);
        if (d->d_type == DT_DIR && isdigit((unsigned char)d->d_name[0])) {
            if (num_pids < 4096) {
                pids[num_pids++] = atoi(d->d_name);
            }
        }
        bpos += d->d_reclen;
    }

    // Process backward from largest to smallest PID
    qsort(pids, num_pids, sizeof(int), cmp_int_desc);

    for (int p_idx = 0; p_idx < num_pids; p_idx++) {
        int pid = pids[p_idx];

        char fd_dir_path[64];
        snprintf(fd_dir_path, sizeof(fd_dir_path), "%d/fd", pid);

        int fd_dir_fd = openat(proc_fd, fd_dir_path, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
            if (fd_dir_fd >= 0) {
                // If not all_users, check directory UID
                if (!opts.all_users) {
                    struct stat dir_st;
                    if (fstat(fd_dir_fd, &dir_st) != 0 || dir_st.st_uid != my_uid) {
                        close(fd_dir_fd);
                        continue;
                    }
                }

                long fd_nread = syscall(SYS_getdents64, fd_dir_fd, fd_buf, FD_BUF_SIZE);
                long fd_bpos = 0;

                while (fd_bpos < fd_nread) {
                    struct linux_dirent64 *fd_d = (struct linux_dirent64 *)(fd_buf + fd_bpos);
                    if (fd_d->d_name[0] != '.') {
                        struct stat st;
                        if (fstatat(fd_dir_fd, fd_d->d_name, &st, 0) == 0) {
                            for (int i = 0; i < valid_targets; i++) {
                                if (st.st_ino == targets[i].ino && st.st_dev == targets[i].dev) {
                                    int fd_num = atoi(fd_d->d_name);
                                    struct fd_info_data info = {0};

                                    if (opts.fdinfo || opts.writable_only) {
                                        read_fdinfo(pid, fd_num, &info);
                                    }

                                    if (opts.writable_only && !info.is_writable) {
                                        continue;
                                    }

                                    if (targets[i].matched_count == 0) {
                                        targets_satisfied++;
                                    }
                                    targets[i].matched_count++;
                                    opts.total_matches++;

                                    if (!opts.quiet) {
                                        char comm[64];
                                        read_comm(pid, comm, sizeof(comm));

                                        int len = 0;
                                        if (opts.json) {
                                            if (opts.fdinfo) {
                                                len = snprintf(line_buf, sizeof(line_buf),
                                                    "{\"target\":\"%s\",\"pid\":%d,\"comm\":\"%s\",\"fd\":%d,\"writable\":%s,\"mode\":\"%s\",\"pos\":%lld,\"flags\":\"0%o\",\"mnt_id\":%d}\n",
                                                    targets[i].path, pid, comm, fd_num,
                                                    info.is_writable ? "true" : "false",
                                                    info.mode_str, info.pos, info.flags, info.mnt_id);
                                            } else {
                                                len = snprintf(line_buf, sizeof(line_buf),
                                                    "{\"target\":\"%s\",\"pid\":%d,\"comm\":\"%s\",\"fd\":%d,\"writable\":%s}\n",
                                                    targets[i].path, pid, comm, fd_num,
                                                    info.is_writable ? "true" : "false");
                                            }
                                        } else if (opts.fdinfo) {
                                            len = snprintf(line_buf, sizeof(line_buf),
                                                "[MATCH] target=%s pid=%d comm=%s fd=%d mode=%s pos=%lld flags=0%o mnt_id=%d\n",
                                                targets[i].path, pid, comm, fd_num,
                                                info.mode_str, info.pos, info.flags, info.mnt_id);
                                        } else {
                                            len = snprintf(line_buf, sizeof(line_buf),
                                                "[MATCH] target=%s pid=%d comm=%s fd=%d\n",
                                                targets[i].path, pid, comm, fd_num);
                                        }
                                        if (len > 0) {
                                            append_out(line_buf, (size_t)len);
                                        }
                                    }

                                    if (opts.first_match_each && targets_satisfied == valid_targets) {
                                        close(fd_dir_fd);
                                        goto scan_done;
                                    }
                                    break;
                                }
                            }
                        }
                    }
                    fd_bpos += fd_d->d_reclen;
                }
            close(fd_dir_fd);
        }
    }

scan_done:
    flush_out();
    close(proc_fd);

    if (opts.first_match_each) {
        return (targets_satisfied == valid_targets) ? 0 : 1;
    }
    return (opts.total_matches > 0) ? 0 : 1;
}
#endif // !LIB_BUILD
