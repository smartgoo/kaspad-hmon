import argparse
import os
import subprocess
import sys
import time
from datetime import datetime


def parse_args():
    parser = argparse.ArgumentParser(description="Monitor disk I/O via /proc/<pid>/io")
    parser.add_argument("pid", type=int, help="PID of the process to monitor")
    parser.add_argument("-m", "--minutes", type=float, default=5, help="Duration to monitor in minutes (default: 5)")
    parser.add_argument("-i", "--interval", type=float, default=1, help="Sampling interval in seconds (default: 1)")
    return parser.parse_args()


def check_pid(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        print(f"Error: PID {pid} does not exist")
        sys.exit(1)
    except PermissionError:
        pass


def get_process_name(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        return "unknown"


def get_descendant_pids(pid):
    pids = set()
    try:
        result = subprocess.run(
            ["ps", "--ppid", str(pid), "-o", "pid="],
            capture_output=True, text=True,
        )
        for line in result.stdout.strip().splitlines():
            child_pid = int(line.strip())
            pids.add(child_pid)
            pids.update(get_descendant_pids(child_pid))
    except (ValueError, subprocess.SubprocessError):
        pass
    return pids


def read_proc_io(pid):
    """Read /proc/<pid>/io and return dict of counters, or None if unreadable."""
    try:
        with open(f"/proc/{pid}/io") as f:
            data = {}
            for line in f:
                key, val = line.strip().split(": ")
                data[key] = int(val)
            return data
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def get_parent_pid(pid):
    """Return the parent PID by reading /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            parts = f.read().split()
            return int(parts[3])
    except (FileNotFoundError, PermissionError, IndexError, ValueError):
        return None


def get_process_tree_info(pid):
    """Build a dict mapping each tracked PID to its role (parent/child), name, and ppid."""
    tree = {}
    tree[pid] = {
        "name": get_process_name(pid),
        "role": "parent",
        "ppid": get_parent_pid(pid),
    }
    for child in get_descendant_pids(pid):
        tree[child] = {
            "name": get_process_name(child),
            "role": "child",
            "ppid": get_parent_pid(child),
        }
    return tree


def collect_samples(pid, interval, total_samples, raw_path):
    print(f"Logging to: {raw_path}")
    print(f"Monitoring for {total_samples * interval:.0f}s ({total_samples} samples @ {interval}s interval)")
    print()

    seen_pids = set()
    prev_counters = {}
    samples = []
    per_pid_samples = {}  # pid -> list of delta dicts
    pid_info = {}  # pid -> {name, role, ppid}

    with open(raw_path, "w") as f:
        f.write(f"# /proc/<pid>/io monitor started {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"# Target PID: {pid}, interval: {interval}s, samples: {total_samples}\n")
        f.write(f"# Fields: timestamp, read_bytes_delta, write_bytes_delta, cancelled_write_bytes_delta, "
                f"rchar_delta, wchar_delta, syscr_delta, syscw_delta, pids_tracked\n\n")

        for i in range(total_samples):
            all_pids = {pid} | get_descendant_pids(pid)
            new_pids = all_pids - seen_pids
            if new_pids:
                msg = f"# [{datetime.now():%H:%M:%S}] Tracking PIDs: {sorted(all_pids)}"
                if seen_pids:
                    msg += f" (new: {sorted(new_pids)})"
                print(msg)
                f.write(msg + "\n")
                seen_pids = all_pids
                # Record info for any new PIDs
                for np in new_pids:
                    if np not in pid_info:
                        pid_info[np] = {
                            "name": get_process_name(np),
                            "role": "parent" if np == pid else "child",
                            "ppid": get_parent_pid(np),
                        }

            gone_pids = seen_pids - all_pids
            if gone_pids:
                msg = f"# [{datetime.now():%H:%M:%S}] PIDs exited: {sorted(gone_pids)}"
                print(msg)
                f.write(msg + "\n")
                for gp in gone_pids:
                    prev_counters.pop(gp, None)
                seen_pids = all_pids

            now = datetime.now()
            total_deltas = {
                "read_bytes": 0, "write_bytes": 0, "cancelled_write_bytes": 0,
                "rchar": 0, "wchar": 0, "syscr": 0, "syscw": 0,
            }
            per_pid_lines = []

            for p in sorted(all_pids):
                io = read_proc_io(p)
                if io is None:
                    continue

                prev = prev_counters.get(p)
                if prev is not None:
                    pid_deltas = {}
                    for key in total_deltas:
                        delta = max(0, io.get(key, 0) - prev.get(key, 0))
                        total_deltas[key] += delta
                        pid_deltas[key] = delta
                    per_pid_lines.append(
                        f"#   PID {p}: rb={pid_deltas['read_bytes']} "
                        f"wb={pid_deltas['write_bytes']}"
                    )
                    if i > 0:
                        per_pid_samples.setdefault(p, []).append(pid_deltas)

                prev_counters[p] = io

            ts = now.strftime("%H:%M:%S")

            line = (f"{ts}  read_bytes={total_deltas['read_bytes']}  "
                    f"write_bytes={total_deltas['write_bytes']}  "
                    f"cancelled={total_deltas['cancelled_write_bytes']}  "
                    f"rchar={total_deltas['rchar']}  wchar={total_deltas['wchar']}  "
                    f"syscr={total_deltas['syscr']}  syscw={total_deltas['syscw']}  "
                    f"pids={sorted(all_pids)}")
            f.write(line + "\n")
            for pl in per_pid_lines:
                f.write(pl + "\n")
            f.flush()

            if i > 0:
                samples.append({"time": ts, "interval": interval, **total_deltas})

            time.sleep(interval)

    return samples, per_pid_samples, pid_info


def _format_bytes(b):
    """Format byte count to a human-readable string."""
    if b >= 1024 * 1024 * 1024:
        return f"{b / 1024 / 1024 / 1024:.2f} GB"
    if b >= 1024 * 1024:
        return f"{b / 1024 / 1024:.1f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


def _stats_section(samples_list, interval):
    """Build stats lines for a list of sample dicts. Returns a string."""
    if not samples_list:
        return "No I/O samples recorded.\n"

    n = len(samples_list)
    read_rates = [s["read_bytes"] / interval / 1024 for s in samples_list]
    write_rates = [s["write_bytes"] / interval / 1024 for s in samples_list]
    rchar_rates = [s["rchar"] / interval / 1024 for s in samples_list]
    wchar_rates = [s["wchar"] / interval / 1024 for s in samples_list]
    syscr_rates = [s["syscr"] / interval for s in samples_list]
    syscw_rates = [s["syscw"] / interval for s in samples_list]

    total_read = sum(s["read_bytes"] for s in samples_list)
    total_write = sum(s["write_bytes"] for s in samples_list)
    total_cancelled = sum(s["cancelled_write_bytes"] for s in samples_list)
    total_rchar = sum(s["rchar"] for s in samples_list)
    total_wchar = sum(s["wchar"] for s in samples_list)
    total_syscr = sum(s["syscr"] for s in samples_list)
    total_syscw = sum(s["syscw"] for s in samples_list)

    return f"""Disk Throughput - read_bytes / write_bytes (KB/s)
{'-' * 55}
              {'Read':>12}  {'Write':>12}
Average:      {sum(read_rates)/n:>10.1f}   {sum(write_rates)/n:>10.1f}
Peak:         {max(read_rates):>10.1f}   {max(write_rates):>10.1f}
Min:          {min(read_rates):>10.1f}   {min(write_rates):>10.1f}

VFS Throughput - rchar / wchar (KB/s)
{'-' * 55}
              {'Read':>12}  {'Write':>12}
Average:      {sum(rchar_rates)/n:>10.1f}   {sum(wchar_rates)/n:>10.1f}
Peak:         {max(rchar_rates):>10.1f}   {max(wchar_rates):>10.1f}
Min:          {min(rchar_rates):>10.1f}   {min(wchar_rates):>10.1f}

Syscall Rate (ops/s)
{'-' * 55}
              {'Read':>12}  {'Write':>12}
Average:      {sum(syscr_rates)/n:>10.1f}   {sum(syscw_rates)/n:>10.1f}
Peak:         {max(syscr_rates):>10.1f}   {max(syscw_rates):>10.1f}

Totals
{'-' * 55}
Disk Read:        {total_read:>12}  ({_format_bytes(total_read):>10})
Disk Written:     {total_write:>12}  ({_format_bytes(total_write):>10})
Cancelled Writes: {total_cancelled:>12}  ({_format_bytes(total_cancelled):>10})
VFS Read (rchar): {total_rchar:>12}  ({_format_bytes(total_rchar):>10})
VFS Write (wchar):{total_wchar:>12}  ({_format_bytes(total_wchar):>10})
Read Syscalls:    {total_syscr:>12,}
Write Syscalls:   {total_syscw:>12,}

Activity
{'-' * 55}
Samples with disk read:   {sum(1 for r in read_rates if r > 0):>4} / {n}
Samples with disk write:  {sum(1 for w in write_rates if w > 0):>4} / {n}
"""


def generate_summary(samples, per_pid_samples, pid_info, pid, process_name, interval, duration_min, summary_path):
    if not samples:
        report = "I/O Monitor Summary\n\nNo I/O activity was recorded for this process during the monitoring period.\n"
        with open(summary_path, "w") as f:
            f.write(report)
        print(report)
        return

    n = len(samples)

    report = f"""I/O Monitor Summary (/proc/pid/io)
{'=' * 55}
Process:          {process_name} (PID {pid}) + children
Duration:         {duration_min:.1f} minutes ({n} samples @ {interval}s)
Period:           {samples[0]['time']} - {samples[-1]['time']}
Total PIDs:       {len(pid_info)}

Aggregate (All Processes Combined)
{'=' * 55}
{_stats_section(samples, interval)}
Process Tree Detail
{'=' * 55}
"""
    parent_pids = [p for p, info in pid_info.items() if info["role"] == "parent"]
    child_pids = sorted(p for p, info in pid_info.items() if info["role"] == "child")

    for p in parent_pids + child_pids:
        info = pid_info[p]
        role_label = "Parent" if info["role"] == "parent" else "Child"
        ppid_str = f", PPID {info['ppid']}" if info["ppid"] is not None else ""
        report += f"[{role_label}] {info['name']} (PID {p}{ppid_str})\n"
        report += f"{'-' * 55}\n"

        pid_samples = per_pid_samples.get(p, [])
        if pid_samples:
            report += _stats_section(pid_samples, interval)
        else:
            report += "No I/O samples recorded for this process.\n"
        report += "\n"

    with open(summary_path, "w") as f:
        f.write(report)

    print(report)


def monitor_pid(pid, args):
    """Run monitoring and report generation for a single PID."""
    process_name = get_process_name(pid)
    total_samples = int((args.minutes * 60) / args.interval)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join("reports", f"{process_name}_{pid}_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)

    raw_path = os.path.join(run_dir, "proc_io_raw.log")
    summary_path = os.path.join(run_dir, "summary")

    print(f"Process: {process_name} (PID {pid})")
    print(f"Run dir: {run_dir}")
    print()

    samples, per_pid_samples, pid_info = collect_samples(pid, args.interval, total_samples, raw_path)
    generate_summary(samples, per_pid_samples, pid_info, pid, process_name, args.interval, args.minutes, summary_path)

    print(f"Raw log:  {raw_path}")
    print(f"Summary:  {summary_path}")


def main():
    args = parse_args()
    check_pid(args.pid)
    monitor_pid(args.pid, args)


if __name__ == "__main__":
    main()
