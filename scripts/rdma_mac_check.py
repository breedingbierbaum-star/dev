#!/usr/bin/env python3
"""检查 eth1 及以上网卡的 MAC 是否与 cloud-init 元数据一致。"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

FILE = '/run/cloud-init/instance-data.json'
NET_DIR = '/sys/class/net'


def colorize(text, code, stream=None):
    stream = sys.stdout if stream is None else stream
    if stream.isatty() and os.environ.get('TERM') != 'dumb' and 'NO_COLOR' not in os.environ:
        return f'\033[{code}m{text}\033[0m'
    return text


def display_width(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
               for char in text)


def cell(value, width):
    text = ' '.join(str(value).split())
    if display_width(text) > width:
        result = ''
        for char in text:
            if display_width(result + char) > width - 3:
                break
            result += char
        text = result + '...'
    return text + ' ' * max(0, width - display_width(text))


class ArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message, file=None):
        if message:
            stream = sys.stderr if file is None else file
            super()._print_message(colorize(message, '36', stream), stream)


def collect_links(obj, links):
    if isinstance(obj, dict):
        items = obj.get('links')
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and isinstance(item.get('id'), str) and item['id']:
                    links[item['id']] = item
        for value in obj.values():
            collect_links(value, links)
    elif isinstance(obj, list):
        for value in obj:
            collect_links(value, links)


def inspect_interface(number, eth, links):
    network_id = f'network_{number}'
    result = dict(name=eth, network=network_id, actual='未读取', expected='缺失',
                  rdma_type='未设置', reason='', advice='')
    link = links.get(network_id)
    if link:
        expected = link.get('ethernet_mac_address')
        result['expected'] = expected.strip().lower() if isinstance(expected, str) and expected.strip() else '缺失'
        try:
            extra = json.loads(link.get('extra_data') or '{}')
        except (ValueError, TypeError):
            extra = {}
        if isinstance(extra, dict):
            result['rdma_type'] = str(extra.get('RdmaDataType', '未设置'))
    try:
        with open(f'{NET_DIR}/{eth}/address', encoding='utf-8') as f:
            result['actual'] = f.read().strip().lower()
    except (OSError, UnicodeError) as exc:
        result.update(reason=f'无法读取网卡 MAC：{exc}', advice='检查网卡是否存在及 address 文件读取权限')
        return result
    if not link:
        result.update(reason=f'cloud-init 中找不到 {network_id}', advice='检查 cloud-init 网络元数据及网卡与 network 编号的对应关系')
    elif result['expected'] == '缺失':
        result.update(reason='元数据中缺少有效 MAC 地址', advice=f'检查 {network_id} 的 ethernet_mac_address 字段')
    elif result['actual'] != result['expected']:
        result.update(reason='实际 MAC 与元数据 MAC 不一致', advice='核对网卡配置与 cloud-init 元数据，确认正确地址后处理')
    return result


def print_summary(results, issues, started):
    failed = sum(bool(result['reason']) for result in results)
    passed = len(results) - failed
    code = int(bool(failed or issues))
    print('  ' + '-' * 76)
    print('\n结果：' + colorize('需要处理' if code else '全部通过', '33' if code else '32'))
    counts = []
    if passed:
        counts.append(f'通过 {passed}')
    if failed:
        counts.append(f'失败 {failed}')
    if issues:
        counts.append(f'检查异常 {len(issues)}')
    print('  ' + ' / '.join(counts))
    failures = [result for result in results if result['reason']]
    if failures or issues:
        print('\n' + colorize('处理详情', '1'))
    for title, detail, advice in issues:
        print(f'\n  {title} · ' + colorize('失败', '31'))
        for line in detail.splitlines():
            print(f'    {line}')
        print(f'    建议：{advice}')
    for result in failures:
        print(f"\n  {result['name']} / {result['network']} · " + colorize('失败', '31'))
        print(f"    原因：{result['reason']}")
        print(f"    实际 MAC：  {result['actual']}")
        print(f"    元数据 MAC：{result['expected']}")
        print(f"    RDMA 类型： {result['rdma_type']}")
        print(f"    建议：{result['advice']}")
    print(f'\n耗时 {time.monotonic() - started:.1f} 秒 · 退出码 {code}\n', flush=True)
    return code


def main(argv=None):
    parser = ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''使用示例：
  python3 scripts/rdma_mac_check.py
  python3 scripts/rdma_mac_check.py > rdma_mac_check.log 2>&1

只读检查，自动发现 eth1、eth2 等网卡，排除管理网卡 eth0。
仅校验 MAC 一致性，不验证 RDMA 通信功能。
颜色仅在交互终端启用；日志保持纯文本，设置 NO_COLOR 可禁用颜色。''')
    parser.parse_args(argv)
    started = time.monotonic()
    print('\n' + colorize('RDMA 网卡 MAC 一致性检查', '1'))
    print('模式：只读检查 · 排除 eth0')
    print('数据源：' + FILE)
    results, issues = [], []
    try:
        with open(FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeError) as exc:
        issues.append(('元数据读取', str(exc), '检查 cloud-init 元数据文件是否存在、JSON 格式及读取权限'))
        return print_summary(results, issues, started)
    links = {}
    collect_links(data, links)
    try:
        interfaces = []
        for name in os.listdir(NET_DIR):
            match = re.fullmatch(r'eth([1-9][0-9]*)', name)
            if match:
                interfaces.append((int(match.group(1)), name))
        interfaces.sort()
    except OSError as exc:
        issues.append(('网卡发现', str(exc), '检查 /sys/class/net 是否可访问'))
        return print_summary(results, issues, started)
    if not interfaces:
        issues.append(('网卡发现', '未发现 eth1 及以上编号的网卡', '检查网卡是否存在及命名；本脚本仅匹配 eth1、eth2 等名称'))
    else:
        print('\n  ' + '  '.join((cell('#', 3), cell('网卡', 8), cell('对应网络', 14),
                                  cell('状态', 4), cell('实际 MAC', 17), 'RDMA 类型')))
        print('  ' + '-' * 76, flush=True)
        for index, (number, eth) in enumerate(interfaces, 1):
            result = inspect_interface(number, eth, links)
            results.append(result)
            status = colorize('失败' if result['reason'] else '通过', '31' if result['reason'] else '32')
            print('  ' + '  '.join((cell(index, 3), cell(eth, 8), cell(result['network'], 14),
                                      status, cell(result['actual'], 17), cell(result['rdma_type'], 18).rstrip())), flush=True)
    return print_summary(results, issues, started)


if __name__ == '__main__':
    sys.exit(main())
