"""Multi-project vLLM monitor; standard library only."""
import json
import math
import os
import re
import shlex
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def load_dotenv(path):
    """Load simple NAME=value entries; existing process variables take priority."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f".env 第 {number} 行格式无效")
        parts = shlex.split(value, comments=True)
        if len(parts) > 1:
            raise ValueError(f".env 第 {number} 行：包含空格的值请加引号")
        os.environ.setdefault(name, parts[0] if parts else "")


load_dotenv(Path(__file__).resolve().parent.parent / ".env")
PROMETHEUS = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
REFRESH_SECONDS = max(5, int(os.getenv("REFRESH_SECONDS", "30")))
CONFIG_PATH = Path(os.getenv("SERVICES_CONFIG", str(Path(__file__).resolve().parent.parent / "services.json")))
INDEX = Path(__file__).with_name("index.html").read_bytes()
GROUP = "monitor_project,monitor_node"
SELECTOR = '{job="vllm"}'


def queries():
    def metric(name):
        return "vllm:" + name + SELECTOR

    def total(name):
        return f"sum by ({GROUP}) ({metric(name)})"

    def rate(name):
        return f"sum by ({GROUP}) (rate({metric(name)}[15m]))"

    def p95(name):
        return f"histogram_quantile(0.95,sum by (le,{GROUP}) (rate({metric(name + '_bucket')}[15m])))"

    kv = metric("kv_cache_usage_perc")
    return {
        "up": f"min by ({GROUP}) (up{SELECTOR})",
        "running": total("num_requests_running"),
        "waiting": total("num_requests_waiting"),
        "kv": f"max by ({GROUP}) ({kv}) * 100",
        "imbalance": f"(max by ({GROUP}) ({kv}) - min by ({GROUP}) ({kv})) * 100",
        "ttft": p95("time_to_first_token_seconds"),
        "e2e": p95("e2e_request_latency_seconds"),
        "tpot": p95("inter_token_latency_seconds"),
        "prefill": p95("request_prefill_time_seconds"),
        "prompt": p95("request_prompt_tokens"),
        "cache": f'100 * {rate("prefix_cache_hits_total")} / {rate("prefix_cache_queries_total")}',
        "preempt": f'sum by ({GROUP}) (increase({metric("num_preemptions_total")}[1h]))',
        "input_tps": rate("prompt_tokens_total"),
        "output_tps": rate("generation_tokens_total"),
    }


QUERIES = queries()


def models_from_env():
    """Three fields per endpoint; keep the existing internal metric labels."""
    indices = sorted({int(match.group(1)) for name in os.environ
                      if (match := re.fullmatch(r"MODEL_([1-9][0-9]*)_URL", name))
                      and os.environ[name].strip()})
    projects = []
    for index in indices:
        prefix = f"MODEL_{index}"
        url = os.environ[f"{prefix}_URL"].strip().rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError(f"{prefix}_URL 必须是无凭证、无查询参数的 HTTP(S) 地址")
        # Preserve reverse-proxy prefixes: /model-a/v1 -> /model-a/metrics.
        base = url[:-3] if parsed.path.endswith("/v1") else url
        node = {"id": "endpoint", "name": "服务入口", "role": "engine",
                "api_base_url": base + "/v1", "metrics_url": base + "/metrics"}
        if os.getenv(f"{prefix}_API_KEY"):
            node.update(api_key_env=f"{prefix}_API_KEY", metrics_key_env=f"{prefix}_API_KEY")
        projects.append({"id": f"model-{index}", "name": os.getenv(f"{prefix}_NAME") or f"模型 {index}",
                         "deployment": "standard", "nodes": [node]})
    return projects


def load_config(path=CONFIG_PATH):
    if not path.exists():
        return []
    projects = json.loads(path.read_text())["projects"]
    if not isinstance(projects, list):
        raise ValueError("projects 必须是数组")
    seen = set()
    for project in projects:
        pid = project["id"]
        if not isinstance(pid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", pid) or pid in seen:
            raise ValueError("项目 id 必须唯一且只含字母、数字、下划线或连字符")
        seen.add(pid)
        if project.get("deployment") not in ("standard", "pd"):
            raise ValueError(f"{pid}: deployment 必须是 standard 或 pd")
        if not project.get("nodes"):
            raise ValueError(f"{pid}: 至少配置一个节点")
        nodes = set()
        for node in project["nodes"]:
            nid = node["id"]
            if not isinstance(nid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", nid) or nid in nodes:
                raise ValueError(f"{pid}: 节点 id 无效或重复")
            nodes.add(nid)
            if node.get("role") not in ("gateway", "prefill", "decode", "engine"):
                raise ValueError(f"{pid}/{nid}: role 无效")
            if not (node.get("api_base_url") or node.get("metrics_url")):
                raise ValueError(f"{pid}/{nid}: 需要 api_base_url 或 metrics_url")
            for key in ("api_base_url", "metrics_url"):
                if not node.get(key):
                    continue
                url = urllib.parse.urlsplit(node[key])
                if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
                    raise ValueError(f"{pid}/{nid}: {key} 必须为不含凭证、查询参数或 fragment 的 HTTP(S) URL")
            for key in ("api_key_env", "metrics_key_env"):
                if node.get(key) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", node[key]):
                    raise ValueError(f"{pid}/{nid}: {key} 必须为环境变量名称")
            if "api_key" in node:
                raise ValueError("请使用 api_key_env 引用环境变量，不要在配置中保存 API Key")
        roles = {node["role"] for node in project["nodes"]}
        if project["deployment"] == "pd" and not {"prefill", "decode"}.issubset(roles):
            raise ValueError(f"{pid}: PD 项目必须包含 prefill 和 decode 节点")
    return projects


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to a redirect destination.


def request(url, key_env=None, limit=8 * 1024 * 1024):
    headers = {}
    if key_env:
        secret = os.getenv(key_env)
        if not secret:
            raise ValueError("未设置认证环境变量")
        headers["Authorization"] = "Bearer " + secret
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=5) as response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError("上游响应超过大小限制")
        return body


def safe_error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, ValueError):
        return "配置缺失或响应格式无效"
    return "连接失败或超时"


def query(promql):
    payload = json.loads(request(f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": promql})))
    if payload.get("status") != "success":
        raise ValueError("Prometheus 查询失败")
    values = {}
    for result in payload.get("data", {}).get("result", []):
        labels = result["metric"]
        value = float(result["value"][1])
        values[(labels.get("monitor_project"), labels.get("monitor_node"))] = value if math.isfinite(value) else None
    return values


def probe(node):
    if not node.get("api_base_url"):
        return {"state": "unconfigured", "message": "未配置 API 探测"}
    started = time.monotonic()
    try:
        # api_base_url includes /v1 (or another OpenAI-compatible prefix).
        payload = json.loads(request(node["api_base_url"].rstrip("/") + "/models", node.get("api_key_env")))
        if not isinstance(payload.get("data"), list):
            raise ValueError("非 models 响应")
        return {"state": "ok", "latency_ms": round((time.monotonic() - started) * 1000), "message": "API 可达 · 认证通过"}
    except Exception as exc:
        return {"state": "error", "message": safe_error(exc)}


def diagnose(v, configured=True):
    findings = []
    if not configured:
        return [["warning", "未接入指标", "仅 API URL 和 Key 无法读取 KV、排队和引擎吞吐；请配置 metrics_url。"]]
    if v.get("up") != 1:
        return [["critical" if v.get("up") == 0 else "warning", "指标采集失败" if v.get("up") == 0 else "等待指标采集", "检查节点 /metrics、认证配置以及 Prometheus 状态。"]]
    if (v.get("waiting") or 0) > 0 and (v.get("kv") or 0) >= 90:
        findings.append(["critical", "KV 容量压力", "排队伴随高 KV 占用，检查长上下文并发和容量。"])
    elif (v.get("waiting") or 0) > 0:
        findings.append(["warning", "存在排队", "持续观察队列；结合当前节点角色检查 Prefill 或 Decode 处理能力。"])
    if (v.get("preempt") or 0) > 0:
        findings.append(["critical", "请求发生抢占", "KV 压力已影响执行，检查并发与长请求分布。"])
    if (v.get("ttft") or 0) > 60 and (v.get("prompt") or 0) > 128000:
        findings.append(["warning", "长上下文 Prefill 压力", "比较相同 Prompt 长度下的 TTFT，避免跨工作负载直接比较。"])
    if (v.get("imbalance") or 0) > 40:
        findings.append(["warning", "Engine 负载不均", "若持续存在，检查 DP 路由和调度。"])
    if v.get("cache") is not None and v["cache"] < 1 and (v.get("input_tps") or 0) > 0:
        findings.append(["warning", "前缀缓存收益低", "检查公共前缀是否稳定一致。"])
    if any(v.get(key) is None for key in ("running", "waiting", "kv")):
        findings.append(["warning", "核心指标不完整", "节点可能是网关或 vLLM 版本不同；缺失指标不会按零处理。"])
    return findings or [["ok", "暂未发现明显瓶颈", "基于当前可用指标；仍需观察业务高峰和历史变化。"]]


def collect(projects):
    values, errors = {}, {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        tasks = {name: pool.submit(query, expr) for name, expr in QUERIES.items()} if any(n.get("metrics_url") for p in projects for n in p["nodes"]) else {}
        probes = {(p["id"], n["id"]): pool.submit(probe, n) for p in projects for n in p["nodes"]}
        for name, task in tasks.items():
            try:
                values[name] = task.result()
            except Exception as exc:
                errors[name] = safe_error(exc)
        result = []
        for project in projects:
            item = {key: project.get(key, "") for key in ("id", "name", "deployment", "environment")}
            item["nodes"] = []
            for node in project["nodes"]:
                identity = (project["id"], node["id"])
                v = {name: values.get(name, {}).get(identity) if node.get("metrics_url") else None for name in QUERIES}
                # A failed scrape must not present old gauge/rate values as current.
                if v["up"] != 1:
                    v = {name: v["up"] if name == "up" else None for name in QUERIES}
                public = {key: node.get(key, "") for key in ("id", "name", "role", "api_base_url", "metrics_url")}
                public.update(values=v, api=probes[identity].result(), findings=diagnose(v, bool(node.get("metrics_url"))), auth_configured=bool(node.get("api_key_env")), metrics_configured=bool(node.get("metrics_url")))
                item["nodes"].append(public)
            result.append(item)
    return {"collected_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "refresh_seconds": REFRESH_SECONDS, "projects": result, "errors": errors}


_cache = None
_cache_time = 0
_cache_lock = threading.Lock()
PROJECTS = []


def snapshot():
    global _cache, _cache_time
    with _cache_lock:
        if _cache is None or time.monotonic() - _cache_time >= REFRESH_SECONDS:
            _cache = collect(PROJECTS)
            _cache_time = time.monotonic()
        return _cache


def targets():
    return [{"targets": ["dashboard:3000"], "labels": {"monitor_project": p["id"], "monitor_node": n["id"], "role": n["role"], "__metrics_path__": f'/scrape/{p["id"]}/{n["id"]}'}} for p in PROJECTS for n in p["nodes"] if n.get("metrics_url")]


def report(data):
    lines = ["vLLM 多项目诊断摘要", "采集时间: " + data["collected_at"]]
    if data.get("demo"):
        lines.insert(0, "【演示数据】以下数值仅为面板示意，未连接真实推理服务。")
    if data["errors"]:
        lines.append("Prometheus 查询异常: " + json.dumps(data["errors"], ensure_ascii=False))
    for project in data["projects"]:
        lines.append(f'\n项目: {project["name"] or project["id"]} ({project["deployment"]})')
        for node in project["nodes"]:
            lines.append(f'节点: {node["id"]} / {node["role"]} / {node["api"]["message"]}')
            lines.append(json.dumps(node["values"], ensure_ascii=False))
            lines.extend(f"- [{level}] {title}: {advice}" for level, title, advice in node["findings"])
    lines.append("\n只含监控状态及聚合指标，不含 Prompt、请求正文或 API Key。")
    body = "\n".join(lines).encode()
    if len(body) > 10000:
        body = body[:9900].decode("utf-8", errors="ignore").encode() + "\n…摘要已截断，请缩小项目范围。".encode()
    return body


def demo_snapshot():
    """Deterministic illustration, never probes or reads real configuration."""
    examples = [
        ("glm", "GLM-5.3-Flash", "http://glm.example:8080/v1", 18,
         dict(up=1, running=12, waiting=0, kv=46.8, imbalance=8, output_tps=243.7,
              input_tps=3200, ttft=0.56, tpot=0.021, e2e=12.6, prefill=0.32,
              prompt=4096, cache=72, preempt=0)),
        ("qwen", "Qwen3-32B", "http://qwen.example:8000/v1", 26,
         dict(up=1, running=28, waiting=7, kv=93.2, imbalance=12, output_tps=186.4,
              input_tps=2180, ttft=1.82, tpot=0.038, e2e=28.4, prefill=0.91,
              prompt=8192, cache=38, preempt=3)),
    ]
    projects = []
    for pid, name, url, latency, values in examples:
        projects.append({"id": pid, "name": name, "deployment": "standard", "nodes": [
            {"id": "endpoint", "name": "服务入口", "role": "engine", "api_base_url": url,
             "metrics_url": url[:-3] + "/metrics", "values": values,
             "api": {"state": "ok", "message": "API 可达 · 认证通过", "latency_ms": latency},
             "findings": diagnose(values), "auth_configured": True, "metrics_configured": True}
        ]})
    return {"demo": True, "collected_at": "演示快照", "refresh_seconds": REFRESH_SECONDS,
            "projects": projects, "errors": {}}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path in ("/", "/demo"):
            return self.send(200, "text/html; charset=utf-8", INDEX)
        if path == "/api/demo":
            return self.send(200, "application/json; charset=utf-8", json.dumps(demo_snapshot(), ensure_ascii=False).encode())
        if path == "/api/demo/report":
            return self.send(200, "text/plain; charset=utf-8", report(demo_snapshot()))
        if path == "/health":
            return self.send(200, "text/plain", b"ok\n")
        if path == "/api/targets":
            return self.send(200, "application/json", json.dumps(targets()).encode())
        if path.startswith("/scrape/"):
            node = next((n for p in PROJECTS for n in p["nodes"] if path == f'/scrape/{p["id"]}/{n["id"]}' and n.get("metrics_url")), None)
            if node:
                try:
                    body = request(node["metrics_url"], node.get("metrics_key_env"))
                    return self.send(200, "text/plain; version=0.0.4; charset=utf-8", body)
                except Exception as exc:
                    return self.send(502, "text/plain; charset=utf-8", safe_error(exc).encode())
        if path in ("/api/status", "/api/report"):
            data = snapshot()
            pid = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("project", [None])[0]
            if pid:
                data = dict(data, projects=[p for p in data["projects"] if p["id"] == pid])
            if path == "/api/report":
                return self.send(200, "text/plain; charset=utf-8", report(data))
            return self.send(200, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False, allow_nan=False).encode())
        return self.send(404, "text/plain", b"not found\n")

    def log_message(self, fmt, *args):
        # Avoid logging URL/query strings supplied by clients.
        pass


if __name__ == "__main__":
    PROJECTS = models_from_env() or load_config()
    print(f"Loaded {len(PROJECTS)} projects", flush=True)
    ThreadingHTTPServer((os.getenv("BIND_ADDRESS", "0.0.0.0"), int(os.getenv("PORT", "3000"))), Handler).serve_forever()
