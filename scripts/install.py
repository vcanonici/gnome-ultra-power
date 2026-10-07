#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit, install and remove GNOME ULTRA Power. No login/GDM/sudo PAM edits."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import pwd
import platform
import re
import shutil
import subprocess
import sys
import time
import dbus
from typing import TypedDict, cast

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / "src/ultra_power"
sys.path.insert(0, str(SOURCE))
from discovery import detect, cpu_list
from schema import config as parse_config
from schema import Config, integer, mapping, strings


class Report(TypedDict):
    supported: bool
    gnome: str
    config: Config
    digital: str
    vpn: str
    installation: str


class FileRecord(TypedDict):
    exists: bool


class Manifest(TypedDict):
    uid: int
    extension: str
    profile: str
    pam_hashes: dict[str, str]
    enabled: list[str]
    files: dict[str, FileRecord]

BACKUP = Path("/var/lib/ultra-power-install")
UUID = "ultra-power@vcanonici"
LIB = Path("/usr/local/lib/ultra-power")
UNIT = "ultra-power.service"
PAM = ("common-auth", "gdm-password", "gdm-fingerprint", "sudo", "sudo-i", "login", "sshd")
MODULES = ("daemon.py", "hardware.py", "session_agent.py", "pam_finger.py", "gpu.py", "schema.py")
FILES = {str(LIB / name): SOURCE / name for name in MODULES}
FILES.update({"/usr/local/bin/ultra-power": SOURCE / "cli.py",
              "/etc/pam.d/ultra-power": PROJECT / "scripts/ultra-power.pam",
              "/etc/dbus-1/system.d/io.github.vcanonici.UltraPower.conf": PROJECT / "scripts/io.github.vcanonici.UltraPower.conf",
              "/etc/systemd/system/ultra-power.service": PROJECT / "scripts/ultra-power.service"})
CONFIG = Path("/etc/ultra-power.json")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, timeout=30).strip()


def pam_hashes() -> dict[str, str]:
    return {name: hashlib.sha256(Path("/etc/pam.d", name).read_bytes()).hexdigest()
            for name in PAM if Path("/etc/pam.d", name).exists()}


def account(name: str | None) -> pwd.struct_passwd:
    user = pwd.getpwnam(name or os.environ.get("SUDO_USER", "") or pwd.getpwuid(os.getuid()).pw_name)
    if user.pw_uid < 1000 or not Path(user.pw_dir).is_dir():
        raise ValueError("Escolha uma conta local normal com --user.")
    return user


def extension_path(user: pwd.struct_passwd) -> Path:
    return Path(user.pw_dir) / ".local/share/gnome-shell/extensions" / UUID


def verify_platform() -> None:
    if platform.machine() != "x86_64":
        raise ValueError("v1 suporta arquitetura x86_64.")
    values: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    if values.get("ID") != "ubuntu" or values.get("VERSION_ID") != "24.04":
        raise ValueError("v1 suporta Ubuntu24.04; distribuição diferente recusada.")
    version = run("gnome-shell", "--version")
    if not re.search(r"\b46(?:\.|$)", version):
        raise ValueError("v1 suporta somente GNOME46.")
    for name in ("systemctl", "runuser", "fprintd-list", "gnome-extensions", "powerprofilesctl"):
        if shutil.which(name) is None:
            raise ValueError(f"Dependência ausente: {name}.")
    run("/usr/bin/python3", "-c", "import dbus; from gi.repository import GLib")
    if not Path("/usr/lib/x86_64-linux-gnu/security/pam_fprintd.so").exists():
        raise ValueError("Instale libpam-fprintd.")
    for unit in ("upower", "power-profiles-daemon"):
        if run("systemctl", "is-active", unit) != "active":
            raise ValueError(f"Serviço necessário: {unit}.")
    if not Path("/sys/fs/cgroup/cgroup.controllers").exists() or "cpuset" not in Path("/sys/fs/cgroup/cgroup.controllers").read_text().split():
        raise ValueError("Controlador cpuset/cgroupv2 necessário.")


def fingerprint_enrolled(username: str) -> bool:
    try:
        fingers = subprocess.run(["fprintd-list", username], capture_output=True, text=True,
                                 timeout=10, env={**os.environ, "LC_ALL": "C"})
    except subprocess.TimeoutExpired:
        return False
    return not fingers.returncode and bool(re.search(r"(?m)^\s*-[ ]*#[0-9]+:", fingers.stdout))


def doctor(user: pwd.struct_passwd, offline: bool, containers: bool) -> Report:
    verify_platform()
    bus = dbus.SystemBus()
    manager = dbus.Interface(bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1"), "org.freedesktop.login1.Manager")
    supported_session = False
    for sid, uid, name, seat, path in manager.ListSessions():
        if int(uid) == user.pw_uid and str(seat) == "seat0":
            props = dbus.Interface(bus.get_object("org.freedesktop.login1", path), "org.freedesktop.DBus.Properties")
            supported_session = supported_session or (str(props.Get("org.freedesktop.login1.Session", "Type")) == "wayland" and str(props.Get("org.freedesktop.login1.Session", "State")) == "active" and not props.Get("org.freedesktop.login1.Session", "Remote") and not props.Get("org.freedesktop.login1.Session", "LockedHint"))
    if not supported_session:
        raise ValueError("Entre numa sessão GNOME Wayland local e desbloqueada.")
    battery = dbus.Interface(bus.get_object("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice"), "org.freedesktop.DBus.Properties")
    if int(battery.Get("org.freedesktop.UPower.Device", "Type")) != 2 or not battery.Get("org.freedesktop.UPower.Device", "IsPresent"):
        raise ValueError("Bateria de portátil não detectada pelo UPower.")
    enrolled = fingerprint_enrolled(user.pw_name)
    # Do not install a second coordinator beside the host-specific pilot/TLP.
    for unit in ("thinkpad-power-ac", "tlp", "tuned", "auto-cpufreq"):
        if subprocess.run(["systemctl", "is-active", unit], capture_output=True).returncode == 0:
            raise ValueError(f"Conflito com {unit}; preserve instalação existente.")
    profile = parse_config(detect(user.pw_uid, offline, containers))
    for cpu in cpu_list(Path("/sys/devices/system/cpu/online").read_text()):
        if offline and cpu != 0 and not Path(f"/sys/devices/system/cpu/cpu{cpu}/online").exists():
            raise ValueError("Topologia sem suporte a CPU hotplug.")
    return {"supported": True, "gnome": "46", "config": profile,
            "digital": "cadastrada; alternativa OK vermelho" if enrolled else "não cadastrada; OK vermelho disponível", "vpn": "preservada",
            "installation": "afinidade de CPU" if not offline else "hotplug explícito"}


def session_command(user: pwd.struct_passwd, *args: str) -> str:
    return run("runuser", "-u", user.pw_name, "--", "env",
               f"XDG_RUNTIME_DIR=/run/user/{user.pw_uid}",
               f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{user.pw_uid}/bus", *args)


def trusted_path(path: Path, home: Path | None = None) -> None:
    # Parent symlinks must not redirect root writes into another location.
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"Destino com link simbólico recusado: {path.name}.")
    if home is not None and not path.is_relative_to(home):
        raise ValueError("Destino de usuário inesperado.")


def enabled_extensions(value: str) -> list[str]:
    raw: object = ast.literal_eval(value.removeprefix("@as "))
    return strings(raw)


def manifest_data(raw: object) -> Manifest:
    data = mapping(raw, {"uid", "extension", "profile", "pam_hashes", "enabled", "files"})
    uid = integer(data["uid"], 1000, 2**31 - 1)
    if not isinstance(data["extension"], str) or data["profile"] not in ("performance", "balanced", "power-saver"):
        raise ValueError("Manifesto de instalação inválido.")
    rows = mapping(data["files"], {*FILES, str(CONFIG)})
    files: dict[str, FileRecord] = {}
    for path, value in rows.items():
        row = mapping(value, {"exists"})
        if row["exists"] is not False:
            raise ValueError("v1 nunca substitui arquivo existente.")
        files[path] = {"exists": False}
    hashes = data["pam_hashes"]
    if not isinstance(hashes, dict) or not set(hashes) <= set(PAM) or any(
            not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()):
        raise ValueError("Hashes PAM inválidos.")
    return {"uid": uid, "extension": data["extension"], "profile": cast(str, data["profile"]),
            "enabled": strings(data["enabled"]), "pam_hashes": cast(dict[str, str], hashes), "files": files}


def remove() -> None:
    manifest = manifest_data(json.loads((BACKUP / "manifest.json").read_text()))
    user = pwd.getpwuid(manifest["uid"])
    ext = extension_path(user)
    if set(manifest["files"]) != {*FILES, str(CONFIG)} or manifest["extension"] != str(ext):
        raise ValueError("Manifesto inesperado.")
    if run("systemctl", "show", UNIT, "-p", "LoadState", "--value") != "not-found":
        run("systemctl", "stop", UNIT)
    if Path("/var/lib/ultra-power/recovery.json").exists():
        raise ValueError("Recuperação pendente; concluir antes de remover.")
    subprocess.run(["systemctl", "disable", UNIT], capture_output=True)
    # Remove our UUID only; preserve later unrelated changes to enabled-extensions.
    current = enabled_extensions(session_command(user, "gsettings", "get", "org.gnome.shell", "enabled-extensions"))
    session_command(user, "gsettings", "set", "org.gnome.shell", "enabled-extensions", repr([v for v in current if v != UUID]))
    trusted_path(ext, Path(user.pw_dir))
    if ext.exists(): shutil.rmtree(ext)
    for target, row in manifest["files"].items():
        path = Path(target)
        trusted_path(path)
        path.unlink(missing_ok=True)
    run("systemctl", "daemon-reload")
    run("powerprofilesctl", "set", manifest["profile"])
    if pam_hashes() != manifest["pam_hashes"]:
        raise ValueError("PAM gráfico/sudo mudou; backup preservado para investigação.")
    # Archive instead of overwriting the installation backup.
    BACKUP.rename(BACKUP.with_name(BACKUP.name + "-removed-" + str(time.time_ns())))
    print("ULTRA removido. Saia/entre na sessão para descarregar a interface.")


def install(user: pwd.struct_passwd, report: Report) -> None:
    ext = extension_path(user)
    destinations = [*(Path(p) for p in FILES), CONFIG]
    if BACKUP.exists() or ext.exists() or any(path.exists() for path in destinations):
        raise ValueError("Instalação/destino já existente; nenhuma substituição automática.")
    for path in [*destinations, ext]: trusted_path(path)
    # Ensure user bus exists before making system changes.
    enabled = enabled_extensions(session_command(user, "gsettings", "get", "org.gnome.shell", "enabled-extensions"))
    if not isinstance(enabled, list) or any(not isinstance(v, str) for v in enabled):
        raise ValueError("Lista de extensões inválida.")
    manifest: Manifest = {"uid": user.pw_uid, "extension": str(ext), "pam_hashes": pam_hashes(), "profile": run("powerprofilesctl", "get"),
                "enabled": enabled, "files": {str(path): {"exists": False} for path in destinations}}
    BACKUP.mkdir(mode=0o700)
    (BACKUP / "manifest.json").write_text(json.dumps(manifest, indent=2))
    try:
        LIB.mkdir(mode=0o755, parents=True, exist_ok=True)
        LIB.chmod(0o755)
        for target, source in FILES.items():
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, path)
            path.chmod(0o755 if target == "/usr/local/bin/ultra-power" else 0o644)
        CONFIG.write_text(json.dumps(report["config"], indent=2) + "\n")
        CONFIG.chmod(0o644)
        for parent in reversed(ext.parent.parents):
            if parent.is_relative_to(Path(user.pw_dir)) and not parent.exists():
                parent.mkdir(mode=0o755)
                os.chown(parent, user.pw_uid, user.pw_gid)
        if not ext.parent.exists():
            ext.parent.mkdir(mode=0o755)
            os.chown(ext.parent, user.pw_uid, user.pw_gid)
        shutil.copytree(PROJECT / "extension", ext)
        for path in [ext, *ext.rglob("*")]:
            os.chown(path, user.pw_uid, user.pw_gid)
            path.chmod(0o755 if path.is_dir() else 0o644)
        session_command(user, "gsettings", "set", "org.gnome.shell", "enabled-extensions", repr([*enabled, UUID]))
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", UNIT)
        if pam_hashes() != manifest["pam_hashes"]: raise ValueError("PAM gráfico/sudo mudou!")
        print("Instalado. Salve seu trabalho e saia/entre na sessão; na bateria, confirme ULTRA pela digital ou OK vermelho.")
    except Exception:
        # Always attempt rollback; a failed recovery keeps its private manifest.
        try: remove()
        except Exception as error: print("Recuperação pendente: " + type(error).__name__, file=sys.stderr)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "install", "remove"))
    parser.add_argument("--user")
    parser.add_argument("--offline-cpus", action="store_true")
    parser.add_argument("--pause-containers", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == "remove":
            if os.geteuid() != 0: raise ValueError("Remoção exige sudo.")
            remove(); return
        user = account(args.user)
        report = doctor(user, args.offline_cpus, args.pause_containers)
        if args.action == "doctor": print(json.dumps(report, ensure_ascii=False, indent=2)); return
        if os.geteuid() != 0: raise ValueError("Instalação exige sudo.")
        install(user, report)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, "ULTRA: " + str(error) + "\n")


if __name__ == "__main__": main()
