import os
import json
import tempfile
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

API_KEY = os.getenv("API_KEY", "change-me")
MERAKI_DASHBOARD_API_KEY = os.getenv("MERAKI_DASHBOARD_API_KEY")

# ===== 内置任务注册表 =====
# 以后加功能 = playbooks/ 目录加一个 yml 文件 + 这里加一行，Function App 零改动
PLAYBOOK_DIR = Path(os.getenv("PLAYBOOK_DIR", "/opt/ansible/playbooks"))

TASK_REGISTRY = {
    # 任务名: (playbook 文件名, 必填参数列表)
    # --- Meraki（只读） ---
    "meraki_get_switch_ports":          ("meraki_get_switch_ports.yml",          ["serial"]),
    "meraki_get_switch_port_statuses":  ("meraki_get_switch_port_statuses.yml",  ["serial"]),
    # --- NX-OS（写操作，nxos_config_ 前缀 = 变更类任务） ---
    "nxos_config_add_vrf":              ("nxos_add_vrf.yml",                     ["device_host", "vrf_name"]),
}


class PlaybookRequest(BaseModel):
    playbook: str
    extra_vars: dict | None = None   # 参数注入


class TaskRequest(BaseModel):
    task: str
    params: dict | None = None


def verify_api_key(x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")


def _execute(playbook_yaml: str, extra_vars: dict | None, timeout: int = 600):
    """公共执行逻辑"""
    if not playbook_yaml.strip():
        raise HTTPException(status_code=400, detail="Playbook is empty")

    with tempfile.TemporaryDirectory() as tmpdir:
        playbook_path = Path(tmpdir) / "playbook.yml"
        playbook_path.write_text(playbook_yaml, encoding="utf-8")

        env = os.environ.copy()
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"
        env["ANSIBLE_STDOUT_CALLBACK"] = "json"      # stdout 整体为 JSON，容器内解析
        if MERAKI_DASHBOARD_API_KEY:
            env["MERAKI_DASHBOARD_API_KEY"] = MERAKI_DASHBOARD_API_KEY

        # 注意：不再强制 --connection local。
        # 本地执行的 playbook（Meraki 等）在 yml 里显式声明 connection: local；
        # 网络设备 playbook（NX-OS 等）在 yml 里声明 network_cli 并靠 ansible_host 重定向。
        cmd = ["ansible-playbook", str(playbook_path), "-i", "localhost,"]
        if extra_vars:
            cmd += ["-e", json.dumps(extra_vars)]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
            return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
        except subprocess.TimeoutExpired:
            return {"returncode": -1, "stdout": "", "stderr": "Ansible execution timed out"}
        except Exception as e:
            return {"returncode": -1, "stdout": "", "stderr": str(e)}


def _extract_data(stdout: str):
    """从 json callback 的 stdout 中取出最后一个任务的 debug 数据，返回干净 JSON"""
    try:
        doc = json.loads(stdout)
        tasks = []
        for play in doc.get("plays", []):
            tasks.extend(play.get("tasks", []))
        for t in reversed(tasks):
            for host_result in t.get("hosts", {}).values():
                for key in ("result.meraki_response", "result.response", "result.json",
                            "result.config", "result.commands", "result.updates", "msg"):
                    if key in host_result:
                        return host_result[key]
        # 没有匹配键则返回最后一个任务的完整结果（如 nxos 配置模块的 changed/commands）
        if tasks:
            last_hosts = tasks[-1].get("hosts", {})
            for host_result in last_hosts.values():
                return {k: v for k, v in host_result.items() if not k.startswith("_ansible")}
        return None
    except Exception:
        return None


@app.post("/run-ansible")
def run_ansible(req: PlaybookRequest, x_api_key: str = Header(None)):
    """原有接口：执行调用方传入的完整 playbook YAML（保持不变，向后兼容）"""
    verify_api_key(x_api_key)
    return _execute(req.playbook, req.extra_vars)


@app.post("/run-task")
def run_task(req: TaskRequest, x_api_key: str = Header(None)):
    """按任务名执行容器内置 playbook"""
    verify_api_key(x_api_key)

    entry = TASK_REGISTRY.get(req.task)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown task: {req.task}")

    filename, required = entry
    params = req.params or {}
    missing = [p for p in required if not params.get(p)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required params: {missing}")

    playbook_path = PLAYBOOK_DIR / filename
    if not playbook_path.exists():
        raise HTTPException(status_code=500, detail=f"Playbook not found in image: {filename}")

    result = _execute(playbook_path.read_text(encoding="utf-8"), params)
    result["data"] = _extract_data(result.get("stdout", ""))
    return result


@app.get("/tasks")
def list_tasks(x_api_key: str = Header(None)):
    """列出容器支持的所有内置任务（供 Function App / 调用方发现能力）"""
    verify_api_key(x_api_key)
    return {"tasks": {name: {"required_params": req} for name, (f, req) in TASK_REGISTRY.items()}}


@app.get("/health")
def health():
    return {"status": "healthy"}
