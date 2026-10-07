# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed configuration and strict validation at JSON/process boundaries."""

from __future__ import annotations
import re
from typing import NotRequired, TypedDict, cast


class CpuPreset(TypedDict):
    online: list[int]
    session: list[int]


class Config(TypedDict):
    uid: int
    presets: dict[str, CpuPreset]
    offline_cpus: NotRequired[bool]
    pause_containers: NotRequired[bool]


class SessionState(TypedDict):
    brightness: int
    units: list[str]


class RecoveryState(TypedDict):
    boot_id: str
    uid: int
    knobs: dict[str, str]
    online: dict[str, str]
    allowed: str
    blue: list[str]
    units: list[str]
    session: SessionState
    profile: str
    domains: NotRequired[list[str]]


USER_UNITS = {
    "tracker-miner-fs-3.service",
    "gnome-remote-desktop.service",
    "docker-desktop.service",
}
ROOT_UNITS = {
    "docker.socket",
    "docker.service",
    "libvirtd.socket",
    "libvirtd-ro.socket",
    "libvirtd-admin.socket",
    "libvirtd.service",
}


def mapping(raw: object, keys: set[str]) -> dict[str, object]:
    if (
        not isinstance(raw, dict)
        or any(not isinstance(k, str) for k in raw)
        or set(raw) != keys
    ):
        raise ValueError("Estrutura inesperada.")
    return cast(dict[str, object], raw)


def integer(raw: object, low: int, high: int) -> int:
    if type(raw) is not int or not low <= raw <= high:
        raise ValueError("Inteiro fora da faixa.")
    return raw


def strings(raw: object, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(raw, list) or any(not isinstance(v, str) for v in raw):
        raise ValueError("Lista de textos esperada.")
    result = cast(list[str], raw)
    if len(set(result)) != len(result) or (
        allowed is not None and not set(result) <= allowed
    ):
        raise ValueError("Lista inesperada.")
    return result


def config(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ValueError("Configuracao invalida.")
    options = {"offline_cpus", "pause_containers"}
    keys = {"uid", "presets"} | (set(raw) & options)
    data = mapping(raw, keys)
    uid = integer(data["uid"], 1000, 2**31 - 1)
    for option in options:
        if type(data.get(option, False)) is not bool:
            raise ValueError(f"{option} exige booleano.")
    presets = mapping(data["presets"], {"minimal", "responsive"})
    result: dict[str, CpuPreset] = {}
    for name, value in presets.items():
        item = mapping(value, {"online", "session"})
        parsed: dict[str, list[int]] = {}
        for key, array in item.items():
            if not isinstance(array, list):
                raise ValueError("CPUs exigem lista.")
            cpus = [integer(cpu, 0, 1023) for cpu in array]
            if not cpus or len(set(cpus)) != len(cpus):
                raise ValueError("CPUs vazias ou repetidas.")
            parsed[key] = cpus
        if 0 not in parsed["online"] or not set(parsed["session"]) <= set(
            parsed["online"]
        ):
            raise ValueError("Afinidade invalida.")
        result[name] = {"online": parsed["online"], "session": parsed["session"]}
    return {"uid": uid, "offline_cpus": cast(bool, data.get("offline_cpus", False)),
            "pause_containers": cast(bool, data.get("pause_containers", False)), "presets": result}


def session(raw: object) -> SessionState:
    data = mapping(raw, {"brightness", "units"})
    return {"brightness": integer(data["brightness"], -1, 100),
            "units": strings(data["units"], USER_UNITS)}


def recovery(raw: object) -> RecoveryState:
    required = {
        "boot_id",
        "uid",
        "knobs",
        "online",
        "allowed",
        "blue",
        "units",
        "session",
        "profile",
    }
    data = mapping(
        raw,
        required
        | ({"domains"} if isinstance(raw, dict) and "domains" in raw else set()),
    )
    uid = integer(data["uid"], 1000, 2**31 - 1)
    for key in ("boot_id", "allowed", "profile"):
        if not isinstance(data[key], str):
            raise ValueError("Campo de texto invalido.")
    if data["profile"] not in ("performance", "balanced", "power-saver"):
        raise ValueError("Perfil invalido.")
    allowed = cast(str, data["allowed"])
    if allowed and not re.fullmatch(r"[0-9,-]+", allowed):
        raise ValueError("Afinidade original invalida.")
    result: dict[str, dict[str, str]] = {}
    for key in ("online", "knobs"):
        raw_map = data[key]
        if not isinstance(raw_map, dict):
            raise ValueError("Mapa de hardware invalido.")
        values: dict[str, str] = {}
        for path, value in raw_map.items():
            if not isinstance(path, str) or not isinstance(value, str):
                raise ValueError("Hardware exige textos.")
            valid = False
            if key == "online":
                valid = bool(
                    re.fullmatch(r"/sys/devices/system/cpu/cpu[0-9]{1,4}/online", path)
                ) and value in ("0", "1")
            elif re.fullmatch(
                r"/sys/devices/system/cpu/intel_pstate/(no_turbo|min_perf_pct|max_perf_pct)",
                path,
            ):
                valid = value.isdigit() and int(value) <= (
                    1 if path.endswith("no_turbo") else 100
                )
            elif re.fullmatch(
                r"/sys/devices/system/cpu/cpufreq/policy[0-9]{1,4}/energy_performance_preference",
                path,
            ):
                valid = value in (
                    "default",
                    "performance",
                    "balance_performance",
                    "balance_power",
                    "power",
                )
            elif re.fullmatch(r"/sys/class/leds/[A-Za-z0-9_.:-]*kbd_backlight/brightness", path):
                valid = value.isdigit() and int(value) <= 255
            elif re.fullmatch(r"/sys/devices/system/cpu/cpufreq/policy[0-9]{1,4}/scaling_governor", path):
                valid = value in ("powersave", "performance", "schedutil", "ondemand", "conservative", "userspace")
            elif path == "/sys/devices/system/cpu/cpufreq/boost":
                valid = value in ("0", "1")
            elif re.fullmatch(r"/sys/bus/pci/devices/[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]/power/control", path):
                valid = value in ("auto", "on")
            if not valid:
                raise ValueError("Caminho ou valor de hardware fora do escopo.")
            values[path] = value
        result[key] = values
    blue = strings(data["blue"])
    if any(not re.fullmatch(r"/org/bluez/hci[0-9]+", p) for p in blue):
        raise ValueError("Adaptador inesperado.")
    domains = strings(data.get("domains", []))
    if any(
        not re.fullmatch(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", u)
        for u in domains
    ):
        raise ValueError("UUID de VM inesperado.")
    return {
        "domains": domains,
        "boot_id": cast(str, data["boot_id"]),
        "uid": uid,
        "knobs": result["knobs"],
        "online": result["online"],
        "allowed": allowed,
        "blue": blue,
        "units": strings(data["units"], ROOT_UNITS),
        "session": session(data["session"]),
        "profile": cast(str, data["profile"]),
    }
