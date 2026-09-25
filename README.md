# Azure_ACR · Ansible 执行器（Serverless 容器）

一套**跑在 Azure Container Apps 上的 Ansible 执行器**：把常用的网络 / 云自动化动作做成「内置任务」，
由一个很小的 FastAPI 程序（`main.py`）按任务名调用 `playbooks/` 里的 playbook 执行。
调用方（例如 macOS 上的 **AIChatApp**）只需要 `GET /tasks` 发现能力、`POST /run-task` 执行任务，
**不需要**自己写 playbook。

```
本仓库（GitHub: gtsdrt/Azure_ACR）
   │  push 到 main（任何提交都会触发构建）
   ▼
GitHub Actions ── docker build / push ──▶ Azure Container Registry
   │                                       gtsdrtaiansible.azurecr.io/ansible-executor:<git-sha>
   │ az containerapp update                        │
   └───────────────────────────────▶ Azure Container Apps: ansible-executor（资源组 ai-api）
                                                    ▲
                                                    │ HTTPS + X-API-Key
                            AIChatApp（gtsdrt/gtsdrt_ai_hub_macos）的「Serverless 容器」工具组
```

## 接口

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| `GET` | `/health` | 无 | 健康检查 → `{"status": "healthy"}` |
| `GET` | `/tasks` | `X-API-Key` | 列出内置任务 → `{"tasks": {"<任务名>": {"required_params": [...]}}}` |
| `POST` | `/run-task` | `X-API-Key` | 执行内置任务：`{"task": "<任务名>", "params": {...}}` |
| `POST` | `/run-ansible` | `X-API-Key` | ⚠️ 执行调用方传入的**任意** playbook YAML（向后兼容保留；**AIChatApp 故意不暴露给 AI**） |

- 鉴权头：`X-API-Key: <API_KEY>`
- 交互式文档：`/docs`（FastAPI 自带）
- `/run-task` 返回 `{returncode, stdout, stderr, data}`；其中 `data` 是 `main.py` 从 Ansible **json callback** 输出里
  抽出来的干净结果（依次找 `result.meraki_response` → `result.response` → `result.json` → `result.config` →
  `result.commands` → `result.updates` → `msg`，取最后一个命中的）。

## 内置任务（`main.py` 的 `TASK_REGISTRY`）

| 任务名 | 必填参数 | playbook | 用途 | 只读 |
|---|---|---|---|---|
| `meraki_get_switch_ports` | `serial` | `meraki_get_switch_ports.yml` | 查 Meraki 交换机端口配置 | ✅ |
| `meraki_get_switch_port_statuses` | `serial` | `meraki_get_switch_port_statuses.yml` | 查交换机端口实时状态 | ✅ |
| `meraki_get_firewall_rules` | `network_id` ⚠️ | `meraki_get_firewall_rules.yml` | 查 MX 的 L3 防火墙规则 | ✅ |
| `nxos_config_add_vrf` | `device_host`, `vrf_name` | `nxos_add_vrf.yml` | 在 NX-OS 设备上创建 VRF | ❌ 写 |
| `azure_create_vnet` | `resource_group_name`, `vnet_name`, `location` | `azure_create_vnet.yml` | 建/更新 VNet（含 1 个委派给 Container Apps 的子网） | ❌ 写 |
| `azure_create_subnets` | `resource_group_name`, `vnet_name`, `subnet_names`, `subnet_prefixes` | `azure_create_subnets.yml` | 在已有 VNet 里批量建子网 | ❌ 写 |
| `azure_create_nsg` | `resource_group_name`, `nsg_names` | `azure_create_nsg.yml` | 批量建 NSG | ❌ 写 |
| `azure_associate_subnet_nsg` | `resource_group_name`, `vnet_name`, `nsg_name`, `subnet_names` | `azure_associate_subnet_nsg.yml` | 把子网关联到 NSG | ❌ 写 |
| `azure_delete_resource-group` | `resource_group_name` | `azure_delete_resource-group.yml` | ⚠️ 删除整个资源组（`force_delete_nonempty: true`） | ❌ 写 |

### 使用注意

- 任务名 `azure_delete_resource-group` 用的是**连字符**，不是下划线，容易写错。
- `subnet_names` / `subnet_prefixes` / `nsg_names` 传的是**逗号分隔字符串**（如 `"subnet1,subnet2"`），不是 JSON 数组。
- ⚠️ **已知不一致**：`meraki_get_firewall_rules` 的 playbook 用的是 `networkId: "{{ network_id }}"`，
  但 `TASK_REGISTRY` 里登记的必填参数是 `serial` → 目前按注册表传 `serial` 调用会失败（缺 `network_id`）。
  修法二选一：把注册表改成 `["network_id"]`，或把 playbook 改成用 `serial`（见 `devices_switch_ports_info` 的写法）。
- `TASK_REGISTRY` 里 `meraki_get_firewall_rules` 重复登记了一行（dict 字面量会去重，无害，可清理）。
- `playbooks/junos_get_interface_config.yml` 已在镜像里，但**没有注册**到 `TASK_REGISTRY`
  （目前只能通过 `/run-ansible` 使用；它需要 `JUNOS_USERNAME` / `JUNOS_PASSWORD`）。
- `azure_create_vnet.yml` 内部变量名（`vnet_name_override`、`azure_location`）与对外参数名（`vnet_name`、`location`）
  不一致，靠 extra-vars 覆盖生效 —— 改这个 playbook 时注意别踩坑。

## 环境变量

| 变量 | 用于 | 说明 |
|---|---|---|
| `API_KEY` | 全部接口 | `X-API-Key` 的校验值。代码默认 `change-me`，**生产必须改**（建议放 Container App secret） |
| `MERAKI_DASHBOARD_API_KEY` | Meraki 任务 | playbook 里用 `lookup('env', 'MERAKI_DASHBOARD_API_KEY')` 读取 |
| `AZURE_SUBSCRIPTION_ID` | Azure 任务 | Azure 相关 playbook 第一步就校验它非空，缺了直接 fail |
| `NXOS_USERNAME` / `NXOS_PASSWORD` | `nxos_config_add_vrf` | NX-OS 设备登录凭据 |
| `PLAYBOOK_DIR` | 可选 | 镜像内默认 `/opt/ansible/playbooks` |

Azure 侧认证走 **托管身份**（playbook 里 `auth_source: msi`）→ 这个 Container App 的托管身份
需要在目标订阅 / 资源组上具备相应权限。

## 本地构建与运行

```bash
docker build -t ansible-executor .
docker run --rm -p 8000:8000 \
  -e API_KEY=dev-key \
  -e MERAKI_DASHBOARD_API_KEY=xxx \
  ansible-executor

curl -s -H "X-API-Key: dev-key" http://localhost:8000/tasks

curl -s -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
  -d '{"task":"meraki_get_switch_ports","params":{"serial":"Q2XX-XXXX-XXXX"}}' \
  http://localhost:8000/run-task
```

## 怎么加一个新任务

1. 新建 `playbooks/<name>.yml`；最后一个任务用 `ansible.builtin.debug` 把结果打出来
   （`main.py` 的 `_extract_data` 靠这个抽 `data`，否则只能看原始 stdout）；
2. 在 `main.py` 的 `TASK_REGISTRY` 加一行：`"<任务名>": ("<name>.yml", ["param1", "param2"])`；
3. 提交并推 `main` → GitHub Actions 自动重建镜像并更新 Container App，**不用手动操作**；
4. 想让 AI 能调用它：只读任务可以加进 AIChatApp 侧该容器的 `allowed_tasks`，或让容器在 `/tasks` 里回 `read_only: true`；
   写操作默认被 AIChatApp 拦下（这是有意的安全设计）。

## 部署

- **自动**：`.github/workflows/ansible-executor-AutoDeployTrigger-*.yml`，触发条件是
  `on: push: branches: [main]` + `paths: ['**']` → **任何**提交（哪怕只改 README）都会构建镜像并
  `az containerapp update` 滚动部署。
- **手动**：`gh workflow run <workflow 文件名>`；或
  `az containerapp update -n ansible-executor -g ai-api --image gtsdrtaiansible.azurecr.io/ansible-executor:<tag>`。
- 容器处于 **Stopped** 时接口会返回 Container App 自己的 HTML 404 → 先启动：
  `az containerapp start -n ansible-executor -g ai-api`。
- 镜像基础：`python:3.11-slim` + `ansible-core` + `fastapi` / `uvicorn` / `meraki`；
  Ansible collections 见 `requirements.yml`（`cisco.meraki`、`cisco.nxos`、`cisco.ios`、`cisco.nd`、
  `junipernetworks.junos`、`paloaltonetworks.panos`、`azure.azcollection==3.21.0` 等）。

## 安全提示

- `/run-ansible` 可以执行**任意** playbook（等于把容器里的自动化权限交出去），只给可信调用方使用；
  AIChatApp 有意不把这个接口暴露给 AI，只给 `/run-task` + 任务白名单。
- `API_KEY` 不要保持默认的 `change-me`；建议放进 Container App 的 secret，用 `secretref` 注入。
- Azure 侧一律用托管身份（`msi`），不在容器里存放长期密钥。

## 谁在调用它

macOS 客户端 **AIChatApp**（<https://github.com/gtsdrt/gtsdrt_ai_hub_macos>）的「Serverless 容器」工具组共 3 个工具：
`container_list_endpoints` / `container_list_tasks` / `container_run_task`，底层就是本容器的
`GET /tasks` 与 `POST /run-task`；容器地址与 API Key 配在
`~/Library/Application Support/AIChatApp/containers.json`（每次调用重新读取，改完立即生效）。
默认白名单**只放行只读任务**（3 个 Meraki 查询），写操作要在注册表里显式 `allowed_tasks` 放开。
