# iomonitor

Monitor disk I/O per-process via `/proc/<pid>/io`.

## Usage

```
sudo python3 iomonitor.py <pid> [-m MINUTES] [-i INTERVAL] [-o OUTPUT_DIR]
```

- `-m` — Duration to monitor in minutes (default: 5)
- `-i` — Sampling interval in seconds (default: 1)
- `-o` — Parent directory for run output (default: `reports`)

Output is written to `reports/<process>_<pid>_<timestamp>/` containing:
- `proc_io_raw.log` — Raw sample data
- `summary` — Plain text summary with aggregate and per-process stats

## Metrics

All metrics come from `/proc/<pid>/io`, which the Linux kernel exposes per-process.

**Disk Read (`read_bytes`)** — Bytes actually fetched from the storage device. This reflects real physical I/O that hit disk. If data was served from the page cache (RAM), it does not appear here.

**Disk Written (`write_bytes`)** — Bytes actually sent to the storage device. This is the real write load on your disk. Linux buffers writes in the page cache and flushes them asynchronously, so this may lag behind when the process called `write()`.

**Cancelled Writes (`cancelled_write_bytes`)** — Bytes that were staged for write-back to disk but then cancelled before being flushed. This typically happens when a process writes to a temporary file and deletes it before the kernel flushes dirty pages to disk. High values here mean the process is churning through temp data that never persists.

**VFS Read (`rchar`)** — Total bytes the process requested via `read()` syscalls, regardless of where the data came from. This includes data served from the page cache, pipes, sockets, and actual disk reads. It is always >= `read_bytes`. The gap between `rchar` and `read_bytes` tells you how much was served from cache.

**VFS Write (`wchar`)** — Total bytes the process passed to `write()` syscalls. Includes writes to pipes, sockets, and buffered file writes that may not have hit disk yet. Always >= `write_bytes`.

**Read Syscalls (`syscr`)** — Number of `read()`-family syscall invocations (not bytes). High counts with low byte totals indicate many small reads, which can be a performance concern due to syscall overhead.

**Write Syscalls (`syscw`)** — Number of `write()`-family syscall invocations. Lots of small writes may indicate the process could benefit from buffering.
