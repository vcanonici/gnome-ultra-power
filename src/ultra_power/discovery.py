# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only topology discovery. Never exports host names, serials or credentials."""
from pathlib import Path
import re
from schema import Config, CpuPreset


def cpu_list(value: str) -> list[int]:
    if not re.fullmatch(r"[0-9,-]+", value.strip()):
        raise ValueError("Lista de CPUs inválida.")
    result: set[int] = set()
    for token in value.strip().split(","):
        bounds = token.split("-")
        start, end = int(bounds[0]), int(bounds[-1])
        if len(bounds) > 2 or not 0 <= start <= end <= 1023:
            raise ValueError("Faixa de CPUs inválida.")
        result.update(range(start, end + 1))
    return sorted(result)


def number(path: Path, fallback: int) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return fallback


def detect(uid: int, offline: bool = False, containers: bool = False,
           root: Path = Path("/")) -> Config:
    base = root / "sys/devices/system/cpu"
    available = cpu_list((base / "online").read_text())
    if 0 not in available:
        raise ValueError("CPU0 precisa estar online na instalação.")
    cores: dict[tuple[int, int], list[int]] = {}
    for cpu in available:
        path = base / f"cpu{cpu}"
        key = (number(path / "topology/physical_package_id", 0),
               number(path / "topology/core_id", cpu))
        cores.setdefault(key, []).append(cpu)

    def rank(cpus: list[int]) -> tuple[int, int, bool, int]:
        cpu = min(cpus)
        # Prefer physical cores with lower max frequency/fewer SMT siblings.
        frequency = number(base / f"cpu{cpu}/cpufreq/cpuinfo_max_freq", 0)
        return frequency, len(cpus), cpu == 0, cpu

    ordered = sorted(cores.values(), key=rank)
    minimal = sorted(min(group) for group in ordered[:2])
    responsive = sorted(min(group) for group in ordered[:min(6, len(ordered))])

    def preset(session: list[int]) -> CpuPreset:
        return {"session": session, "online": sorted({0, *session}) if offline else available}

    return {"uid": uid, "offline_cpus": offline, "pause_containers": containers,
            "presets": {"minimal": preset(minimal), "responsive": preset(responsive)}}
