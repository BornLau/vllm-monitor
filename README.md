# vLLM 模型监控

每个模型只需要 **名称、URL、API Key**。面板并排展示 API 在线状态、探测响应时间，以及能获取到的 KV、排队、吞吐和首字延迟。不再需要配置项目、节点角色或 PD 拓扑。

## 最简单的接入方式

复制 `.env.example` 为 `.env`，填写：

```dotenv
MODEL_1_NAME='GLM-5.3-Flash'
MODEL_1_URL='http://你的服务器:8080'
MODEL_1_API_KEY='你的 Key'

MODEL_2_NAME='Qwen3-32B'
MODEL_2_URL='http://你的服务器:8000'
MODEL_2_API_KEY='你的 Key'
```

URL 带不带 `/v1` 都可以。没有认证时，Key 留空。第三个模型继续填写 `MODEL_3_NAME`、`MODEL_3_URL`、`MODEL_3_API_KEY`，以此类推。

启动（修改配置后也运行同一条）：

```bash
docker compose up -d --build
```

- **真实监控**：http://127.0.0.1:3000
- **两个模型的面板示意**：http://127.0.0.1:3000/demo （固定示例数据，不探测真实地址）

不需要填写 `services.json` 或修改 Prometheus 配置。`.env` 已被 Git 忽略，Key 只由后端使用。

## 自动监控哪些内容

| 接口情况 | 面板能看到的内容 |
| --- | --- |
| `/v1/models` 可访问 | API 是否在线、认证是否通过、探测响应时间 |
| 同一端口还开放 `/metrics` | KV 占用、等待队列、运行请求、吞吐、TTFT 等 |
| 没有 `/metrics` 或采集未就绪 | 性能指标显示 `—`；API 状态独立展示 |

自动使用同一个 Key 请求 API 和 metrics；不跟随重定向。若 URL 带代理前缀，例如 `/model-a/v1`，对应指标路径为 `/model-a/metrics`。如果实际 metrics 在其他端口、需要另一把 Key，简化模式只显示入口连通性。

**PD 分离先忽略内部节点。** 单一推理入口通常不能自动发现 P/D 节点；只有网关主动暴露各节点状态或带节点标签的聚合指标时，才有可能通过一个端口观察它们。当前按一个入口监控，不声称入口在线等于所有后端节点健康，也不把节点 P95 相加。参考 [vLLM PD 说明](https://docs.vllm.ai/en/latest/features/disagg_prefill/) 与 [代理示例](https://docs.vllm.ai/en/latest/examples/disaggregated/disaggregated_serving/)。

## 指标口径

- GET `/models` 不产生推理请求，只验证入口和认证；其响应耗时不是生成延迟，也不证明特定模型能成功生成。
- 一个配置项对应一个服务端点，metrics 统计该端点暴露的引擎指标。如果同一 URL 后面路由多个模型，本版不拆分这些模型的指标。
- 吞吐和 P95 使用 15 分钟窗口，抢占使用 1 小时窗口；缓存使用 `prefix_cache_hits_total / prefix_cache_queries_total`。指标依据 [vLLM 官方文档](https://docs.vllm.ai/en/latest/usage/metrics/)。
- 无样本、缺失、采集失败的性能指标显示 `—`，不当作零。各入口按独立标签查询，避免混合。
- Prometheus 自动发现服务、每 15 秒采集，保存 30 天；面板每 30 秒刷新。首次启动需等待发现及采样。
- 示意页及其导出文件均明确标注演示数据；真实监控不会回退成演示数据。

## 本地开发

无需 Docker 也能先查看示意：

```bash
BIND_ADDRESS=127.0.0.1 python3 dashboard/app.py
```

Python 启动自动读取根目录 `.env`。使用简单 `NAME='value'` 格式，含空格或 `#` 的值加引号；现有进程环境变量优先，不做 shell 表达式执行。默认 Prometheus 地址供容器使用，本地对接时设置 `PROMETHEUS_URL=http://127.0.0.1:9090`。

```bash
python3 -m unittest discover -s tests -v
```

Python 3.9+，无额外运行依赖。旧的 `services.json` 格式保留兼容：仅在没有配置 `MODEL_n_URL` 时读取；容器如仍需使用旧文件，要自行挂载并设置 `SERVICES_CONFIG`。

端口只发布到宿主机回环地址，无内置登录。远程使用 SSH 转发：

```bash
ssh -L 3000:127.0.0.1:3000 用户名@服务器
```

`docker compose down` 保留历史数据卷，追加 `-v` 才会删除历史。
