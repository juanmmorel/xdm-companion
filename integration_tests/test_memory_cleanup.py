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
