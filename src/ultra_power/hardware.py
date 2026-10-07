#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional, fixed-scope hardware changes. Boot never resumes ULTRA."""

import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
import re
import errno
import dbus
from gpu import wait_for_suspend, nvidia_devices
from typing import Any
from schema import Config, RecoveryState, SessionState, recovery, session

HERE = Path(__file__).resolve().parent
SYSTEM_UNITS = (
    "docker.socket",
    "docker.service",
    "libvirtd.socket",
    "libvirtd-ro.socket",
    "libvirtd-admin.socket",
    "libvirtd.service",
)
STATE = Path("/var/lib/ultra-power/recovery.json")
RUNTIME = Path("/run/ultra-power/active")


def run(args: list[str], check: bool = True) -> str:
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=40,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (subprocess.TimeoutExpired, OSError):
        raise RuntimeError("Comando indisponível ou sem resposta: " + args[0]) from None
    if check and p.returncode:
        raise RuntimeError("Falha em " + args[0])
    return p.stdout.strip()


def atomic(path: Path, data: RecoveryState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o600)
        os.replace(name, path)
        parent = os.open(path.parent, os.O_DIRECTORY)
        os.fsync(parent)
        os.close(parent)
    finally:
        Path(name).unlink(missing_ok=True)


def agent(
    uid: int, action: str, state: SessionState | None = None, pause_containers: bool = False
) -> SessionState | None:
    username = pwd.getpwuid(uid).pw_name
    args = [
        "runuser",
        "-u",
        username,
        "--",
        "env",
        "XDG_RUNTIME_DIR=/run/user/" + str(uid),
        "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/" + str(uid) + "/bus",
        "/usr/bin/python3",
        str(HERE / "session_agent.py"),
        action,
    ]
    if pause_containers and action == "snapshot": args.append("--containers")
    try:
        p = subprocess.run(
            args,
            input=json.dumps(state) if state is not None else None,
            capture_output=True,
            text=True,
            timeout=65,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise RuntimeError("Sessão sem resposta; recuperação preservada.") from None
    if p.returncode:
        raise RuntimeError(
            "Acao de sessao incompleta; estado de recuperacao preservado."
        )
    return session(json.loads(p.stdout)) if action == "snapshot" else None


class Hardware:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.uid = int(config["uid"])
        self.bus = dbus.SystemBus()

    def props(self, name: str, path: str) -> Any:
        return dbus.Interface(
            self.bus.get_object(name, path), "org.freedesktop.DBus.Properties"
        )

    def battery(self) -> bool:
        return bool(
            self.props("org.freedesktop.UPower", "/org/freedesktop/UPower").Get(
                "org.freedesktop.UPower", "OnBattery"
            )
        )

    def profile(self, value: str | None = None) -> str:
        p = self.props("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")
        if value == "performance":
            available = {str(row["Profile"]) for row in p.Get("net.hadess.PowerProfiles", "Profiles")}
            if value not in available: value = "balanced"
        if value is not None:
            p.Set("net.hadess.PowerProfiles", "ActiveProfile", dbus.String(value))
        return str(p.Get("net.hadess.PowerProfiles", "ActiveProfile"))

    def local_session(self) -> bool:
        sessions = dbus.Interface(
            self.bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"),
            "org.freedesktop.login1.Manager",
        ).ListSessions()
        for sid, uid, user, seat, path in sessions:
            if int(uid) != self.uid or str(seat) != "seat0":
                continue
            props = self.props("org.freedesktop.login1", path)
            if (
                str(props.Get("org.freedesktop.login1.Session", "State")) == "active"
                and str(props.Get("org.freedesktop.login1.Session", "Type")) == "wayland"
                and not props.Get("org.freedesktop.login1.Session", "Remote")
                and not props.Get("org.freedesktop.login1.Session", "LockedHint")
            ):
                return True
        return False

    def energy_paths(self) -> list[Path]:
        """Inactive cpufreq policies remain in sysfs but reject reads/writes."""
        paths: list[Path] = []
        for path in Path("/sys/devices/system/cpu/cpufreq").glob(
            "policy*/energy_performance_preference"
        ):
            try:
                affected = (path.parent / "affected_cpus").read_text().strip()
            except OSError as exc:
                if exc.errno == errno.EBUSY:
                    continue
                raise RuntimeError(f"Falha ao consultar CPUs ativas em {path.parent.name}.") from exc
            if affected:
                paths.append(path)
        return paths

    def knob_paths(self) -> list[Path]:
        paths = []
        for name in ("no_turbo", "min_perf_pct", "max_perf_pct"):
            p = Path("/sys/devices/system/cpu/intel_pstate") / name
            if p.exists():
                paths.append(p)
        paths.extend(self.energy_paths())
        for path in self.energy_paths():
            governor = path.parent / "scaling_governor"
            if governor.exists():
                paths.append(governor)
        boost = Path("/sys/devices/system/cpu/cpufreq/boost")
        if boost.exists(): paths.append(boost)
        paths.extend(Path("/sys/class/leds").glob("*kbd_backlight/brightness"))
        for device in nvidia_devices():
            if (device / "power/control").exists(): paths.append(device / "power/control")
        return paths

    def snapshot(self) -> RecoveryState:
        if STATE.exists():
            raise RuntimeError("Recuperacao anterior pendente.")
        if not self.battery():
            raise RuntimeError("ULTRA disponivel somente na bateria.")
        if not self.local_session():
            raise RuntimeError("Sessao local desbloqueada necessaria.")
        knobs = {str(p): p.read_text().strip() for p in self.knob_paths()}
        online = {
            str(p): p.read_text().strip()
            for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/online")
        }
        for preset in self.config["presets"].values():
            for cpu in preset["online"]:
                if not (Path("/sys/devices/system/cpu") / f"cpu{cpu}").is_dir():
                    raise RuntimeError("Topologia mudou; executar doctor e reinstalar.")
        old_allowed = run(
            [
                "systemctl",
                "show",
                f"user-{self.uid}.slice",
                "-p",
                "AllowedCPUs",
                "--value",
            ]
        )
        blue = []
        try:
            objects = dbus.Interface(
                self.bus.get_object("org.bluez", "/", introspect=False),
                "org.freedesktop.DBus.ObjectManager",
            ).GetManagedObjects(timeout=3)
            connected = any(
                x.get("org.bluez.Device1", {}).get("Connected", False)
                for x in objects.values()
            )
            if not connected:
                blue = [
                    str(path)
                    for path, data in objects.items()
                    if data.get("org.bluez.Adapter1", {}).get("Powered", False)
                ]
        except dbus.DBusException:
            pass
        session_state = agent(self.uid, "snapshot", pause_containers=self.config.get("pause_containers", False))
        assert session_state is not None
        state: RecoveryState = {
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "uid": self.uid,
            "knobs": knobs,
            "online": online,
            "allowed": old_allowed,
            "blue": blue,
            "units": [
                u
                for u in (SYSTEM_UNITS if self.config.get("pause_containers") else ())
                if run(["systemctl", "is-active", u], False) == "active"
            ],
            "session": session_state,
            "profile": self.profile(),
        }
        if "libvirtd.service" in state["units"]:
            domains = run(["virsh", "list", "--state-running", "--uuid"]).splitlines()
            if any(
                not re.fullmatch(
                    r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", u
                )
                for u in domains
            ):
                raise RuntimeError("Identidade de VM inesperada.")
            state["domains"] = domains
        if any(
            run(["systemctl", "is-enabled", unit], False).startswith("masked")
            for unit in state["units"]
        ):
            raise RuntimeError(
                "Servico ativo ja mascarado; preservar politica existente."
            )
        atomic(STATE, state)
        return state

    def cpus(self, preset: str) -> None:
        values = self.config["presets"][preset]
        online = set(values["online"])
        allowed = ",".join(map(str, values["session"]))
        if 0 not in online or not set(values["session"]).issubset(online):
            raise ValueError("Preset invalido.")
        # Bring required CPUs online before expanding affinity.
        if self.config.get("offline_cpus", False):
            for cpu in sorted(online):
                p = Path(f"/sys/devices/system/cpu/cpu{cpu}/online")
                if p.exists(): p.write_text("1")
        run(
            [
                "systemctl",
                "set-property",
                "--runtime",
                f"user-{self.uid}.slice",
                "AllowedCPUs=" + allowed,
            ]
        )
        turbo = Path("/sys/devices/system/cpu/intel_pstate/no_turbo")
        if turbo.exists():
            turbo.write_text("1")
        # Set EPP while the policy is active, BEFORE offlining its CPUs.
        # Also skip already inactive policies when changing presets/resuming.
        boost = Path("/sys/devices/system/cpu/cpufreq/boost")
        if boost.exists(): boost.write_text("0")
        for p in self.energy_paths():
            try:
                governor = p.parent / "scaling_governor"
                if governor.exists() and governor.read_text().strip() == "performance":
                    governor.write_text("powersave")
                p.write_text("power")
            except OSError as exc:
                raise RuntimeError(f"Falha ao aplicar economia em {p.parent.name}: {exc.strerror}.") from exc
        if self.config.get("offline_cpus", False):
            for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*/online"):
                if int(p.parent.name[3:]) not in online: p.write_text("0")

    def enter(self) -> None:
        state = self.snapshot()
        try:
            agent(self.uid, "enter", state["session"])
            for uuid in state.get("domains", []):
                run(["virsh", "suspend", uuid])
            for unit in state["units"]:
                run(["systemctl", "mask", "--runtime", "--now", unit])
            for path in state["blue"]:
                self.props("org.bluez", path).Set(
                    "org.bluez.Adapter1", "Powered", dbus.Boolean(False)
                )
            self.profile("power-saver")
            for kbd in Path("/sys/class/leds").glob("*kbd_backlight/brightness"):
                maximum = int((kbd.parent / "max_brightness").read_text())
                kbd.write_text(str(min(1, maximum)))
            for path in state["knobs"]:
                if path.endswith("/power/control"):
                    Path(path).write_text("auto")
            self.cpus("minimal")
            # Services (including remote desktop/CUDA) must be paused first.
            # GPU residency alone is advisory: the user chose partial savings.
            wait_for_suspend()
            RUNTIME.parent.mkdir(mode=0o700, exist_ok=True)
            RUNTIME.touch(mode=0o600)
        except Exception:
            self.restore()
            raise

    def restore(self, target: str | None = None) -> None:
        RUNTIME.unlink(missing_ok=True)
        if not STATE.exists():
            if target:
                self.profile(target)
            return
        state = recovery(json.loads(STATE.read_text()))
        errors: list[str] = []
        if state["uid"] != self.uid:
            raise RuntimeError(
                "UID do journal difere da configuracao; preservar recuperacao."
            )
        # Restore CPUs first: the recovery/menu must never remain starved.
        for path, value in state["online"].items():
            if not self.config.get("offline_cpus", False):
                continue
            try:
                Path(path).write_text("1")
            except OSError:
                errors.append("CPU")
        try:
            run(
                [
                    "systemctl",
                    "set-property",
                    "--runtime",
                    f"user-{state['uid']}.slice",
                    "AllowedCPUs=" + state["allowed"],
                ]
            )
        except RuntimeError:
            errors.append("afinidade")
        for path, value in sorted(state["knobs"].items(), key=lambda item: item[0].endswith("scaling_governor")):
            try:
                if Path(path).exists():
                    Path(path).write_text(value)
            except OSError:
                errors.append("hardware")
        for path, value in state["online"].items():
            if self.config.get("offline_cpus", False) and value == "0":
                try:
                    Path(path).write_text("0")
                except OSError:
                    errors.append("CPU-original")
        for unit in state["units"]:
            try:
                run(["systemctl", "unmask", "--runtime", unit])
                run(["systemctl", "start", unit])
            except RuntimeError:
                errors.append("servico")
        for uuid in state.get("domains", []):
            try:
                if run(["virsh", "domstate", uuid]) == "paused":
                    run(["virsh", "resume", uuid])
            except RuntimeError:
                errors.append("VM")
        for path in state["blue"]:
            try:
                self.props("org.bluez", path).Set(
                    "org.bluez.Adapter1", "Powered", dbus.Boolean(True)
                )
            except dbus.DBusException:
                errors.append("bluetooth")
        try:
            agent(state["uid"], "restore", state["session"])
        except RuntimeError:
            errors.append("sessao")
        try:
            self.profile(
                target
                or ("power-saver" if self.battery() else "performance")
            )
        except dbus.DBusException:
            errors.append("perfil")
        if errors:
            raise RuntimeError("Recuperacao pendente: " + ",".join(sorted(set(errors))))
        STATE.unlink()
