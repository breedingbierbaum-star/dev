#!/usr/bin/env python3
"""检查 virsh、电源、libvirtd、IPMI 和端口；默认自动修复，可选只读模式。"""
import argparse
from collections import Counter
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata


def colorize(text, code, stream=None):
    stream = sys.stdout if stream is None else stream
    if stream.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return f'\033[{code}m{text}\033[0m'
    return text


def status_text(status):
    return colorize(status, {'通过': '32', '已修复': '36', '失败': '31',
                             '待处理': '33', '跳过': '90'}.get(status, '0'))


def display_width(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
               for char in text)


def pad(text, width):
    return text + ' ' * max(0, width - display_width(text))


def brief(text, width=44):
    text = ' '.join(text.split())
    if display_width(text) <= width:
        return text
    result = ''
    for char in text:
        if display_width(result + char) > width - 3:
            break
        result += char
    return result + '...'


class ArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            stream = sys.stderr if file is None else file
            super()._print_message(colorize(message, '36', stream), stream)


class CheckError(Exception):
    pass


class Checker:
    def __init__(self, args):
        self.args = args
        self.results = []
        self.started = time.monotonic()

    def report(self, name, status, detail, advice=''):
        self.results.append((name, status, detail, advice))
        summary = '检查未通过，详见下方' if status == '失败' else brief(detail)
        label = status_text(status) + ' ' * max(0, 6 - display_width(status))
        print(f'  {len(self.results)}  {pad(name, 16)}  {label}  {summary}', flush=True)

    def command(self, command, timeout=None):
        if self.args.verbose:
            print('$ ' + shlex.join(command), flush=True)
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    errors='replace', timeout=timeout or self.args.timeout,
                                    check=False)
        except FileNotFoundError as exc:
            raise CheckError(f'命令不存在：{command[0]}') from exc
        except PermissionError as exc:
            raise CheckError(f'无权执行：{command[0]}') from exc
        except subprocess.TimeoutExpired as exc:
            raise CheckError(f'{command[0]} 执行超时（{timeout or self.args.timeout:g} 秒）') from exc
        except OSError as exc:
            raise CheckError(f'{command[0]} 执行失败：{exc}') from exc
        if self.args.verbose:
            for output in (result.stdout, result.stderr):
                if output.strip():
                    print(output.strip(), flush=True)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip() or '无错误输出'
            raise CheckError(f'{command[0]} 退出码 {result.returncode}：{detail}')
        return result.stdout.strip()

    def state(self):
        output = self.command(['systemctl', 'show', self.args.service,
                               '--property=LoadState', '--property=ActiveState', '--property=SubState'])
        states = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        if not all(states.get(k) for k in ('LoadState', 'ActiveState', 'SubState')):
            raise CheckError('服务状态信息不完整')
        if states['LoadState'] != 'loaded':
            raise CheckError(f"服务未正常加载：{states['LoadState']}")
        return states['ActiveState'] == 'active' and states['SubState'] == 'running', states

    def wait_for(self, action, label):
        # 有限次数重试；每次操作也受 --timeout 限制。
        error = None
        for attempt in range(self.args.attempts):
            try:
                return action()
            except CheckError as exc:
                error = exc
                if attempt + 1 < self.args.attempts:
                    print(f'  等待{label}：{attempt + 1}/{self.args.attempts} 次未通过，'
                          f'{self.args.interval:g} 秒后复查', flush=True)
                    time.sleep(self.args.interval)
        raise CheckError(f'{label}验证失败：{error}')

    def virsh(self):
        self.command(['virsh', '-c', 'qemu:///system', 'list'])

    def service_ready(self):
        ready, states = self.state()
        if not ready:
            raise CheckError(f"{states['ActiveState']}/{states['SubState']}")

    def ports(self):
        try:
            with urllib.request.urlopen(self.args.api_url.rstrip('/') + '/v1/ports',
                                        timeout=self.args.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise CheckError(f'端口查询 HTTP {exc.code}：{exc.reason}') from exc
        except (OSError, ValueError, UnicodeError) as exc:
            raise CheckError(f'端口查询失败：{exc}') from exc
        ports = data.get('ports') if isinstance(data, dict) else None
        if not isinstance(ports, list):
            raise CheckError('接口返回缺少有效 ports 数组')
        seen = set()
        for port in ports:
            if not isinstance(port, dict):
                raise CheckError('端口记录不是对象')
            ident = port.get('id')
            if (isinstance(ident, bool) or not isinstance(ident, (str, int))
                    or str(ident).strip() in ('', '.', '..')):
                raise CheckError('端口 ID 无效，未执行删除')
            if str(ident) in seen:
                raise CheckError(f'端口 ID 重复：{ident}')
            seen.add(str(ident))
            if port.get('status') not in ('up', 'down'):
                raise CheckError(f'端口 {ident} 状态未知：{port.get("status")!r}；未执行删除')
        return ports

    def delete_port(self, ident):
        url = self.args.api_url.rstrip('/') + '/v1/vports/' + urllib.parse.quote(ident, safe='')
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method='DELETE'),
                                        timeout=self.args.timeout) as response:
                print(f'  端口 {ident}：删除请求 HTTP {response.status}', flush=True)
        except urllib.error.HTTPError as exc:
            raise CheckError(f'端口 {ident} 删除 HTTP {exc.code}：{exc.reason}') from exc
        except (OSError, ValueError) as exc:
            raise CheckError(f'端口 {ident} 删除失败：{exc}') from exc

    def check_ports(self):
        ports = self.ports()
        down = [str(p['id']) for p in ports if p['status'] == 'down']
        if not down:
            return '通过', f'共 {len(ports)} 个端口，无 down 端口'
        if self.args.check_only:
            return '待处理', f'{len(down)} 个 down 端口：{", ".join(down)}；只读模式未删除'
        errors = []
        for ident in down:
            try:
                self.delete_port(ident)
            except CheckError as exc:
                errors.append(str(exc))
                print(f'  {exc}', flush=True)
        def verify():
            remaining = {str(p['id']) for p in self.ports()} & set(down)
            if remaining:
                raise CheckError('原端口仍存在：' + ', '.join(sorted(remaining)))
        try:
            self.wait_for(verify, '端口删除结果')
        except CheckError as exc:
            errors.append(str(exc))
        if errors:
            raise CheckError('；'.join(errors))
        return '已修复', f'{len(down)} 个 down 端口已删除并验证'

    def execute(self):
        print('\n' + colorize('LIBVIRT 主机检查', '1'))
        print('模式：' + ('只读检查' if self.args.check_only else '检查并自动修复'))
        print('\n  #  ' + pad('检查项', 16) + '  ' + pad('状态', 6) + '  说明')
        print('  ' + '-' * 70, flush=True)
        virsh_error = None
        try:
            self.virsh()
        except CheckError as exc:
            virsh_error = str(exc)
        try:
            value = self.command(['/usr/bin/bes-power', 'status']).lower()
            if value not in ('on', 'off'):
                raise CheckError(f'预期 on 或 off，实际 {value!r}')
            power = ('通过', f'查询正常，当前电源状态 {value}', '')
        except CheckError as exc:
            power = ('失败', str(exc), '检查 bes-power 安装、权限与设备状态')
        service = ('通过', 'active/running', '')
        restarted = False
        try:
            ready, states = self.state()
            if not ready:
                detail = f"{states['ActiveState']}/{states['SubState']}"
                if self.args.check_only:
                    service = ('待处理', detail + '；只读模式未重启', '确认维护条件后使用自动修复模式')
                else:
                    if os.geteuid() != 0:
                        raise CheckError('重启需要 root 权限，请使用 sudo 运行')
                    print(f'  {self.args.service} 当前 {detail}，正在重启', flush=True)
                    self.command(['systemctl', 'restart', self.args.service], timeout=90)
                    self.wait_for(self.service_ready, '服务启动')
                    restarted = True
                    service = ('已修复', detail + ' → 重启 → active/running', '')
            if restarted:
                try:
                    self.wait_for(self.virsh, 'virsh 连接')
                    virsh_error = None
                except CheckError as exc:
                    virsh_error = str(exc)
                    raise
        except CheckError as exc:
            service = ('失败', str(exc), f'检查 systemctl status {self.args.service} 和 journalctl -u {self.args.service}')
        self.report('virsh 连接', '失败' if virsh_error else ('已修复' if restarted else '通过'),
                    virsh_error or 'qemu:///system 连接正常',
                    '检查 libvirt 日志、连接权限及服务状态' if virsh_error else '')
        self.report('bes-power', *power)
        self.report(self.args.service, *service)
        try:
            self.command(['ipmitool', '-I', 'open', '-U', 'operator', '-E', 'mc', 'info'])
            self.report('IPMI', '通过', '管理控制器查询成功')
        except CheckError as exc:
            self.report('IPMI', '失败', str(exc), '检查 ipmitool、本地 IPMI 设备权限及驱动')
        if any(status not in ('通过', '已修复') for _, status, _, _ in self.results):
            self.report('端口处理', '跳过', '前置检查未全部通过', '先处理上述失败或待处理项，再运行检查')
        else:
            try:
                status, detail = self.check_ports()
                self.report('端口处理', status, detail,
                            '确认端口可删除后使用自动修复模式' if status == '待处理' else '')
            except CheckError as exc:
                self.report('端口处理', '失败', str(exc), '检查端口管理接口、服务日志及对应端口状态')
        counts = Counter(status for _, status, _, _ in self.results)
        code = int(any(status not in ('通过', '已修复') for _, status, _, _ in self.results))
        print('  ' + '-' * 70)
        print('\n结果：' + colorize('全部通过' if code == 0 else '需要处理', '32' if code == 0 else '33'))
        print('  ' + ' / '.join(f'{key} {counts[key]}' for key in
                               ('通过', '已修复', '失败', '待处理', '跳过') if counts[key]))
        issues = [(name, status, detail, advice) for name, status, detail, advice in self.results
                  if status in ('失败', '待处理')]
        if issues:
            print('\n' + colorize('处理详情', '1'))
        for name, status, detail, advice in issues:
            print(f'\n  {name} · {status_text(status)}')
            for line in detail.splitlines():
                print(f'    {line}')
            if advice:
                print(f'    建议：{advice}')
        print(f'\n耗时 {time.monotonic() - self.started:.1f} 秒 · 退出码 {code}\n', flush=True)
        return code


def positive(value):
    number = float(value)
    if not 0 < number < float('inf'):
        raise argparse.ArgumentTypeError('必须是有限正数')
    return number


def parse_args(argv=None):
    parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''使用示例：
  python3 scripts/check_libvirt.py --check-only       # 只读检查
  sudo python3 scripts/check_libvirt.py               # 检查并自动修复
  python3 scripts/check_libvirt.py --check-only --verbose
  python3 scripts/check_libvirt.py --check-only > check_libvirt.log 2>&1

默认模式会重启异常服务并删除 down 端口，请确认维护条件后运行。
颜色仅在交互终端启用；重定向或通过管道保存日志时输出纯文本。
设置 NO_COLOR 环境变量可禁用颜色。''')
    parser.add_argument('--check-only', action='store_true', help='只检查，不重启服务或删除端口')
    parser.add_argument('--verbose', action='store_true', help='显示完整命令及输出')
    parser.add_argument('--service', default='libvirtd', help='systemd 服务名（默认 libvirtd）')
    parser.add_argument('--api-url', default='http://127.0.0.1:2929', help='端口管理接口基础地址')
    parser.add_argument('--timeout', type=positive, default=30, help='单次命令/HTTP 请求超时秒数（默认 30）')
    parser.add_argument('--attempts', type=int, choices=range(1, 11), default=5, metavar='1..10', help='修复后验证次数（默认 5）')
    parser.add_argument('--interval', type=positive, default=2, help='验证重试间隔秒数（默认 2）')
    args = parser.parse_args(argv)
    url = urllib.parse.urlsplit(args.api_url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.query or url.fragment or url.username or url.password:
        parser.error('--api-url 必须是无账号、查询参数或片段的 HTTP(S) 地址')
    if not args.service or args.service.startswith('-'):
        parser.error('--service 必须是有效服务名')
    return args


def main(argv=None):
    try:
        return Checker(parse_args(argv)).execute()
    except KeyboardInterrupt:
        print('\n检查被中断；已执行的修复不会自动回滚。', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
