#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
import json, sys, dbus
from typing import TypedDict


class Status(TypedDict):
    Active: bool
    Pending: bool
    Applying: bool
    CpuPreset: str
    LastError: str
    ApprovalMessage: str
    GpuRuntimeState: str
    GpuWarning: str


bus = dbus.SystemBus()
obj = bus.get_object("io.github.vcanonici.UltraPower", "/io/github/vcanonici/UltraPower")
if len(sys.argv) > 1 and sys.argv[1] == "off":
    obj.Disable("", dbus_interface="io.github.vcanonici.UltraPower")
elif len(sys.argv) > 1 and sys.argv[1] == "responsive":
    obj.SetPreset("responsive", dbus_interface="io.github.vcanonici.UltraPower")
else:
    data = obj.GetAll(
        "io.github.vcanonici.UltraPower", dbus_interface="org.freedesktop.DBus.Properties"
    )
    status: Status = {
        "Active": bool(data["Active"]),
        "Pending": bool(data["Pending"]),
        "Applying": bool(data["Applying"]),
        "CpuPreset": str(data["CpuPreset"]),
        "LastError": str(data["LastError"]),
        "ApprovalMessage": str(data["ApprovalMessage"]),
        "GpuRuntimeState": str(data["GpuRuntimeState"]),
        "GpuWarning": str(data["GpuWarning"]),
    }
    print(json.dumps(status, ensure_ascii=False))
