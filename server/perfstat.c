// perfstat.c — Fast in-process process tree metrics and top-process statistics
//
// Extracts PID, comm, nice, total CPU ticks (utime + stime), and RSS in bytes
// directly from /proc/<pid>/stat without heap allocations or Python string splitting.
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <dirent.h>
#include <stdint.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <ctype.h>

#define PROC_BUF_SIZE 65536
#define STAT_BUF_SIZE 1024

struct linux_dirent64 {
    unsigned long long d_ino;
    long long          d_off;
    unsigned short     d_reclen;
    unsigned char      d_type;
    char               d_name[];
};

struct aur_proc_stat {
    int32_t pid;
    char comm[64];
    int32_t nice;
    uint64_t ticks;
    uint64_t rss_bytes;
};

// Reads /proc and extracts process stats for all accessible PIDs into out_stats.
// Returns the count of extracted entries (>= 0), or -1 on error.
int aur_get_proc_stats(struct aur_proc_stat *out_stats, int max_entries) {
    if (!out_stats || max_entries <= 0) return 0;

    int proc_fd = open("/proc", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (proc_fd < 0) return -1;

    static long cached_page_size = 0;
    if (cached_page_size == 0) {
        cached_page_size = sysconf(_SC_PAGESIZE);
        if (cached_page_size <= 0) cached_page_size = 4096;
    }

    char proc_buf[PROC_BUF_SIZE];
    long nread = syscall(SYS_getdents64, proc_fd, proc_buf, PROC_BUF_SIZE);
    long bpos = 0;
    int count = 0;

    char stat_buf[STAT_BUF_SIZE];

    while (bpos < nread && count < max_entries) {
        struct linux_dirent64 *d = (struct linux_dirent64 *)(proc_buf + bpos);
        if (d->d_type == DT_DIR && isdigit((unsigned char)d->d_name[0])) {
            int pid = atoi(d->d_name);
            if (pid > 0) {
                char stat_path[64];
                snprintf(stat_path, sizeof(stat_path), "%d/stat", pid);
                int sfd = openat(proc_fd, stat_path, O_RDONLY | O_CLOEXEC);
                if (sfd >= 0) {
                    ssize_t sn = read(sfd, stat_buf, sizeof(stat_buf) - 1);
                    close(sfd);
                    if (sn > 0) {
                        stat_buf[sn] = '\0';
                        char *open_p = strchr(stat_buf, '(');
                        char *close_p = strrchr(stat_buf, ')');
                        if (open_p && close_p && close_p > open_p) {
                            struct aur_proc_stat *e = &out_stats[count];
                            e->pid = pid;

                            size_t comm_len = (size_t)(close_p - (open_p + 1));
                            if (comm_len >= sizeof(e->comm)) comm_len = sizeof(e->comm) - 1;
                            memcpy(e->comm, open_p + 1, comm_len);
                            e->comm[comm_len] = '\0';

                            // Parse space-separated fields after closing paren
                            char *tokens[24];
                            int num_toks = 0;
                            char *p = close_p + 1;
                            while (*p && num_toks < 24) {
                                while (*p == ' ') p++;
                                if (!*p) break;
                                tokens[num_toks++] = p;
                                while (*p && *p != ' ') p++;
                                if (*p == ' ') {
                                    *p = '\0';
                                    p++;
                                }
                            }

                            if (num_toks >= 22) {
                                uint64_t utime = strtoull(tokens[11], NULL, 10);
                                uint64_t stime = strtoull(tokens[12], NULL, 10);
                                int32_t nice = (int32_t)strtol(tokens[16], NULL, 10);
                                uint64_t rss_pages = strtoull(tokens[21], NULL, 10);

                                e->ticks = utime + stime;
                                e->nice = nice;
                                e->rss_bytes = rss_pages * (uint64_t)cached_page_size;
                                count++;
                            }
                        }
                    }
                }
            }
        }
        bpos += d->d_reclen;
    }

    close(proc_fd);
    return count;
}
