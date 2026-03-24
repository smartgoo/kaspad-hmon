# kaspad-hmon

Currently supports monitoring disk on Linux only.

Samples disk I/O for a given process, for a given duration, at given intervals. Produces a summary report & disk chart.

A number of ways this can be improved:
- Support macOS/Windows
- Support CPU/mem
- Jupyter notebook
- Integration to kaspad via RPC to correlate hardware utilization to events (to the extent possible for an external monitor like this)


## Usage

```
sudo python3 linux.py <pid> [-m MINUTES] [-i INTERVAL]
```

The `<pid>` should be the **kaspad process**.

- `-m` — Duration to monitor in minutes (default: 5)
- `-i` — Sampling interval in seconds (default: 1)

Requires `sudo` to read `/proc/<pid>/io` for other users' processes.

Output is written to `reports/<process>_<pid>_<timestamp>/` containing:
- `proc_io_raw.log` — Raw sample data
- `summary` — Plain text summary with aggregate and per-process stats
- `io_metrics.jpg` — Chart showing disk throughput, VFS throughput, and syscall rates over time

## Metrics

All metrics come from `/proc/<pid>/io`, which the Linux kernel exposes per-process.

**Disk Read (`read_bytes`)** — Bytes actually fetched from the storage device. This reflects real physical I/O that hit disk. If data was served from the page cache (RAM), it does not appear here.

**Disk Written (`write_bytes`)** — Bytes actually sent to the storage device. This is the real write load on your disk. Linux buffers writes in the page cache and flushes them asynchronously, so this may lag behind when the process called `write()`.

**Cancelled Writes (`cancelled_write_bytes`)** — Bytes that were staged for write-back to disk but then cancelled before being flushed. This typically happens when a process writes to a temporary file and deletes it before the kernel flushes dirty pages to disk. High values here mean the process is churning through temp data that never persists.

**VFS Read (`rchar`)** — Total bytes the process requested via `read()` syscalls, regardless of where the data came from. This includes data served from the page cache, pipes, sockets, and actual disk reads. It is always >= `read_bytes`. The gap between `rchar` and `read_bytes` tells you how much was served from cache.

**VFS Write (`wchar`)** — Total bytes the process passed to `write()` syscalls. Includes writes to pipes, sockets, and buffered file writes that may not have hit disk yet. Always >= `write_bytes`.

**Read Syscalls (`syscr`)** — Number of `read()`-family syscall invocations (not bytes). High counts with low byte totals indicate many small reads.

**Write Syscalls (`syscw`)** — Number of `write()`-family syscall invocations.
