# SPDX-License-Identifier: GPL-3.0-or-later
"""NVIDIA consumers: reviewed identity, never kill a compositor or a root task."""

import os
from pathlib import Path
import signal
import json
import re
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

RuntimeState = Literal["suspended", "active", "unknown", "not-present"]
SHELL_UNIT = "org.gnome.Shell@wayland.service"

PROTECTED = {
    "gnome-shell",
    "Xwayland",
    "Xorg",
    "gdm",
    "gdm-wayland-session",
    "systemd",
    "dbus-daemon",
    "dbus-broker",
    "gnome-remote-de",  # Linux comm is limited to 15 bytes.
    "gnome-remote-desktop-daemon",
}


@dataclass(frozen=True)
class Reference:
    token: str
    name: str
    owner: int
    executable: str
    cgroups: tuple[str, ...]

    def in_unit(self, unit: str) -> bool:
        return any(unit in path.split("/") for path in self.cgroups)

    def protected(self) -> bool:
        return (
            self.name in PROTECTED
            or self.executable in PROTECTED
            or self.in_unit(SHELL_UNIT)
        )


def references() -> list[Reference]:
    """Open descriptors are references, not proof of active GPU rendering."""
    devices = {str(p) for p in Path("/dev").glob("nvidia[0-9]*")}
    for card in Path("/sys/class/drm").glob("renderD*"):
        vendor = card / "device/vendor"
        if vendor.exists() and vendor.read_text().strip() == "0x10de":
            devices.add("/dev/dri/" + card.name)
    rows: list[Reference] = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if not any(os.readlink(fd) in devices for fd in (proc / "fd").iterdir()):
                continue
            pid = int(proc.name)
            name = (proc / "comm").read_text().strip()
            start = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
            owner = proc.stat().st_uid
            rows.append(Reference(
                f"{pid}:{start}", name, owner, (proc / "exe").resolve().name,
                tuple(line.split(":", 2)[2] for line in
                      (proc / "cgroup").read_text().splitlines()),
            ))
        except (OSError, ValueError, IndexError):
            continue
    return sorted(rows, key=lambda row: row.token)


@lru_cache(maxsize=16)
def primary_card(uid: int, token: str, boot: str) -> str | None:
    """Renderer selection is fixed for this compositor instance/boot."""
    pid = token.split(":")[0]
    try:
        result = subprocess.run(
            ["/usr/bin/journalctl", "--boot=0", f"_PID={pid}", f"_UID={uid}",
             f"_BOOT_ID={boot}", f"_SYSTEMD_USER_UNIT={SHELL_UNIT}",
             "--output=json", "--no-pager", "--grep=selected primary", "-n", "1"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode or not result.stdout.strip():
            return None
        record: object = json.loads(result.stdout)
        if not isinstance(record, dict):
            return None
        message = record.get("MESSAGE")
        if not isinstance(message, str):
            return None
        match = re.search(r"\bGPU (/dev/dri/card[0-9]+) selected primary\b", message)
        return match[1] if match else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def intel_desktop(uid: int, rows: list[Reference]) -> bool:
    for row in rows:
        if row.owner == uid and row.executable == "gnome-shell" and row.in_unit(SHELL_UNIT):
            try:
                boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")
                card = primary_card(uid, row.token, boot)
                if card is None:
                    return False
                device = Path("/sys/class/drm", Path(card).name, "device")
                return (device / "vendor").read_text().strip() in {"0x8086", "0x1002"}
            except OSError:
                return False
    return False


def review_rows(uid: int, rows: list[Reference], intel: bool) -> list[tuple[str, str, bool]]:
    result: list[tuple[str, str, bool]] = []
    for row in rows:
        # These exact units are paused transactionally AFTER fresh fingerprint approval.
        managed_remote = row.in_unit("gnome-remote-desktop.service") and (
            row.owner == uid
        )
        passive_desktop = (intel and row.owner == uid and row.in_unit(SHELL_UNIT)
                           and row.executable in {"gnome-shell", "Xwayland"})
        if managed_remote or passive_desktop:
            continue
        result.append((row.token, row.name, row.owner == uid and not row.protected()))
    return result


def consumers(uid: int) -> list[tuple[str, str, bool]]:
    rows = references()
    return review_rows(uid, rows, intel_desktop(uid, rows))


def nvidia_devices() -> list[Path]:
    result: list[Path] = []
    for path in Path("/sys/bus/pci/devices").glob("*"):
        try:
            if (path / "vendor").read_text().strip() == "0x10de" and (path / "class").read_text().strip().startswith("0x03"):
                result.append(path)
        except OSError:
            continue
    return sorted(result)


def runtime_state() -> RuntimeState:
    devices = nvidia_devices()
    if not devices: return "not-present"
    values: list[str] = []
    try:
        values = [(device / "power/runtime_status").read_text().strip() for device in devices]
    except OSError:
        return "unknown"
    if all(value == "suspended" for value in values): return "suspended"
    if "active" in values: return "active"
    return "unknown"


def wait_for_suspend() -> RuntimeState:
    deadline = time.monotonic() + 5
    while True:
        state = runtime_state()
        if state != "active" or time.monotonic() >= deadline:
            return state
        time.sleep(0.1)


def warning(active: bool, state: RuntimeState) -> str:
    if not active or state in {"suspended", "not-present"}:
        return ""
    if state == "active":
        return "ULTRA ativo — NVIDIA ainda ligada; economia parcial."
    return "ULTRA ativo — estado da NVIDIA desconhecido; economia da GPU não confirmada."


def terminate_reviewed(uid: int, reviewed: tuple[str, ...] | list[str]) -> None:
    current = consumers(uid)
    if set(reviewed) != {row[0] for row in current} or any(
        not row[2] for row in current
    ):
        raise RuntimeError(
            "Lista de processos mudou ou inclui o servidor grafico. Reabra o aviso."
        )
    handles = []
    try:
        for token, name, allowed in current:
            pid = int(token.split(":")[0])
            fd = os.pidfd_open(pid)
            # Re-read identity after pidfd_open to prevent PID reuse.
            fresh = consumers(uid)
            if set(reviewed) != {row[0] for row in fresh} or any(not row[2] for row in fresh):
                os.close(fd)
                raise RuntimeError("Processo mudou durante a aprovacao.")
            handles.append(fd)
        for fd in handles:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
    finally:
        for fd in handles:
            os.close(fd)
