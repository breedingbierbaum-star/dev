import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch, MagicMock

spec = importlib.util.spec_from_file_location('check_libvirt', Path(__file__).resolve().parents[1] / 'scripts' / 'check_libvirt.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LibvirtTests(unittest.TestCase):
    def checker(self, *args):
        return m.Checker(m.parse_args(['--attempts', '2', '--interval', '0.001', *args]))

    def execute(self, checker):
        with contextlib.redirect_stdout(io.StringIO()):
            return checker.execute()

    def command(self, argv, **kwargs):
        if argv[0] == '/usr/bin/bes-power':
            return 'off'
        if argv[:2] == ['systemctl', 'show']:
            return 'LoadState=loaded\nActiveState=active\nSubState=running'
        return ''

    def test_check_only_has_no_mutations(self):
        checker = self.checker('--check-only')
        with patch.object(checker, 'command', side_effect=self.command) as command, patch.object(checker, 'ports', return_value=[{'id': 'p1', 'status': 'down'}]), patch.object(checker, 'delete_port') as delete:
            self.assertEqual(self.execute(checker), 1)
            delete.assert_not_called()
            self.assertFalse(any(c.args[0][:2] == ['systemctl', 'restart'] for c in command.call_args_list))
            self.assertEqual(checker.results[-1][1], '待处理')

    def test_inactive_read_only_skips_ports(self):
        checker = self.checker('--check-only')
        with patch.object(checker, 'command', side_effect=self.command) as command, patch.object(checker, 'state', return_value=(False, {'ActiveState':'inactive','SubState':'dead'})), patch.object(checker, 'ports') as ports:
            self.assertEqual(self.execute(checker), 1)
            ports.assert_not_called()
            self.assertEqual(checker.results[-1][1], '跳过')
            self.assertFalse(any(c.args[0][:2] == ['systemctl', 'restart'] for c in command.call_args_list))

    def test_repair_rechecks_virsh(self):
        checker = self.checker()
        states = [(False, {'ActiveState':'inactive','SubState':'dead'}), (True, {})]
        with patch.object(checker, 'command', side_effect=self.command), patch.object(checker, 'state', side_effect=states), patch.object(checker, 'virsh', side_effect=[m.CheckError('not ready'), None]), patch.object(checker, 'ports', return_value=[]), patch.object(m.os, 'geteuid', return_value=0):
            self.assertEqual(self.execute(checker), 0)
            self.assertEqual(checker.results[0][1], '已修复')

    def test_virsh_failure_after_restart_is_not_success(self):
        checker = self.checker()
        with patch.object(checker, 'command', side_effect=self.command), patch.object(checker, 'state', side_effect=[(False, {'ActiveState':'inactive','SubState':'dead'}), (True,{})]), patch.object(checker, 'virsh', side_effect=[None, m.CheckError('failed'), m.CheckError('failed')]), patch.object(checker, 'ports') as ports, patch.object(m.os, 'geteuid', return_value=0):
            self.assertEqual(self.execute(checker), 1)
            self.assertEqual(checker.results[0][1], '失败')
            ports.assert_not_called()

    def test_unknown_port_status_rejected(self):
        checker = self.checker()
        response = MagicMock()
        response.__enter__.return_value = io.StringIO(json.dumps({'ports':[{'id':'p1'}]}))
        with patch.object(m.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(m.CheckError, '状态未知'):
                checker.ports()

    def test_eventual_deletion(self):
        checker = self.checker()
        with patch.object(checker, 'ports', side_effect=[[{'id':'p1','status':'down'}], [{'id':'p1','status':'down'}], []]), patch.object(checker, 'delete_port') as delete, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(checker.check_ports()[0], '已修复')
            delete.assert_called_once_with('p1')

    def test_delete_error_still_fails_if_port_disappears(self):
        checker = self.checker()
        with patch.object(checker, 'ports', side_effect=[[{'id':'p1','status':'down'}], []]), patch.object(checker, 'delete_port', side_effect=m.CheckError('HTTP 500')), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(m.CheckError, 'HTTP 500'):
                checker.check_ports()

    def test_command_errors(self):
        checker = self.checker()
        for error, expected in [(FileNotFoundError(), '命令不存在'), (PermissionError(), '无权执行'), (subprocess.TimeoutExpired('virsh', 1), '超时')]:
            with self.subTest(expected=expected), patch.object(m.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(m.CheckError, expected):
                    checker.command(['virsh'])


if __name__ == '__main__':
    unittest.main()
