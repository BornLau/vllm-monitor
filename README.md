# vLLM 多项目推理服务观测台

沿用 Python 标准库 + Prometheus 架构，集中监控多个 URL / API Key，支持独立部署与 Gateway / Prefill / Decode 分离部署。提供项目总览、搜索与部署筛选、节点拓扑、API 认证探测、节点性能指标、瓶颈诊断及不超过 10KB 的文本摘要。

## 启动

```bash
cp services.example.json services.json
cp .env.example .env
# 编辑 services.json 中的真实地址，删除不需要的示例项目
# 编辑 .env 中的 Key；无认证的节点移除对应 *_key_env 字段
docker compose up -d --build
```

打开 http://127.0.0.1:3000 。仓库不保存真实地址配置与 Key；未创建 services.json 时，本地 Python 启动显示接入引导。Docker 启动前必须创建该文件及 .env。

```bash
curl -f http://127.0.0.1:9090/-/ready
curl -f http://127.0.0.1:3000/health
```

远程服务器使用 SSH 转发访问：

```bash
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 用户名@服务器
```

端口默认只监听宿主机回环地址。界面不含登录系统，需要多人访问时请在前面部署带认证的反向代理。

## 接入配置

一个项目包含多个节点。完整示例见 [services.example.json](services.example.json)。项目 id 全局唯一、节点 id 在项目内唯一，仅允许字母、数字、下划线、连字符。

| 字段 | 说明 |
| --- | --- |
| 项目 `deployment` | `standard` 或 `pd`；PD 必须配置 prefill 和 decode |
| 节点 `role` | `engine`、`gateway`、`prefill` 或 `decode` |
| `api_base_url` | OpenAI 兼容 API 基址，包含 `/v1` 或实际代理前缀；探测追加 `/models` |
| `api_key_env` | API Bearer Key 所在环境变量名；留空/删除则不使用认证 |
| `metrics_url` | 完整指标地址，可与 API 使用不同域名、端口、路径 |
| `metrics_key_env` | 指标接口 Bearer Key 的环境变量名；独立于 API Key |

每个节点至少配置 API 或 metrics 地址之一，URL 不允许内嵌凭证、查询参数或 fragment。每个 API / metrics 可以引用不同环境变量。配置变化后：

```bash
docker compose up -d --force-recreate dashboard
```

Prometheus 通过 HTTP 服务发现读取 dashboard 的 `/api/targets`，约 30 秒发现变更，再每 15 秒经后端代理采集节点指标；无需逐个维护 Prometheus target。dashboard 在后端注入指标 Key，发现接口不含 Key。生产地址必须从 dashboard 容器可达；宿主机服务可使用 `host.docker.internal`。

## 指标与 PD 语义

- 查询按 `monitor_project`、`monitor_node` 标签隔离，并限定 `job="vllm"`。各节点内部聚合 Engine 指标，不把不同项目或 P/D 的数据混合。
- API 探测使用 GET `/models`，只检查可达性、认证和响应结构，不证明生成请求一定成功，不产生推理流量。
- 只有 API URL + Key 时可监控上述连通性；KV、排队、抢占和吞吐需要访问每个引擎的 `/metrics`。
- 吞吐 / P95 使用 15 分钟窗口，抢占使用 1 小时窗口；缓存命中使用 `prefix_cache_hits_total / prefix_cache_queries_total`。无请求、缺失或不支持的指标显示 `—`，不当作零。
- 采集失败时隐藏该节点旧指标；API 探测失败、Prometheus 查询失败、未接入指标与部分指标缺失均单独提示，不显示为正常。
- PD 节点的 TTFT / 请求耗时仅代表该节点，不能相加得到全链路 P95。Gateway 不暴露兼容 histogram 时没有端到端延迟。拓扑按角色配置绘制，不验证实际路由；未实现 connector 特有的 KV 传输监控。
- Prometheus 保存 30 天原始数据，当前界面展示快照；历史可在本机 9090 Prometheus 页面查询。多个浏览器共享后端刷新缓存。
- 文本导出只含监控状态和聚合指标；完整项目/节点 URL 仅在界面显示。错误响应不回传上游正文或 Key；认证请求禁止跟随重定向。

指标依据 [vLLM 官方生产指标文档](https://docs.vllm.ai/en/latest/usage/metrics/)；各版本和 connector 暴露的指标有所不同。现有诊断阈值为启发式建议，不替代业务 SLO。

## 本地开发与验证

```bash
SERVICES_CONFIG=services.json PROMETHEUS_URL=http://127.0.0.1:9090 BIND_ADDRESS=127.0.0.1 python3 dashboard/app.py
python3 -m unittest discover -s tests -v
```

Python 直接启动时不会自动加载 `.env`，需在启动环境设置所引用的变量。运行要求 Python 3.9+；Docker 使用 Python 3.12。没有额外 Python 或前端构建依赖。

```bash
docker compose logs --tail=100 dashboard prometheus
docker compose down
```

`down` 保留历史数据卷；只有显式加 `-v` 才会删除历史数据。
