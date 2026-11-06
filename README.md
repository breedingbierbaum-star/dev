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

脚本：[scripts/check_libvirt.py](scripts/check_libvirt.py)。仅使用 Python 标准库，无需安装 `libvirt-python`。

### 运行条件

在目标 Linux 主机运行，依赖 systemd、`virsh`、`/usr/bin/bes-power`、`ipmitool`，
并要求本机 `http://127.0.0.1:2929` 提供端口管理接口。
需要访问 `qemu:///system` 和本地 IPMI 设备；自动重启 libvirtd 需要 root 权限。
本脚本针对使用 `libvirtd` 服务的主机，不适用于仅运行模块化守护进程的部署。

### 使用方式

```bash
# 交互输入密码，避免将密码明文写入命令历史；使用 Bash 执行。
read -r -s -p 'IPMI password: ' IPMI_PASSWORD
printf '\n'
export IPMI_PASSWORD
sudo --preserve-env=IPMI_PASSWORD python3 scripts/check_libvirt.py
unset IPMI_PASSWORD
```

IPMI 用户固定为 `operator`，使用 `ipmitool -I open -U operator -E mc info`。
脚本继承环境变量 `IPMI_PASSWORD`，仓库不保存密码；本地 open 接口是否使用密码由驱动决定。
`sudo --preserve-env` 需要符合主机的 sudo 策略。

### 执行流程与影响

1. 执行 `virsh -c qemu:///system list`，检查 `/usr/bin/bes-power status` 是否成功且输出 `on` 或 `off`。
2. 展示 libvirtd 状态，并查询 `LoadState`、`ActiveState` 和 `SubState`。
   服务已加载但不是 `active (running)` 时，自动尝试重启并复查服务和 virsh。
   状态查询失败、信息不完整或服务未加载时返回失败，不尝试重启。
   服务已正常运行但 virsh 失败时，仅报告异常。
3. 检查 IPMI 管理控制器信息；即使服务检查失败，也会执行这一步。
4. 仅当服务和 IPMI 检查均通过时，调用 `GET /v1/ports`，
   对 `status` 严格等于 `down` 的端口逐个调用 `DELETE /v1/vports/{id}`，
   再次查询并确认原端口 ID 已消失。删除失败会继续处理其他端口，最终返回失败。

**此脚本包含自动修复操作，没有只读或预演模式。运行可能重启 libvirtd，
并删除 down 状态的 vport；请在允许这些操作的目标主机上执行。**
端口 API 预期返回 `{"ports": [{"id": "port-id", "status": "down"}]}`；
空列表视为无待处理端口。请求默认超时 30 秒，重启命令超时 90 秒。
脚本不接受命令行参数，也没有 `--help` 模式。

### 结果判断

- 退出码 `0`：服务、IPMI 检查以及必要的端口处理全部成功。
- 退出码 `1`：检测到异常、重启失败、权限不足或端口查询、删除、复查失败。

输出包含执行命令和检查结果，排障时可先查看失败步骤。

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
