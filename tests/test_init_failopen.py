"""T2 — init() fail-open by contract.

跨 50 服务的硬契约:可观测 init 永不抛、永不阻塞 boot。
缺 DSN / 坏 DSN / GlitchTip 宕 / 注册失败 / 网络超时 —— 一律只记 warning。
"""
import logging
import time

import pytest

import zlx_ops_sdk


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 每个用例从干净 env 起步,避免外部 SENTRY_DSN / 开关污染
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("ZLX_OPS_DISABLED", raising=False)
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("RELEASE", raising=False)


def _patch_sentry(monkeypatch, init_fn):
    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", init_fn)
    # set_tag 可能在 init 成功后被调,给个 no-op 兜底
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)


# --- happy path -------------------------------------------------------------

def test_happy_path_inits_with_release(monkeypatch):
    calls = {}

    def fake_init(*args, **kwargs):
        calls.update(kwargs)

    _patch_sentry(monkeypatch, fake_init)

    res = zlx_ops_sdk.init(
        "my-service",
        dsn="https://pub@glitchtip.example/1",
        release="abc123",
        repo="zj1123581321/my-service",
    )

    assert res.enabled is True
    assert calls["dsn"] == "https://pub@glitchtip.example/1"
    # release 带 repo 上下文(git SHA tag 形态)
    assert "abc123" in calls["release"]


def test_dsn_read_from_env(monkeypatch):
    seen = {}
    _patch_sentry(monkeypatch, lambda *a, **k: seen.update(k))
    monkeypatch.setenv("SENTRY_DSN", "https://pub@host/9")

    res = zlx_ops_sdk.init("svc")

    assert res.enabled is True
    assert seen["dsn"] == "https://pub@host/9"


# --- fail-open paths --------------------------------------------------------

def test_missing_dsn_is_noop_app_continues(monkeypatch, caplog):
    _patch_sentry(monkeypatch, lambda *a, **k: pytest.fail("不该被调用"))

    with caplog.at_level(logging.WARNING, logger="zlx_ops_sdk"):
        res = zlx_ops_sdk.init("svc")  # 无 DSN

    assert res.enabled is False
    assert res.reason == "no_dsn"
    assert any("DSN" in r.message or "dsn" in r.message.lower() for r in caplog.records)


def test_glitchtip_unreachable_init_raises_app_continues(monkeypatch, caplog):
    def boom(*a, **k):
        raise ConnectionError("glitchtip down")

    _patch_sentry(monkeypatch, boom)

    with caplog.at_level(logging.WARNING, logger="zlx_ops_sdk"):
        res = zlx_ops_sdk.init("svc", dsn="https://pub@host/1")

    # 关键:不抛,应用照常启动
    assert res.enabled is False
    assert res.reason == "init_error"
    assert caplog.records, "init 失败必须留 warning"


def test_kill_switch_env_makes_init_noop(monkeypatch):
    monkeypatch.setenv("ZLX_OPS_DISABLED", "1")
    _patch_sentry(monkeypatch, lambda *a, **k: pytest.fail("kill switch 下不该 init"))

    res = zlx_ops_sdk.init("svc", dsn="https://pub@host/1")

    assert res.enabled is False
    assert res.reason == "disabled"


def test_init_timeout_is_bounded(monkeypatch, caplog):
    def hang(*a, **k):
        time.sleep(30)  # 模拟 init 挂起

    _patch_sentry(monkeypatch, hang)

    start = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="zlx_ops_sdk"):
        res = zlx_ops_sdk.init("svc", dsn="https://pub@host/1", timeout=0.3)
    elapsed = time.monotonic() - start

    # boot 不被挂起:有界返回
    assert elapsed < 5
    assert res.enabled is False
    assert res.reason == "timeout"


def test_init_never_raises_on_garbage(monkeypatch):
    # 即便传入完全离谱的参数,也只能 fail-open,不能把异常抛给被观测者
    _patch_sentry(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    res = zlx_ops_sdk.init("svc", dsn=object())  # type: ignore[arg-type]
    assert res.enabled is False
