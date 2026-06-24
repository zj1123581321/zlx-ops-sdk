"""T5(GlitchTip 6.2 适配)— @monitor heartbeat URL 死人开关。

GlitchTip 6.2 把 Sentry 的 check_in envelope 列为 IgnoredItemType(收下但丢弃),
其 cron 死人开关走 Heartbeat 监控:
  POST {DOMAIN}/api/0/organizations/{org}/heartbeat_check/{endpoint_id}/
语义 = ping-on-alive:
  任务成功 → ping(标记"活着")
  任务抛/漏启动 → 不 ping → GlitchTip 端因"该来没来"标记 down 并告警
  heartbeat 端点宕 → 任务仍正常跑/正常抛(fail-open)
"""
import logging

import pytest

import zlx_ops_sdk


@pytest.fixture(autouse=True)
def _no_sentry_checkin(monkeypatch):
    # 把 sentry capture_checkin 变 no-op,隔离出 heartbeat 行为单独测
    import sentry_sdk.crons

    monkeypatch.setattr(sentry_sdk.crons, "capture_checkin", lambda **k: "id")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ZLX_HEARTBEAT_URL", raising=False)


def _patch_ping(monkeypatch, fail=False):
    pings = []

    def fake_urlopen(req, timeout=None):
        pings.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout})
        if fail:
            raise ConnectionError("heartbeat endpoint down")

        class _Resp:
            def read(self):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return pings


URL = "http://gt/api/0/organizations/personal/heartbeat_check/abc/"


def test_success_pings_heartbeat(monkeypatch):
    pings = _patch_ping(monkeypatch)

    @zlx_ops_sdk.monitor(monitor_slug="job", heartbeat_url=URL)
    def job():
        return "ok"

    assert job() == "ok"
    assert len(pings) == 1
    assert pings[0]["url"] == URL
    assert pings[0]["method"] == "POST"
    assert pings[0]["timeout"] is not None  # 有界,不挂起


def test_failure_does_not_ping_and_reraises(monkeypatch):
    pings = _patch_ping(monkeypatch)

    @zlx_ops_sdk.monitor(monitor_slug="job", heartbeat_url=URL)
    def job():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        job()
    # dead-man 语义:失败不 ping,让 GlitchTip 因"漏到"告警
    assert pings == []


def test_heartbeat_endpoint_down_task_still_runs(monkeypatch, caplog):
    _patch_ping(monkeypatch, fail=True)

    @zlx_ops_sdk.monitor(monitor_slug="job", heartbeat_url=URL)
    def job():
        return "result"

    with caplog.at_level(logging.WARNING, logger="zlx_ops_sdk"):
        assert job() == "result"  # 端点宕,任务照常完成
    assert any("heartbeat" in r.message.lower() for r in caplog.records)


def test_heartbeat_url_from_env(monkeypatch):
    pings = _patch_ping(monkeypatch)
    monkeypatch.setenv("ZLX_HEARTBEAT_URL", URL)

    @zlx_ops_sdk.monitor(monitor_slug="job")
    def job():
        return 1

    job()
    assert len(pings) == 1 and pings[0]["url"] == URL


def test_no_heartbeat_url_is_noop(monkeypatch):
    pings = _patch_ping(monkeypatch)

    @zlx_ops_sdk.monitor(monitor_slug="job")  # 无 url 无 env
    def job():
        return 1

    assert job() == 1
    assert pings == []  # 没配 heartbeat 就不 ping,不报错
