"""Multi-project vLLM monitor; standard library only."""
import json
from datetime import datetime, timedelta, timezone
import math
import os
import re
import shlex
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from storage import ConflictError, ModelStore


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
REFRESH_SECONDS = max(3, int(os.getenv("REFRESH_SECONDS", "3")))
CONFIG_PATH = Path(os.getenv("SERVICES_CONFIG", str(Path(__file__).resolve().parent.parent / "services.json")))
INDEX = Path(__file__).with_name("index.html").read_bytes()
GROUP = "monitor_project,monitor_node"
SELECTOR = '{job="vllm"}'
STORE = None
CONFIG_TOKEN = secrets.token_urlsafe(32)
ALLOWED_HOSTS = set(os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1,dashboard").split(","))


def queries():
    def metric(name):
        return "vllm:" + name + SELECTOR

    def total(name):
        return f"sum by ({GROUP}) ({metric(name)})"

    def instant_rate(name):
        # The range only locates samples; irate uses the latest two, not a
        # minute-long average. Apply before aggregation to handle resets.
        return f"sum by ({GROUP}) (irate({metric(name)}[1m]))"

    def p50(name):
        return f"histogram_quantile(0.50,sum by (le,{GROUP}) (irate({metric(name + '_bucket')}[1m])))"

    kv = metric("kv_cache_usage_perc")
    expressions = {
        "up": f"min by ({GROUP}) (up{SELECTOR})",
        "running": total("num_requests_running"),
        "waiting": total("num_requests_waiting"),
        "kv": f"max by ({GROUP}) ({kv}) * 100",
        "imbalance": f"(max by ({GROUP}) ({kv}) - min by ({GROUP}) ({kv})) * 100",
        "ttft": p50("time_to_first_token_seconds"),
        "e2e": p50("e2e_request_latency_seconds"),
        "tpot": p50("inter_token_latency_seconds"),
        "prefill": p50("request_prefill_time_seconds"),
        "prompt": p50("request_prompt_tokens"),
        "cache": f'100 * {instant_rate("prefix_cache_hits_total")} / {instant_rate("prefix_cache_queries_total")}',
        "preempt": f'sum by ({GROUP}) (increase({metric("num_preemptions_total")}[1h]))',
        "input_tps": instant_rate("prompt_tokens_total"),
        "output_tps": instant_rate("generation_tokens_total"),
    }

    # Positive activity includes requests that finished between scrapes. Missing
    # evidence is unknown, not idle: all signals must exist to establish zero.
    signals = [expressions[name] for name in ("running", "waiting", "input_tps", "output_tps")]
    signals += [instant_rate(name + "_count") for name in
                ("e2e_request_latency_seconds", "time_to_first_token_seconds")]
    active = " or ".join(f"({expr} > 0)" for expr in signals)
    idle = f" and on ({GROUP}) ".join(f"({expr} == 0)" for expr in signals)
    expressions["activity"] = f"(({active}) or ({idle})) > bool 0"
    return expressions


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


def request(url, key_env=None, limit=8 * 1024 * 1024, secret=None):
    headers = {}
    if secret is None and key_env:
        secret = os.getenv(key_env)
        if not secret:
            raise ValueError("未设置认证环境变量")
    if secret:
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


STAT_WINDOWS = {"24h": "1d", "7d": "7d", "30d": "30d"}


def daily_periods(window, now):
    """Calendar days in Asia/Shanghai, including today's partial day."""
    days = {"24h": 1, "7d": 7, "30d": 30}[window]
    local = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return [(int((midnight - timedelta(days=i)).timestamp()),
             (midnight - timedelta(days=i)).strftime("%Y-%m-%d")) for i in reversed(range(days))]


def usage_statistics(window):
    projects = current_configuration()[1]
    end = int(time.time())
    periods = daily_periods(window, end)
    counters = {"requests": "request_success_total", "input_tokens": "prompt_tokens_total",
                "output_tokens": "generation_tokens_total"}

    def read(metric):
        def fetch(path, params):
            payload = json.loads(request(f"{PROMETHEUS}/api/v1/{path}?" + urllib.parse.urlencode(params)))
            if payload.get("status") != "success":
                raise ValueError("统计查询失败")
            return payload.get("data", {}).get("result", [])
        def expression(duration):
            function = "avg_over_time" if metric == "num_requests_waiting" else "increase"
            return f"sum by ({GROUP},model_name) ({function}(vllm:{metric}{SELECTOR}[{duration}]))"
        result = []
        if len(periods) > 1:
            expr = expression("1d")
            result = fetch("query_range", dict(query=expr, start=periods[0][0]+86400,
                                               end=periods[-1][0], step=86400))
        elapsed = end - periods[-1][0]
        if elapsed > 0:
            expr = expression(f"{elapsed}s")
            for item in fetch("query", dict(query=expr, time=end)):
                result.append(dict(metric=item["metric"], values=[item["value"]]))
        return result

    # Completed windows end at the next midnight; today's window ends now.
    date_for_time = {start+86400: date for start, date in periods[:-1]}
    date_for_time[end] = periods[-1][1]
    series = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {name: pool.submit(read, metric) for name, metric in dict(counters, waiting="num_requests_waiting").items()}
        for name, task in tasks.items():
            for result in task.result():
                labels = result["metric"]
                identity = (labels.get("monitor_project"), labels.get("monitor_node"), labels.get("model_name", ""))
                for timestamp, raw in result.get("values", []):
                    date = date_for_time.get(int(float(timestamp)))
                    if date is None:
                        continue
                    value = float(raw)
                    series.setdefault(identity, {}).setdefault(date, {})[name] = max(0, value) if math.isfinite(value) else None

    def values(raw):
        result = {key: raw.get(key) for key in counters}
        result["total_tokens"] = (result["input_tokens"] + result["output_tokens"]
                                  if all(result[k] is not None for k in ("input_tokens", "output_tokens")) else None)
        return result

    rows = []
    for project in projects:
        for node in project["nodes"]:
            if not node.get("metrics_url"):
                continue
            matches = [(key[2], raw) for key, raw in series.items() if key[:2] == (project["id"], node["id"])]
            for model, raw in matches or [("", {})]:
                daily = [dict(date=date, waiting=raw.get(date, {}).get("waiting"), **values(raw.get(date, {}))) for _, date in periods]
                totals = {key: sum(day[key] for day in daily if day[key] is not None)
                          if any(day[key] is not None for day in daily) else None
                          for key in (*counters, "total_tokens")}
                rows.append(dict(project=project.get("name") or project["id"], node=node.get("name") or node["id"],
                                 display_model=project.get("alias") or model or "未提供 model_name", model=model or "未提供 model_name", daily=daily, **totals))
    totals = {key: sum(row[key] for row in rows if row[key] is not None) if any(row[key] is not None for row in rows) else None
              for key in (*counters, "total_tokens")}
    partial = any(day[key] is None for row in rows for day in row["daily"] for key in counters)
    return dict(window=window, end=end, timezone="Asia/Shanghai", dates=[date for _, date in periods],
                partial=partial, rows=rows, totals=totals)


AVERAGE_WINDOWS = ("12h", "24h")
AVERAGE_STEP = 3  # Match prometheus.yml; independent of chart display resolution.


def average_queries(window):
    expressions = {}
    for direction, metric in (("input", "prompt_tokens_total"), ("output", "generation_tokens_total")):
        raw = f"vllm:{metric}{SELECTOR}"
        # Fresh counters and successful scrapes only. Filter individual series
        # before aggregation so stale workers cannot inflate throughput.
        fresh = f"(time() - timestamp({raw}) < {AVERAGE_STEP * 2})"
        rate = f"sum by ({GROUP},model_name) (irate({raw}[1m]) and {fresh})"
        rate = f"(({rate}) and on ({GROUP}) ({QUERIES['up']} == 1))"
        positive = f"({rate} > 0)[{window}:{AVERAGE_STEP}s]"
        expressions[direction + "_average"] = f"avg_over_time({positive})"
        expressions[direction + "_active_seconds"] = f"count_over_time({positive}) * {AVERAGE_STEP}"
        expressions[direction + "_observed_seconds"] = f"count_over_time(({rate} >= 0)[{window}:{AVERAGE_STEP}s]) * {AVERAGE_STEP}"
    return expressions


def throughput_averages(window):
    if window not in AVERAGE_WINDOWS:
        raise ValueError("不支持的平均值范围")
    projects = current_configuration()[1]
    end = int(time.time())
    expressions = average_queries(window)

    def read(expr):
        params = urllib.parse.urlencode({"query": expr, "time": end})
        payload = json.loads(request(f"{PROMETHEUS}/api/v1/query?" + params))
        if payload.get("status") != "success":
            raise ValueError("平均吞吐查询失败")
        return payload.get("data", {}).get("result", [])

    series = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        tasks = {key: pool.submit(read, expr) for key, expr in expressions.items()}
        for key, task in tasks.items():
            for result in task.result():
                labels = result["metric"]
                identity = (labels.get("monitor_project"), labels.get("monitor_node"), labels.get("model_name", ""))
                value = float(result["value"][1])
                series.setdefault(identity, {})[key] = value if math.isfinite(value) and value >= 0 else None
    rows = []
    for project in projects:
        for node in project["nodes"]:
            if not node.get("metrics_url"):
                continue
            matches = [(key[2], raw) for key, raw in series.items() if key[:2] == (project["id"], node["id"])]
            for model, raw in matches or [("", {})]:
                values = {key: raw.get(key) for key in expressions}
                for direction in ("input", "output"):
                    if values[direction + "_observed_seconds"] is not None and values[direction + "_active_seconds"] is None:
                        values[direction + "_active_seconds"] = 0
                rows.append(dict(project=project.get("name") or project["id"], node=node.get("name") or node["id"],
                                 display_model=project.get("alias") or model or "未提供 model_name", model=model or "未提供 model_name", **values))
    return dict(window=window, end=end, step_seconds=AVERAGE_STEP, rows=rows)


HISTORY_METRICS = ("output_tps", "ttft", "waiting", "kv")
HISTORY_WINDOWS = (2, 15, 60, 360, 720, 1440)


def historical_samples(minutes):
    """Read persisted history; bound each series to approximately 1200 points."""
    projects = current_configuration()[1]
    end = int(time.time())
    step = max(3, math.ceil(minutes * 60 / 1200))
    start = end - minutes * 60

    def read(name):
        expr = QUERIES[name]
        if name in ("output_tps", "ttft"):
            # Match live charts: confirmed idle intervals use the baseline;
            # unknown activity and active intervals without observations do not.
            expr = (f'(({expr}) and on ({GROUP}) ({QUERIES["activity"]} > 0))'
                    f' or on ({GROUP}) (0 * ({QUERIES["activity"]} == 0))')
        expr = f'({expr}) and on ({GROUP}) ({QUERIES["up"]} == 1)'
        params = urllib.parse.urlencode(dict(query=expr, start=start, end=end, step=step))
        payload = json.loads(request(f"{PROMETHEUS}/api/v1/query_range?" + params))
        if payload.get("status") != "success":
            raise ValueError("Prometheus 历史查询失败")
        return payload.get("data", {}).get("result", [])

    series = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {name: pool.submit(read, name) for name in HISTORY_METRICS}
        for name, task in tasks.items():
            for result in task.result():
                labels = result["metric"]
                identity = (labels.get("monitor_project"), labels.get("monitor_node"))
                points = series.setdefault(identity, {})
                for timestamp, raw in result["values"]:
                    value = float(raw)
                    points.setdefault(round(float(timestamp) * 1000), {})[name] = value if math.isfinite(value) else None
    entries = []
    for project in projects:
        for node in project["nodes"]:
            points = series.get((project["id"], node["id"]), {}) if node.get("metrics_url") else {}
            entries.append(dict(project_id=project["id"], node={key: node.get(key, "") for key in
                                ("id", "api_base_url", "metrics_url")},
                                samples=[dict(time=t * 1000, values={name: points.get(t * 1000, {}).get(name)
                                         for name in HISTORY_METRICS}) for t in range(start, end + 1, step)]))
    return dict(entries=entries, step_ms=step * 1000)


def probe(node):
    if not node.get("api_base_url"):
        return {"state": "unconfigured", "message": "未配置 API 探测"}
    started = time.monotonic()
    try:
        # api_base_url includes /v1 (or another OpenAI-compatible prefix).
        payload = json.loads(request(node["api_base_url"].rstrip("/") + "/models", node.get("api_key_env"), secret=node.get("_api_key")))
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
            item = {key: project.get(key, "") for key in ("id", "name", "alias", "deployment", "environment")}
            item["nodes"] = []
            for node in project["nodes"]:
                identity = (project["id"], node["id"])
                v = {name: values.get(name, {}).get(identity) if node.get("metrics_url") else None for name in QUERIES}
                # A failed scrape must not present old gauge/rate values as current.
                if v["up"] != 1:
                    v = {name: v["up"] if name == "up" else None for name in QUERIES}
                activity = "active" if (v.get("activity") or 0) > 0 else "idle" if v.get("activity") == 0 else "unknown"
                findings = diagnose(v, bool(node.get("metrics_url")))
                # Keep instantaneous gauges and event totals truthful. Performance
                # statistics are only available for confirmed active intervals.
                if activity != "active":
                    for name in ("input_tps", "output_tps", "ttft", "e2e", "tpot", "prefill", "prompt", "cache"):
                        v[name] = None
                public = {key: node.get(key, "") for key in ("id", "name", "role", "api_base_url", "metrics_url")}
                public.update(values=v, activity=activity, api=probes[identity].result(), findings=findings, auth_configured=bool(node.get("api_key_env") or node.get("_api_key")), metrics_configured=bool(node.get("metrics_url")))
                item["nodes"].append(public)
            result.append(item)
    return {"collected_at_ms": int(time.time() * 1000), "collected_at": time.strftime("%Y-%m-%d %H:%M:%S %z"), "refresh_seconds": REFRESH_SECONDS, "projects": result, "errors": errors}


_cache = None
_cache_time = 0
_cache_revision = None
_cache_lock = threading.Lock()
PROJECTS = []


def snapshot():
    global _cache, _cache_time, _cache_revision
    with _cache_lock:
        while True:
            revision, projects = current_configuration()
            if _cache is None or revision != _cache_revision or time.monotonic() - _cache_time >= REFRESH_SECONDS:
                data = collect(projects)
                if current_configuration()[0] != revision:
                    continue  # Discard a result collected against outdated credentials/URLs.
                _cache, _cache_time, _cache_revision = data, time.monotonic(), revision
            return _cache


def current_configuration():
    if STORE is None:
        return 0, PROJECTS
    revision, rows = STORE.snapshot()
    return revision, [project for project, _ in rows]


def targets():
    return [{"targets": ["dashboard:3000"], "labels": {"monitor_project": p["id"], "monitor_node": n["id"], "role": n["role"], "__metrics_path__": f'/scrape/{p["id"]}/{n["id"]}'}} for p in current_configuration()[1] for n in p["nodes"] if n.get("metrics_url")]


def report(data):
    lines = ["vLLM 多项目诊断摘要", "采集时间: " + data["collected_at"]]
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


class Handler(BaseHTTPRequestHandler):
    def json_response(self, status, payload):
        return self.send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False).encode())

    def trusted_host(self):
        try:
            host = urllib.parse.urlsplit("http://" + self.headers.get("Host", "")).hostname
            return bool(host) and ("*" in ALLOWED_HOSTS or host in ALLOWED_HOSTS)
        except ValueError:
            return False

    def mutate(self):
        if not self.trusted_host() or not secrets.compare_digest(self.headers.get("X-Config-Token", ""), CONFIG_TOKEN):
            return self.json_response(403, {"error": "请求校验失败，请刷新页面重试"})
        origin = self.headers.get("Origin")
        if origin and origin not in ("http://" + self.headers.get("Host", ""), "https://" + self.headers.get("Host", "")):
            return self.json_response(403, {"error": "不允许跨站修改配置"})
        if STORE is None:
            return self.json_response(503, {"error": "配置数据库未初始化"})
        path = urllib.parse.urlsplit(self.path).path
        match = re.fullmatch(r"/api/models/([A-Za-z0-9_-]+)", path)
        if not ((self.command == "POST" and path == "/api/models") or (self.command in ("PUT", "DELETE") and match)):
            return self.json_response(404, {"error": "接口不存在"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 16384 or self.headers.get_content_type() != "application/json":
                return self.json_response(400, {"error": "需要不超过 16KB 的 JSON 配置"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("配置必须是 JSON 对象")
            if self.command == "DELETE":
                STORE.delete(match.group(1), data.get("version"))
                return self.json_response(200, {"ok": True})
            model = STORE.save(data, match.group(1) if match else None)
            return self.json_response(201 if self.command == "POST" else 200, {"model": model})
        except ConflictError as exc:
            return self.json_response(409, {"error": str(exc)})
        except KeyError:
            return self.json_response(404, {"error": "模型已不存在，请刷新列表"})
        except (json.JSONDecodeError, UnicodeError):
            return self.json_response(400, {"error": "JSON 格式无效"})
        except ValueError as exc:
            return self.json_response(400, {"error": str(exc)})
        except Exception:
            return self.json_response(500, {"error": "配置保存失败，请检查数据库目录权限或磁盘空间"})

    do_POST = mutate
    do_PUT = mutate
    do_DELETE = mutate

    def send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted_host():
            return self.json_response(403, {"error": "不允许此访问地址"})
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/config":
            if STORE is None:
                return self.json_response(503, {"error": "配置数据库未初始化"})
            return self.json_response(200, dict(STORE.list_public(), token=CONFIG_TOKEN))
        if path == "/":
            return self.send(200, "text/html; charset=utf-8", INDEX)
        if path in ("/stats", "/statistics"):
            return self.send(200, "text/html; charset=utf-8", Path(__file__).with_name("statistics.html").read_bytes())
        if path == "/api/averages":
            window = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("window", ["12h"])[0]
            if window not in AVERAGE_WINDOWS:
                return self.json_response(400, {"error": "平均值仅支持 12 / 24 小时"})
            try:
                return self.json_response(200, throughput_averages(window))
            except Exception as exc:
                return self.json_response(502, {"error": safe_error(exc)})
        if path == "/api/statistics":
            window = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("window", ["30d"])[0]
            if window not in STAT_WINDOWS:
                return self.json_response(400, {"error": "不支持的统计范围"})
            try:
                return self.json_response(200, usage_statistics(window))
            except Exception as exc:
                return self.json_response(502, {"error": safe_error(exc)})
        if path == "/theme.css":
            return self.send(200, "text/css; charset=utf-8", Path(__file__).with_name("theme.css").read_bytes())
        if path == "/statistics-bars.js":
            return self.send(200, "text/javascript; charset=utf-8", Path(__file__).with_name("statistics-bars.js").read_bytes())
        if path == "/averages.js":
            return self.send(200, "text/javascript; charset=utf-8", Path(__file__).with_name("averages.js").read_bytes())
        if path == "/charts.js":
            return self.send(200, "text/javascript; charset=utf-8", Path(__file__).with_name("charts.js").read_bytes())
        if path == "/health":
            return self.send(200, "text/plain", b"ok\n")
        if path == "/api/history":
            try:
                minutes = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("minutes", ["2"])[0])
                if minutes not in HISTORY_WINDOWS:
                    raise ValueError("无效历史范围")
            except ValueError:
                return self.json_response(400, {"error": "不支持的历史范围"})
            try:
                return self.json_response(200, historical_samples(minutes))
            except Exception as exc:
                return self.json_response(502, {"error": safe_error(exc)})
        if path == "/api/targets":
            return self.send(200, "application/json", json.dumps(targets()).encode())
        if path.startswith("/scrape/"):
            node = next((n for p in current_configuration()[1] for n in p["nodes"] if path == f'/scrape/{p["id"]}/{n["id"]}' and n.get("metrics_url")), None)
            if node:
                try:
                    body = request(node["metrics_url"], node.get("metrics_key_env"), secret=node.get("_metrics_key"))
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
    STORE = ModelStore(os.getenv("CONFIG_DB", str(Path(__file__).resolve().parent.parent / "data/models.sqlite3")))
    STORE.initialize(lambda: models_from_env() or load_config())
    print(f"Loaded {len(current_configuration()[1])} projects; http://127.0.0.1:{os.getenv('PORT', '3000')}", flush=True)
    ThreadingHTTPServer((os.getenv("BIND_ADDRESS", "0.0.0.0"), int(os.getenv("PORT", "3000"))), Handler).serve_forever()
