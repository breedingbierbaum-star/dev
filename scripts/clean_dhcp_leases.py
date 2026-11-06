#!/usr/bin/env python3
"""按 IP 清理 ISC DHCP IPv4 租约。默认预览；加 --apply 执行。"""
import argparse
import datetime
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata

HEADER = re.compile(r'^lease\s+([^\s{]+)\s*\{\s*(?:#.*)?$')
END = re.compile(r'^\s*}\s*(?:#.*)?$')


def colorize(text, code, stream=None):
    stream = sys.stdout if stream is None else stream
    if stream.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return f'\033[{code}m{text}\033[0m'
    return text


def cell(value, width):
    text = str(value)
    size = sum(0 if unicodedata.combining(ch) else
               2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1 for ch in text)
    return text + ' ' * max(0, width - size)


class ArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            stream = sys.stderr if file is None else file
            super()._print_message(colorize(message, '36', stream), stream)


def show_counts(counts, apply=False):
    print('\n  ' + '  '.join((cell('#', 3), cell('目标 IP', 15), cell('租约数', 6), '处理计划')))
    print('  ' + '-' * 66)
    for index, ip in enumerate(sorted(counts, key=ipaddress.IPv4Address), 1):
        label = ('清理目标' if apply else '待清理') if counts[ip] else '无匹配'
        status = colorize(label, '33' if counts[ip] else '90')
        print('  ' + '  '.join((cell(index, 3), cell(ip, 15), cell(counts[ip], 6), status)), flush=True)
    print('  ' + '-' * 66)
    print(f'\n统计：目标 IP {len(counts)} / 命中 IP {sum(n > 0 for n in counts.values())} / 租约记录 {sum(counts.values())}', flush=True)


def progress(message):
    print('  ' + message, flush=True)


def parse_ips(path):
    result = set()
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        value = line.split('#', 1)[0].strip()
        if value:
            try:
                result.add(str(ipaddress.IPv4Address(value)))
            except ValueError as exc:
                raise ValueError(f'{path} 第 {number} 行 IP 无效: {value}') from exc
    if not result:
        raise ValueError('IP 列表为空')
    return result


def clean(data, ips):
    lines = data.splitlines(keepends=True)
    result, counts = [], dict.fromkeys(ips, 0)
    i = 0
    while i < len(lines):
        match = HEADER.match(lines[i].rstrip('\r\n'))
        if not match or match.group(1) not in ips:
            result.append(lines[i]); i += 1; continue
        start, ip = i, match.group(1)
        i += 1
        # lease 内可能有 on expiry 等嵌套块，不能在第一个右括号结束。
        depth = 1
        while i < len(lines) and depth:
            # 忽略注释及引号内的括号（含转义字符）。
            line = lines[i]
            quoted = escaped = False
            for ch in line:
                if escaped:
                    escaped = False; continue
                if quoted and ch == '\\':
                    escaped = True; continue
                if ch == '"':
                    quoted = not quoted; continue
                if not quoted:
                    if ch == '#': break
                    if ch == '{': depth += 1
                    elif ch == '}': depth -= 1
                    if depth == 0: break
            i += 1
        if depth:
            raise ValueError(f'租约 {ip} 块不完整，拒绝修改（第 {start + 1} 行）')
        counts[ip] += 1
    return ''.join(result), counts


def run(*args, check=True):
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def execute(argv=None):
    parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''使用示例：
  python3 scripts/clean_dhcp_leases.py ips.txt             # 预览检查
  sudo python3 scripts/clean_dhcp_leases.py ips.txt --apply # 正式清理
  python3 scripts/clean_dhcp_leases.py ips.txt > dhcp_preview.log 2>&1

IP 列表每行一个 IPv4 地址，支持空行和 # 注释。
正式清理会停止 DHCP 服务、备份租约文件、清理并验证，随后恢复服务。
颜色仅在交互终端启用；日志保持纯文本，设置 NO_COLOR 可禁用颜色。''')
    parser.add_argument('ip_file', type=Path, help='IP 列表，每行一个 IPv4 地址')
    parser.add_argument('--leases', type=Path, default=Path('/var/lib/dhcp/dhcpd.leases'), help='租约文件（默认 /var/lib/dhcp/dhcpd.leases）')
    parser.add_argument('--service', default='isc-dhcp-server', help='systemd 服务名（默认 isc-dhcp-server）')
    parser.add_argument('--apply', action='store_true', help='实际停止服务并修改；默认仅预览')
    args = parser.parse_args(argv)
    print('\n' + colorize('DHCP 租约清理', '1'))
    print('模式：' + ('正式清理' if args.apply else '预览检查 · 不修改文件、不操作服务'))
    print(f'IP 列表：{args.ip_file}\n租约文件：{args.leases}\n服务：{args.service}', flush=True)
    ips = parse_ips(args.ip_file)
    if not args.leases.is_file() or args.leases.is_symlink():
        raise ValueError(f'租约文件不存在、不是普通文件或是符号链接: {args.leases}')
    if not args.apply:
        _, counts = clean(args.leases.read_text(encoding='utf-8', errors='surrogateescape'), ips)
        show_counts(counts)
        print('\n结果：' + colorize('预览完成', '32'))
        print('  未修改文件；添加 --apply 正式清理。' if any(counts.values()) else '  未找到匹配租约，无需清理。')
        return
    if os.geteuid() != 0:
        raise PermissionError('实际执行需要 root 权限')
    state = run('systemctl', 'is-active', args.service, check=False)
    if state.returncode != 0 or state.stdout.strip() != 'active':
        raise RuntimeError(f'服务当前不是 active，未执行操作: {args.service} ({state.stdout.strip() or state.stderr.strip()})')
    stopped = False
    print('\n' + colorize('执行进度', '1'), flush=True)
    try:
        # stop 失败也尝试恢复，避免服务已停止但命令报错的情况。
        stopped = True
        progress('停止 DHCP 服务...')
        run('systemctl', 'stop', args.service)
        state = run('systemctl', 'is-active', args.service, check=False)
        if state.stdout.strip() != 'inactive':
            raise RuntimeError(f'服务未确认停止: {state.stdout.strip()}')
        progress('服务已停止，读取租约文件')
        data = args.leases.read_text(encoding='utf-8', errors='surrogateescape')
        updated, counts = clean(data, ips)
        show_counts(counts, apply=True)
        print('\n' + colorize('清理进度', '1'), flush=True)
        if not any(counts.values()):
            progress('未找到匹配租约，无需修改。')
        else:
            stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup = args.leases.with_name(args.leases.name + '.bak.' + stamp)
            shutil.copy2(args.leases, backup)
            progress(f'备份文件：{backup}')
            fd, name = tempfile.mkstemp(prefix='.dhcp-leases-', dir=str(args.leases.parent))
            try:
                stat = args.leases.stat()
                with os.fdopen(fd, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
                    os.fchmod(f.fileno(), stat.st_mode & 0o7777)
                    os.fchown(f.fileno(), stat.st_uid, stat.st_gid)
                    f.write(updated); f.flush(); os.fsync(f.fileno())
                os.replace(name, args.leases)
            finally:
                if os.path.exists(name): os.unlink(name)
            _, remaining = clean(args.leases.read_text(encoding='utf-8', errors='surrogateescape'), ips)
            if any(remaining.values()):
                raise RuntimeError(f'清理后验证失败，仍存在租约: {remaining}')
            progress('租约文件验证通过：目标 IP 记录已清理。')
    finally:
        if stopped:
            progress('恢复 DHCP 服务...')
            start = run('systemctl', 'start', args.service, check=False)
            state = run('systemctl', 'is-active', args.service, check=False)
            if start.returncode != 0 or state.stdout.strip() != 'active':
                raise RuntimeError(f'服务启动失败，请立即检查 systemctl status {args.service}；{start.stderr.strip()}')
            progress('DHCP 服务：' + colorize('active（正常）', '32'))
    print('\n结果：' + colorize('清理完成' if any(counts.values()) else '无需清理', '32'))


def main(argv=None):
    started = time.monotonic()
    code = 0
    try:
        execute(argv)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        code = 1
        # 与 stdout 的表格保持顺序，重定向时不插入 ANSI 控制符。
        sys.stdout.flush()
        print('\n结果：' + colorize('执行失败', '31', sys.stderr), file=sys.stderr)
        print('\n处理详情', file=sys.stderr)
        for line in str(exc).splitlines():
            print('  ' + line, file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            for line in exc.stderr.strip().splitlines():
                print('  ' + line, file=sys.stderr)
        print('  建议：检查上述错误及文件权限；正式清理失败时，请确认 DHCP 服务状态，按需检查备份。', file=sys.stderr)
    print(f'\n耗时 {time.monotonic() - started:.1f} 秒 · 退出码 {code}\n', flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
