import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import queue_poller


def test_poll_once_returns_false_when_not_configured(monkeypatch):
    monkeypatch.setattr(queue_poller, "QUEUE_WORKER_URL", "")
    monkeypatch.setattr(queue_poller, "QUEUE_SHARED_SECRET", "")
    assert queue_poller.poll_once() is False


def test_poll_once_returns_false_when_nothing_pending(monkeypatch):
    monkeypatch.setattr(queue_poller, "QUEUE_WORKER_URL", "https://queue.example")
    monkeypatch.setattr(queue_poller, "QUEUE_SHARED_SECRET", "secret")

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"pending": False}
    fake_resp.raise_for_status.return_value = None

    with patch("queue_poller.requests.get", return_value=fake_resp) as mock_get:
        result = queue_poller.poll_once()

    assert result is False
    assert mock_get.call_args.kwargs["headers"]["X-Queue-Secret"] == "secret"


def test_poll_once_returns_false_when_worker_unreachable(monkeypatch):
    monkeypatch.setattr(queue_poller, "QUEUE_WORKER_URL", "https://queue.example")
    monkeypatch.setattr(queue_poller, "QUEUE_SHARED_SECRET", "secret")

    with patch("queue_poller.requests.get", side_effect=Exception("network error")):
        result = queue_poller.poll_once()

    assert result is False


def test_poll_once_processes_pending_request_and_reports_done(monkeypatch):
    monkeypatch.setattr(queue_poller, "QUEUE_WORKER_URL", "https://queue.example")
    monkeypatch.setattr(queue_poller, "QUEUE_SHARED_SECRET", "secret")

    fake_get_resp = MagicMock()
    fake_get_resp.json.return_value = {"pending": True, "requested_at": "2026-07-16T10:00:00Z"}
    fake_get_resp.raise_for_status.return_value = None

    with patch("queue_poller.requests.get", return_value=fake_get_resp), \
         patch("queue_poller.requests.post") as mock_post, \
         patch("queue_poller.run_batch", return_value={"created": 3, "ids": [1, 2, 3]}):
        result = queue_poller.poll_once()

    assert result is True
    mock_post.assert_called_once()
    call = mock_post.call_args
    assert call.args[0] == "https://queue.example/api/complete"
    assert call.kwargs["headers"]["X-Queue-Secret"] == "secret"
    sent = call.kwargs["json"]
    assert sent["status"] == "done"
    assert sent["requested_at"] == "2026-07-16T10:00:00Z"
    assert "3 posts" in sent["result_summary"]


def test_poll_once_reports_error_when_pipeline_raises(monkeypatch):
    monkeypatch.setattr(queue_poller, "QUEUE_WORKER_URL", "https://queue.example")
    monkeypatch.setattr(queue_poller, "QUEUE_SHARED_SECRET", "secret")

    fake_get_resp = MagicMock()
    fake_get_resp.json.return_value = {"pending": True, "requested_at": "2026-07-16T10:00:00Z"}
    fake_get_resp.raise_for_status.return_value = None

    with patch("queue_poller.requests.get", return_value=fake_get_resp), \
         patch("queue_poller.requests.post") as mock_post, \
         patch("queue_poller.run_batch", side_effect=RuntimeError("ollama down")):
        result = queue_poller.poll_once()

    assert result is True
    sent = mock_post.call_args.kwargs["json"]
    assert sent["status"] == "error"
    assert "ollama down" in sent["result_summary"]
