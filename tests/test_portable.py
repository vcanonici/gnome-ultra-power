# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable topology and installer input checks; all mutations are isolated."""
import importlib.util
from pathlib import Path
import sys
import os
import pwd
from unittest.mock import patch
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/ultra_power'))
from discovery import cpu_list, detect
from schema import config
import hardware
import gpu

spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Portable(unittest.TestCase):
    def topology(self, root, groups, frequencies):
        base = root / 'sys/devices/system/cpu'
        base.mkdir(parents=True)
        (base / 'online').write_text(','.join(str(c) for c in groups))
        for cpu, core in groups.items():
            folder = base / f'cpu{cpu}'
            (folder / 'topology').mkdir(parents=True)
            (folder / 'cpufreq').mkdir()
            (folder / 'topology/core_id').write_text(str(core))
            (folder / 'topology/physical_package_id').write_text('0')
            (folder / 'cpufreq/cpuinfo_max_freq').write_text(str(frequencies[cpu]))

    def test_hybrid_shifted_ids_and_other_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.topology(root, {0:0, 1:0, 4:4, 5:5}, {0:5000, 1:5000, 4:3000, 5:3000})
            result = config(detect(1007, root=root))
            self.assertEqual(result['uid'], 1007)
            self.assertEqual(result['presets']['minimal']['session'], [4,5])
            self.assertEqual(result['presets']['minimal']['online'], [0,1,4,5])
            self.assertFalse(result['offline_cpus'])
            self.assertFalse(result['pause_containers'])
            self.assertEqual(config(detect(1007, True, root=root))['presets']['minimal']['online'], [0,4,5])

    def test_single_core_without_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / 'sys/devices/system/cpu'
            base.mkdir(parents=True)
            (base / 'online').write_text('0')
            self.assertEqual(config(detect(1015, root=root))['presets']['minimal']['session'], [0])

    def test_smt_selects_physical_cores(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.topology(root, {0:0,1:1,2:0,3:1}, {n:3000 for n in range(4)})
            self.assertEqual(config(detect(1001, root=root))['presets']['minimal']['session'], [0,1])

    def test_invalid_ranges(self):
        for value in ('-1','0-1024','4-2','1-2-3','0,,1','x',''):
            with self.subTest(value=value), self.assertRaises(ValueError): cpu_list(value)
        self.assertEqual(cpu_list('0-2,4,2'), [0,1,2,4])

    def test_rejects_lab_bypass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            base=root/'sys/devices/system/cpu'; base.mkdir(parents=True)
            (base/'online').write_text('0')
            result=detect(1001,root=root); result['lab']=True
            with self.assertRaises(ValueError): config(result)

    def test_no_vpn_or_global_remote_login_stopped(self):
        self.assertNotIn('tailscaled.service', hardware.SYSTEM_UNITS)
        self.assertNotIn('windscribe.service', hardware.SYSTEM_UNITS)
        self.assertNotIn('gnome-remote-desktop.service', hardware.SYSTEM_UNITS)

    def test_extension_array_rejects_code(self):
        self.assertEqual(installer.enabled_extensions('@as []'), [])
        self.assertEqual(installer.enabled_extensions("['keep@example']"), ['keep@example'])
        for value in ('__import__("os").system("false")', '[1]', '{"key":1}'):
            with self.subTest(value=value), self.assertRaises((ValueError, SyntaxError)): installer.enabled_extensions(value)

    def test_symlink_root_destination_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'real').mkdir(); (root/'link').symlink_to(root/'real')
            with self.assertRaises(ValueError): installer.trusted_path(root/'link/file')

    def test_install_umask_preserves_readable_session_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'project'; (source/'extension').mkdir(parents=True)
            (source/'extension/metadata.json').write_text('{}')
            module=source/'daemon.py'; module.write_text('# module')
            home=root/'home'; home.mkdir()
            user=pwd.struct_passwd(('demo','x',os.getuid(),os.getgid(),'Demo',str(home),'/bin/sh'))
            lib=root/'lib'; destination=lib/'daemon.py'
            configuration={'uid':1001,'presets':{'minimal':{'online':[0],'session':[0]},'responsive':{'online':[0],'session':[0]}}}
            report={'config':configuration}
            with patch.object(installer,'LIB',lib), patch.object(installer,'PROJECT',source), \
                 patch.object(installer,'FILES',{str(destination):module}), \
                 patch.object(installer,'CONFIG',root/'config.json'), \
                 patch.object(installer,'BACKUP',root/'backup'), \
                 patch.object(installer,'pam_hashes',return_value={}), \
                 patch.object(installer,'session_command',return_value='[]'), \
                 patch.object(installer,'run',return_value='balanced'):
                previous=os.umask(0o077)
                try: installer.install(user,report)
                finally: os.umask(previous)
            self.assertEqual(lib.stat().st_mode & 0o777,0o755)
            self.assertEqual(destination.stat().st_mode & 0o777,0o644)
            self.assertEqual((root/'backup').stat().st_mode & 0o777,0o700)

    def test_malicious_manifest_refused(self):
        row={'uid':1001,'extension':'/tmp/extension','profile':'balanced','pam_hashes':{},'enabled':[],
             'files':{str(p):{'exists':False} for p in [*installer.FILES,installer.CONFIG]}}
        self.assertEqual(installer.manifest_data(row)['uid'],1001)
        row['files']['/etc/passwd']={'exists':False}
        with self.assertRaises(ValueError): installer.manifest_data(row)


if __name__=='__main__': unittest.main()
