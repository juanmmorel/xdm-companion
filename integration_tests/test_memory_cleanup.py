import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import main
import providers.youtube as youtube_provider


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


def test_youtube_drops_raw_player_payload_before_extractor_fallback(monkeypatch):
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  payload = {
      "url": page_url,
      "user_agent": "Test browser",
      "cookies": [],
      "youtube_playback": {
          "video_id": "dQw4w9WgXcQ",
          "title": "Memory test",
          "source_heights": [1080, 360],
          "formats": [{
              "itag": 18,
              "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=18",
              "mime_type": 'video/mp4; codecs="avc1.64001F, mp4a.40.2"',
              "width": 640,
              "height": 360,
              "fps": 30,
              "audio_track": {
                  "id": "en.1",
                  "name": "English (Original)",
                  "is_default": True,
              },
              "drm": False,
          }],
      },
  }
  extractor_observations = []

  def extract_info(*_args):
    extractor_observations.append("youtube_playback" not in payload)
    return {
        "title": "Memory test",
        "formats": [{
            "format_id": "extractor-1080",
            "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=137",
            "height": 1080,
            "vcodec": "avc1",
            "acodec": "mp4a.40.2",
            "language": "en",
            "language_preference": 10,
            "audio_is_default": True,
            "ext": "mp4",
            "protocol": "https",
            "tbr": 4000,
        }],
        "_xdm_companion_diagnostics": {"yt_dlp_authenticated": True, "flags": []},
    }

  monkeypatch.setattr(main, "_read_request_payload", lambda: payload)
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", extract_info)
  with main._format_sessions_lock:
    main._format_sessions.clear()

  response = main.app.test_client().post(
      "/formats",
      headers={"Origin": "chrome-extension://" + "a" * 32},
      json=payload,
  )

  assert response.status_code == 200
  assert extractor_observations == [True]
  assert response.get_json()["formats"][0]["height"] == 1080
