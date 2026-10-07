# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/ultra_power"))
import gpu
import hardware
import daemon
from daemon import Service, Error


class GpuPolicy(unittest.TestCase):
    def ref(self, name, uid=1000, unit=gpu.SHELL_UNIT, token='42:1'):
        return gpu.Reference(token, name[:15], uid, name,
                             (f'/user.slice/user-{uid}.slice/{unit}',))

    def test_intel_compositor_and_xwayland_are_not_workload_blockers(self):
        rows = [self.ref('gnome-shell'), self.ref('Xwayland', token='43:1')]
        self.assertEqual(gpu.review_rows(1000, rows, True), [])

    def test_nvidia_or_unknown_compositor_remains_protected(self):
        rows = [self.ref('gnome-shell'), self.ref('Xwayland', token='43:1')]
        self.assertEqual([r[2] for r in gpu.review_rows(1000, rows, False)], [False, False])

    def test_other_gpu_app_in_shell_cgroup_is_not_exempt(self):
        self.assertEqual(gpu.review_rows(1000, [self.ref('gpu-app')], True),
                         [('42:1', 'gpu-app', False)])

    def test_remote_unit_is_managed_but_same_name_app_is_protected(self):
        managed = self.ref('gnome-remote-desktop-daemon', unit='gnome-remote-desktop.service')
        unmanaged = self.ref('gnome-remote-desktop-daemon', unit='app-test.scope')
        self.assertEqual(gpu.review_rows(1000, [managed], False), [])
        self.assertEqual(gpu.review_rows(1000, [unmanaged], False)[0][2], False)

    def test_other_user_remote_is_not_our_managed_unit(self):
        row = self.ref('gnome-remote-desktop-daemon', uid=1001,
                       unit='gnome-remote-desktop.service')
        self.assertEqual(gpu.review_rows(1000, [row], True)[0][2], False)

    def test_root_and_other_uid_never_killable(self):
        rows = [self.ref('gpu-app', uid=0, unit='app.scope'),
                self.ref('gpu-app', uid=1001, unit='app.scope', token='43:1')]
        self.assertEqual([r[2] for r in gpu.review_rows(1000, rows, True)], [False, False])

    def test_real_app_is_reviewable(self):
        self.assertEqual(gpu.review_rows(1000, [self.ref('gpu-app', unit='app.scope')], True),
                         [('42:1', 'gpu-app', True)])

    def test_renderer_journal_uses_boot_pid_uid_and_unit(self):
        gpu.primary_card.cache_clear()
        with patch.object(gpu.subprocess, 'run', return_value=Mock(
                returncode=0, stdout=json.dumps({'MESSAGE': 'GPU /dev/dri/card9 selected primary given udev rule'}))) as run:
            self.assertEqual(gpu.primary_card(1000, '42:1', 'boot'), '/dev/dri/card9')
            args = run.call_args.args[0]
            for selector in ('_PID=42', '_UID=1000', '_BOOT_ID=boot', f'_SYSTEMD_USER_UNIT={gpu.SHELL_UNIT}'):
                self.assertIn(selector, args)
        gpu.primary_card.cache_clear()

    def test_missing_renderer_evidence_is_not_intel(self):
        with patch.object(gpu, 'primary_card', return_value=None):
            self.assertFalse(gpu.intel_desktop(1000, [self.ref('gnome-shell')]))

    def test_gpu_status_missing_is_unknown_not_suspended(self):
        with tempfile.TemporaryDirectory() as d, patch.object(gpu, 'nvidia_devices', return_value=[Path(d)/'absent']):
            self.assertEqual(gpu.runtime_state(), 'unknown')
            self.assertIn('não confirmada', gpu.warning(True, gpu.runtime_state()))

    def test_gpu_suspend_wait_is_bounded_and_success_is_observed(self):
        with patch.object(gpu, 'runtime_state', side_effect=['active', 'suspended']), \
             patch.object(gpu.time, 'sleep'):
            self.assertEqual(gpu.wait_for_suspend(), 'suspended')
        with patch.object(gpu, 'runtime_state', return_value='active'), \
             patch.object(gpu.time, 'monotonic', side_effect=[0, 6]):
            self.assertEqual(gpu.wait_for_suspend(), 'active')

    def test_warning_only_when_ultra_active_and_not_suspended(self):
        self.assertEqual(gpu.warning(False, 'active'), '')
        self.assertEqual(gpu.warning(True, 'suspended'), '')
        self.assertIn('economia parcial', gpu.warning(True, 'active'))

    def test_app_becoming_protected_after_pidfd_open_never_killed(self):
        with patch.object(gpu, 'consumers', side_effect=[
                [('42:1', 'app', True)], [('42:1', 'gnome-shell', False)]]), \
             patch.object(gpu.os, 'pidfd_open', return_value=9), \
             patch.object(gpu.os, 'close') as close, \
             patch.object(gpu.signal, 'pidfd_send_signal') as kill:
            with self.assertRaises(RuntimeError):
                gpu.terminate_reviewed(1000, ['42:1'])
            kill.assert_not_called()
            close.assert_called_once_with(9)


class Activation(unittest.TestCase):
    def service(self):
        obj = Mock()
        obj.config = {'uid': 1000}
        obj.hardware.battery.return_value = True
        obj.hardware.local_session.return_value = True
        obj.active = obj.pending = obj.applying = False
        obj.epoch = 0
        return obj

    def test_ac_rejected_before_gpu_inventory(self):
        obj = self.service()
        obj.hardware.battery.return_value = False
        with patch.object(daemon, 'consumers') as scan:
            with self.assertRaisesRegex(Error, 'carregador'):
                Service.GetBlockers(obj, ':1.test')
            scan.assert_not_called()

    def test_partial_with_protected_refs_still_requires_pam(self):
        obj = self.service()
        with patch.object(daemon, 'STATE') as journal, \
             patch.object(daemon, 'consumers', return_value=[('42:1', 'gnome-shell', False)]), \
             patch.object(daemon.subprocess, 'Popen') as pam, \
             patch.object(daemon.GLib, 'timeout_add'):
            journal.exists.return_value = False
            Service.RequestEnable(obj, False, [], ':1.test')
            pam.assert_called_once()
            self.assertTrue(obj.pending)
            self.assertFalse(obj.active)
            obj.job.assert_not_called()

    def test_force_protected_rejected_before_pam(self):
        obj = self.service()
        with patch.object(daemon, 'STATE') as journal, \
             patch.object(daemon, 'consumers', return_value=[('42:1', 'gnome-shell', False)]), \
             patch.object(daemon.subprocess, 'Popen') as pam:
            journal.exists.return_value = False
            with self.assertRaises(Error):
                Service.RequestEnable(obj, True, ['42:1'], ':1.test')
            pam.assert_not_called()

    def test_hardware_partial_commits_only_after_pausing_services(self):
        hw = hardware.Hardware.__new__(hardware.Hardware)
        hw.uid = 1000; hw.config = {'uid':1000}
        hw.snapshot = Mock(return_value={'session': {}, 'units': ['gnome-remote-desktop.service'], 'blue': [], 'knobs': {}})
        hw.profile = Mock()
        hw.cpus = Mock()
        hw.restore = Mock()
        order = []
        with tempfile.TemporaryDirectory() as d, \
             patch.object(hardware, 'RUNTIME', Path(d)/'active'), \
             patch.object(hardware, 'agent', side_effect=lambda *a, **kw: order.append('session paused')), \
             patch.object(hardware, 'run', side_effect=lambda *a: order.append('system paused')), \
             patch.object(hardware, 'wait_for_suspend', side_effect=lambda: order.append('gpu checked') or 'active'), \
             patch.object(hardware, 'Path', side_effect=lambda *a: Path(d)/'no-keyboard'):
            hw.enter()
            self.assertTrue((Path(d)/'active').exists())
        self.assertEqual(order, ['session paused', 'system paused', 'gpu checked'])
        hw.restore.assert_not_called()


if __name__ == '__main__':
    unittest.main()
