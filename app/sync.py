"""rsync-over-SSH sync engine.

Builds and runs rsync commands that push a source repo path to a target machine's
remote deployment path. Supports:
  - full tree sync (whole directories) and single-file sync
  - resumable, delta transfer with checksums (the default rsync-over-ssh flags)
  - NTFS-safe flag sets (drop ownership/permission bits on ntfs mounts)
  - per-target host/user/key configuration

All commands run via subprocess. Progress is streamed to a callback so the UI can
show live output.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    ok: bool
    command: str
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0

    @property
    def summary(self) -> str:
        if self.ok:
            return "completed"
        return f"failed (exit {self.returncode})"

    @property
    def returncode(self) -> int | None:
        # stored by caller; kept for API symmetry
        return getattr(self, "_returncode", None)


def _default_flags(ntfs: bool = False) -> list[str]:
    """rsync flags. -goP dropped on NTFS (ntfs-3g can't store owner/perms)."""
    base = ["-a"] if ntfs else ["-a", "--owner", "--group", "--perms"]
    return [
        *base,
        "-H",          # preserve hard links
        "--numeric-ids",  # don't try to map uid/gid (avoids chown failures)
        "--partial",   # keep partially transferred files for resume
        "--progress",  # live progress
        "--stats",     # transfer stats at the end
    ]


def build_rsync_command(
    host: str,
    user: str,
    local_path: str,
    remote_root: str,
    key: str | None = None,
    ntfs: bool = False,
    extra_args: list[str] | None = None,
    remote_subpath: str | None = None,
) -> tuple[list[str], str]:
    """Build an rsync-over-ssh command.

    Returns (command_list, remote_dest). local_path may be a file or directory;
    trailing slashes are handled so directories sync their *contents*.

    When ``remote_subpath`` is given the source is pushed to that exact nested
    path under ``remote_root`` (file -> file), mirroring the source layout on the
    target. Otherwise the contents of local_path are synced into remote_root.
    """
    flags = _default_flags(ntfs)
    ssh_cmd = f"ssh -o BatchMode=yes -o ConnectTimeout=15"
    if key:
        ssh_cmd += f" -i {shlex.quote(key)}"

    if remote_subpath is not None:
        dest = remote_root.rstrip("/") + "/" + remote_subpath.lstrip("/")
        cmd = [
            "rsync", *flags,
            "-e", ssh_cmd,
            shlex.quote(local_path),
            f"{user}@{host}:{dest}",
        ]
        if extra_args:
            cmd[1:1] = extra_args
        return cmd, dest

    # Ensure remote dest is a directory (sync contents into it).
    local_path = local_path.rstrip("/")
    has_trailing_slash = "/" in local_path and local_path.endswith("/")
    if has_trailing_slash:
        remote_dest = remote_root.rstrip("/") + "/"
    else:
        # For single files, sync the file INTO the dest dir.
        remote_dest = remote_root.rstrip("/") + "/"

    cmd = [
        "rsync", *flags,
        "-e", ssh_cmd,
        shlex.quote(local_path) if not local_path.startswith("/") else local_path,
        f"{user}@{host}:{remote_dest}",
    ]
    if extra_args:
        cmd[1:1] = extra_args
    return cmd, remote_dest


def run_rsync(
    host: str,
    user: str,
    local_path: str,
    remote_root: str,
    key: str | None = None,
    ntfs: bool = False,
    on_output=None,
    extra_args: list[str] | None = None,
    remote_subpath: str | None = None,
    cancel_hook=None,
) -> SyncResult:
    """Run an rsync-over-ssh command and return a SyncResult.

    When ``remote_subpath`` is set the source lands at that nested path under
    remote_root; the destination's parent directory is created on the target
    first so rsync can't fail with "No such file or directory".
    """
    cmd, remote_dest = build_rsync_command(
        host, user, local_path, remote_root, key=key, ntfs=ntfs, extra_args=extra_args,
        remote_subpath=remote_subpath,
    )

    if remote_subpath is not None:
        parent = os.path.dirname(remote_dest.rstrip("/"))
        if parent:
            _ensure_remote_dir(host, user, parent, key=key)

    cmd_str = " ".join(shlex.quote(c) for c in cmd)

    if on_output:
        on_output(f"$ {cmd_str}")

    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines = []
    for line in iter(proc.stdout.readline, ""):
        if cancel_hook is not None and cancel_hook():
            proc.terminate()
            break
        output_lines.append(line.rstrip("\n"))
        if on_output:
            on_output(line.rstrip("\n"))
    try:
        proc.stdout.close()
    except Exception:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    stdout = "\n".join(output_lines)
    result = SyncResult(
        ok=proc.returncode == 0,
        command=cmd_str,
        stdout=stdout,
        stderr="",
    )
    result._returncode = proc.returncode
    return result


def _ensure_remote_dir(host: str, user: str, remote_dir: str, key: str | None = None,
                       timeout: int = 30) -> None:
    """Best-effort ``ssh ... mkdir -p`` of a directory on the target.

    Non-fatal: rsync surfaces any real failure below. Used to pre-create nested
    destination directories so pushing a file to an exact path can't fail with
    "No such file or directory".
    """
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if key:
        ssh_cmd.append(shlex.quote(key))
    ssh_cmd += [f"{user}@{host}", f"mkdir -p {shlex.quote(remote_dir)}"]
    try:
        subprocess.run(ssh_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        # Non-fatal; rsync will surface any real failure below.
        pass


def test_connection(host: str, user: str, key: str | None = None) -> tuple[bool, str]:
    """Test SSH connectivity to a target (BatchMode, no password prompt)."""
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if key:
        ssh_cmd += ["-i", key]
    ssh_cmd += [f"{user}@{host}", "hostname"]
    try:
        proc = subprocess.run(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip() or "connected"
        return False, (proc.stderr.strip() or f"exit {proc.returncode}")
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError as e:
        return False, str(e)


def remote_disk_usage(host: str, user: str, path: str, key: str | None = None,
                      timeout: int = 20) -> dict | None:
    """Fetch disk usage of a remote path via ``df -Pk`` over SSH.

    Returns ``{total_bytes, used_bytes, free_bytes, percent}`` or ``None`` when the
    command fails (no connectivity, path absent, host unreachable). Best-effort; a
    target whose deployment path doesn't exist yet simply reports nothing.
    """
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    if key:
        ssh_cmd += ["-i", key]
    ssh_cmd += [f"{user}@{host}", f"df -Pk {shlex.quote(path)}"]
    try:
        proc = subprocess.run(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    # POSIX df: header line + one data line per filesystem.
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    # Filesystem  1024-blocks Used Available Capacity Mounted-on ...
    try:
        total = int(parts[1]) * 1024
        used = int(parts[2]) * 1024
        free = int(parts[3]) * 1024
    except (IndexError, ValueError):
        return None
    percent = None
    cap = parts[4] if len(parts) > 4 else ""
    if cap.endswith("%"):
        try:
            percent = int(cap[:-1])
        except ValueError:
            pass
    return {"total_bytes": total, "used_bytes": used, "free_bytes": free, "percent": percent}


def local_disk_usage(path: str) -> dict | None:
    """shutil.disk_usage for a path; ``None`` on failure."""
    try:
        u = shutil.disk_usage(str(path))
    except (OSError, AttributeError):
        return None
    if not u:
        return None
    pct = round(u.used / u.total * 100) if u.total else None
    return {"total_bytes": u.total, "used_bytes": u.used, "free_bytes": u.free, "percent": pct}
