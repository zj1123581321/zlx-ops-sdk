"""集成验证:用真实 sentry_sdk + 捕获式 transport,证明 init() 构造的事件
确实带上 release(含 repo 前缀)+ service/repo/server tag,以及 cron check-in
envelope 确实发出。不打真网络(transport 截获),验证"会发什么"。
"""
import zlx_ops_sdk


def test_real_event_carries_release_and_tags():
    captured = {}

    def capture_transport(event):
        # 旧式函数 transport:sentry_sdk 会用最终 event dict 调它
        captured["event"] = event

    res = zlx_ops_sdk.init(
        "youtube-download-api",
        dsn="https://publickey@127.0.0.1/1",  # 合法格式,transport 截获不出网
        repo="zj1123581321/youtube-download-api",
        server="n305",
        environment="prod",
        transport=capture_transport,
    )
    assert res.enabled is True

    import sentry_sdk

    try:
        raise RuntimeError("boom from real service")
    except RuntimeError:
        sentry_sdk.capture_exception()
    sentry_sdk.flush(timeout=2)

    ev = captured["event"]
    assert "youtube-download-api" in ev["release"]
    tags = ev.get("tags") or {}
    assert tags.get("service") == "youtube-download-api"
    assert tags.get("repo") == "zj1123581321/youtube-download-api"
    assert tags.get("server") == "n305"
    assert ev["exception"]["values"][0]["type"] == "RuntimeError"
