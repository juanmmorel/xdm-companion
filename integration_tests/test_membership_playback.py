import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import main
import providers.youtube as youtube_provider


def test_youtube_diagnostics_keep_only_redacted_counts_and_flags():
  safe = youtube_provider._safe_youtube_diagnostics({
      "browser_diagnostics": {
          "cookie_count": 7,
          "account_markers": {"login_info": True, "sapisid": True},
          "queries": [{"kind": "playback_host", "count": 6, "error": ""}],
          "cookie_value": "never-log-this",
      },
      "youtube_player_diagnostics": {
          "player_found": True,
          "formats": 2,
          "adaptive_formats": 18,
          "video_formats": 16,
          "direct_video_formats": 0,
          "encrypted_video_formats": 16,
          "source_heights": [2160, 1080, "secret"],
          "dash_manifest": True,
          "video_url": "https://example.invalid/private",
      },
  })

  assert safe["cookie_count"] == 7
  assert safe["account_markers"]["login_info"] is True
  assert safe["queries"] == [{"kind": "playback_host", "count": 6, "error": ""}]
  assert safe["player_video_formats"] == 16
  assert safe["player_direct_formats"] == 0
  assert safe["player_encrypted_formats"] == 16
  assert safe["player_source_heights"] == [2160, 1080]
  assert "cookie_value" not in safe
  assert "video_url" not in safe


def test_youtube_format_diagnostics_classify_streams_without_exposing_urls():
  summary = youtube_provider._youtube_format_diagnostics({
      "formats": [
          {
              "format_id": "muxed",
              "url": "https://private.example/muxed",
              "height": 360,
              "vcodec": "avc1",
              "acodec": "mp4a",
              "audio_is_default": True,
          },
          {
              "format_id": "video-only",
              "url": "https://private.example/video",
              "height": 2160,
              "vcodec": "av01",
              "acodec": "none",
          },
          {
              "format_id": "manifest-video",
              "url": "https://private.example/video2",
              "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/test",
              "height": 1440,
              "vcodec": "vp9",
              "acodec": "none",
          },
          {
              "format_id": "audio-only",
              "url": "https://private.example/audio",
              "vcodec": "none",
              "acodec": "opus",
          },
      ],
  })

  assert summary["formats"] == 4
  assert summary["video"] == 3
  assert summary["audio_only"] == 1
  assert summary["audio_streams"] == 2
  assert summary["video_with_url"] == 3
  assert summary["video_with_audio"] == 1
  assert summary["video_only_with_manifest"] == 1
  assert summary["video_only_without_manifest"] == 1
  assert summary["video_heights"] == [2160, 1440, 360]
  assert "url" not in summary


def test_single_unambiguous_youtube_audio_track_allows_muxed_formats():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  info = {"formats": [
      *[{
          "format_id": f"audio-{index}",
          "url": f"https://rr1---sn.googlevideo.com/videoplayback?audio={index}",
          "height": 0,
          "vcodec": "none",
          "acodec": "opus",
          "language": "en",
          "audio_track_id": "en.1",
          "language_preference": -1,
      } for index in range(3)],
      {
          "format_id": "muxed-1080",
          "url": "https://rr1---sn.googlevideo.com/videoplayback?video=1080",
          "height": 1080,
          "vcodec": "avc1",
          "acodec": "mp4a",
          "language": "en",
          "audio_track_id": "en.1",
          "language_preference": -1,
      },
  ]}

  original = youtube_provider._youtube_original_audio_track(info)
  options = main._available_formats(info, page_url, [], "Test browser")

  assert original == {
      "language": "en", "id": "en.1", "name": "", "is_original": True,
      "inferred": True,
  }
  assert [option["height"] for option, _media in options] == [1080]


def test_unique_music_track_without_language_is_kept_as_original_audio():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  info = {"formats": [
      {
          "format_id": "audio-music-low",
          "url": "https://rr1---sn.googlevideo.com/audio/low",
          "vcodec": "none",
          "acodec": "opus",
          "audio_track_id": "instrumental-track",
      },
      {
          "format_id": "audio-music-high",
          "url": "https://rr1---sn.googlevideo.com/audio/high",
          "vcodec": "none",
          "acodec": "mp4a",
          "audio_track_id": "instrumental-track",
      },
      {
          "format_id": "muxed-1080",
          "url": "https://rr1---sn.googlevideo.com/video/muxed",
          "height": 1080,
          "vcodec": "avc1",
          "acodec": "mp4a",
          "audio_track_id": "instrumental-track",
      },
  ]}

  original = youtube_provider._youtube_original_audio_track(info)
  options = main._available_formats(info, page_url, [], "Test browser")

  assert original == {
      "language": "", "id": "instrumental-track", "name": "",
      "is_original": True, "inferred": True,
  }
  assert [option["height"] for option, _media in options] == [1080]


def test_multiple_unlabelled_audio_variants_without_track_identity_are_not_guessed():
  info = {"formats": [
      {"format_id": "audio-a", "url": "https://rr1---sn.googlevideo.com/a",
       "vcodec": "none", "acodec": "opus"},
      {"format_id": "audio-b", "url": "https://rr1---sn.googlevideo.com/b",
       "vcodec": "none", "acodec": "mp4a"},
  ]}

  assert youtube_provider._youtube_original_audio_track(info) is None


def test_youtube_does_not_infer_original_audio_when_multiple_languages_are_present():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  info = {"formats": [
      {
          "format_id": "audio-en", "url": "https://rr1---sn.googlevideo.com/a",
          "vcodec": "none", "acodec": "opus", "language": "en",
          "audio_track_id": "en.1", "language_preference": -1,
      },
      {
          "format_id": "audio-es", "url": "https://rr1---sn.googlevideo.com/b",
          "vcodec": "none", "acodec": "opus", "language": "es",
          "audio_track_id": "es.1", "language_preference": -1,
      },
      {
          "format_id": "muxed-1080", "url": "https://rr1---sn.googlevideo.com/c",
          "height": 1080, "vcodec": "avc1", "acodec": "mp4a",
      },
  ]}

  assert youtube_provider._youtube_original_audio_track(info) is None
  assert main._available_formats(info, page_url, [], "Test browser") == []


def test_authenticated_youtube_error_does_not_blame_login_when_original_audio_is_ambiguous(monkeypatch):
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: {
      "title": "Video",
      "formats": [
          {
              "format_id": "audio-en", "url": "https://rr1---sn.googlevideo.com/a",
              "vcodec": "none", "acodec": "opus", "language": "en",
              "audio_track_id": "en.1", "language_preference": -1,
          },
          {
              "format_id": "audio-es", "url": "https://rr1---sn.googlevideo.com/b",
              "vcodec": "none", "acodec": "opus", "language": "es",
              "audio_track_id": "es.1", "language_preference": -1,
          },
          {
              "format_id": "muxed", "url": "https://rr1---sn.googlevideo.com/c",
              "height": 1080, "vcodec": "avc1", "acodec": "mp4a",
          },
      ],
      "_xdm_companion_diagnostics": {"yt_dlp_authenticated": True, "flags": []},
  })
  response = main.app.test_client().post(
      "/formats",
      headers={"Origin": "chrome-extension://" + "d" * 32},
      json={
          "url": page_url,
          "user_agent": "Test browser",
          "cookies": [],
          "youtube_playback": {
              "video_id": "dQw4w9WgXcQ",
              "formats": [{
                  "itag": 137,
                  "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=137",
                  "mime_type": 'video/mp4; codecs="avc1.640028"',
                  "height": 1080,
                  "drm": False,
              }],
          },
      },
  )

  assert response.status_code == 422
  message = response.get_json()["message"]
  assert "La sesión fue reconocida" in message
  assert "Confirma que el navegador esté conectado" not in message


def _youtube_playback_payload(video_id="dQw4w9WgXcQ"):
  return {
      "video_id": video_id,
      "title": "Member video",
      "source_heights": [1080],
      "formats": [{
          "itag": 137,
          "url": "https://rr1---sn.googlevideo.com/videoplayback?expire=123",
          "mime_type": 'video/mp4; codecs="avc1.640028, mp4a.40.2"',
          "width": 1920,
          "height": 1080,
          "fps": 30,
          "bitrate": 4_000_000,
          "audio_track": {
              "id": "en.1", "name": "English (Original)", "is_default": True,
          },
          "drm": False,
      }],
  }


def test_browser_youtube_player_formats_are_validated_and_drm_is_filtered():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  playback = _youtube_playback_payload()

  info = youtube_provider._safe_youtube_playback(playback, page_url)
  assert info["_xdm_companion_diagnostics"]["source"] == "browser_player"
  assert info["formats"][0]["height"] == 1080
  assert info["formats"][0]["acodec"] == "mp4a.40.2"
  assert info["formats"][0]["language"] == "en"
  assert youtube_provider._safe_youtube_playback(
      _youtube_playback_payload("M7lc1UVf-VE"), page_url,
  ) is None

  playback["formats"][0]["drm"] = True
  drm_info = youtube_provider._safe_youtube_playback(playback, page_url)
  assert drm_info is not None
  assert main._available_formats(drm_info, page_url, [], "Test browser") == []


def test_browser_playback_preserves_explicit_evidence_that_the_player_has_no_audio():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  playback = {
      "video_id": "dQw4w9WgXcQ",
      "audio_streams_present": False,
      "source_heights": [1080],
      "formats": [{
          "itag": 137,
          "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=137",
          "mime_type": 'video/mp4; codecs="avc1.640028"',
          "height": 1080,
      }],
  }

  info = youtube_provider._safe_youtube_playback(playback, page_url)

  assert info["_xdm_companion_player_audio_streams_present"] is False
  assert info["formats"][0]["acodec"] == "none"


def test_silent_video_classification_requires_both_player_and_extractor_evidence():
  browser_info = {
      "_xdm_companion_player_audio_streams_present": False,
      "_xdm_companion_source_heights": [1080],
      "formats": [{"has_drm": False}],
  }
  extractor_info = {
      "formats": [{"vcodec": "avc1", "acodec": "none"}],
      "_xdm_companion_diagnostics": {"flags": []},
  }
  summary = {"formats": 1, "video": 1, "audio_streams": 0}

  assert youtube_provider._youtube_silent_video_confirmed(
      browser_info, extractor_info, summary,
  ) is True
  assert youtube_provider._youtube_silent_video_confirmed(
      browser_info, extractor_info, {**summary, "audio_streams": 1},
  ) is False
  assert youtube_provider._youtube_silent_video_confirmed(
      browser_info, extractor_info, summary,
      {"language": "en", "is_original": True},
  ) is False


def test_browser_player_selects_youtube_original_audio_not_the_current_dub():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  original_track = {
      "id": "en.1",
      "language": "en",
      "name": "English (Original)",
      "is_original": True,
  }
  playback = {
      "video_id": "dQw4w9WgXcQ",
      "title": "Video with auto-dub",
      "source_heights": [1080],
      "formats": [
          {
              "itag": 18,
              "url": "https://rr1---sn.googlevideo.com/videoplayback?track=es",
              "mime_type": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
              "height": 720,
              "bitrate": 2_000_000,
              "audio_track": {"id": "es.2", "is_default": False},
              "drm": False,
          },
          {
              "itag": 18,
              "url": "https://rr1---sn.googlevideo.com/videoplayback?track=en",
              "mime_type": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
              "height": 720,
              "bitrate": 1_500_000,
              "audio_track": {"id": "en.1", "is_default": True},
              "drm": False,
          },
      ],
  }

  info = youtube_provider._safe_youtube_playback(playback, page_url, original_track)
  options = main._available_formats(
      info, page_url, [], "Test browser", original_track, "es-DO,es",
  )

  assert len(options) == 1
  option, media = options[0]
  assert option["audio_language"] == "en"
  assert media["url"].endswith("track=en")
  assert media["headers"]["Accept-Language"] == ["en"]


def test_original_audio_track_id_overrides_a_misleading_default_flag():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  original_track = {
      "id": "en.1", "language": "en", "name": "English (Original)",
      "is_original": True,
  }
  playback = {
      "video_id": "dQw4w9WgXcQ",
      "title": "Video with two audio tracks",
      "formats": [
          {
              "itag": 18,
              "url": "https://rr1---sn.googlevideo.com/videoplayback?track=es",
              "mime_type": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
              "height": 720,
              "audio_track": {"id": "es.2", "is_default": True},
              "drm": False,
          },
          {
              "itag": 18,
              "url": "https://rr1---sn.googlevideo.com/videoplayback?track=en",
              "mime_type": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
              "height": 720,
              "audio_track": {"id": "en.1", "is_default": False},
              "drm": False,
          },
      ],
  }

  info = youtube_provider._safe_youtube_playback(playback, page_url, original_track)
  options = main._available_formats(
      info, page_url, [], "Test browser", original_track,
  )

  assert len(options) == 1
  assert options[0][0]["format_id"] == "18-en.1"
  assert options[0][1]["url"].endswith("track=en")


def test_highest_browser_tier_survives_long_lists_of_lower_formats():
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  playback = _youtube_playback_payload()
  playback["formats"] = [
      {
          "itag": index,
          "url": f"https://rr1---sn.googlevideo.com/videoplayback?itag={index}",
          "mime_type": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
          "width": 640,
          "height": 360,
          "bitrate": 500_000 + index,
          "audio_track": {"id": "en.1", "is_default": True},
          "drm": False,
      }
      for index in range(1, 91)
  ]
  playback["formats"].append({
      "itag": 999,
      "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=999",
      "mime_type": 'video/mp4; codecs="avc1.640033, mp4a.40.2"',
      "width": 3840,
      "height": 2160,
      "bitrate": 20_000_000,
      "audio_track": {"id": "en.1", "is_default": True},
      "drm": False,
  })
  playback["source_heights"] = [2160, 360]

  info = youtube_provider._safe_youtube_playback(playback, page_url)
  options = main._available_formats(info, page_url, [], "Test browser")

  assert options[0][0]["height"] == 2160
  assert [option["height"] for option, _media in options] == [2160, 360]


def test_extractor_is_consulted_only_for_higher_player_tiers_without_direct_urls(monkeypatch):
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  browser_playback = _youtube_playback_payload()
  browser_playback["formats"][0]["height"] = 360
  browser_playback["formats"][0]["width"] = 640
  browser_playback["source_heights"] = [2160, 1080, 360]

  def extracted_info(*_args):
    return {
        "title": "Member video",
        "formats": [{
            "format_id": "extractor-2160",
            "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=2160",
            "height": 2160,
            "vcodec": "av01",
            "acodec": "mp4a.40.2",
            "language": "en",
            "language_preference": 10,
            "ext": "mp4",
            "tbr": 20_000,
        }],
    }

  monkeypatch.setattr(main, "_extract_video_info", extracted_info)
  response = main.app.test_client().post(
      "/formats",
      headers={"Origin": "chrome-extension://" + "b" * 32},
      json={
          "url": page_url,
          "user_agent": "Test browser",
          "cookies": [],
          "youtube_playback": browser_playback,
      },
  )

  assert response.status_code == 200
  assert response.get_json()["formats"][0]["height"] == 2160


def test_extractor_video_only_dash_tier_is_combined_with_browser_original_audio_metadata(monkeypatch):
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  browser_playback = _youtube_playback_payload()
  browser_playback["formats"][0]["height"] = 360
  browser_playback["formats"][0]["width"] = 640
  extracted = {
      "title": "Member video",
      "formats": [{
          "format_id": "extractor-2160",
          "url": "https://rr1---sn.googlevideo.com/videoplayback?itag=2160",
          "manifest_url": "https://manifest.googlevideo.com/api/manifest/dash/id/video/expire/123",
          "height": 2160,
          "vcodec": "av01",
          "acodec": "none",
          "ext": "mp4",
          "tbr": 20_000,
      }],
      "_xdm_companion_diagnostics": {"yt_dlp_authenticated": True, "flags": []},
  }

  assert main._available_formats(extracted, page_url, [], "Test browser") == []
  monkeypatch.setattr(main, "_extract_video_info", lambda *_args: extracted)
  response = main.app.test_client().post(
      "/formats",
      headers={"Origin": "chrome-extension://" + "c" * 32},
      json={
          "url": page_url,
          "user_agent": "Test browser",
          "cookies": [],
          "youtube_playback": browser_playback,
      },
  )

  assert response.status_code == 200
  body = response.get_json()
  assert body["formats"][0]["height"] == 2160
  assert body["formats"][0]["format_id"] == "extractor-2160"
  assert body["notice"] is None


def test_formats_uses_authorized_browser_player_before_yt_dlp(monkeypatch):
  page_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)

  def unexpected_ytdlp(*_args, **_kwargs):
    raise AssertionError("browser-authorized formats should be used first")

  monkeypatch.setattr(main, "_extract_video_info", unexpected_ytdlp)
  response = main.app.test_client().post(
      "/formats",
      headers={"Origin": "chrome-extension://" + "a" * 32},
      json={
          "url": page_url,
          "user_agent": "Test browser",
          "cookies": [
              {"name": "LOGIN_INFO", "value": "member", "domain": ".youtube.com"},
              {"name": "SAPISID", "value": "account", "domain": ".youtube.com"},
          ],
          "youtube_playback": _youtube_playback_payload(),
      },
  )

  assert response.status_code == 200
  body = response.get_json()
  assert body["status"] == "ready"
  assert body["formats"][0]["height"] == 1080
