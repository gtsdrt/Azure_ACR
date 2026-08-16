FROM python:3.11-slim

# 安装系统依赖（如果 ansible 或某些包需要编译，但一般纯 Python 包不需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 包
RUN /usr/local/bin/python3.11 -m pip install --no-cache-dir \
    "ansible-core>=2.16" fastapi uvicorn meraki

# 安装 Ansible collections
COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml \
    --collections-path /usr/share/ansible/collections

# 关键：安装 azure.azcollection 所需的全部 Python 依赖
# 直接使用 collection 自带的 requirements.txt
RUN /usr/local/bin/python3.11 -m pip install --no-cache-dir \
    -r /usr/share/ansible/collections/ansible_collections/azure/azcollection/requirements.txt

# 验证关键模块可导入（可选）
RUN /usr/local/bin/python3.11 -c "import azure.mgmt.marketplaceordering; print('azure.mgmt.marketplaceordering OK')"

WORKDIR /app
COPY main.py /app/main.py
COPY playbooks/ /opt/ansible/playbooks/
ENV PLAYBOOK_DIR=/opt/ansible/playbooks

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]