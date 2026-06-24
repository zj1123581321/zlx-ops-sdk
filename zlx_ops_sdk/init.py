"""init() —— fail-open by contract。

跨 50+ 服务的硬契约:可观测的 init 永不抛、永不阻塞 boot。
缺 DSN / 坏 DSN / GlitchTip 宕 / 注册失败 / 网络超时 —— 一律只记 warning。
理由:若 init 能抛,一次 GlitchTip 故障或一个坏 release 会同时让 50 服务开不了机。
可观测绝不能搞死被观测者。
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass

logger = logging.getLogger("zlx_ops_sdk")

#: 整个 init 的网络调用上界(秒)。超过即放弃并继续 boot。
DEFAULT_INIT_TIMEOUT = 5.0
#: kill switch:置为 "1" 时整个 init 直接 no-op。
DISABLE_ENV = "ZLX_OPS_DISABLED"
#: DSN 来源 env;库本身不持有任何密钥。
DSN_ENV = "SENTRY_DSN"


@dataclass(frozen=True)
class InitResult:
    """init 的结果。enabled=False 永远不是错误,只是"可观测没接上,应用照常跑"。"""

    enabled: bool
    reason: str = ""


def _resolve_release(repo: str | None) -> str:
    """解析 release = git SHA tag,带 repo 前缀。全程 fail-open。"""
    sha = os.environ.get("GIT_SHA") or os.environ.get("RELEASE")
    if not sha:
        try:
            sha = (
                subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                )
                .decode()
                .strip()
            )
        except Exception:
            sha = "unknown"
    return f"{repo}@{sha}" if repo else sha


def init(
    service: str,
    *,
    dsn: str | None = None,
    release: str | None = None,
    server: str | None = None,
    repo: str | None = None,
    environment: str | None = None,
    timeout: float = DEFAULT_INIT_TIMEOUT,
    **sentry_kwargs,
) -> InitResult:
    """一行接入可观测。永不抛异常,永不阻塞 boot 超过 ``timeout`` 秒。

    :param service: 服务名,打到 ``service`` tag。
    :param dsn: GlitchTip/Sentry DSN;缺省从 env ``SENTRY_DSN`` 读。
    :param release: 缺省解析为 git SHA(带 repo 前缀)。
    :param server: 主机名,打到 ``server`` tag。
    :param repo: 仓库名,打到 ``repo`` tag 并作为 release 前缀。
    :param timeout: init 网络调用上界;超时即放弃并继续 boot。
    """
    try:
        if os.environ.get(DISABLE_ENV) == "1":
            logger.info("zlx_ops_sdk: 被 %s=1 关闭,init 跳过", DISABLE_ENV)
            return InitResult(False, "disabled")

        dsn = dsn or os.environ.get(DSN_ENV)
        if not dsn:
            logger.warning(
                "zlx_ops_sdk: 缺 DSN(env %s 未设),可观测关闭,应用照常启动", DSN_ENV
            )
            return InitResult(False, "no_dsn")

        resolved_release = release or _resolve_release(repo)
        holder: dict = {}

        def _do_init() -> None:
            try:
                import sentry_sdk

                sentry_sdk.init(
                    dsn=dsn,
                    release=resolved_release,
                    environment=environment,
                    **sentry_kwargs,
                )
                # 用 global scope:tag 进程内全线程生效。
                # (init 跑在 worker 线程,sentry 2.x 的 current/isolation scope
                #  是 context-local,在此设的 tag 不会到主线程捕获的事件上。)
                scope = sentry_sdk.get_global_scope()
                scope.set_tag("service", service)
                if repo:
                    scope.set_tag("repo", repo)
                if server:
                    scope.set_tag("server", server)
                holder["ok"] = True
            except BaseException as exc:  # noqa: BLE001 — 线程内必须吞干净
                holder["error"] = exc

        t = threading.Thread(target=_do_init, name="zlx-ops-init", daemon=True)
        t.start()
        t.join(timeout)

        if t.is_alive():
            logger.warning(
                "zlx_ops_sdk: init 超过 %.1fs 超时,不阻塞 boot,继续启动", timeout
            )
            return InitResult(False, "timeout")
        if "error" in holder:
            logger.warning(
                "zlx_ops_sdk: init 失败(%r),应用照常启动", holder["error"]
            )
            return InitResult(False, "init_error")
        return InitResult(True, "ok")
    except BaseException as exc:  # noqa: BLE001 — 终极兜底,绝不外抛
        logger.warning("zlx_ops_sdk: init 意外失败(%r),应用照常启动", exc)
        return InitResult(False, "unexpected_error")
