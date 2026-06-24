"""@monitor —— cron 死人开关,基于 GlitchTip 自带 Cron Monitor。

底层用 ``sentry_sdk.capture_checkin``,不自建上报。契约:
  任务成功 → check-in OK
  任务抛   → check-in error,**异常仍向上抛(不吞)**
  漏启动   → 传 schedule 注册到 server 端,由 GlitchTip 标记 missed
  端点宕   → check-in 失败只记 warning,**任务照常跑/照常抛**(fail-open)
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("zlx_ops_sdk")


def _build_monitor_config(schedule: Optional[str], **extra) -> Optional[dict]:
    """把 crontab 字符串 + 可选项组装成 GlitchTip monitor_config。"""
    if schedule is None:
        return None
    cfg: dict[str, Any] = {"schedule": {"type": "crontab", "value": schedule}}
    cfg.update({k: v for k, v in extra.items() if v is not None})
    return cfg


def _safe_checkin(**kwargs) -> Optional[str]:
    """capture_checkin 的 fail-open 包装。端点宕只记 warning,绝不外抛。"""
    try:
        from sentry_sdk.crons import capture_checkin

        return capture_checkin(**kwargs)
    except Exception as exc:  # noqa: BLE001 — check-in 永不能搞死任务
        logger.warning(
            "zlx_ops_sdk: cron check-in 失败(%r),任务不受影响继续", exc
        )
        return None


def monitor(
    *,
    monitor_slug: str,
    schedule: Optional[str] = None,
    checkin_margin: Optional[int] = None,
    max_runtime: Optional[int] = None,
    timezone: Optional[str] = None,
) -> Callable:
    """装饰一个 cron 任务,自动做 GlitchTip check-in。

    :param monitor_slug: GlitchTip monitor 的 slug。
    :param schedule: crontab 表达式;传了才注册 schedule、server 端才能标记漏启动。
    :param checkin_margin: 允许迟到的分钟数。
    :param max_runtime: 最大运行分钟数,超时 server 端标记。
    :param timezone: schedule 的时区。
    """
    monitor_config = _build_monitor_config(
        schedule,
        checkin_margin=checkin_margin,
        max_runtime=max_runtime,
        timezone=timezone,
    )

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            check_in_id = _safe_checkin(
                monitor_slug=monitor_slug,
                status="in_progress",
                monitor_config=monitor_config,
            )
            started = time.monotonic()
            try:
                result = fn(*args, **kwargs)
            except BaseException:
                _safe_checkin(
                    monitor_slug=monitor_slug,
                    status="error",
                    check_in_id=check_in_id,
                    duration=time.monotonic() - started,
                    monitor_config=monitor_config,
                )
                raise  # 异常仍向上抛,不吞
            _safe_checkin(
                monitor_slug=monitor_slug,
                status="ok",
                check_in_id=check_in_id,
                duration=time.monotonic() - started,
                monitor_config=monitor_config,
            )
            return result

        return wrapper

    return decorator
