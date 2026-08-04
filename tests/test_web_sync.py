"""Web API 同步端点轻量测试。"""

import pytest
from fastapi.testclient import TestClient

from xshare.data.sync_config import init_sync_config
from xshare.web_server import app


@pytest.fixture
def client(db_conn):
    init_sync_config()
    return TestClient(app)


def test_sync_coverage_endpoint(client):
    r = client.get("/api/sync/coverage?lookback_trading_days=10")
    assert r.status_code == 200
    data = r.json()
    assert "stock" in data and "index" in data and "fund" in data
    assert data["stock"]["target_days"] == 10


def test_sync_history_with_query(client):
    from xshare.data.task_queue import enqueue

    enqueue("news")
    r = client.get("/api/sync/history?job=news&limit=5")
    assert r.status_code == 200
    assert "history" in r.json()


def test_sync_enqueue_with_body(client):
    r = client.post(
        "/api/sync/jobs/daily/enqueue",
        json={"days": 30, "backfill": True},
    )
    assert r.status_code == 200
    data = r.json()
    assert "queued" in data


def test_sync_task_cancel(client):
    from xshare.data.task_queue import enqueue

    task_id = enqueue("daily")
    r = client.post(f"/api/sync/tasks/{task_id}/cancel")
    assert r.status_code == 200
    assert "已取消" in r.json().get("message", "")
