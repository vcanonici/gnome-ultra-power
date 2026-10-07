#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fixed user-session operations. VPNs, firewall and browser tabs are untouched."""
import json
import os
import subprocess
import sys
from typing import Any
import dbus
from schema import SessionState, session

UNITS = ("tracker-miner-fs-3.service", "gnome-remote-desktop.service", "docker-desktop.service")


def run(args: list[str], check: bool = True) -> str:
    p = subprocess.run(args, capture_output=True, text=True, timeout=20,
                       env={**os.environ, "LC_ALL": "C"})
    if check and p.returncode:
        raise RuntimeError("Falha na ação de sessão: " + args[0])
    return p.stdout.strip()


def screen() -> Any:
    return dbus.Interface(dbus.SessionBus().get_object(
        "org.gnome.SettingsDaemon.Power", "/org/gnome/SettingsDaemon/Power", introspect=False),
        "org.freedesktop.DBus.Properties")


def snapshot(containers: bool = False) -> SessionState:
    try:
        brightness = int(screen().Get("org.gnome.SettingsDaemon.Power.Screen", "Brightness", timeout=3))
    except dbus.DBusException:
        brightness = -1
    units = [unit for unit in UNITS if (containers or unit != "docker-desktop.service")
             and run(["systemctl", "--user", "is-active", unit], False) == "active"]
    if any(run(["systemctl", "--user", "is-enabled", unit], False).startswith("masked") for unit in units):
        raise RuntimeError("Serviço já mascarado; preservar configuração existente.")
    return {"brightness": brightness, "units": units}


def enter(state: SessionState) -> dict[str, str]:
    if state["brightness"] >= 0:
        screen().Set("org.gnome.SettingsDaemon.Power.Screen", "Brightness", dbus.Int32(min(20, state["brightness"])))
    for unit in state["units"]:
        run(["systemctl", "--user", "mask", "--runtime", "--now", unit])
    return {}


def restore(state: SessionState) -> dict[str, str]:
    if state["brightness"] >= 0:
        screen().Set("org.gnome.SettingsDaemon.Power.Screen", "Brightness", dbus.Int32(state["brightness"]))
    for unit in state["units"]:
        run(["systemctl", "--user", "unmask", "--runtime", unit])
        run(["systemctl", "--user", "start", unit])
    return {}


if __name__ == "__main__":
    try:
        action = sys.argv[1]
        result: SessionState | dict[str, str]
        if action == "snapshot": result = snapshot("--containers" in sys.argv[2:])
        else:
            data = session(json.load(sys.stdin))
            result = {"enter": enter, "restore": restore}[action](data)
        print(json.dumps(result))
    except Exception as error:
        print(type(error).__name__ + ": ação de sessão incompleta.", file=sys.stderr)
        raise SystemExit(1)
