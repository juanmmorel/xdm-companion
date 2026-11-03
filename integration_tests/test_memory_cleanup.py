import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import main


def test_trim_unused_memory_collects_python_and_glibc_allocations(monkeypatch):
  calls = []
  monkeypatch.setattr(main.gc, "collect", lambda: calls.append("gc"))
  monkeypatch.setattr(main, "_malloc_trim", lambda padding: calls.append(padding))

  main._trim_unused_memory()

  assert calls == ["gc", 0]


def test_request_memory_cleanup_runs_only_after_extraction(monkeypatch):
  calls = []
  monkeypatch.setattr(main, "_trim_unused_memory", lambda: calls.append("trim"))

  with main.app.test_request_context("/health"):
    pass
  assert calls == []

  with main.app.test_request_context("/formats"):
    main.g._xdm_ytdlp_extraction_used = True
  assert calls == ["trim"]


def test_request_payload_does_not_cache_player_json():
  payload = {"url": "https://www.youtube.com/watch?v=abc123def45", "player": {"formats": [1, 2]}}
  with main.app.test_request_context("/formats", method="POST", json=payload) as context:
    assert main._read_request_payload() == payload
    assert context.request._cached_json == (Ellipsis, Ellipsis)
    assert not hasattr(context.request, "_cached_data")
