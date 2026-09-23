"""Durable model configuration. Secrets never appear in public records."""
import copy
import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit


class ConflictError(Exception):
    pass


def normalize_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError("请输入有效的服务 URL")
    value = value.strip().rstrip("/")
    if any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("URL 不能包含空格或控制字符")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ValueError("URL 或端口格式无效") from None
    if (url.scheme not in ("http", "https") or not url.hostname or url.username
            or url.password or url.query or url.fragment or (port is not None and port < 1)):
        raise ValueError("请输入 HTTP(S) 地址，不要在 URL 中填写 Key 或查询参数")
    return value if url.path.endswith("/v1") else value + "/v1"


def editable(project):
    return (project.get("deployment") == "standard" and len(project["nodes"]) == 1
            and project["nodes"][0].get("api_base_url")
            and project["nodes"][0].get("metrics_url") == project["nodes"][0]["api_base_url"][:-3] + "/metrics"
            and project["nodes"][0].get("_api_key", "") == project["nodes"][0].get("_metrics_key", ""))


def public_model(project, version):
    node = project["nodes"][0]
    return {"id": project["id"], "name": project.get("name") or project["id"],
            "alias": project.get("alias", ""), "url": node.get("api_base_url", ""), "has_api_key": bool(node.get("_api_key")),
            "version": version, "editable": bool(editable(project))}


class ModelStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create with restrictive permissions before sqlite ever writes a secret.
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with closing(self.connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, config TEXT NOT NULL, version INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL)")

    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute("PRAGMA secure_delete=ON")
        return conn

    def initialize(self, initial):
        """Import environment/legacy file exactly once, including an empty setup."""
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone():
                return
            projects = copy.deepcopy(initial())
            for project in projects:
                for node in project["nodes"]:
                    for source, dest in (("api_key_env", "_api_key"), ("metrics_key_env", "_metrics_key")):
                        env_name = node.pop(source, None)
                        if env_name:
                            if not os.getenv(env_name):
                                raise ValueError(f"导入配置失败：未设置 {env_name}")
                            node[dest] = os.environ[env_name]
                conn.execute("INSERT INTO models VALUES (?, ?, 1)", (project["id"], json.dumps(project)))
            conn.execute("INSERT INTO meta VALUES ('revision', 1)")

    def snapshot(self):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN")
            revision = conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]
            rows = conn.execute("SELECT config,version FROM models ORDER BY rowid").fetchall()
            return revision, [(json.loads(config), version) for config, version in rows]

    def list_public(self):
        revision, rows = self.snapshot()
        return {"revision": revision, "models": [public_model(p, v) for p, v in rows]}

    def save(self, data, model_id=None):
        if not isinstance(data, dict):
            raise ValueError("配置必须是 JSON 对象")
        name = data.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("模型名称需为 1–100 个字符")
        alias = data.get("alias", "")
        if not isinstance(alias, str) or len(alias.strip()) > 100 or any(ord(c) < 32 for c in alias):
            raise ValueError("模型别名最多 100 个字符，不能包含控制字符")
        url = normalize_url(data.get("url"))
        secret = data.get("api_key", "")
        if not isinstance(secret, str) or len(secret) > 4096 or any(ord(c) < 32 or ord(c) > 126 for c in secret):
            raise ValueError("API Key 格式无效")
        clear_key = data.get("clear_key", False)
        if not isinstance(clear_key, bool) or (clear_key and secret):
            raise ValueError("清除 Key 时不能同时填写新 Key")
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            old = None
            if model_id:
                row = conn.execute("SELECT config,version FROM models WHERE id=?", (model_id,)).fetchone()
                if row is None:
                    raise KeyError(model_id)
                old, version = json.loads(row[0]), row[1]
                if not editable(old):
                    raise ValueError("旧版复杂节点配置暂不支持在简化表单中编辑")
                if data.get("version") != version:
                    raise ConflictError("配置已被其他页面修改，请重新打开编辑")
                if not secret and not clear_key:
                    secret = old["nodes"][0].get("_api_key", "")
            else:
                model_id, version = "model-" + uuid.uuid4().hex[:12], 0
            node_id = old["nodes"][0]["id"] if old and old["nodes"][0]["api_base_url"] == url else "endpoint-" + uuid.uuid4().hex[:12]
            project = {"id": model_id, "name": name.strip(), "alias": alias.strip() if "alias" in data else (old or {}).get("alias", ""), "deployment": "standard", "nodes": [
                {"id": node_id, "role": "engine", "name": "服务入口", "api_base_url": url,
                 "metrics_url": url[:-3] + "/metrics", "_api_key": secret, "_metrics_key": secret}
            ]}
            conn.execute("INSERT OR REPLACE INTO models VALUES (?, ?, ?)", (model_id, json.dumps(project), version + 1))
            conn.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
            return public_model(project, version + 1)

    def delete(self, model_id, version):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT version FROM models WHERE id=?", (model_id,)).fetchone()
            if row is None:
                raise KeyError(model_id)
            if version != row[0]:
                raise ConflictError("配置已更新，请刷新后再删除")
            conn.execute("DELETE FROM models WHERE id=?", (model_id,))
            conn.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
