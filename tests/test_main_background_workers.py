import app.main as main_module


def test_background_workers_skip_stateful_jobs_in_json_store_mode(monkeypatch):
    monkeypatch.setenv("USE_JSON_STORES", "true")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "memory")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    assert main_module._background_worker_names() == ["retention"]


def test_background_workers_enable_redis_and_rotation_when_configured(monkeypatch):
    monkeypatch.setenv("USE_JSON_STORES", "false")
    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    assert main_module._background_worker_names() == [
        "retention",
        "rotation",
        "revocation",
        "budget_cleanup",
    ]
