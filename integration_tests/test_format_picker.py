import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import main
import providers.youtube as youtube_provider


def test_youtube_lists_high_resolution_direct_video_streams_and_labels_missing_audio():
  info = {
      "title": "Test video",
      "formats": [
          {
              "format_id": "1080-dash-video-only",
              "url": "https://manifest.googlevideo.com/video.mpd",
              "manifest_url": "https://manifest.googlevideo.com/video.mpd",
              "height": 1080,
              "vcodec": "avc1",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "http_dash_segments",
              "tbr": 5000,
          },
          {
              "format_id": "2160-direct-video-only",
              "url": "https://cdn.example/2160",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/2160",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "https",
              "tbr": 9000,
          },
          {
              "format_id": "1440-direct-video-only",
              "url": "https://cdn.example/1440",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/1440",
              "height": 1440,
              "vcodec": "vp9",
              "acodec": "none",
              "ext": "webm",
              "protocol": "https",
              "tbr": 7000,
          },
          {
              "format_id": "720",
              "url": "https://cdn.example/720",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "ext": "mp4",
              "fps": 30,
              "tbr": 2500,
              "language": "en",
              "audio_is_default": True,
          },
          {
              "format_id": "480-webm",
              "url": "https://cdn.example/480.webm",
              "height": 480,
              "vcodec": "vp9",
              "acodec": "opus",
              "ext": "webm",
              "tbr": 900,
          },
          {
              "format_id": "480-mp4",
              "url": "https://cdn.example/480.mp4",
              "height": 480,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "ext": "mp4",
              "tbr": 1200,
          },
      ],
  }

  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert [option["height"] for option, _media in options] == [2160, 1440, 1080, 720]
  assert [option["format_id"] for option, _media in options] == [
      "2160-direct-video-only", "1440-direct-video-only", "1080-dash-video-only", "720",
  ]
  assert options[0][1]["url"] == "https://manifest.googlevideo.com/api/manifest/dash/id/2160"
  assert options[0][0]["label"] == "2160p"
  assert options[1][1]["url"] == "https://manifest.googlevideo.com/api/manifest/dash/id/1440"
  assert options[1][0]["label"] == "1440p"
  assert options[2][1]["url"] == "https://manifest.googlevideo.com/video.mpd"
  assert options[2][1]["protocol"] == "http_dash_segments"
  assert options[2][1]["mime_type"] == "application/dash+xml"
  assert options[2][0]["label"] == "1080p"


def test_sd_only_source_is_ranked_by_actual_resolution_without_fixed_cutoff():
  info = {
      "formats": [
          {"format_id": "480", "url": "https://cdn.example/480.mp4", "height": 480,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "tbr": 1200,
           "language": "en", "audio_is_default": True},
          {"format_id": "360", "url": "https://cdn.example/360.mp4", "height": 360,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "tbr": 800,
           "language": "en", "audio_is_default": True},
          {"format_id": "240", "url": "https://cdn.example/240.mp4", "height": 240,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4", "tbr": 400,
           "language": "en", "audio_is_default": True},
      ],
  }

  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert [option["height"] for option, _media in options] == [480, 360, 240]


def test_youtube_video_only_4k_is_listed_when_it_has_a_dash_manifest():
  info = {
      "title": "4K video",
      "formats": [
          {
              "format_id": "313",
              "url": "https://cdn.example/video-2160-only",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/video/source/youtube",
              "height": 2160,
              "vcodec": "vp9",
              "acodec": "none",
              "ext": "webm",
              "protocol": "https",
              "tbr": 14000,
          },
          {
              "format_id": "140",
              "url": "https://cdn.example/audio-only",
              "height": 0,
              "vcodec": "none",
              "acodec": "opus",
              "ext": "webm",
              "protocol": "https",
          },
      ],
  }

  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert [option["height"] for option, _media in options] == [2160]
  option, media = options[0]
  assert option["label"] == "2160p"
  assert media["url"] == info["formats"][0]["manifest_url"]
  assert media["protocol"] == "http_dash_segments"
  assert media["mime_type"] == "application/dash+xml"


def test_muxed_format_keeps_its_language_specific_url_instead_of_generic_manifest():
  info = {
      "title": "Video en español",
      "formats": [
          {
              "format_id": "720-es",
              "url": "https://cdn.example/muxed-es.mp4",
              "manifest_url": "https://manifest.example/all-audio-languages.mpd",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "es",
              "audio_is_default": True,
              "ext": "mp4",
              "protocol": "https",
          },
          {
              "format_id": "720-en",
              "url": "https://cdn.example/muxed-en.mp4",
              "manifest_url": "https://manifest.example/all-audio-languages.mpd",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "en",
              "audio_is_default": False,
              "ext": "mp4",
              "protocol": "https",
          },
      ],
  }

  options = main._available_formats(
      info,
      "https://youtube.com/watch?v=test",
      [],
      "test-agent",
      {"language": "es-419", "id": "es.2", "is_original": True},
      "es-DO,es,en-US",
  )

  assert len(options) == 1
  option, media = options[0]
  assert option["format_id"] == "720-es"
  assert option["audio_language"] == "es"
  assert media["url"] == "https://cdn.example/muxed-es.mp4"
  assert media["headers"]["Accept-Language"] == ["es-419"]


def test_original_audio_overrides_browser_language_and_selected_auto_dub():
  info = {
      "title": "Video con pistas múltiples",
      "formats": [
          {
              "format_id": "720-en-high-bitrate",
              "url": "https://cdn.example/muxed-en.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "en",
              "audio_is_default": True,
              "ext": "mp4",
              "tbr": 6000,
          },
          {
              "format_id": "720-es-lower-bitrate",
              "url": "https://cdn.example/muxed-es.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "es",
              "audio_is_default": False,
              "ext": "mp4",
              "tbr": 1800,
          },
      ],
  }

  options = main._available_formats(
      info,
      "https://www.youtube.com/watch?v=multiple-audio",
      [],
      "test-agent",
      {"language": "en", "id": "en.1", "name": "English (Original)", "is_original": True},
      "en-US,en,es-DO",
  )

  assert len(options) == 1
  option, media = options[0]
  assert option["format_id"] == "720-en-high-bitrate"
  assert media["url"] == "https://cdn.example/muxed-en.mp4"


def test_original_audio_mode_excludes_muxed_formats_with_unknown_language():
  info = {
      "formats": [{
          "format_id": "720-unknown-audio",
          "url": "https://cdn.example/muxed-unknown.mp4",
          "height": 720,
          "vcodec": "avc1",
          "acodec": "mp4a",
          "ext": "mp4",
      }],
  }

  options = main._available_formats(
      info,
      "https://www.youtube.com/watch?v=unknown-audio",
      [],
      "test-agent",
      {"language": "es-419", "id": "es.2", "name": "Español", "is_original": True},
      "en-US",
  )

  assert options == []


def test_extractor_original_track_metadata_recovers_formats_when_browser_track_is_missing():
  info = {
      "title": "Video con idioma original informado por el extractor",
      "formats": [
          {
              "format_id": "2160-video-only",
              "url": "https://rr1.googlevideo.com/videoplayback/video-only",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/test",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
              "protocol": "https",
          },
          {
              "format_id": "720-en-original",
              "url": "https://rr1.googlevideo.com/videoplayback/audio=en",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a.40.2",
              "language": "en",
              "language_preference": 10,
              "protocol": "https",
          },
          {
              "format_id": "720-es-dub",
              "url": "https://rr1.googlevideo.com/videoplayback/audio=es",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a.40.2",
              "language": "es",
              "language_preference": -1,
              "protocol": "https",
          },
      ],
  }

  options = main._available_formats(
      info,
      "https://www.youtube.com/watch?v=extractor-audio-fallback",
      [],
      "test-agent",
      audio_track=None,
      accept_language="es-DO,es",
  )

  assert [option["height"] for option, _media in options] == [2160, 720]
  assert options[0][1]["audio_language"] == "en"
  assert options[1][0]["format_id"] == "720-en-original"


def test_browser_audio_language_is_validated_before_being_used_as_a_header():
  page_url, _cookies, _agent, _title, audio_track, accept_language = main._safe_browser_data({
      "url": "https://youtube.com/watch?v=test",
      "audio_track": {"language": "es-419", "id": "es.2", "name": "Español"},
      "accept_language": "es-DO, *;q=0.9,../../bad, en-US;q=0.8",
  })

  assert page_url == "https://youtube.com/watch?v=test"
  assert audio_track == {
      "language": "es-419", "id": "es.2", "name": "Español", "is_original": False,
  }
  assert accept_language == "es-DO,en-US"


def test_drm_formats_remain_excluded():
  info = {
      "formats": [
          {
              "format_id": "2160-drm",
              "url": "https://manifest.googlevideo.com/api/manifest/dash/id/drm",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/drm",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "http_dash_segments",
              "has_drm": True,
          },
          {
              "format_id": "720-clear",
              "url": "https://cdn.example/720.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "en",
              "audio_is_default": True,
              "ext": "mp4",
              "protocol": "https",
          },
      ],
  }

  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert [option["height"] for option, _media in options] == [720]


def test_formats_marked_maybe_drm_remain_available_for_a_normal_xdm_attempt():
  info = {
      "formats": [
          {
              "format_id": "2160-maybe",
              "url": "https://manifest.googlevideo.com/api/manifest/dash/id/maybe",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/maybe",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "http_dash_segments",
              "has_drm": "maybe",
          },
      ],
  }

  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert [option["height"] for option, _media in options] == [2160]


def test_membership_extraction_error_explains_which_chrome_account_to_use():
  message_without_session = youtube_provider._youtube_extraction_error(
      RuntimeError("Join this channel to get access to members-only content"),
      [],
  )
  member_cookie = {
      "name": "SAPISID", "value": "account-session", "domain": ".youtube.com",
      "hostOnly": False, "path": "/", "secure": True,
  }
  login_info_cookie = {
      "name": "LOGIN_INFO", "value": "account-state", "domain": ".youtube.com",
      "hostOnly": False, "path": "/", "secure": True,
  }
  message_with_session = youtube_provider._youtube_extraction_error(
      RuntimeError("This video is members-only"),
      [member_cookie, login_info_cookie],
  )

  assert "Inicia sesión en el navegador" in message_without_session
  assert "membresía activa" in message_with_session


def test_membership_error_identifies_when_ytdlp_requires_playback_validation():
  message = youtube_provider._youtube_extraction_error(
      RuntimeError("Join this channel to get access to members-only content"),
      [],
      yt_dlp_authenticated=True,
      diagnostics={"po_token_required"},
  )

  assert "sesión de YouTube" in message
  assert "validación de reproducción" in message
  assert "descifrado DRM" in message


def test_ytdlp_log_collector_records_only_safe_diagnostic_categories():
  collector = main._YtDlpLogCollector(youtube_provider)
  collector.warning("web client https formats require a GVS PO Token which was not provided")
  collector.warning("Some formats have been skipped as they are DRM protected")

  assert collector.flags == {"po_token_required", "drm_formats_filtered"}


def test_ytdlp_recognizes_account_cookies_from_the_temporary_cookie_file():
  cookie_file = main._write_cookie_file(
      "https://www.youtube.com/watch?v=test",
      [
          {"name": "SAPISID", "value": "synthetic", "domain": ".youtube.com",
           "hostOnly": False, "path": "/", "secure": True, "httpOnly": True,
           "session": True},
          {"name": "LOGIN_INFO", "value": "synthetic", "domain": ".youtube.com",
           "hostOnly": False, "path": "/", "secure": True, "httpOnly": True,
           "session": True},
      ],
  )
  assert cookie_file
  try:
    with main.yt_dlp.YoutubeDL({"cookiefile": cookie_file, "quiet": True}) as downloader:
      extractor = downloader.get_info_extractor("Youtube")
      extractor.initialize()
      assert extractor.is_authenticated is True
  finally:
    os.unlink(cookie_file)


def test_youtu_be_cookie_export_keeps_youtube_account_cookies():
  cookie_file = main._write_cookie_file(
      "https://youtu.be/example",
      [
          {"name": "SAPISID", "value": "synthetic", "domain": ".youtube.com",
           "hostOnly": False, "path": "/", "secure": True, "session": True},
          {"name": "LOGIN_INFO", "value": "synthetic", "domain": ".youtube.com",
           "hostOnly": False, "path": "/", "secure": True, "session": True},
      ],
  )
  assert cookie_file
  try:
    contents = Path(cookie_file).read_text(encoding="utf-8")
    with main.yt_dlp.YoutubeDL({"cookiefile": cookie_file, "quiet": True}) as downloader:
      extractor = downloader.get_info_extractor("Youtube")
      extractor.initialize()
      assert extractor.is_authenticated is True
    assert "SAPISID" in contents
    assert "LOGIN_INFO" in contents
  finally:
    os.unlink(cookie_file)


def test_youtube_extraction_uses_account_cookies_and_requests_hls_quality(monkeypatch):
  captured = {}
  cookies_seen_by_extractor = []

  class FakeYoutubeDL:
    def __init__(self, options):
      captured.update(options)

    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def get_info_extractor(self, _name):
      class FakeExtractor:
        is_authenticated = True

        def initialize(self):
          pass

      return FakeExtractor()

    def extract_info(self, url, download=False):
      assert url == "https://www.youtube.com/watch?v=members-video"
      assert download is False
      cookies_seen_by_extractor.append(Path(captured["cookiefile"]).read_text(encoding="utf-8"))
      return {"title": "Members video", "formats": []}

  monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
  monkeypatch.setattr(main, "_js_runtimes", lambda: {"node": {"path": "/usr/bin/node"}})
  info = main._extract_video_info(
      "https://www.youtube.com/watch?v=members-video",
      [
          {
              "name": "SAPISID", "value": "account-session", "domain": ".youtube.com",
              "hostOnly": False, "path": "/", "secure": True, "httpOnly": True,
              "session": True,
          },
          {
              "name": "LOGIN_INFO", "value": "account-state", "domain": ".youtube.com",
              "hostOnly": False, "path": "/", "secure": True, "httpOnly": True,
              "session": True,
          },
      ],
      "Chrome test agent",
      {"language": "es-419", "id": "es.2", "is_original": True},
      "es-DO,es",
  )

  assert info["title"] == "Members video"
  assert captured["cookiefile"]
  assert "account-session" in cookies_seen_by_extractor[0]
  assert captured["http_headers"]["User-Agent"] == "Chrome test agent"
  assert captured["http_headers"]["Accept-Language"] == "es-419"
  assert "format" not in captured, "format discovery must not preselect a muxed stream"
  assert captured["no_warnings"] is False
  assert captured["verbose"] is True, "capture safe diagnostic categories for YouTube"
  # yt-dlp expands "default" for the authenticated session and keeps its
  # cookie-aware Safari client for additional pre-muxed HLS formats.
  assert captured["extractor_args"]["youtube"]["player_client"] == ["default", "web_safari"]


def test_public_youtube_can_still_request_pre_mixed_hls_client(monkeypatch):
  captured = {}

  class FakeYoutubeDL:
    def __init__(self, options):
      captured.update(options)

    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def get_info_extractor(self, _name):
      class FakeExtractor:
        is_authenticated = False

        def initialize(self):
          pass

      return FakeExtractor()

    def extract_info(self, _url, download=False):
      return {"title": "Public video", "formats": []}

  monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
  main._extract_video_info("https://www.youtube.com/watch?v=public", [], "test-agent")

  assert captured["extractor_args"]["youtube"]["player_client"] == ["default", "web_safari"]


def test_format_inventory_does_not_offer_silent_direct_youtube_video_only_heights():
  info = {
      "formats": [
          {"format_id": "2160", "url": "https://cdn.example/2160", "height": 2160,
           "vcodec": "av01", "acodec": "none"},
          {"format_id": "480", "url": "https://cdn.example/480", "height": 480,
           "vcodec": "avc1", "acodec": "mp4a", "language": "en",
           "audio_is_default": True},
      ],
  }
  options = main._available_formats(
      info, "https://youtube.com/watch?v=test", [], "test-agent",
      {"language": "en", "id": "en.1", "is_original": True},
  )

  assert main._format_inventory(info, options) == {
      "source_heights": [2160, 480],
      "compatible_heights": [480],
      "standalone_video_heights": [2160],
      "drm_filtered": False,
  }


def test_formats_notice_reports_youtube_playback_validation_block(monkeypatch):
  client = main.app.test_client()
  origin = "chrome-extension://" + "a" * 32
  info = {
      "title": "Test video",
      "_xdm_companion_diagnostics": {
          "yt_dlp_authenticated": True,
          "flags": ["po_token_required"],
      },
      "formats": [
          {"format_id": "480", "url": "https://cdn.example/480.mp4", "height": 480,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4",
           "language": "en", "audio_is_default": True},
      ],
  }
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: info)
  with main._format_sessions_lock:
    main._format_sessions.clear()

  response = client.post(
      "/formats",
      json={
          "url": "https://www.youtube.com/watch?v=test",
          "cookies": [],
          "audio_track": {"id": "en.1", "language": "en", "is_original": True},
      },
      headers={"Origin": origin},
  )

  assert response.status_code == 200
  assert response.json["notice"]
  assert "requisitos actuales de reproducción" in response.json["notice"]


def test_format_endpoints_are_extension_only_and_send_chosen_quality(monkeypatch):
  client = main.app.test_client()
  origin = "chrome-extension://" + "a" * 32
  page_url = "https://www.youtube.com/watch?v=test"
  info = {
      "title": "Test video",
      "formats": [
          {
              "format_id": "2160-dash-video-only",
              "url": "https://cdn.example/2160-video-only",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/test",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "https",
          },
          {
              "format_id": "1440-dash-video-only",
              "url": "https://cdn.example/1440-video-only",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/test-1440",
              "height": 1440,
              "vcodec": "av01",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "https",
          },
          {
              "format_id": "1080-dash-video-only",
              "url": "https://manifest.googlevideo.com/video.mpd",
              "height": 1080,
              "vcodec": "avc1",
              "acodec": "none",
              "ext": "mp4",
              "protocol": "http_dash_segments",
          },
          {
              "format_id": "720",
              "url": "https://cdn.example/video.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "en",
              "audio_is_default": True,
              "ext": "mp4",
          },
          {
              "format_id": "480",
              "url": "https://cdn.example/480.mp4",
              "height": 480,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "es",
              "audio_is_default": False,
              "ext": "mp4",
          },
      ],
  }
  sent = []
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: info)
  monkeypatch.setattr(
      main,
      "_send_to_xdm",
      lambda media, preferred_height=None: sent.append((media, preferred_height)),
  )
  with main._format_sessions_lock:
    main._format_sessions.clear()

  denied = client.post("/formats", json={"url": page_url})
  assert denied.status_code == 403

  preflight = client.options(
      "/formats",
      headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
  )
  assert preflight.status_code == 204
  assert preflight.headers["Access-Control-Allow-Origin"] == origin

  formats = client.post(
      "/formats",
      json={
          "url": page_url,
          "cookies": [],
          "user_agent": "test-agent",
          "title": "Test",
          "audio_track": {"id": "en.1", "language": "en", "is_original": True},
      },
      headers={"Origin": origin},
  )
  assert formats.status_code == 200
  assert [option["height"] for option in formats.json["formats"]] == [2160, 1440, 1080, 720]
  assert formats.json["formats"][0]["format_id"] == "2160-dash-video-only"
  assert "url" not in formats.json["formats"][0]
  token = formats.json["session"]

  selected = client.post(
      "/select",
      json={"session": token, "format_id": "2160-dash-video-only"},
      headers={"Origin": origin},
  )
  assert selected.status_code == 200
  assert selected.json["status"] == "queued"
  assert sent[0][0]["url"] == "https://manifest.googlevideo.com/api/manifest/dash/id/test"
  assert sent[0][1] == 2160

  expired = client.post(
      "/select",
      json={"session": token, "format_id": "2160-dash-video-only"},
      headers={"Origin": origin},
  )
  assert expired.status_code == 410


def test_format_endpoint_sends_the_original_audio_language_to_xdm(monkeypatch):
  client = main.app.test_client()
  origin = "chrome-extension://" + "e" * 32
  info = {
      "title": "Video con audio original en inglés",
      "formats": [
          {
              "format_id": "720-es-dub",
              "url": "https://cdn.example/muxed-es.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "es",
              "audio_is_default": False,
              "ext": "mp4",
              "protocol": "https",
          },
          {
              "format_id": "720-en-original",
              "url": "https://cdn.example/muxed-en.mp4",
              "height": 720,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "language": "en",
              "audio_is_default": True,
              "ext": "mp4",
              "protocol": "https",
          },
      ],
  }
  sent = []
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: info)
  monkeypatch.setattr(
      main,
      "_send_to_xdm",
      lambda media, preferred_height=None: sent.append((media, preferred_height)),
  )
  with main._format_sessions_lock:
    main._format_sessions.clear()

  formats = client.post(
      "/formats",
      json={
          "url": "https://www.youtube.com/watch?v=test",
          "cookies": [],
          "audio_track": {
              "id": "en.1", "language": "en", "name": "English (Original)",
              "is_original": True,
          },
          "accept_language": "es-DO,es",
      },
      headers={"Origin": origin},
  )

  assert formats.status_code == 200
  assert formats.json["formats"][0]["audio_language"] == "en"
  assert formats.json["audio_track"]["language"] == "en"
  selected = client.post(
      "/select",
      json={"session": formats.json["session"], "format_id": "720-en-original"},
      headers={"Origin": origin},
  )

  assert selected.status_code == 200
  assert sent[0][0]["url"] == "https://cdn.example/muxed-en.mp4"
  assert sent[0][0]["headers"]["Accept-Language"] == ["en"]


def test_formats_orders_available_tiers_without_fixed_sd_cutoff(monkeypatch):
  client = main.app.test_client()
  origin = "chrome-extension://" + "d" * 32
  page_url = "https://www.youtube.com/watch?v=sd-only"
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: {
      "title": "SD-only video",
      "formats": [
          {"format_id": "480", "url": "https://cdn.example/480.mp4", "height": 480,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4",
           "language": "en", "audio_is_default": True},
          {"format_id": "360", "url": "https://cdn.example/360.mp4", "height": 360,
           "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4",
           "language": "en", "audio_is_default": True},
      ],
  })
  with main._format_sessions_lock:
    main._format_sessions.clear()

  response = client.post(
      "/formats",
      json={
          "url": page_url,
          "cookies": [],
          "user_agent": "test-agent",
          "audio_track": {"id": "en.1", "language": "en", "is_original": True},
      },
      headers={"Origin": origin},
  )

  assert response.status_code == 200
  assert [option["height"] for option in response.json["formats"]] == [480, 360]
  assert response.json["notice"] is None


def test_manifest_selection_uses_requested_resolution():
  variants = [
      {"id": 1080, "info": "1080p, video/mp4, 5 Mbps"},
      {"id": 720, "info": "720p, video/mp4, 3 Mbps"},
      {"id": 480, "info": "480p, video/mp4, 1 Mbps"},
  ]

  selected = main._select_detected_variant(variants, 720)

  assert selected["id"] == 720


def test_manifest_selection_can_start_the_requested_4k_variant():
  variants = [
      {"id": 4320, "info": "4320p, video/mp4, 25 Mbps"},
      {"id": 2160, "info": "2160p, video/mp4, 12 Mbps"},
      {"id": 1080, "info": "1080p, video/mp4, 5 Mbps"},
  ]

  selected = main._select_detected_variant(variants, 2160)

  assert selected["id"] == 2160


def test_dash_quality_is_sent_to_xdm_as_a_manifest_with_preferred_height(monkeypatch):
  requests = []
  started = []

  class Response:
    status_code = 200

  monkeypatch.setattr(
      main.xdm_http,
      "post",
      lambda url, json, timeout: requests.append((url, json, timeout)) or Response(),
  )
  monkeypatch.setattr(
      main,
      "_start_detected_video",
      lambda tab_id, height: started.append((tab_id, height)),
  )
  media = {
      "url": "https://manifest.googlevideo.com/video.mpd",
      "title": "Members video",
      "filename": "Members video.mp4",
      "mime_type": "application/dash+xml",
      "headers": {},
      "cookie": None,
      "page_url": "https://www.youtube.com/watch?v=members-video",
      "protocol": "http_dash_segments",
  }

  result = main._send_to_xdm(media, preferred_height=1080)

  assert result == "queued"
  assert requests[0][0] == f"{main.XDM_BASE_URL}/media"
  assert requests[0][1]["mimeType"] == "application/dash+xml"
  assert requests[0][1]["filename"] == "Members video"
  assert started[0][0].startswith("xdm-companion-")
  assert started[0][1] == 1080


def test_dash_manifest_is_filtered_to_the_original_audio_language():
  manifest = b'''<?xml version="1.0"?>
  <MPD xmlns="urn:mpeg:dash:schema:mpd:2011">
    <Period>
      <AdaptationSet id="video" contentType="video" mimeType="video/mp4">
        <BaseURL>video/</BaseURL><Representation id="v1" />
      </AdaptationSet>
      <AdaptationSet id="spanish" contentType="audio" mimeType="audio/mp4" lang="es-419">
        <BaseURL>audio/es/</BaseURL><Representation id="a-es" />
      </AdaptationSet>
      <AdaptationSet id="english" contentType="audio" mimeType="audio/mp4" lang="en">
        <BaseURL>audio/en/</BaseURL><Representation id="a-en" />
      </AdaptationSet>
    </Period>
  </MPD>'''

  filtered = youtube_provider._filter_dash_manifest(
      manifest,
      "https://manifest.googlevideo.com/api/manifest/dash/video.mpd?sig=abc",
      "es-419",
  )
  root = main.ET.fromstring(filtered)
  adaptations = [element for element in root.iter() if youtube_provider._element_local_name(element) == "AdaptationSet"]

  assert [element.get("id") for element in adaptations] == ["video", "spanish"]
  base_urls = [element.text for element in root.iter() if youtube_provider._element_local_name(element) == "BaseURL"]
  assert base_urls == [
      "https://manifest.googlevideo.com/api/manifest/dash/video.mpd?sig=abc",
      "https://manifest.googlevideo.com/api/manifest/dash/video/",
      "https://manifest.googlevideo.com/api/manifest/dash/audio/es/",
  ]


def test_dash_manifest_refuses_to_fall_back_to_a_different_audio_language():
  manifest = b'''<MPD><Period>
    <AdaptationSet contentType="video"><Representation id="v" /></AdaptationSet>
    <AdaptationSet contentType="audio" lang="en"><Representation id="a" /></AdaptationSet>
  </Period></MPD>'''

  try:
    youtube_provider._filter_dash_manifest(manifest, "https://manifest.googlevideo.com/video.mpd", "es")
  except main.CompanionError as error:
    assert "no contiene audio en es" in str(error)
  else:
    raise AssertionError("A mismatched-language MPD must not be handed to XDM")


def test_selected_youtube_dash_quality_is_sent_with_only_the_original_audio_track(monkeypatch):
  client = main.app.test_client()
  origin = "chrome-extension://" + "f" * 32
  manifest = b'''<MPD><Period>
    <AdaptationSet contentType="video"><Representation id="v" /></AdaptationSet>
    <AdaptationSet contentType="audio" lang="es"><Representation id="a-es" /></AdaptationSet>
    <AdaptationSet contentType="audio" lang="en"><Representation id="a-en" /></AdaptationSet>
  </Period></MPD>'''
  info = {
      "title": "Video con audio seleccionado",
      "formats": [{
          "format_id": "2160-video-only",
          "url": "https://r1.googlevideo.com/videoplayback/video-only",
          "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/test",
          "height": 2160,
          "vcodec": "av01",
          "acodec": "none",
          "ext": "mp4",
          "protocol": "https",
      }],
  }
  sent = []

  class Response:
    status_code = 200

  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: info)
  monkeypatch.setattr(
      youtube_provider,
      "_read_youtube_manifest",
      lambda *_args: (manifest, "https://manifest.googlevideo.com/api/manifest/dash/id/test"),
  )
  monkeypatch.setattr(
      main.xdm_http,
      "post",
      lambda url, json, timeout: sent.append((url, json)) or Response(),
  )
  monkeypatch.setattr(main, "_start_detected_video", lambda *_args: None)
  with main._format_sessions_lock:
    main._format_sessions.clear()
  with youtube_provider._manifest_proxies_lock:
    youtube_provider._manifest_proxies.clear()

  formats = client.post(
      "/formats",
      json={
          "url": "https://www.youtube.com/watch?v=audio-test",
          "cookies": [],
          "audio_track": {
              "language": "en", "id": "en.1", "name": "English (Original)",
              "is_original": True,
          },
      },
      headers={"Origin": origin},
  )
  selected = client.post(
      "/select",
      json={"session": formats.json["session"], "format_id": "2160-video-only"},
      headers={"Origin": origin},
  )

  assert formats.status_code == 200
  assert selected.status_code == 200
  assert sent[0][0] == f"{main.XDM_BASE_URL}/media"
  manifest_url = sent[0][1]["url"]
  assert manifest_url.startswith("http://127.0.0.1:21121/manifest/")
  manifest_response = client.get(manifest_url.removeprefix("http://127.0.0.1:21121"))
  assert manifest_response.status_code == 200
  parsed = main.ET.fromstring(manifest_response.data)
  audio_languages = [
      element.get("lang")
      for element in parsed.iter()
      if youtube_provider._element_local_name(element) == "AdaptationSet"
      and youtube_provider._is_dash_audio_adaptation(element)
  ]
  assert audio_languages == ["en"]


def test_direct_video_only_quality_is_sent_to_xdm_as_a_direct_download(monkeypatch):
  requests = []

  class Response:
    status_code = 200

  monkeypatch.setattr(
      main.xdm_http,
      "post",
      lambda url, json, timeout: requests.append((url, json, timeout)) or Response(),
  )
  media = {
      "url": "https://cdn.example/stream?token=synthetic",
      "title": "Test video",
      "filename": "Test video.mp4",
      "mime_type": "video/mp4",
      "headers": {},
      "cookie": None,
      "page_url": "https://www.youtube.com/watch?v=test",
      "protocol": "https",
  }

  result = main._send_to_xdm(media, preferred_height=2160)

  assert result == "queued"
  assert requests[0][0] == f"{main.XDM_BASE_URL}/download"
  assert requests[0][1]["url"] == "https://cdn.example/stream?token=synthetic"
  assert requests[0][1]["mimeType"] == "video/mp4"
