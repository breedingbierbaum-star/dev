#!/usr/bin/env python3
"""检查 virsh、bes-power 和 libvirtd，必要时重启 libvirtd。"""
import os
import json
import urllib.request
import urllib.parse
import urllib.error
import subprocess
import sys

TIMEOUT = 30


def run(command, timeout=TIMEOUT, env=None):
    print('\n$ ' + ' '.join(command), flush=True)
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                errors='replace', timeout=timeout, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print('执行失败: {}'.format(exc), flush=True)
        return None
    if result.stdout.strip():
        print(result.stdout.strip(), flush=True)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr, flush=True)
    return result


def virsh_check():
    result = run(['virsh', '-c', 'qemu:///system', 'list'])
    ok = result is not None and result.returncode == 0
    print('virsh list: {}'.format('正常' if ok else '异常'))
    return ok


def check_services():
    initial_virsh = virsh_check()
    power = run(['/usr/bin/bes-power', 'status'])
    power_value = power.stdout.strip().lower() if power is not None else ''
    power_ok = power is not None and power.returncode == 0 and power_value in ('on', 'off')
    print('bes-power status: {}'.format('正常 ({})'.format(power_value) if power_ok else '异常，预期输出 on 或 off'))

    # status 供人工查看；is-active 的精确输出用于判断是否运行。
    run(['systemctl', '--no-pager', '--full', 'status', 'libvirtd'])
    state_result = run(['systemctl', 'show', 'libvirtd', '--property=LoadState',
                        '--property=ActiveState', '--property=SubState'])
    if state_result is None or state_result.returncode != 0:
        print('无法检查 libvirtd 状态，未尝试重启。')
        return 1
    states = dict(line.split('=', 1) for line in state_result.stdout.splitlines() if '=' in line)
    if not all(key in states for key in ('LoadState', 'ActiveState', 'SubState')):
        print('libvirtd 状态信息不完整，未尝试重启。')
        return 1
    if states['LoadState'] != 'loaded':
        print('libvirtd 服务未正常加载（{}），无法确认服务，未尝试重启。'.format(states['LoadState']))
        return 1
    if states['ActiveState'] == 'active' and states['SubState'] == 'running':
        print('libvirtd: active (running)')
        final_virsh = initial_virsh
    else:
        print('libvirtd 不是 active (running)，尝试重启。')
        if os.geteuid() != 0:
            print('重启需要 root 权限，请使用 sudo 运行脚本。')
            return 1
        restart = run(['systemctl', 'restart', 'libvirtd'], timeout=90)
        run(['systemctl', '--no-pager', '--full', 'status', 'libvirtd'])
        after = run(['systemctl', 'show', 'libvirtd', '--property=LoadState',
                     '--property=ActiveState', '--property=SubState'])
        after_states = dict(line.split('=', 1) for line in after.stdout.splitlines() if '=' in line) if after else {}
        final_virsh = virsh_check()
        if (restart is None or restart.returncode != 0 or after is None or after.returncode != 0
                or after_states.get('LoadState') != 'loaded'
                or after_states.get('ActiveState') != 'active'
                or after_states.get('SubState') != 'running'):
            print('libvirtd 重启或状态复查失败。')
            return 1
    if not final_virsh or not power_ok:
        print('\n检查结果：存在异常，请查看上面的输出。')
        return 1
    print('\nvirsh、bes-power 和 libvirtd 检查正常。')
    return 0


def check_ports():
    base = 'http://127.0.0.1:2929'
    print('\n检查端口状态：GET /v1/ports', flush=True)
    try:
        with urllib.request.urlopen(base + '/v1/ports', timeout=TIMEOUT) as response:
            data = json.load(response)
        ports = data.get('ports') if isinstance(data, dict) else None
        if not isinstance(ports, list) or any(not isinstance(port, dict) for port in ports):
            raise ValueError('返回数据不包含有效的 ports 数组')
        down = [port for port in ports if port.get('status') == 'down']
        if any(not isinstance(port.get('id'), (str, int)) or isinstance(port.get('id'), bool)
               or str(port.get('id')).strip() in ('', '.', '..') for port in down):
            raise ValueError('存在没有有效 id 的 down 端口，未执行删除')
    except (OSError, ValueError, UnicodeError) as exc:
        print('端口查询失败: {}'.format(exc))
        return False
    if not down:
        print('没有状态为 down 的端口。')
        return True
    print('发现 {} 个 down 端口。'.format(len(down)))
    success = True
    for port in down:
        port_id = str(port['id'])
        path_id = urllib.parse.quote(port_id, safe='')
        print('删除 vport: {}'.format(port_id), flush=True)
        request = urllib.request.Request(base + '/v1/vports/' + path_id, method='DELETE')
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                print('删除请求成功，HTTP {}'.format(response.status))
        except (OSError, ValueError) as exc:
            print('删除失败: {}'.format(exc))
            success = False
    print('\n重新查询端口，验证删除结果。')
    try:
        with urllib.request.urlopen(base + '/v1/ports', timeout=TIMEOUT) as response:
            data = json.load(response)
        ports_after = data.get('ports') if isinstance(data, dict) else None
        if not isinstance(ports_after, list) or any(
            not isinstance(port, dict) or 'id' not in port or port['id'] is None
            or isinstance(port['id'], bool) or not isinstance(port['id'], (str, int))
            or not str(port['id']).strip() for port in ports_after
        ):
            raise ValueError('返回的端口列表或端口 ID 无效，无法验证')
        remaining = {str(port['id']) for port in ports_after}
        for port in down:
            port_id = str(port['id'])
            if port_id in remaining:
                print('验证失败：端口 {} 仍在列表中。'.format(port_id))
                success = False
            else:
                print('验证成功：端口 {} 已从列表消失。'.format(port_id))
    except (OSError, ValueError, UnicodeError) as exc:
        print('删除后端口验证失败: {}'.format(exc))
        success = False
    return success


def main():
    service_result = check_services()
    print('\n最后检查 IPMI 管理控制器信息。')
    env = os.environ.copy()
    result = run(['ipmitool', '-I', 'open', '-U', 'operator', '-E', 'mc', 'info'], env=env)
    ipmi_ok = result is not None and result.returncode == 0
    print('ipmitool mc info: {}'.format('正常' if ipmi_ok else '异常'))
    if service_result != 0 or not ipmi_ok:
        print('\n最终结果：存在异常，请检查上面的输出。')
        return 1
    if not check_ports():
        print('\n最终结果：端口检查或删除失败。')
        return 1
    print('\n最终结果：各项检查和端口处理完成。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
