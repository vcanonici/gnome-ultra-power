# SPDX-License-Identifier: GPL-3.0-or-later
"""Both approval paths keep the same state, session and process safeguards."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src/ultra_power'))
import daemon
from daemon import Service, Error


class ExplicitConfirmation(unittest.TestCase):
    def service(self):
        obj=Mock()
        obj.config={'uid':1001}
        obj.active=obj.pending=obj.applying=False
        obj.owner=None; obj.epoch=3
        obj.hardware.battery.return_value=True
        obj.hardware.local_session.return_value=True
        obj.bus.get_unix_user.return_value=1001
        obj.check=lambda *a,**k:Service.check(obj,*a,**k)
        obj._validate_request=lambda *a,**k:Service._validate_request(obj,*a,**k)
        return obj

    def test_ok_is_explicit_alternative_without_pam(self):
        obj=self.service()
        with patch.object(daemon,'STATE') as journal, patch.object(daemon,'consumers',return_value=[]), patch.object(daemon.subprocess,'Popen') as pam:
            journal.exists.return_value=False
            Service.ConfirmEnable(obj,False,[],':1.local')
        pam.assert_not_called()
        obj.job.assert_called_once_with('enter',token=4,reviewed=None)
        self.assertEqual(obj.owner,':1.local')

    def test_ok_rejects_ac_and_locked_session(self):
        for key in ('battery','local_session'):
            obj=self.service(); getattr(obj.hardware,key).return_value=False
            with self.subTest(key=key), patch.object(daemon,'STATE') as journal, patch.object(daemon,'consumers') as scan:
                journal.exists.return_value=False
                with self.assertRaises(Error): Service.ConfirmEnable(obj,False,[],':1.local')
                scan.assert_not_called(); obj.job.assert_not_called()

    def test_ok_never_activates_as_root_or_other_uid(self):
        for uid in (0,1002):
            obj=self.service(); obj.bus.get_unix_user.return_value=uid
            with self.subTest(uid=uid), self.assertRaises(Error): Service.ConfirmEnable(obj,False,[],':1.foreign')
            obj.job.assert_not_called()

    def test_ok_preserves_protected_process(self):
        obj=self.service()
        with patch.object(daemon,'STATE') as journal, patch.object(daemon,'consumers',return_value=[('42:1','gnome-shell',False)]):
            journal.exists.return_value=False
            with self.assertRaises(Error): Service.ConfirmEnable(obj,True,['42:1'],':1.local')
        obj.job.assert_not_called()

    def test_ok_rechecks_reviewed_identity(self):
        obj=self.service()
        with patch.object(daemon,'STATE') as journal, patch.object(daemon,'consumers',return_value=[('42:2','gpu-app',True)]):
            journal.exists.return_value=False
            with self.assertRaises(Error): Service.ConfirmEnable(obj,True,['42:1'],':1.local')
        obj.job.assert_not_called()

    def test_ok_can_replace_own_pending_fingerprint(self):
        obj=self.service(); obj.pending=True; obj.owner=':1.local'
        with patch.object(daemon,'STATE') as journal, patch.object(daemon,'consumers',return_value=[]):
            journal.exists.return_value=False
            Service.ConfirmEnable(obj,False,[],':1.local')
        obj.cancel.assert_called_once(); obj.job.assert_called_once()

    def test_ok_cannot_replace_another_clients_pending_approval(self):
        obj=self.service(); obj.pending=True; obj.owner=':1.other'
        with patch.object(daemon,'STATE') as journal:
            journal.exists.return_value=False
            with self.assertRaises(Error): Service.ConfirmEnable(obj,False,[],':1.local')
        obj.cancel.assert_not_called();obj.job.assert_not_called()

    def test_ok_rejects_pending_recovery(self):
        obj=self.service()
        with patch.object(daemon,'STATE') as journal:
            journal.exists.return_value=True
            with self.assertRaises(Error): Service.ConfirmEnable(obj,False,[],':1.local')
        obj.job.assert_not_called()


if __name__=='__main__':unittest.main()
