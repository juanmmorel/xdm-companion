import json
import os
from pathlib import Path
import subprocess
import sys
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import ytdlp_worker


class FakeYtDlp:
  def __init__(self, info=None, error=None, authenticated=True, captures=None):
    self.info = info
    self.error = error
    self.authenticated = authenticated
    self.captures = captures if captures is not None else {}

  def YoutubeDL(self, options):
    self.captures["options"] = options
    owner = self

    class FakeDownloader:
      def __enter__(self):
        return self

      def __exit__(self, *_args):
        return False

      def get_info_extractor(self, extractor_key):
        owner.captures["extractor_key"] = extractor_key

        class FakeExtractor:
          is_authenticated = owner.authenticated

          def initialize(self):
            owner.captures["initialized"] = True

        return FakeExtractor()

      def extract_info(self, url, download=False):
        owner.captures["url"] = url
        owner.captures["download"] = download
        if owner.error:
          raise owner.error
        return owner.info

    return FakeDownloader()


def test_video_worker_preserves_resolution_audio_auth_and_request_headers():
  captures = {}
  info = {
      "title": "Audio original",
      "availability": "public",
      "formats": [
          {
              "format_id": "2160-video",
              "url": "https://media.example/2160",
              "manifest_url": "https://media.example/video.mpd",
              "protocol": "http_dash_segments",
              "mime_type": "application/dash+xml",
              "height": 2160,
              "fps": 60,
              "vcodec": "av01",
              "acodec": "none",
              "audio_track_is_default": False,
              "filesize_approx": 123456,
              "http_headers": {"Referer": "https://www.youtube.com/watch?v=abc"},
              "unused_large_field": "discard me",
          },
          {
              "format_id": "audio-original",
              "url": "https://media.example/audio",
              "protocol": "https",
              "ext": "m4a",
              "vcodec": "none",
              "acodec": "mp4a.40.2",
              "language": "es",
              "language_preference": 10,
              "audio_track_id": "es.1",
              "audio_track_name": "Original",
              "audio_track": {"name": "Original", "displayName": "Español"},
              "audio_is_default": True,
          },
      ],
      "thumbnails": [{"url": "https://media.example/large-thumbnail"}],
      "description": "Large metadata that the picker does not consume",
  }
  command = {
      "operation": "video",
      "url": "https://www.youtube.com/watch?v=abc123def45",
      "options": {
          "skip_download": True,
          "http_headers": {"User-Agent": "browser-agent", "Accept-Language": "es"},
          "cookiefile": "/tmp/private-cookie-jar.txt",
      },
      "extractor_key": "Youtube",
      "check_authenticated_session": True,
  }

  result = ytdlp_worker.execute_request(
      command,
      ytdlp_module=FakeYtDlp(info=info, authenticated=True, captures=captures),
  )

  assert result["ok"] is True
  assert result["authenticated"] is True
  assert result["info"]["formats"][0]["height"] == 2160
  assert result["info"]["formats"][0]["manifest_url"].endswith("video.mpd")
  assert result["info"]["formats"][1]["audio_track"]["name"] == "Original"
  assert result["info"]["formats"][1]["audio_track_id"] == "es.1"
  assert "thumbnails" not in result["info"]
  assert "unused_large_field" not in result["info"]["formats"][0]
  assert captures["initialized"] is True
  assert captures["download"] is False
  assert captures["options"]["cookiefile"] == command["options"]["cookiefile"]
  assert captures["options"]["http_headers"]["Accept-Language"] == "es"


def test_playlist_worker_returns_only_the_flat_entries_needed_by_companion():
  captures = {}
  info = {
      "_type": "playlist",
      "title": "Lista",
      "playlist_count": 25,
      "entries": [
          {"id": "Video001", "title": "Uno", "url": "Video001", "duration": 900},
          {"id": "Video002", "title": "Dos", "webpage_url": "https://www.youtube.com/watch?v=Video002"},
          {"id": "Video003", "title": "Tres", "original_url": "https://www.youtube.com/watch?v=Video003"},
          {"id": "Video004", "title": "No se pide", "url": "Video004"},
      ],
      "description": "not needed",
  }
  command = {
      "operation": "playlist",
      "url": "https://www.youtube.com/playlist?list=PLexample",
      "options": {"extract_flat": "in_playlist", "playlistend": 3},
      "extractor_key": "Youtube",
      "max_items": 2,
  }

  result = ytdlp_worker.execute_request(
      command,
      ytdlp_module=FakeYtDlp(info=info, captures=captures),
  )

  assert result["ok"] is True
  assert result["info"]["_type"] == "playlist"
  assert result["info"]["playlist_count"] == 25
  assert [item["title"] for item in result["info"]["entries"]] == ["Uno", "Dos", "Tres"]
  assert set(result["info"]["entries"][0]) == {"id", "title", "url"}
  assert captures["download"] is False


def test_worker_returns_authentication_and_diagnostic_flags_on_extraction_error():
  class FailingYtDlp(FakeYtDlp):
    def YoutubeDL(self, options):
      owner = self

      class FakeDownloader:
        def __enter__(self):
          options["logger"].warning("YouTube PO token is required for this player client")
          return self

        def __exit__(self, *_args):
          return False

        def get_info_extractor(self, _extractor_key):
          class FakeExtractor:
            is_authenticated = True

            def initialize(self):
              pass

          return FakeExtractor()

        def extract_info(self, _url, download=False):
          raise owner.error

      return FakeDownloader()

  result = ytdlp_worker.execute_request(
      {
          "operation": "video",
          "url": "https://www.youtube.com/watch?v=abc123def45",
          "options": {},
          "extractor_key": "Youtube",
          "check_authenticated_session": True,
      },
      ytdlp_module=FailingYtDlp(error=RuntimeError("members-only")),
  )

  assert result["ok"] is False
  assert result["authenticated"] is True
  assert result["error"] == "members-only"
  assert "po_token_required" in result["flags"]


def test_subprocess_invoker_uses_json_pipe_and_discards_worker_stderr(monkeypatch):
  captured = {}
  response = {"ok": True, "info": {"title": "Test"}, "flags": []}

  def fake_run(**kwargs):
    captured.update(kwargs)
    kwargs["stdout"].write(json.dumps(response).encode("utf-8"))
    return CompletedProcess(kwargs["args"], 0)

  monkeypatch.setattr(ytdlp_worker.subprocess, "run", fake_run)
  command = {"operation": "video", "url": "https://example.test/video", "options": {"skip_download": True}}

  result = ytdlp_worker.invoke_extractor_worker(command)

  assert result == response
  assert captured["args"][0] == sys.executable
  assert captured["args"][1].endswith("ytdlp_worker.py")
  assert json.loads(captured["input"]) == command
  assert captured["stderr"] == ytdlp_worker.subprocess.DEVNULL
  assert captured["stdout"].closed


def test_worker_invoker_runs_the_real_json_protocol_without_downloading():
  with pytest.raises(ytdlp_worker.YtdlpWorkerError, match="Operación de extracción no válida"):
    ytdlp_worker.invoke_extractor_worker({"operation": "invalid", "options": {}})


def test_service_import_does_not_load_ytdlp_into_the_long_lived_parent():
  core_path = Path(__file__).resolve().parents[1] / "core"
  script = (
      "import sys; "
      f"sys.path.insert(0, {str(core_path)!r}); "
      "import main; "
      "print('yt_dlp' in sys.modules)"
  )
  completed = subprocess.run(
      [sys.executable, "-B", "-c", script],
      check=True,
      capture_output=True,
      text=True,
      timeout=30,
  )

  assert completed.stdout.strip() == "False"


def test_worker_output_size_is_checked_before_reading(monkeypatch):
  monkeypatch.setattr(ytdlp_worker, "_MAX_WORKER_OUTPUT_BYTES", 4)

  def oversized_run(**kwargs):
    kwargs["stdout"].write(b"12345")
    return CompletedProcess(kwargs["args"], 0)

  monkeypatch.setattr(ytdlp_worker.subprocess, "run", oversized_run)

  with pytest.raises(ytdlp_worker.YtdlpWorkerError, match="más información de la permitida"):
    ytdlp_worker.invoke_extractor_worker({"operation": "video", "options": {}})
