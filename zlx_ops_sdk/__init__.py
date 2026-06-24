"""zlx-ops-sdk —— 一行接入可观测(GlitchTip / Sentry 兼容)。

设计契约见 README:init 与 cron 装饰器全程 fail-open,绝不搞死被观测者。
"""
from .cron import monitor
from .init import DEFAULT_INIT_TIMEOUT, DISABLE_ENV, DSN_ENV, InitResult, init

__version__ = "0.1.0"

__all__ = [
    "init",
    "monitor",
    "InitResult",
    "DEFAULT_INIT_TIMEOUT",
    "DISABLE_ENV",
    "DSN_ENV",
    "__version__",
]
