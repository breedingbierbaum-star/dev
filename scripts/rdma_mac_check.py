"""检查 eth1 及以上网卡的 MAC 是否与 cloud-init 元数据一致。"""

import os
import re
import json

FILE = '/run/cloud-init/instance-data.json'


def collect_links(obj, links):
    if isinstance(obj, dict):
        items = obj.get('links')
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get('id'):
                    links[item['id']] = item
        for value in obj.values():
            collect_links(value, links)
    elif isinstance(obj, list):
        for value in obj:
            collect_links(value, links)


def main():
    with open(FILE, 'r') as f:
        data = json.load(f)

    # 收集 cloud-init 中的网络元数据
    links = {}
    collect_links(data, links)

    # 自动发现 eth1、eth2……，排除管理网卡 eth0
    interfaces = []
    for name in os.listdir('/sys/class/net'):
        match = re.fullmatch(r'eth([1-9][0-9]*)', name)
        if match:
            interfaces.append((int(match.group(1)), name))

    interfaces.sort()

    print('=' * 58)
    print('          RDMA 网卡 MAC 地址一致性检测')
    print('=' * 58)

    if not interfaces:
        print('检测未通过：未发现 eth1 及以上编号的网卡')
        raise SystemExit(1)

    errors = []

    for number, eth in interfaces:
        network_id = f'network_{number}'
        mac_path = f'/sys/class/net/{eth}/address'

        with open(mac_path) as f:
            actual_mac = f.read().strip().lower()

        link = links.get(network_id)

        print(f'\n[网卡 {eth}]  <-->  [对应网络 {network_id}]')

        if not link:
            print('  检测结果：检测未通过')
            print(f'  原因：cloud-init 中找不到 {network_id}')
            errors.append(f'{eth} 缺少对应网络元数据 {network_id}')
            continue

        expected_mac = (
            link.get('ethernet_mac_address') or ''
        ).strip().lower()

        try:
            extra = json.loads(link.get('extra_data') or '{}')
            if not isinstance(extra, dict):
                extra = {}
        except (ValueError, TypeError):
            extra = {}

        rdma_type = extra.get('RdmaDataType', '未设置')

        print(f'  实际 MAC：  {actual_mac}')
        print(f'  元数据 MAC：{expected_mac or "缺失"}')
        print(f'  RDMA 类型： {rdma_type}')

        if not expected_mac:
            print('  检测结果：检测未通过')
            print('  原因：元数据中缺少 MAC 地址')
            errors.append(f'{eth} 的 {network_id} 缺少 MAC')
        elif actual_mac != expected_mac:
            print('  检测结果：检测未通过')
            print('  原因：实际 MAC 与元数据 MAC 不一致')
            errors.append(f'{eth} 与 {network_id} 的 MAC 不一致')
        else:
            print('  检测结果：检测通过，MAC 匹配')

    print('\n' + '=' * 58)

    if errors:
        print(f'最终结果：检测未通过（发现 {len(errors)} 项问题）')
        print('\n问题清单：')
        for i, error in enumerate(errors, 1):
            print(f'  {i}. {error}')
        raise SystemExit(1)
    else:
        print(f'最终结果：检测通过！共 {len(interfaces)} 张网卡全部匹配。')

    print('=' * 58)


if __name__ == '__main__':
    main()
