# vLLM 模型监控

在页面添加模型，填写 **名称、URL、API Key**，保存即可接入。支持编辑、保留或替换 Key、移除模型。配置变更不需要重启容器。

## 启动

```bash
docker compose up -d --build
```

打开 http://127.0.0.1:3000 ，点击右上角 **管理模型 → 添加模型**。

- 名称：例如 `GLM-5.3-Flash`。
- URL：例如 `http://你的服务器:8080`，带不带 `/v1` 都可以。
- API Key：没有认证就留空。编辑时留空保留原 Key，也可以显式清除。

配置立即保存，API 探测在随后刷新时使用新配置，Prometheus 通常在约 30 秒内发现新采集目标，再按 15 秒间隔采样。首次出现速率或 P95 还需要足够的请求样本。保存失败会明确提示，不会显示成功。

**已有旧版容器需要执行一次上述命令升级。升级后日常增删改模型无需重启。** Docker Compose 要求 2.24+（支持可选 `.env`）；没有 `.env` 也可以启动。


## 数据保存在哪里

| 数据 | 保存位置 | 重启后 | 保留时间 |
| --- | --- | --- | --- |
| 模型名称、URL、API Key | SQLite，容器内 `/data/models.sqlite3`，挂载 `dashboard-data` 卷 | 保留 | 直到在页面移除配置 |
| vLLM 性能指标历史 | Prometheus 时序数据库，`prometheus-data` 卷 | 保留 | 默认 30 天 |
| API 探测结果及诊断摘要 | dashboard 内存缓存 | 重新采集 | 当前快照，尚无历史持久化 |

移除模型只停止后续采集并删除配置，不删除历史指标；历史按 Prometheus 的保留策略到期。改名或更换 Key 保留指标身份；更换 URL 创建新的节点指标身份，避免把旧地址的数值显示为新地址状态。页面折线从本次打开后累积真实采样，最多保留 6 小时，刷新页面后重新累积；历史查询仍可到本机 Prometheus 9090 页面。

`docker compose down` 保留两个数据卷；**`docker compose down -v` 会删除配置与历史指标**。备份模型配置时可停止 dashboard 后复制 SQLite 文件；备份 Prometheus 请使用其快照流程或停机备份整个数据卷。

API Key 存储于服务端 SQLite，文件权限为 `0600`；不回显到页面、不写入状态 API、服务发现或导出摘要。当前 Key 未做应用层加密，数据库文件及备份应按凭证管理。`data/` 和 `.env` 均被 Git 忽略。

## 原有配置如何迁移

新数据库首次启动时自动导入 `.env` 中的 `MODEL_n_NAME / MODEL_n_URL / MODEL_n_API_KEY`，没有这些变量时兼容导入 `services.json`。后续以数据库为准，不再从环境变量覆盖页面编辑；即使把所有模型删除，重启也不会重新导入。旧 `.env` 中不再需要的模型变量可在迁移后删除。

项目不再附带环境变量或旧版配置示例，新用户直接在页面配置即可。旧版 PD / 自定义端口或不同 metrics Key 的配置会保留采集能力；简化表单不支持编辑复杂配置，可移除后按单入口重新添加。容器若要首次导入旧 `services.json`，需挂载文件并设置 `SERVICES_CONFIG`。

## 自动监控哪些内容

| 接口情况 | 面板内容 |
| --- | --- |
| `/v1/models` 可访问 | API 在线状态、认证结果、探测响应时间 |
| 同一端口还开放 `/metrics` | KV、等待队列、运行请求、吞吐、首字延迟等 |
| 没有 `/metrics` 或采集未就绪 | 性能显示 `—`，API 状态独立展示 |

同一个 Key 用于 API 和 metrics，不跟随重定向。带代理前缀如 `/model-a/v1` 时，对应指标地址为 `/model-a/metrics`。如果实际 metrics 使用其他端口或另一把 Key，简化模式只能看到入口连通性。

PD 分离先按单入口监控，不自动推断背后的节点状态。只有网关主动提供各节点状态或带节点标签的聚合指标，才可能通过一个端口观察内部节点。参考 [vLLM PD 说明](https://docs.vllm.ai/en/latest/features/disagg_prefill/)。

- `/models` 探测不产生推理请求，不证明特定模型能够成功生成；探测耗时不是生成延迟。
- 每个配置对应一个服务端点；同一 URL 后面路由多个模型时，本版不拆分它们的指标。
- 吞吐 / P95 窗口为 15 分钟，抢占为 1 小时；无样本、缺失及采集失败显示 `—`，不当作零。
- 指标依据 [vLLM 官方文档](https://docs.vllm.ai/en/latest/usage/metrics/)，不同版本可能不完整。节点 P95 不相加。

## 本地开发与访问

```bash
ALLOWED_HOSTS='*' BIND_ADDRESS=0.0.0.0 python3 dashboard/app.py
python3 -m unittest discover -s tests -v
```

Python 3.9+，仅使用标准库。本地数据库默认是 `data/models.sqlite3`，可用 `CONFIG_DB` 改位置。本地对接 Prometheus 可设置 `PROMETHEUS_URL=http://127.0.0.1:9090`。

端口仅发布到宿主机回环地址；配置写入带请求校验与跨站保护，**没有内置用户登录**。远程用 SSH 转发：

```bash
ssh -L 3000:127.0.0.1:3000 用户名@服务器
```

如使用已有认证的反向代理，需将访问域名加入 `ALLOWED_HOSTS`（逗号分隔，不含端口），并保留 `dashboard` 以便容器内采集发现。默认允许 `localhost,127.0.0.1,::1,dashboard`。

### 多模型布局

模型数量没有两个的限制，可在管理模型中连续添加 5–10 个或更多模型。首页每行一个模型，左侧关键数值、右侧四张趋势图（输出吞吐、首字延迟 P95、等待队列、KV Cache）。支持搜索、状态筛选、排序、暂停、窗口切换与展开诊断；窄屏自动排列为两列图表。

### 流式趋势

默认每 3 秒读取一次状态，可用 `REFRESH_SECONDS` 配置（最短 3 秒）。Prometheus 仍按自己的采集间隔更新，页面刷新不等于底层产生新样本；吞吐与 P95 仍是 15 分钟统计窗口。图表按实际快照时间向左移动，最多延后一轮显示以平滑衔接；断点判定会容纳一次正常的上游采集耗时。历史仅在当前浏览器页面内累积，不会生成模拟曲线，采集失败、缺失指标或较长停采仍显示断点。

`/v1/models` 返回 404 时使用灰色“/v1/models 探测受限”提示，不把发现接口缺失当成推理服务故障；401、5xx 和指标异常仍正常提示。独立预览页面与 `/demo` 演示接口已移除。

无 Docker 的本地启动：`ALLOWED_HOSTS='*' BIND_ADDRESS=0.0.0.0 PORT=3000 python3 dashboard/app.py`。

## 镜像与网络访问

Prometheus 固定为 `prom/prometheus:v3.13.3`（3.13 LTS）；Python 保持原来的 `python:3.12-alpine` 浮动标签。版本依据：[Prometheus LTS](https://prometheus.io/docs/introduction/release-cycle/)、[Python 官方镜像清单](https://github.com/docker-library/official-images/blob/master/library/python)。

Compose 默认将监控页面发布到 `0.0.0.0:3000`，通过 `http://服务器IP:3000` 访问。`ALLOWED_HOSTS` 默认为 `*`，接受各机器地址；可在 `.env` 中改成指定域名/IP列表。页面没有登录认证，可达该端口的用户能管理配置。Prometheus 9090 仍只发布到本机。
