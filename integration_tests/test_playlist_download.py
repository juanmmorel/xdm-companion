import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core")))

import main
from errors import CompanionError


PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLexample"
ORIGIN = "chrome-extension://" + "a" * 32


def test_playlist_enumeration_uses_flat_entries_and_preserves_order(monkeypatch):
  captured = {}

  class FakeYoutubeDL:
    def __init__(self, options):
      captured["options"] = options

    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def extract_info(self, _url, download=False):
      assert download is False
      return {
          "_type": "playlist",
          "title": "Lista de prueba",
          "playlist_count": 2,
          "entries": [
              {"id": "Abcdefgh_01", "title": "Primero", "url": "Abcdefgh_01"},
              {
                  "id": "Abcdefgh_02",
                  "title": "Segundo",
                  "webpage_url": "https://www.youtube.com/watch?v=Abcdefgh_02",
              },
          ],
      }

  monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
  monkeypatch.setattr(main, "_js_runtimes", lambda: {})
  monkeypatch.setattr(main, "_write_cookie_file", lambda *_args: None)

  playlist = main._extract_playlist_entries(PLAYLIST_URL, [], "test-agent", "es")

  assert playlist["title"] == "Lista de prueba"
  assert [entry["title"] for entry in playlist["entries"]] == ["Primero", "Segundo"]
  assert [entry["position"] for entry in playlist["entries"]] == [1, 2]
  assert playlist["entries"][0]["url"] == "https://www.youtube.com/watch?v=Abcdefgh_01"
  assert playlist["entries"][1]["url"] == "https://www.youtube.com/watch?v=Abcdefgh_02"
  assert main._canonical_playlist_url(
      "https://www.youtube.com/watch?v=Abcdefgh_01&list=PLexample"
  ) == PLAYLIST_URL
  assert captured["options"]["noplaylist"] is False
  assert captured["options"]["extract_flat"] == "in_playlist"
  assert captured["options"]["playlistend"] == main._playlist_config(PLAYLIST_URL)[1]["max_items"] + 1


def test_playlist_preview_reports_entries_with_direct_video_links(monkeypatch):
  job_id = "e" * 32
  job = {
      "id": job_id,
      "page_url": PLAYLIST_URL,
      "status": "enumerating",
      "created_at": 10,
      "finished_at": None,
      "issues": [],
  }
  entries = [
      {"position": 1, "url": "https://www.youtube.com/watch?v=Abcdefgh_01", "title": "Uno"},
      {"position": 2, "url": "", "title": "No disponible"},
      {"position": 3, "url": "https://www.youtube.com/watch?v=Abcdefgh_03", "title": "Tres"},
  ]
  with main._playlist_jobs_lock:
    main._playlist_jobs[job_id] = job
    main._active_playlist_urls[PLAYLIST_URL] = job_id
  monkeypatch.setattr(
      main,
      "_extract_playlist_entries",
      lambda *_args: {"title": "Lista de prueba", "entries": entries},
  )

  try:
    main._preview_playlist_job(job_id, PLAYLIST_URL, [], "test-agent", "es")
    assert job["status"] == "awaiting_selection"
    assert job["total"] == 3
    assert job["downloadable_count"] == 2
    assert job["_entries"] == entries
  finally:
    with main._playlist_jobs_lock:
      main._playlist_jobs.pop(job_id, None)
      main._active_playlist_urls.pop(PLAYLIST_URL, None)


def test_playlist_job_enqueues_individual_videos_in_order_with_numbered_names(monkeypatch):
  job_id = "b" * 32
  page_url = PLAYLIST_URL + "&test=ordered"
  job = {
      "id": job_id,
      "title": "Lista de prueba",
      "status": "enumerating",
      "total": None,
      "processed": 0,
      "queued": 0,
      "skipped": 0,
      "failed": 0,
      "current_position": None,
      "current_title": "",
      "message": "",
      "issues": [],
      "created_at": 10,
      "finished_at": None,
  }
  with main._playlist_jobs_lock:
    main._playlist_jobs[job_id] = job
    main._active_playlist_urls[page_url] = job_id

  entries = [
      {"position": 1, "url": "https://www.youtube.com/watch?v=Abcdefgh_01", "title": "Primero"},
      {"position": 2, "url": "", "title": "Sin enlace"},
      {"position": 3, "url": "https://www.youtube.com/watch?v=Abcdefgh_03", "title": "Tercero"},
  ]
  sent = []
  pending = {}

  monkeypatch.setattr(
      main,
      "_extract_playlist_entries",
      lambda *_args: {"title": "Lista de prueba", "entries": entries},
  )

  def resolve(payload, _page_url, _cookies, _user_agent, audio_track, _language):
    assert audio_track is None, "playlist items must resolve their own original audio track"
    media = {
        "url": "https://cdn.example/video.mp4",
        "title": payload["title"],
        "filename": payload["title"] + ".mp4",
        "ext": "mp4",
    }
    option = {"format_id": payload["title"], "height": 1080}
    session = f"{len(pending) + 1:032x}"
    pending[session] = (option, media)
    return {}, [(option, media)], {}, session, None

  def consume(session, format_id):
    selection = pending.pop(session, None)
    return selection if selection and selection[0]["format_id"] == format_id else None

  monkeypatch.setattr(main, "_resolve_format_options", resolve)
  monkeypatch.setattr(main, "_consume_format_selection", consume)
  monkeypatch.setattr(main, "_send_to_xdm", lambda media, **_kwargs: sent.append(media.copy()))

  try:
    main._process_playlist_job(job_id, page_url, [], "test-agent", "es")
    assert [media["title"] for media in sent] == ["001 - Primero", "003 - Tercero"]
    assert [media["filename"] for media in sent] == ["001 - Primero.mp4", "003 - Tercero.mp4"]
    assert job["status"] == "completed_with_issues"
    assert job["queued"] == 2
    assert job["skipped"] == 1
    assert job["failed"] == 0
    assert job["processed"] == 3
    assert job["issues"][0]["position"] == 2
  finally:
    with main._playlist_jobs_lock:
      main._playlist_jobs.pop(job_id, None)
      main._active_playlist_urls.pop(page_url, None)


def test_playlist_start_and_status_are_extension_only_and_reuse_active_job(monkeypatch):
  client = main.app.test_client()
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)

  class ParkedThread:
    def __init__(self, *args, **_kwargs):
      self.args = args

    def start(self):
      return None

  monkeypatch.setattr(main.threading, "Thread", ParkedThread)
  with main._playlist_jobs_lock:
    main._playlist_jobs.clear()
    main._active_playlist_urls.clear()

  try:
    denied = client.post("/playlist/start", json={"url": PLAYLIST_URL})
    assert denied.status_code == 403

    started = client.post(
        "/playlist/start",
        json={"url": PLAYLIST_URL, "cookies": [], "title": "Lista"},
        headers={"Origin": ORIGIN},
    )
    assert started.status_code == 200
    job_id = started.json["job"]["id"]
    assert started.json["job"]["status"] == "enumerating"

    duplicate = client.post(
        "/playlist/start",
        json={"url": PLAYLIST_URL, "cookies": []},
        headers={"Origin": ORIGIN},
    )
    assert duplicate.status_code == 200
    assert duplicate.json["job"]["id"] == job_id

    status = client.post(
        "/playlist/status",
        json={"job_id": job_id},
        headers={"Origin": ORIGIN},
    )
    assert status.status_code == 200
    assert status.json["job"]["id"] == job_id
  finally:
    with main._playlist_jobs_lock:
      main._playlist_jobs.clear()
      main._active_playlist_urls.clear()


@pytest.mark.parametrize("limit", [5, 20])
def test_playlist_queue_accepts_only_supported_limits_and_preserves_order(monkeypatch, limit):
  client = main.app.test_client()
  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  captured = []

  class ParkedThread:
    def __init__(self, *, target, args, **_kwargs):
      self.target = target
      self.args = args

    def start(self):
      captured.append((self.target, self.args))

  monkeypatch.setattr(main.threading, "Thread", ParkedThread)
  job_id = "f" * 32
  entries = [
      {
          "position": position,
          "url": "" if position == 5 else f"https://www.youtube.com/watch?v=Video{position:06d}",
          "title": f"Video {position}",
      }
      for position in range(1, 46)
  ]
  job = {
      "id": job_id,
      "page_url": PLAYLIST_URL,
      "title": "Lista",
      "status": "awaiting_selection",
      "total": len(entries),
      "downloadable_count": 44,
      "selection_limit": None,
      "_entries": entries,
      "processed": 0,
      "queued": 0,
      "skipped": 0,
      "failed": 0,
      "current_position": None,
      "current_title": "",
      "message": "Elige cuántos videos descargar.",
      "issues": [],
      "created_at": main.time.time(),
      "finished_at": None,
  }
  with main._playlist_jobs_lock:
    main._playlist_jobs[job_id] = job
    main._active_playlist_urls[PLAYLIST_URL] = job_id

  payload = {"url": PLAYLIST_URL, "cookies": [], "job_id": job_id, "limit": limit}
  try:
    invalid = client.post(
        "/playlist/queue",
        json={**payload, "limit": 15},
        headers={"Origin": ORIGIN},
    )
    assert invalid.status_code == 400
    assert job["status"] == "awaiting_selection"

    queued = client.post("/playlist/queue", json=payload, headers={"Origin": ORIGIN})
    assert queued.status_code == 200
    assert queued.json["job"]["status"] == "processing"
    assert queued.json["job"]["total"] == limit
    assert queued.json["job"]["selection_limit"] == limit
    assert len(captured) == 1
    target, args = captured[0]
    assert target is main._process_playlist_entries
    selected = args[2]
    assert len(selected) == limit
    assert [entry["position"] for entry in selected] == [
        position for position in range(1, len(entries) + 1) if position != 5
    ][:limit]
  finally:
    with main._playlist_jobs_lock:
      main._playlist_jobs.pop(job_id, None)
      main._active_playlist_urls.pop(PLAYLIST_URL, None)


def test_playlist_over_limit_is_rejected_before_any_download(monkeypatch):
  class FakeYoutubeDL:
    def __init__(self, _options):
      pass

    def __enter__(self):
      return self

    def __exit__(self, *_args):
      return False

    def extract_info(self, _url, download=False):
      return {"_type": "playlist", "playlist_count": 3, "entries": []}

  monkeypatch.setattr(main.yt_dlp, "YoutubeDL", FakeYoutubeDL)
  monkeypatch.setattr(main, "_js_runtimes", lambda: {})
  monkeypatch.setattr(main, "_write_cookie_file", lambda *_args: None)
  provider = main.provider_config_for_url(PLAYLIST_URL, main.SITE_CATALOG)
  monkeypatch.setattr(main, "_playlist_config", lambda _url: (provider, {"max_items": 2}))

  try:
    main._extract_playlist_entries(PLAYLIST_URL, [], "test-agent")
  except CompanionError as error:
    assert "límite configurado de 2" in str(error)
  else:
    raise AssertionError("an oversized playlist must be rejected as a whole")
