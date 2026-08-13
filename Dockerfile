# 参考模板：请与你现有的 Dockerfile 合并
# 相对你现有镜像，关键差异只有两处：COPY playbooks/ 和 ENV PLAYBOOK_DIR
FROM python:3.11-slim

RUN pip install --no-cache-dir "ansible-core>=2.16" fastapi uvicorn meraki
COPY requirements.yml /tmp/requirements.yml
RUN ansible-galaxy collection install -r /tmp/requirements.yml

WORKDIR /app
COPY main.py /app/main.py
COPY playbooks/ /opt/ansible/playbooks/
ENV PLAYBOOK_DIR=/opt/ansible/playbooks

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
