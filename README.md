# zlx-ops-sdk

一行接入可观测的极小内部库,跨 50+ 自托管服务共用。底层用
[`sentry_sdk`](https://docs.sentry.io/platforms/python/)(GlitchTip 兼容),**不自建上报**。

它把"我手动转发告警"变成"告警带 repo/release 上下文自己路由到飞书"。

## 核心契约:fail-open by contract

> 可观测**绝不能搞死被观测者**。

跨 50 服务共用一个库,若 `init` 能抛异常,一次 GlitchTip 故障或一个坏 release 会
**同时让 50 服务开不了机**。因此:

- `init(...)` 整个包 try/except —— 缺 DSN / 坏 DSN / GlitchTip 宕 / 注册失败 / 网络超时
  一律**只记 warning,永不抛、永不阻塞应用启动或 cron**。
- **显式 timeout**:init 的网络调用有界(默认 5s),不挂起 boot。
- **kill switch**:env `ZLX_OPS_DISABLED=1` → 整个 init 直接 no-op 返回。
- 库本身**不持有任何密钥**;DSN 从 env `SENTRY_DSN` 读。

## 安装

```bash
pip install "zlx-ops-sdk~=0.1"   # 见下方"版本钉法 / rollout 规则"
```

依赖 `sentry-sdk>=2.0,<3.0`。

## 用法

### 1. 一行 init

放在应用入口(`main`、`app.py`、cron 脚本顶部)最早处:

```python
import zlx_ops_sdk

zlx_ops_sdk.init(
    "youtube-download-api",          # service 名(必填)
    repo="zj1123581321/youtube-download-api",
    server="n305",
    environment="prod",
    # dsn 缺省从 env SENTRY_DSN 读;release 缺省解析 git SHA
)
```

- `dsn`:缺省读 env `SENTRY_DSN`;缺失 → fail-open(只 warning,应用照常启动)。
- `release`:缺省解析为 git SHA(带 `repo@` 前缀);也可读 env `GIT_SHA` / `RELEASE`。
- 返回 `InitResult(enabled, reason)`;`enabled=False` 不是错误,只是"可观测没接上,应用照常跑"。
- `timeout`:init 网络调用上界(秒),默认 `5.0`。

未捕获异常此后自动上报到 GlitchTip,带 `service` / `repo` / `server` / `release` tag。

### 2. cron 死人开关 `@monitor`

用 GlitchTip 自带 Cron Monitor,声明 `monitor_slug`(在 GlitchTip 建好同名 monitor):

```python
from zlx_ops_sdk import monitor

@monitor(monitor_slug="nightly-sync", schedule="0 3 * * *", timezone="Asia/Shanghai")
def nightly_sync():
    do_work()
```

四路行为:

| 情况 | 行为 |
|---|---|
| 任务成功 | check-in `ok` |
| 任务抛异常 | check-in `error`,**异常仍向上抛(不吞)** |
| 漏启动 | 传了 `schedule` → server 端注册,GlitchTip 标记 missed |
| check-in 端点宕 | 只记 warning,**任务照常跑/照常抛**(fail-open) |

可选参数:`schedule`(crontab)、`checkin_margin`、`max_runtime`、`timezone`。
**只有传了 `schedule`,GlitchTip 才能检测"漏启动"**。

## 版本钉法 / rollout 规则(爆炸半径管控)

一个 SDK 版本跨 50 服务 = 一个坏 release 同步炸 50 服务 boot。因此:

1. **钉主版本,不钉 `latest`/`@master`**:每服务 `pip install "zlx-ops-sdk~=0.1"`
   (等价 `>=0.1,<1.0`)。补丁/小版本自动收,主版本升级必须手动。
2. **canary 先升,再手动批量**:
   - 选 1 个低爆炸半径服务做 canary,先升新版本,观察 ≥24h(boot 正常 + 告警/check-in 正常路由)。
   - canary 通过后,再分批把其余服务 bump 到新版本,**不一次性全舰队升级**。
3. **主版本 bump(如 0.x → 1.0)= 破坏性变更**:走完整 canary,绝不批量直推。
4. fail-open 是兜底而非许可证:即便如此,版本钉 + canary 仍是防同步爆炸的第一道闸。

## 测试

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

覆盖:init fail-open(缺 DSN / init 抛 / 超时有界 / kill switch / happy / env DSN)、
cron 四路(成功 / 失败重抛 / schedule 注册 / 端点宕 fail-open)。

## 给下游(Lane C / D4 模板)的接入说明

- 包名:`zlx-ops-sdk`,import 名:`zlx_ops_sdk`,依赖钉 `~=0.1`。
- init 签名:`zlx_ops_sdk.init(service, *, dsn=None, release=None, server=None, repo=None, environment=None, timeout=5.0, **sentry_kwargs) -> InitResult`
- cron 签名:`zlx_ops_sdk.monitor(*, monitor_slug, schedule=None, checkin_margin=None, max_runtime=None, timezone=None)`
- env 契约:`SENTRY_DSN`(DSN 来源)、`ZLX_OPS_DISABLED=1`(kill switch)、`GIT_SHA`/`RELEASE`(release 兜底)。
