# Python 脚本项目

用于编写和运行日常 Python 脚本，要求 Python 3.12 或更新版本。初始项目只使用标准库，无需安装第三方依赖。

## 初始化

在项目根目录运行：

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell 激活命令：`.venv\Scripts\Activate.ps1`。

## 运行脚本

```bash
python scripts/hello.py
python scripts/hello.py --name 小明
```

新脚本放在 `scripts/` 目录下，推荐使用 `main()` 函数和 `if __name__ == "__main__":` 入口，避免导入脚本时执行任务。

## 运行测试

```bash
python -m unittest discover -s tests -v
```

## 第三方依赖

需要第三方包时，先将依赖及版本约束写入 `requirements.txt`，再运行：

```bash
python -m pip install -r requirements.txt
```

不要提交虚拟环境、密钥或 `.env` 文件；如需记录环境变量格式，可创建不含真实密钥的 `.env.example`。

## RDMA 网卡 MAC 地址一致性检测

在具有 cloud-init 网络元数据的 Linux 目标主机上运行：

```bash
python3 scripts/rdma_mac_check.py
```

脚本读取 `/run/cloud-init/instance-data.json` 和 `/sys/class/net/`，自动检查
`eth1`、`eth2` 等网卡与 `network_1`、`network_2` 等元数据的 MAC 是否一致，
排除管理网卡 `eth0`，并显示 `RdmaDataType`。需有读取这些文件的权限。
全部匹配时退出码为 `0`；无目标网卡、缺少对应网络或 MAC、MAC 不一致时退出码为 `1`。
该脚本校验 MAC 一致性，不验证 RDMA 通信功能。

## libvirt、IPMI 和端口检查

脚本：[scripts/check_libvirt.py](scripts/check_libvirt.py)。仅使用 Python 标准库，
无需安装 libvirt-python。适用于使用 libvirtd 的 Linux/systemd 主机，
依赖 virsh、/usr/bin/bes-power、ipmitool 及本机端口管理接口。

### 运行方式

```bash
# 只读检查：不重启服务、不删除端口
python3 scripts/check_libvirt.py --check-only
# 保持原有行为：允许自动重启异常服务、删除 down 端口
sudo --preserve-env=IPMI_PASSWORD python3 scripts/check_libvirt.py
# 显示完整执行命令与输出
python3 scripts/check_libvirt.py --check-only --verbose
python3 scripts/check_libvirt.py --help
```

需要访问 qemu:///system 和本地 IPMI 设备，重启服务需要 root 权限。
IPMI 固定使用 `ipmitool -I open -U operator -E mc info`，
继承 IPMI_PASSWORD 环境变量；仓库不保存密码。
如需输入密码，可使用 Bash：

```bash
read -r -s -p 'IPMI password: ' IPMI_PASSWORD
printf '\n'
export IPMI_PASSWORD
sudo --preserve-env=IPMI_PASSWORD python3 scripts/check_libvirt.py
unset IPMI_PASSWORD
```

sudo 保留环境变量需符合主机策略；本地 open 接口是否使用密码取决于驱动。

### 行为与输出

检查 virsh 连接、bes-power 电源状态、libvirtd、IPMI 和端口。
bes-power 返回 on 或 off 均表示查询成功，输出会明确标注当前电源状态。
服务未加载或状态信息不完整时不重启；已加载但不是 active/running 时，
默认尝试重启，再有限次数复查服务和 virsh。
已正常运行但 virsh 失败时报告异常，不自动重启。

前四项未全部通过或修复时，端口处理显示“跳过”及原因。
端口接口为 `GET /v1/ports`，预期返回
`{"ports":[{"id":"port-id","status":"down"}]}`，允许空数组。
全部记录均校验 ID 与状态，支持 up/down；缺失、未知状态或重复 ID 会报错，
不会将其当作正常端口。实际接口如支持其他状态，需要先扩展校验规则。

只读模式发现 down 端口时显示“待处理”，不删除。
默认模式对 down 端口调用 `DELETE /v1/vports/{id}`，
继续处理其余端口并重试查询，确认原 ID 已消失。
单个删除请求失败仍会报告失败，即使复查时端口已消失。

**直接运行仍包含自动修复操作，可能重启服务、删除 down vport；
需要纯检查时请显式传入 --check-only。修复操作不会自动回滚。**

默认精简输出，使用“通过、已修复、失败、待处理、跳过”标注结果，
最终汇总各类数量、异常原因、建议操作、总耗时和退出码。
重试时显示等待进度；完整命令及输出由 --verbose 开启，失败时始终显示错误原因。
virsh 的最终结果在服务复查后输出，开始后的检查阶段可能需要等待命令超时。

### 可选参数与退出码

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| --check-only | 关闭 | 禁止服务重启与端口删除 |
| --verbose | 关闭 | 显示命令及完整输出 |
| --service | libvirtd | systemd 服务名；不改变 virsh 连接地址 |
| --api-url | http://127.0.0.1:2929 | 端口管理接口基础地址 |
| --timeout | 30 | 单次命令或 HTTP 请求超时秒数；重启命令固定 90 秒 |
| --attempts | 5 | 每项修复后验证的最大尝试次数，范围 1–10 |
| --interval | 2 | 验证重试间隔秒数 |

验证总时长包含每次操作的超时，并非固定总超时；单次 DELETE 不自动重试。
退出码 0 表示全部通过或已修复；1 表示有失败、待处理或跳过项；
2 表示参数错误；130 表示用户中断。

## ISC DHCP IPv4 租约清理

脚本：[scripts/clean_dhcp_leases.py](scripts/clean_dhcp_leases.py)。按 IP 列表删除
ISC DHCP 租约文件中的全部匹配记录，包括同一 IP 的多条历史记录。
仅使用 Python 标准库；预览需要读取文件，实际执行需要 Linux、systemd 和 root 权限。

### 准备 IP 列表与预览

创建 `ips.txt`，每行一个 IPv4 地址，支持空行、重复地址和 `#` 注释：

```text
# 需要清理的地址
192.0.2.10
192.0.2.11 # 第二个地址
```

在项目根目录运行，默认只显示每个 IP 匹配的记录数，不修改文件或操作服务：

```bash
python3 scripts/clean_dhcp_leases.py ips.txt
python3 scripts/clean_dhcp_leases.py --help
```

默认租约文件为 `/var/lib/dhcp/dhcpd.leases`，默认服务为 `isc-dhcp-server`。
可通过 `--leases` 和 `--service` 指定实际路径与服务名：

```bash
python3 scripts/clean_dhcp_leases.py ips.txt --leases /var/lib/dhcpd/dhcpd.leases --service dhcpd
```

### 实际清理

确认预览和目标主机后，添加 `--apply`：

```bash
sudo python3 scripts/clean_dhcp_leases.py ips.txt --apply
# 自定义路径和服务时，预览与执行应使用相同参数
sudo python3 scripts/clean_dhcp_leases.py ips.txt --leases /var/lib/dhcpd/dhcpd.leases --service dhcpd --apply
```

执行时先确认服务为 active，再停止服务并确认 inactive，重新读取租约文件。
存在匹配记录时，在同目录创建 `dhcpd.leases.bak.<时间戳>` 形式的备份，
通过同目录临时文件和原子替换写入清理结果，保留文件权限位、属主和属组，
随后检查目标 IP 记录是否消失。脚本识别租约内的嵌套块，遇到目标租约块不完整时拒绝修改。
IP 列表无效、为空，或租约文件不存在、不是普通文件、是符号链接时会报错。

**实际清理会短暂停止 DHCP 服务，影响期间的地址分配和续租。
即使没有匹配记录，`--apply` 也会停止并重新启动服务，但不会写文件或创建备份。**
进入停止步骤后，即使清理报错，脚本也会尝试启动服务并确认 active；
恢复失败时需立即人工检查。脚本没有自动回滚文件的功能，
需要恢复时应先停止对应服务，再使用输出的备份路径恢复文件并启动、检查服务。
替换文件不保证保留 ACL、扩展属性或 SELinux 标签，使用这些机制的主机需另行核对。

清理服务端记录不会主动释放客户端持有的地址，也不会删除 DHCP 配置中的静态保留项；
客户端继续使用原地址时仍需协调，避免地址冲突。
此脚本针对 ISC DHCP IPv4 文本租约文件，不适用于 Kea、dnsmasq 或 IPv6 租约。

### 结果判断

正常预览或清理完成返回 `0`；文件、权限、租约解析或服务操作失败返回 `1`；
命令行参数错误由 argparse 返回 `2`。请结合输出的匹配数量、备份路径和服务恢复状态判断结果。
