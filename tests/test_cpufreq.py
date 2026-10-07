# SPDX-License-Identifier: GPL-3.0-or-later
import errno
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/ultra_power"))
from hardware import Hardware
import hardware


class CpuHotplug(unittest.TestCase):
    def test_inactive_policy_is_skipped_without_masking_other_errors(self):
        hw = Hardware.__new__(Hardware)
        active, offline, empty = MagicMock(), MagicMock(), MagicMock()
        (active.parent / 'affected_cpus').read_text.return_value = '12'
        (offline.parent / 'affected_cpus').read_text.side_effect = OSError(errno.EBUSY, 'busy')
        (empty.parent / 'affected_cpus').read_text.return_value = ''
        with patch.object(hardware, 'Path') as root:
            root.return_value.glob.return_value = [active, offline, empty]
            self.assertEqual(hw.energy_paths(), [active])
            (offline.parent / 'affected_cpus').read_text.side_effect = OSError(errno.EIO, 'I/O failure')
            with self.assertRaisesRegex(RuntimeError, 'consultar CPUs'):
                hw.energy_paths()

    def test_epp_precedes_offline_and_handles_responsive_transition(self):
        hw = Hardware.__new__(Hardware)
        hw.uid = 1000
        hw.config = {'offline_cpus': True, 'presets': {
            'minimal': {'online': [0, 12, 13], 'session': [12, 13]},
            'responsive': {'online': [0, 1, 2, 3, 12, 13, 14, 15],
                           'session': [0, 1, 2, 3, 12, 13, 14, 15]},
        }}
        online = set(range(20))
        events = []
        pins = {}

        def cpu_path(n):
            p = Mock()
            p.parent.name = f'cpu{n}'
            p.exists.return_value = n != 0

            def write(value):
                if value == '1': online.add(n)
                else: online.discard(n)
                events.append((value, n))
            p.write_text.side_effect = write
            return p

        for n in range(20): pins[n] = cpu_path(n)

        def paths(name):
            if '/cpu' in name and name.split('/cpu')[-1].split('/')[0].isdigit():
                return pins[int(name.split('/cpu')[-1].split('/')[0])]
            p = Mock()
            p.exists.return_value = False
            p.glob.return_value = list(pins.values())
            return p

        def energies():
            result = []
            for n in sorted(online):
                p = MagicMock()
                (p.parent / 'scaling_governor').exists.return_value = False
                def write(value, cpu=n):
                    if cpu not in online: raise OSError(errno.EBUSY, 'inactive policy')
                    self.assertEqual(value, 'power')
                    events.append(('epp', cpu))
                p.write_text.side_effect = write
                result.append(p)
            return result

        hw.energy_paths = energies
        with patch.object(hardware, 'Path', side_effect=paths), patch.object(hardware, 'run'):
            hw.cpus('minimal')
            self.assertEqual(online, {0, 12, 13})
            self.assertLess(max(i for i,e in enumerate(events) if e[0]=='epp'),
                            min(i for i,e in enumerate(events) if e[0]=='0'))
            events.clear()
            hw.cpus('responsive')
            self.assertEqual(online, {0, 1, 2, 3, 12, 13, 14, 15})
            events.clear()
            hw.cpus('minimal')
            self.assertEqual(online, {0, 12, 13})
            self.assertLess(max(i for i,e in enumerate(events) if e[0]=='epp'),
                            min(i for i,e in enumerate(events) if e[0]=='0'))


if __name__ == '__main__':
    unittest.main()
