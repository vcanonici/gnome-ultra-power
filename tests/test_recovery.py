# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/ultra_power"))
import hardware
import gpu
from schema import config as parse_config, recovery as parse_recovery
from daemon import Service, Error
import daemon


class Recovery(unittest.TestCase):
    def test_preset_error_restores_baseline(self):
        obj = Mock()
        obj.applying = False
        obj.config = {"uid": 1000}
        hw = Mock()
        hw.cpus.side_effect = RuntimeError("preset falhou")

        def synchronous_thread(**kwargs):
            worker = Mock()
            worker.start.side_effect = kwargs["target"]
            return worker

        with (
            patch.object(daemon, "Hardware", return_value=hw),
            patch.object(daemon.threading, "Thread", side_effect=synchronous_thread),
            patch.object(
                daemon.GLib, "idle_add", side_effect=lambda fn, *args: fn(*args)
            ),
        ):
            Service.job(obj, "preset", preset="responsive")
        hw.restore.assert_called_once()
        obj.done.assert_called_once_with("preset", "preset falhou", "responsive")

    def test_failed_preset_clears_active(self):
        obj = Mock()
        obj.restore_requested = None
        obj.active = True
        with patch.object(daemon, "STATE") as journal:
            journal.exists.return_value = False
            Service.done(obj, "preset", "preset falhou", "responsive")
        self.assertFalse(obj.active)

    def test_boolean_is_not_uid(self):
        with self.assertRaises(ValueError):
            parse_config({"uid": True, "presets": {}})

    def test_rejects_cpu_alias(self):
        with self.assertRaises(ValueError):
            parse_config(
                {
                    "uid": 1000,
                    "presets": {
                        "minimal": {"online": [0, 12, 12], "session": [12]},
                        "responsive": {"online": [0], "session": [0]},
                    },
                }
            )

    def test_rejects_privileged_path(self):
        raw = {
            "boot_id": "lab",
            "uid": 1000,
            "profile": "balanced",
            "online": {},
            "allowed": "",
            "knobs": {"/etc/pam.d/sudo": "evil"},
            "units": [],
            "blue": [],
            "session": {
                "brightness": 20,
                "units": [],

            },
        }
        with self.assertRaises(ValueError):
            parse_recovery(raw)

    def test_snapshot_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            hardware.atomic(p, {"online": {"cpu1": "1"}})
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(p.read_text())["online"], {"cpu1": "1"})

    def test_failure_restores_transaction(self):
        hw = hardware.Hardware.__new__(hardware.Hardware)
        hw.uid = 1000
        hw.snapshot = Mock(
            return_value={"session": {}, "units": ["docker.service"]}
        )
        hw.restore = Mock()
        with (
            patch.object(hardware, "agent", return_value={}),
            patch.object(hardware, "run", side_effect=RuntimeError("stop failed")),
        ):
            with self.assertRaises(RuntimeError):
                hw.enter()
        hw.restore.assert_called_once()

    def test_failed_recovery_keeps_journal(self):
        hw = hardware.Hardware.__new__(hardware.Hardware)
        hw.config = {"offline_cpus": False}
        hw.battery = Mock(return_value=True)
        hw.uid = 1000
        hw.profile = Mock()
        state = {
            "boot_id": "lab",
            "uid": 1000,
            "profile": "balanced",
            "online": {},
            "allowed": "",
            "knobs": {},
            "units": [],
            "blue": [],
            "session": {
                "brightness": 20,
                "units": [],

            },
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state"
            p.write_text(json.dumps(state))
            with (
                patch.object(hardware, "STATE", p),
                patch.object(hardware, "RUNTIME", Path(d) / "active"),
                patch.object(hardware, "run", return_value=""),
                patch.object(
                    hardware, "agent", side_effect=RuntimeError("session offline")
                ),
            ):
                with self.assertRaises(RuntimeError):
                    hw.restore()
                self.assertTrue(p.exists())

    def test_default_recovery_never_hotplugs_cpus(self):
        hw=hardware.Hardware.__new__(hardware.Hardware)
        hw.uid=1000; hw.config={'offline_cpus':False}; hw.profile=Mock()
        state={'boot_id':'test','uid':1000,'profile':'balanced',
               'online':{'/sys/devices/system/cpu/cpu1/online':'1','/sys/devices/system/cpu/cpu2/online':'0'},
               'allowed':'','knobs':{},'units':[],'blue':[],
               'session':{'brightness':-1,'units':[]}}
        with tempfile.TemporaryDirectory() as tmp:
            journal=Path(tmp)/'state'; journal.write_text(json.dumps(state))
            with patch.object(hardware,'STATE',journal), \
                 patch.object(hardware,'RUNTIME',Path(tmp)/'active'), \
                 patch.object(hardware,'run',return_value=''), \
                 patch.object(hardware,'agent',return_value=None), \
                 patch.object(Path,'write_text') as writes:
                hw.restore('balanced')
                writes.assert_not_called()
            self.assertFalse(journal.exists())

    def test_root_cannot_activate_but_can_recover(self):
        obj = Mock()
        obj.bus.get_unix_user.return_value = 0
        obj.config = {"uid": 1000}
        with self.assertRaises(Error):
            Service.check(obj, ":1.root")
        Service.check(obj, ":1.root", recovery=True)

    def test_stale_force_list_never_kills(self):
        with (
            patch.object(gpu, "consumers", return_value=[("42:2", "app", True)]),
            patch.object(gpu.os, "pidfd_open") as opened,
            patch.object(gpu.signal, "pidfd_send_signal") as kill,
        ):
            with self.assertRaises(RuntimeError):
                gpu.terminate_reviewed(1000, ["42:1"])
            opened.assert_not_called()
            kill.assert_not_called()

    def test_protected_server_never_killed(self):
        with (
            patch.object(
                gpu, "consumers", return_value=[("42:1", "gnome-shell", False)]
            ),
            patch.object(gpu.signal, "pidfd_send_signal") as kill,
        ):
            with self.assertRaises(RuntimeError):
                gpu.terminate_reviewed(1000, ["42:1"])
            kill.assert_not_called()

    def test_reviewed_identity_killed_once(self):
        with (
            patch.object(gpu, "consumers", return_value=[("42:1", "app", True)]),
            patch.object(gpu.os, "pidfd_open", return_value=9),
            patch.object(gpu.os, "close") as close,
            patch.object(gpu.signal, "pidfd_send_signal") as kill,
        ):
            gpu.terminate_reviewed(1000, ["42:1"])
            kill.assert_called_once_with(9, gpu.signal.SIGKILL)
            close.assert_called_once_with(9)


if __name__ == "__main__":
    unittest.main()
