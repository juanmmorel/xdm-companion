import json
import ipaddress
import math
import os
import re
import shutil
import threading
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

import requests
from flask import Flask, jsonify, make_response, request
from errors import CompanionError
from media_utils import (
    _LANGUAGE_TAG_RE, _accept_language_header, _cookie_domain_matches,
    _cookie_header_for_url, _has_confirmed_drm, _is_original_audio_format,
    _manifest_source, _normalize_language, _preferred_audio_languages,
    _safe_accept_language, _safe_audio_track,
)
from providers.registry import adapter_for_url, get_manifest_proxy, provider_config_for_url
from site_catalog import load_site_catalog, provider_for_url, site_domains


app = Flask(__name__)


class _LazyYtDlp:
  """Load yt-dlp only when a media extraction request needs it."""

  _module = None

  def __getattr__(self, name):
    module = self._module
    if module is None:
      import yt_dlp as module
      self._module = module
    return getattr(module, name)


yt_dlp = _LazyYtDlp()
xdm_http = requests.Session()
xdm_http.trust_env = False

CONFIG_DIR = Path.home() / ".xdman" / "companion"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
DOMAINS_FILE = Path(__file__).resolve().with_name("domains.json")
SITE_CATALOG = load_site_catalog(DOMAINS_FILE)
XDM_BASE_URL = "http://127.0.0.1:8597"
XDM_TIMEOUT = (3, 10)
MAX_COOKIE_LENGTH = 64_000
MAX_URL_LENGTH = 8_192
MAX_COOKIE_COUNT = 2_000
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024
_BROWSER_HANDOFF_PATHS = {
    "/intercept", "/formats", "/select", "/playlist/start", "/playlist/queue", "/playlist/status",
}


def _is_extension_origin(origin):
  if not origin or len(origin) > 128:
    return False
  return bool(
      re.fullmatch(r"chrome-extension://[a-p]{32}", origin)
      or re.fullmatch(
          r"moz-extension://(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})",
          origin,
      )
  )


def _is_loopback_request():
  try:
    return ipaddress.ip_address(request.remote_addr or "").is_loopback
  except ValueError:
    return False


@app.before_request
def _protect_browser_handoff():
  if request.path not in _BROWSER_HANDOFF_PATHS:
    return None
  if not _is_loopback_request():
    return jsonify({"status": "error", "message": "Solo se aceptan solicitudes locales."}), 403

  origin = request.headers.get("Origin", "")
  if not _is_extension_origin(origin):
    return jsonify({"status": "error", "message": "La solicitud no viene del puente del navegador."}), 403

  if request.method == "OPTIONS":
    response = make_response("", 204)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Vary"] = "Origin"
    return response
  return None


@app.after_request
def _set_browser_handoff_cors(response):
  origin = request.headers.get("Origin", "")
  if request.path in _BROWSER_HANDOFF_PATHS and _is_extension_origin(origin):
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
  return response

COMPLEX_DOMAINS = tuple(site_domains(SITE_CATALOG))

_active_urls = set()
_active_urls_lock = threading.Lock()
_format_sessions = {}
_format_sessions_lock = threading.Lock()
FORMAT_SESSION_TTL_SECONDS = 5 * 60
MAX_FORMAT_SESSIONS = 64
MAX_PLAYLIST_JOBS = 32
PLAYLIST_JOB_TTL_SECONDS = 6 * 60 * 60
PLAYLIST_PREVIEW_TTL_SECONDS = 15 * 60
_playlist_jobs = {}
_playlist_jobs_lock = threading.Lock()
_active_playlist_urls = {}




class _YtDlpLogCollector:
  """Collect only diagnostic categories; never persist yt-dlp's raw messages."""

  def __init__(self, adapter=None):
    self.adapter = adapter
    self.flags = set()

  def _record(self, message):
    classify = getattr(self.adapter, "classify_extractor_log", None)
    if classify:
      self.flags.update(classify(message))
    elif "javascript runtime" in str(message).lower():
      self.flags.add("javascript_runtime_missing")

  def debug(self, message):
    self._record(message)

  def info(self, _message):
    pass

  def warning(self, message):
    self._record(message)

  def error(self, message):
    self._record(message)


def is_complex_url(url):
  """Return whether URL belongs to a site this companion is configured to handle."""
  try:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
  except (TypeError, ValueError):
    return False

  if parsed.scheme.lower() not in {"http", "https"} or not hostname:
    return False

  return any(
      hostname == domain or hostname.endswith("." + domain)
      for domain in COMPLEX_DOMAINS
  )


def _safe_browser_data(payload):
  page_url = str(payload.get("url") or payload.get("page_url") or "").strip()
  provider = provider_config_for_url(page_url, SITE_CATALOG)
  provider_limit = (provider or {}).get("backend", {}).get("max_page_url_length", MAX_URL_LENGTH)
  try:
    url_limit = max(1, min(MAX_URL_LENGTH, int(provider_limit)))
  except (TypeError, ValueError):
    url_limit = MAX_URL_LENGTH
  if len(page_url) > url_limit or not is_complex_url(page_url):
    raise CompanionError("La página no pertenece a un sitio de video configurado.")

  cookies = payload.get("cookies") or []
  if not isinstance(cookies, list) or len(cookies) > MAX_COOKIE_COUNT:
    raise CompanionError("La sesión del navegador no tiene un formato válido.")
  if sum(
      len(str(cookie.get("name", ""))) + len(str(cookie.get("value", "")))
      for cookie in cookies
      if isinstance(cookie, dict)
  ) > MAX_COOKIE_LENGTH:
    raise CompanionError("La sesión del navegador supera el tamaño permitido.")

  user_agent = str(payload.get("user_agent") or "").strip()[:512]
  title = str(payload.get("title") or "").strip()[:500]
  audio_track = _safe_audio_track(payload.get("audio_track"))
  accept_language = _safe_accept_language(payload.get("accept_language"))
  return page_url, cookies, user_agent, title, audio_track, accept_language


















def _write_cookie_file(page_url, cookies):
  page_host = (urlparse(page_url).hostname or "").lower()
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  if adapter:
    page_host = adapter.cookie_target_host()
  lines = ["# Netscape HTTP Cookie File"]
  for cookie in cookies:
    if not isinstance(cookie, dict):
      continue
    name = str(cookie.get("name") or "")
    value = str(cookie.get("value") or "")
    path = str(cookie.get("path") or "/")
    domain = str(cookie.get("domain") or "").strip().lower()
    host_only = cookie.get("hostOnly")
    if host_only is None:
      host_only = not domain.startswith(".")
    if (
        not name
        or any(ord(char) < 32 for char in name + value + path + domain)
        or not _cookie_domain_matches(page_host, cookie)
    ):
      continue
    cookie_domain = domain.lstrip(".")
    if not host_only:
      cookie_domain = "." + cookie_domain
    try:
      expiration = 0 if cookie.get("session") else int(cookie.get("expirationDate") or 0)
    except (TypeError, ValueError):
      expiration = 0
    fields = (
        cookie_domain,
        "FALSE" if host_only else "TRUE",
        path if path.startswith("/") else "/",
        "TRUE" if cookie.get("secure") else "FALSE",
        str(expiration),
        name,
        value,
    )
    if cookie.get("httpOnly"):
      fields = ("#HttpOnly_" + fields[0],) + fields[1:]
    lines.append("\t".join(fields))

  if len(lines) == 1:
    return None
  temp_file = tempfile.NamedTemporaryFile(
      mode="w",
      encoding="utf-8",
      prefix="xdm-companion-cookies-",
      suffix=".txt",
      delete=False,
  )
  try:
    temp_file.write("\n".join(lines) + "\n")
    return temp_file.name
  finally:
    temp_file.close()




def _js_runtimes():
  runtime_file = Path(__file__).resolve().with_name("runtime.json")
  try:
    configured = json.loads(runtime_file.read_text(encoding="utf-8"))
    name = configured.get("name")
    path = configured.get("path")
    if name in {"deno", "node"} and path and Path(path).is_file():
      return {name: {"path": path}}
  except (OSError, ValueError, AttributeError):
    pass

  available = {
      name: {"path": path}
      for name in ("deno", "node")
      if (path := shutil.which(name))
  }
  return available or {"deno": {}, "node": {}}














def _extract_video_info(page_url, cookies, user_agent, audio_track=None, accept_language=""):
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  headers = {}
  if user_agent:
    headers["User-Agent"] = user_agent
  headers["Referer"] = page_url
  audio_preferences = (
      adapter.original_audio_track({}, audio_track)
      if adapter else audio_track
  )
  language_header = _accept_language_header(
      audio_preferences, "" if adapter else accept_language,
  )
  if language_header:
    headers["Accept-Language"] = language_header

  options = {
      "noplaylist": True,
      "quiet": True,
      "no_warnings": False,
      "verbose": adapter.verbose_diagnostics() if adapter else False,
      "ignoreerrors": False,
      "skip_download": True,
      "http_headers": headers,
      "js_runtimes": _js_runtimes(),
  }
  log_collector = _YtDlpLogCollector(adapter)
  options["logger"] = log_collector
  extractor_key = adapter.extractor_key() if adapter else ""
  extractor_args = adapter.extractor_args() if adapter else {}
  if extractor_args:
    options["extractor_args"] = extractor_args
  cookie_file = _write_cookie_file(page_url, cookies)
  if cookie_file:
    options["cookiefile"] = cookie_file

  yt_dlp_authenticated = None
  try:
    with yt_dlp.YoutubeDL(options) as downloader:
      if extractor_key and adapter.checks_authenticated_session():
        extractor = downloader.get_info_extractor(extractor_key)
        extractor.initialize()
        yt_dlp_authenticated = bool(extractor.is_authenticated)
      info = downloader.extract_info(page_url, download=False)
  except Exception as error:
    error_message = str(error).lower()
    if "javascript runtime" in error_message or "yt-dlp-ejs" in error_message:
      if adapter:
        raise CompanionError(adapter.runtime_requirement_message()) from error
      raise CompanionError(
          "Instala Deno o Node.js compatible y vuelve a intentarlo."
      ) from error
    if adapter:
      provider_name = adapter.display_name()
      app.logger.warning(
          "%s extraction failed (yt_dlp_authenticated=%s, browser_account_cookies=%s, "
          "po_token_required=%s, drm_formats_filtered=%s)",
          provider_name,
          yt_dlp_authenticated,
          adapter.has_account_cookies(cookies),
          "po_token_required" in log_collector.flags,
          "drm_formats_filtered" in log_collector.flags,
      )
      friendly_error = adapter.extraction_error(
          error, cookies, yt_dlp_authenticated, log_collector.flags,
      )
      if friendly_error:
        raise CompanionError(friendly_error) from error
    raise CompanionError(
        "No se pudo obtener un flujo descargable. El sitio puede requerir "
        "inicio de sesión, no ofrecer un formato compatible o haber cambiado."
    ) from error
  finally:
    if cookie_file:
      try:
        os.unlink(cookie_file)
      except OSError:
        pass

  if not isinstance(info, dict):
    raise CompanionError("El sitio no devolvió información de un video.")
  if info.get("_type") in {"playlist", "multi_video"}:
    raise CompanionError("Abre un video individual; no se aceptan listas completas.")
  if adapter:
    info["_xdm_companion_diagnostics"] = adapter.extraction_diagnostics(
        yt_dlp_authenticated, log_collector.flags,
    )
  return info


def _playlist_config(page_url):
  provider = provider_config_for_url(page_url, SITE_CATALOG)
  config = (provider or {}).get("backend", {}).get("playlist", {})
  if not isinstance(config, dict) or config.get("enabled") is not True:
    return provider, None
  try:
    max_items = int(config.get("max_items", 0))
  except (TypeError, ValueError):
    max_items = 0
  if max_items < 1:
    return provider, None
  return provider, {**config, "max_items": max_items}


def _canonical_playlist_url(page_url):
  provider = provider_config_for_url(page_url, SITE_CATALOG)
  config = (provider or {}).get("browser", {}).get("playlist", {})
  if not isinstance(config, dict):
    return page_url
  try:
    parsed = urlparse(page_url)
    query = parse_qs(parsed.query)
  except (TypeError, ValueError):
    return page_url
  template = str(config.get("url_template") or "")
  if not template or "{playlist_id}" not in template:
    return page_url
  for parameter in config.get("query_parameters", []):
    values = query.get(str(parameter))
    if not values or not values[0]:
      continue
    candidate = template.replace("{playlist_id}", quote(values[0], safe=""))
    item_provider = provider_config_for_url(candidate, SITE_CATALOG)
    if item_provider and provider and item_provider.get("id") == provider.get("id"):
      return candidate
  return page_url


def _playlist_entry_url(entry, page_url, provider):
  if not isinstance(entry, dict):
    return ""
  for key in ("webpage_url", "original_url", "url"):
    candidate = str(entry.get(key) or "").strip()
    try:
      parsed = urlparse(candidate)
    except ValueError:
      continue
    if parsed.scheme in {"http", "https"} and parsed.netloc:
      if parsed.username or parsed.password:
        continue
      item_provider = provider_config_for_url(candidate, SITE_CATALOG)
      if item_provider and item_provider.get("id") == provider.get("id"):
        return candidate

  video_id = str(entry.get("id") or "").strip()
  template = str(
      ((provider or {}).get("browser", {}).get("preview") or {}).get("video_url_template")
      or "",
  )
  if video_id and template and "{video_id}" in template:
    candidate = template.replace("{video_id}", quote(video_id, safe=""))
    candidate = urljoin(page_url, candidate)
    item_provider = provider_config_for_url(candidate, SITE_CATALOG)
    if item_provider and item_provider.get("id") == provider.get("id"):
      return candidate
  return ""


def _extract_playlist_entries(page_url, cookies, user_agent, accept_language=""):
  provider, playlist_config = _playlist_config(page_url)
  if not playlist_config:
    raise CompanionError("Este sitio no tiene habilitada la descarga de listas.")

  max_items = playlist_config["max_items"]
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  headers = {"Referer": page_url}
  if user_agent:
    headers["User-Agent"] = user_agent
  if accept_language:
    headers["Accept-Language"] = accept_language

  options = {
      "noplaylist": False,
      "extract_flat": "in_playlist",
      "playlistend": max_items + 1,
      "ignoreerrors": True,
      "quiet": True,
      "no_warnings": False,
      "verbose": adapter.verbose_diagnostics() if adapter else False,
      "skip_download": True,
      "http_headers": headers,
      "js_runtimes": _js_runtimes(),
  }
  log_collector = _YtDlpLogCollector(adapter)
  options["logger"] = log_collector
  extractor_key = adapter.extractor_key() if adapter else ""
  extractor_args = adapter.extractor_args() if adapter else {}
  if extractor_args:
    options["extractor_args"] = extractor_args
  cookie_file = _write_cookie_file(page_url, cookies)
  if cookie_file:
    options["cookiefile"] = cookie_file

  try:
    with yt_dlp.YoutubeDL(options) as downloader:
      info = downloader.extract_info(page_url, download=False)
      if not isinstance(info, dict) or info.get("_type") not in {"playlist", "multi_video"}:
        raise CompanionError("El enlace no corresponde a una lista compatible.")
      declared_count = info.get("playlist_count")
      try:
        if declared_count is not None and int(declared_count) > max_items:
          raise CompanionError(
              f"La lista supera el límite configurado de {max_items} videos; no se inició ninguna descarga."
          )
      except (TypeError, ValueError):
        pass

      raw_entries = info.get("entries")
      if raw_entries is None:
        raise CompanionError("El sitio no devolvió los videos de la lista.")
      entries = []
      for position, entry in enumerate(raw_entries, start=1):
        if position > max_items:
          raise CompanionError(
              f"La lista supera el límite configurado de {max_items} videos; no se inició ninguna descarga."
          )
        item_url = _playlist_entry_url(entry, page_url, provider)
        entries.append({
            "position": position,
            "url": item_url,
            "title": str((entry or {}).get("title") or "").strip()[:500],
        })
      if not entries:
        raise CompanionError("La lista no contiene videos disponibles para procesar.")
      return {
          "title": str(info.get("title") or "Lista de reproducción").strip()[:500],
          "entries": entries,
      }
  except CompanionError:
    raise
  except Exception as error:
    error_message = str(error).lower()
    if "javascript runtime" in error_message or "yt-dlp-ejs" in error_message:
      if adapter:
        raise CompanionError(adapter.runtime_requirement_message()) from error
      raise CompanionError("Instala un runtime de JavaScript compatible y vuelve a intentarlo.") from error
    if adapter:
      friendly_error = adapter.extraction_error(error, cookies, None, log_collector.flags)
      if friendly_error:
        raise CompanionError(friendly_error) from error
    raise CompanionError("No se pudo leer la lista. Comprueba el enlace y el acceso desde el navegador.") from error
  finally:
    if cookie_file:
      try:
        os.unlink(cookie_file)
      except OSError:
        pass


def _media_from_format(
    selected, page_url, cookies, user_agent, title, audio_track=None, accept_language="",
):
  if not isinstance(selected, dict) or not selected.get("url"):
    raise CompanionError("El sitio no ofreció un flujo de video que XDM pueda recibir.")

  media_url = selected["url"]
  protocol = str(selected.get("protocol") or "").lower()
  extension = str(selected.get("ext") or "")
  media_path = urlparse(str(media_url)).path.lower()
  title = str(title or "video").strip()[:500] or "video"
  mime_type = str(selected.get("mime_type") or "")
  if "dash" in protocol or extension == "mpd" or media_path.endswith(".mpd"):
    mime_type = "application/dash+xml"
  elif "m3u8" in protocol or extension == "m3u8" or media_path.endswith(".m3u8"):
    mime_type = "application/vnd.apple.mpegurl"
  elif not mime_type:
    mime_type = {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "m3u8": "application/vnd.apple.mpegurl",
        "mpd": "application/dash+xml",
    }.get(extension, "video/" + extension if extension else "video/*")

  # Retain only request headers that are commonly required by media CDNs.
  media_headers = {}
  extracted_headers = selected.get("http_headers") or {}
  for name, value in extracted_headers.items():
    if name.lower() in {
        "user-agent",
        "referer",
        "origin",
        "accept",
        "accept-language",
    } and value:
      media_headers[name] = [str(value)]
  if user_agent:
    media_headers.setdefault("User-Agent", [user_agent])
  media_headers.setdefault("Referer", [page_url])
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  audio_preferences = adapter.media_audio_track(audio_track) if adapter else audio_track
  language_header = (
      adapter.media_accept_language(audio_track, accept_language)
      if adapter else _accept_language_header(audio_preferences, accept_language)
  )
  if language_header:
    # Use only the original audio track. Browser locale may be an auto-dub.
    for header_name in list(media_headers):
      if header_name.lower() == "accept-language":
        media_headers.pop(header_name)
    media_headers["Accept-Language"] = [language_header]

  height = _format_height(selected)
  try:
    fps = float(selected.get("fps") or 0)
  except (TypeError, ValueError):
    fps = 0

  return {
      "url": media_url,
      "title": title,
      "filename": f"{title}.{extension}" if extension else title,
      "mime_type": mime_type,
      "headers": media_headers,
      "cookie": _cookie_header_for_url(cookies, media_url),
      "page_url": page_url,
      "protocol": protocol,
      "height": height,
      "fps": fps,
      "ext": extension,
      "format_id": str(selected.get("format_id") or ""),
      # XDM parses a DASH manifest itself. Keep the original source URL and
      # original audio language so the local relay removes alternate tracks.
      "source_manifest_url": selected.get("_xdm_companion_source_manifest_url"),
      "audio_language": _normalize_language((audio_preferences or {}).get("language")),
      "audio_track_id": str((audio_preferences or {}).get("id") or "").strip()[:128],
      "audio_track_name": str((audio_preferences or {}).get("name") or "").strip()[:100],
  }


def _extract_media(page_url, cookies, user_agent, browser_title, audio_track=None, accept_language=""):
  info = _extract_video_info(page_url, cookies, user_agent, audio_track, accept_language)
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  if adapter:
    audio_track = adapter.original_audio_track(info, audio_track)
  selected = None
  requested = info.get("requested_downloads") or []
  if requested and isinstance(requested[0], dict):
    selected = requested[0]
  if selected is None and info.get("url"):
    selected = info
  title = str(info.get("title") or browser_title or "video").strip()[:500] or "video"
  return _media_from_format(
      selected, page_url, cookies, user_agent, title, audio_track, accept_language,
  )


def _format_height(video_format):
  """Read resolution from extractor metadata without relying on a site name."""
  try:
    height = float(video_format.get("height") or 0)
  except (TypeError, ValueError):
    height = 0
  if math.isfinite(height) and height > 0:
    return int(height)

  resolution = str(video_format.get("resolution") or "").strip().lower()
  match = re.fullmatch(r"\d+\s*x\s*(\d+)", resolution)
  if not match:
    match = re.fullmatch(r"(\d+)\s*p(?:\d+)?", resolution)
  if not match:
    return 0
  try:
    return int(match.group(1))
  except (TypeError, ValueError):
    return 0


def _format_score(video_format):
  try:
    height = _format_height(video_format)
  except (TypeError, ValueError):
    height = 0
  try:
    fps = float(video_format.get("fps") or 0)
  except (TypeError, ValueError):
    fps = 0
  try:
    bitrate = float(video_format.get("tbr") or 0)
  except (TypeError, ValueError):
    bitrate = 0
  has_audio = str(video_format.get("acodec") or "").lower() not in {"", "none"}
  return height, has_audio, fps, bitrate, video_format.get("ext") == "mp4"


def _audio_language_match_score(video_format, preferred_languages):
  if not preferred_languages or str(video_format.get("acodec") or "").lower() in {"", "none"}:
    return 0
  actual = _normalize_language(video_format.get("language"))
  if not actual:
    return 0
  actual_lower = actual.lower()
  for preferred in preferred_languages:
    preferred_lower = preferred.lower()
    if actual_lower == preferred_lower:
      return 3
    if actual_lower.split("-", 1)[0] == preferred_lower.split("-", 1)[0]:
      return 2
  return -1




def _format_choice_score(video_format, preferred_languages=()):
  height, has_audio, fps, bitrate, is_mp4 = _format_score(video_format)
  manifest_source = _manifest_source(video_format)
  url_path = urlparse(str(video_format.get("url") or "")).path.lower()
  has_dash_manifest = url_path.endswith(".mpd") or bool(
      manifest_source and manifest_source[1] == "http_dash_segments"
  )
  language_match = _audio_language_match_score(video_format, preferred_languages)
  audio_is_default = _is_original_audio_format(video_format)
  return (
      height, audio_is_default, language_match, has_audio,
      bool(manifest_source or has_dash_manifest), fps, bitrate, is_mp4,
  )


def _has_audio_track_metadata(info):
  """Whether any audio rendition carries labels that could distinguish tracks."""
  for video_format in (info or {}).get("formats") or []:
    if not isinstance(video_format, dict):
      continue
    if str(video_format.get("acodec") or "").lower() in {"", "none"}:
      continue
    track = video_format.get("audio_track")
    track = track if isinstance(track, dict) else {}
    if (
        _normalize_language(video_format.get("language"))
        or str(video_format.get("audio_track_id") or "").strip()
        or str(video_format.get("audio_track_name") or track.get("name")
               or track.get("displayName") or "").strip()
        or video_format.get("audio_track_is_default") is False
    ):
      return True
    try:
      if float(video_format.get("language_preference") or 0) <= -10:
        return True
    except (TypeError, ValueError):
      pass
  return False


def _format_size(value):
  try:
    size = int(value)
  except (TypeError, ValueError):
    return ""
  if size <= 0:
    return ""
  if size >= 1024 ** 3:
    return f"~{size / (1024 ** 3):.1f} GB"
  return f"~{size / (1024 ** 2):.0f} MB"




def _format_for_xdm(video_format):
  """Use a specific muxed stream; use its manifest only when audio is separate."""
  has_audio = str(video_format.get("acodec") or "").lower() not in {"", "none"}
  protocol = str(video_format.get("protocol") or "").lower()
  if has_audio and protocol not in {"http_dash_segments", "http_dash_segments_generator"}:
    # A master manifest can contain several audio languages. Replacing a
    # language-specific muxed URL with that generic manifest lets XDM choose
    # a different default track from the one the browser is playing.
    return video_format
  source = _manifest_source(video_format)
  if not source:
    media_url = str(video_format.get("url") or "")
    url_path = urlparse(media_url).path.lower()
    if "dash" not in protocol and not url_path.endswith(".mpd"):
      return video_format
    result = dict(video_format)
    result["_xdm_companion_source_manifest_url"] = media_url
    return result
  manifest_url, protocol = source
  result = dict(video_format)
  result["url"] = manifest_url
  result["protocol"] = protocol
  if protocol == "http_dash_segments":
    result["_xdm_companion_source_manifest_url"] = manifest_url
  if protocol == "http_dash_segments":
    result["mime_type"] = "application/dash+xml"
  elif not result.get("mime_type"):
    result["mime_type"] = "application/vnd.apple.mpegurl"
  return result




def _available_formats(info, page_url, cookies, user_agent, audio_track=None, accept_language=""):
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  audio_preferences = adapter.original_audio_track(info, audio_track) if adapter else audio_track
  original_audio_language = _normalize_language((audio_preferences or {}).get("language"))
  preferred_languages = (
      adapter.preferred_audio_languages(info, audio_track, accept_language)
      if adapter else _preferred_audio_languages(None, accept_language)
  )
  formats_by_height = {}
  for video_format in info.get("formats") or []:
    if not isinstance(video_format, dict):
      continue
    if _has_confirmed_drm(video_format):
      continue
    if video_format.get("vcodec") in (None, "", "none"):
      continue
    height = _format_height(video_format)
    if height <= 0 or not video_format.get("format_id"):
      continue
    url_path = urlparse(str(video_format.get("url") or "")).path.lower()
    manifest_source = _manifest_source(video_format)
    is_dash_manifest = (
        url_path.endswith(".mpd")
        or bool(manifest_source and manifest_source[1] == "http_dash_segments")
    )
    media_format = dict(_format_for_xdm(video_format))
    media_format["height"] = height
    if not media_format.get("url"):
      continue
    has_audio = str(video_format.get("acodec") or "").lower() not in {"", "none"}
    is_original_audio = (
        adapter.audio_format_is_original(video_format, audio_preferences)
        if adapter else _is_original_audio_format(video_format, audio_preferences)
    )
    if adapter:
      if not adapter.accepts_format(
          video_format, audio_preferences, original_audio_language,
          is_dash_manifest, has_audio, is_original_audio,
          audio_absence_confirmed=info.get("_xdm_companion_audio_absence_confirmed") is True,
          audio_streams_present_confirmed=(
              info.get("_xdm_companion_audio_streams_present") is True
          ),
          allow_unlabelled_muxed=(
              info.get("_xdm_companion_audio_streams_present") is True
              and not audio_preferences
              and not original_audio_language
              and not _has_audio_track_metadata(info)
          ),
      ):
        continue
    elif not has_audio and not is_dash_manifest:
      continue
    current = formats_by_height.get(height)
    # At a given height, keep a combined stream or XDM-readable manifest ahead
    # of a raw video-only URL, even if the latter reports a larger bitrate.
    if current is None or _format_choice_score(
        video_format, preferred_languages,
    ) > _format_choice_score(current[0], preferred_languages):
      formats_by_height[height] = (video_format, media_format)

  ordered_heights = sorted(formats_by_height, reverse=True)
  # Rank the actual tiers exposed for this video and keep only the best options.
  # Do not use fixed resolution thresholds: available heights vary by source.
  shown_heights = ordered_heights[:4]

  options = []
  for height in shown_heights:
    video_format, media_format = formats_by_height[height]
    title = str(info.get("title") or "video").strip()[:500] or "video"
    media = _media_from_format(
        media_format, page_url, cookies, user_agent, title, audio_preferences, accept_language,
    )
    audio_absence_confirmed = info.get("_xdm_companion_audio_absence_confirmed") is True
    if audio_absence_confirmed:
      media["audio_absence_confirmed"] = True
    if info.get("_xdm_companion_audio_streams_present") is True:
      media["audio_streams_present_confirmed"] = True
    if audio_preferences and audio_preferences.get("is_original") is True:
      media["audio_track_is_original"] = True
      if audio_preferences.get("inferred") is True:
        media["audio_track_inferred"] = True
    format_path = urlparse(str(video_format.get("url") or "")).path.lower()
    format_manifest = _manifest_source(video_format)
    format_is_manifest = format_path.endswith(".mpd") or bool(
        format_manifest and format_manifest[1] == "http_dash_segments"
    )
    filesize = video_format.get("filesize") or video_format.get("filesize_approx")
    option = {
        "format_id": media["format_id"],
        "height": height,
        "fps": media["fps"] or None,
        "ext": media["ext"],
        "filesize": _format_size(filesize),
        "label": (
            f"{height}p (sin audio)"
          if audio_absence_confirmed
          or (
              str(video_format.get("acodec") or "").lower() in {"", "none"}
              and not (format_is_manifest or format_manifest)
          )
            else f"{height}p"
        ),
    }
    audio_language = _normalize_language(video_format.get("language"))
    if audio_language and str(video_format.get("acodec") or "").lower() not in {"", "none"}:
      option["audio_language"] = audio_language
    options.append((option, media))
  return options


def _format_inventory(info, available):
  """Summarize discovered and XDM-compatible heights without exposing URLs."""
  source_heights = set()
  standalone_video_heights = set()
  for video_format in info.get("formats") or []:
    if not isinstance(video_format, dict) or _has_confirmed_drm(video_format):
      continue
    if video_format.get("vcodec") in (None, "", "none") or not video_format.get("url"):
      continue
    height = _format_height(video_format)
    if height <= 0:
      continue
    source_heights.add(height)
    has_audio = str(video_format.get("acodec") or "").lower() not in {"", "none"}
    has_manifest = bool(_manifest_source(video_format))
    if not has_audio and not has_manifest:
      standalone_video_heights.add(height)
  source_heights.update(
      height for height in (info.get("_xdm_companion_source_heights") or [])
      if isinstance(height, int) and height > 0
  )
  compatible_heights = {option["height"] for option, _media in available}
  return {
      "source_heights": sorted(source_heights, reverse=True),
      "compatible_heights": sorted(compatible_heights, reverse=True),
      "standalone_video_heights": sorted(standalone_video_heights, reverse=True),
      "drm_filtered": bool(info.get("_has_drm")),
  }




def _store_format_session(options):
  token = uuid.uuid4().hex
  now = time.time()
  with _format_sessions_lock:
    expired = [
        key for key, value in _format_sessions.items()
        if value["expires_at"] <= now
    ]
    for key in expired:
      _format_sessions.pop(key, None)
    if len(_format_sessions) >= MAX_FORMAT_SESSIONS:
      oldest = min(_format_sessions, key=lambda key: _format_sessions[key]["created_at"])
      _format_sessions.pop(oldest, None)
    _format_sessions[token] = {
        "created_at": now,
        "expires_at": now + FORMAT_SESSION_TTL_SECONDS,
        "formats": {option["format_id"]: (option, media) for option, media in options},
    }
  return token


def _consume_format_selection(token, format_id):
  if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{32}", token):
    return None
  with _format_sessions_lock:
    session = _format_sessions.get(token)
    if session and session["expires_at"] <= time.time():
      _format_sessions.pop(token, None)
      return None
    if not session:
      return None
    selection = session["formats"].get(format_id)
    if selection:
      # A menu session is one-shot, preventing a double click from queueing
      # two downloads for the same video.
      _format_sessions.pop(token, None)
    return selection


def _ensure_xdm_ready():
  try:
    sync_response = xdm_http.get(
        f"{XDM_BASE_URL}/sync", timeout=XDM_TIMEOUT
    )
  except requests.RequestException as error:
    raise CompanionError(
        "XDM no está disponible. Inicia XDM y confirma que la integración del "
        "navegador esté habilitada."
    ) from error

  if sync_response.status_code != 200:
    raise CompanionError(
        "La integración local de XDM no respondió. Inicia XDM o revisa su "
        "integración del navegador."
    )
  try:
    sync_state = sync_response.json()
  except ValueError as error:
    raise CompanionError("La versión de XDM no reconoce la integración local requerida.") from error
  if sync_state.get("enabled") is not True:
    raise CompanionError("La integración del navegador de XDM no está habilitada.")


def _resolve_format_options(
    payload, page_url, cookies, user_agent, audio_track, accept_language,
):
  adapter = adapter_for_url(page_url, SITE_CATALOG)
  resolver = getattr(adapter, "resolve_format_options", None) if adapter else None
  if resolver:
    return resolver(
        payload,
        page_url,
        cookies,
        user_agent,
        audio_track,
        accept_language,
        extract_info=_extract_video_info,
        available_formats=_available_formats,
        format_inventory=_format_inventory,
        store_session=_store_format_session,
        logger=app.logger,
    )

  info = _extract_video_info(page_url, cookies, user_agent, audio_track, accept_language)
  available = _available_formats(
      info, page_url, cookies, user_agent, audio_track, accept_language,
  )
  inventory = _format_inventory(info, available)
  if not available:
    raise CompanionError("No se encontraron resoluciones compatibles con XDM para este video.")
  token = _store_format_session(available)
  return info, available, inventory, token, None


def _playlist_snapshot(job):
  return {
      "id": job["id"],
      "title": job.get("title") or "Lista de reproducción",
      "status": job["status"],
      "total": job.get("total"),
      "downloadable_count": job.get("downloadable_count", 0),
      "selection_limit": job.get("selection_limit"),
      "processed": job.get("processed", 0),
      "queued": job.get("queued", 0),
      "skipped": job.get("skipped", 0),
      "failed": job.get("failed", 0),
      "current_position": job.get("current_position"),
      "current_title": job.get("current_title", ""),
      "message": job.get("message", ""),
      "issues": list(job.get("issues", [])),
  }


def _prune_playlist_jobs_locked(now=None):
  now = time.time() if now is None else now
  for job_id, job in list(_playlist_jobs.items()):
    finished_expired = (
        job.get("finished_at")
        and now - job["finished_at"] > PLAYLIST_JOB_TTL_SECONDS
    )
    preview_expired = (
        job.get("status") == "awaiting_selection"
        and now - job.get("created_at", now) > PLAYLIST_PREVIEW_TTL_SECONDS
    )
    if finished_expired or preview_expired:
      _playlist_jobs.pop(job_id, None)
      if _active_playlist_urls.get(job.get("page_url")) == job_id:
        _active_playlist_urls.pop(job.get("page_url"), None)
  if len(_playlist_jobs) >= MAX_PLAYLIST_JOBS:
    finished = sorted(
        (job for job in _playlist_jobs.values() if job.get("finished_at")),
        key=lambda job: job["finished_at"],
    )
    while len(_playlist_jobs) >= MAX_PLAYLIST_JOBS and finished:
      _playlist_jobs.pop(finished.pop(0)["id"], None)


def _add_playlist_issue(job, position, title, outcome, reason):
  issue = {
      "position": position,
      "title": str(title or "Video")[:160],
      "outcome": outcome,
      "reason": re.sub(r"https?://\S+", "[enlace]", str(reason or ""))[:240],
  }
  with _playlist_jobs_lock:
    job["issues"].append(issue)
    if len(job["issues"]) > 30:
      del job["issues"][:-30]


def _preview_playlist_job(job_id, page_url, cookies, user_agent, accept_language):
  with _playlist_jobs_lock:
    job = _playlist_jobs.get(job_id)
  if not job:
    return
  try:
    playlist = _extract_playlist_entries(page_url, cookies, user_agent, accept_language)
    entries = playlist["entries"]
    with _playlist_jobs_lock:
      if job.get("status") != "enumerating":
        return
      job["title"] = playlist["title"]
      job["total"] = len(entries)
      job["downloadable_count"] = sum(bool(entry.get("url")) for entry in entries)
      job["_entries"] = entries
      job["status"] = "awaiting_selection"
      job["message"] = "Elige cuántos videos de la lista quieres enviar a XDM."
  except CompanionError as error:
    with _playlist_jobs_lock:
      job["status"] = "failed"
      job["message"] = str(error)[:300]
      job["finished_at"] = time.time()
      _active_playlist_urls.pop(page_url, None)
    return
  except Exception:
    app.logger.exception("No se pudo enumerar una lista de reproducción.")
    with _playlist_jobs_lock:
      job["status"] = "failed"
      job["message"] = "No se pudo leer la lista de reproducción."
      job["finished_at"] = time.time()
      _active_playlist_urls.pop(page_url, None)
    return

def _process_playlist_entries(job_id, page_url, entries, cookies, user_agent, accept_language):
  with _playlist_jobs_lock:
    job = _playlist_jobs.get(job_id)
  if not job:
    return
  with _playlist_jobs_lock:
    job["status"] = "processing"
    job["total"] = len(entries)
    job["processed"] = 0
    job["queued"] = 0
    job["skipped"] = 0
    job["failed"] = 0
    job["current_position"] = None
    job["current_title"] = ""
    job["message"] = "Resolviendo y enviando cada video a XDM en orden."

  width = max(3, len(str(len(entries))))
  for entry in entries:
    position = entry["position"]
    title = entry.get("title") or f"Video {position}"
    with _playlist_jobs_lock:
      job["current_position"] = position
      job["current_title"] = title[:160]
      job["message"] = f"Procesando video {position} de {len(entries)}."
    if not entry.get("url"):
      _add_playlist_issue(
          job, position, title, "skipped", "El sitio no entregó un enlace individual válido.",
      )
      with _playlist_jobs_lock:
        job["skipped"] += 1
        job["processed"] += 1
      continue

    item_url = entry["url"]
    item_payload = {
        "url": item_url,
        "cookies": cookies,
        "user_agent": user_agent,
        "accept_language": accept_language,
        "title": title,
    }
    try:
      _info, available, _inventory, session, _notice = _resolve_format_options(
          item_payload,
          item_url,
          cookies,
          user_agent,
          None,
          accept_language,
      )
      if not available:
        raise CompanionError("No se encontró una calidad compatible con XDM.")
      option, _unused_media = available[0]
      selection = _consume_format_selection(session, option["format_id"])
      if not selection:
        raise CompanionError("La sesión de formato venció antes de procesar el video.")
      _selected_option, media = selection
    except CompanionError as error:
      _add_playlist_issue(job, position, title, "skipped", str(error))
      with _playlist_jobs_lock:
        job["skipped"] += 1
        job["processed"] += 1
      continue
    except Exception:
      app.logger.exception("No se pudo resolver un elemento de una lista de reproducción.")
      _add_playlist_issue(
          job, position, title, "skipped", "No se pudo resolver una calidad compatible.",
      )
      with _playlist_jobs_lock:
        job["skipped"] += 1
        job["processed"] += 1
      continue

    numbered_title = f"{position:0{width}d} - {media.get('title') or title}"[:500]
    media["title"] = numbered_title
    extension = str(media.get("ext") or "").strip()
    media["filename"] = f"{numbered_title}.{extension}" if extension else numbered_title
    try:
      _send_to_xdm(media, preferred_height=option["height"])
    except CompanionError as error:
      _add_playlist_issue(job, position, title, "failed", str(error))
      with _playlist_jobs_lock:
        job["failed"] += 1
        job["processed"] += 1
      continue
    except Exception:
      app.logger.exception("XDM rechazó un elemento de una lista de reproducción.")
      _add_playlist_issue(
          job, position, title, "failed", "No se pudo entregar el video a XDM.",
      )
      with _playlist_jobs_lock:
        job["failed"] += 1
        job["processed"] += 1
      continue

    with _playlist_jobs_lock:
      job["queued"] += 1
      job["processed"] += 1

  with _playlist_jobs_lock:
    job["status"] = "completed_with_issues" if job["skipped"] or job["failed"] else "completed"
    job["current_position"] = None
    job["current_title"] = ""
    job["message"] = "Lista enviada a XDM." if job["status"] == "completed" else "Lista procesada con videos omitidos o fallidos."
    job["finished_at"] = time.time()
    _active_playlist_urls.pop(page_url, None)


def _process_playlist_job(job_id, page_url, cookies, user_agent, accept_language):
  """Compatibility worker used by tests and older in-process callers."""
  with _playlist_jobs_lock:
    job = _playlist_jobs.get(job_id)
  if not job:
    return
  try:
    playlist = _extract_playlist_entries(page_url, cookies, user_agent, accept_language)
    entries = playlist["entries"]
    with _playlist_jobs_lock:
      job["title"] = playlist["title"]
      job["downloadable_count"] = sum(bool(entry.get("url")) for entry in entries)
      job["_entries"] = entries
    _process_playlist_entries(job_id, page_url, entries, cookies, user_agent, accept_language)
  except CompanionError as error:
    with _playlist_jobs_lock:
      job["status"] = "failed"
      job["message"] = str(error)[:300]
      job["finished_at"] = time.time()
      _active_playlist_urls.pop(page_url, None)
  except Exception:
    app.logger.exception("No se pudo procesar una lista de reproducción.")
    with _playlist_jobs_lock:
      job["status"] = "failed"
      job["message"] = "No se pudo procesar la lista de reproducción."
      job["finished_at"] = time.time()
      _active_playlist_urls.pop(page_url, None)


















def _send_to_xdm(media, preferred_height=None):
  media = dict(media)
  adapter = adapter_for_url(media.get("page_url", ""), SITE_CATALOG)
  if adapter:
    media = adapter.prepare_media_for_handoff(media)

  handoff_tab_id = f"xdm-companion-{uuid.uuid4().hex}"
  message = {
      "url": media["url"],
      "cookie": media["cookie"],
      "requestHeaders": media["headers"],
      "responseHeaders": {"Content-Type": [media["mime_type"]]},
      # XDM adds the output container extension itself for HLS/DASH streams.
      "filename": media["title"] if _is_manifest(media) else media["filename"],
      "tabUrl": media["page_url"],
      "tabTitle": media["title"],
      "tabId": handoff_tab_id,
      "referer": media["page_url"],
      "mimeType": media["mime_type"],
  }
  is_manifest = _is_manifest(media)
  endpoint = "/media" if is_manifest else "/download"
  try:
    response = xdm_http.post(
        f"{XDM_BASE_URL}{endpoint}",
        json=message,
        timeout=XDM_TIMEOUT,
    )
  except requests.RequestException as error:
    raise CompanionError("No se pudo entregar el video a XDM.") from error

  if response.status_code != 200:
    raise CompanionError(
        "XDM rechazó el video. Actualiza XDM y activa su integración del navegador."
    )
  if is_manifest:
    _start_detected_video(handoff_tab_id, preferred_height)
  return "queued"


def _process_intercept_job(page_url, cookies, user_agent, browser_title, audio_track=None, accept_language=""):
  try:
    media = _extract_media(
        page_url, cookies, user_agent, browser_title, audio_track, accept_language,
    )
    result = _send_to_xdm(media)
    app.logger.info("Video enviado a XDM: %s (%s)", media["title"], result)
  except CompanionError as error:
    app.logger.warning("No se pudo enviar el video a XDM desde %s: %s", page_url, error)
  except Exception:
    app.logger.exception("Error inesperado al procesar el video de %s", page_url)
  finally:
    with _active_urls_lock:
      _active_urls.discard(page_url)


def _start_intercept_job(
    page_url, cookies, user_agent, browser_title, audio_track=None, accept_language="",
):
  worker = threading.Thread(
      target=_process_intercept_job,
      args=(page_url, cookies, user_agent, browser_title, audio_track, accept_language),
      daemon=True,
      name="xdm-companion-video",
  )
  worker.start()


def _is_manifest(media):
  media_path = urlparse(media["url"]).path.lower()
  return (
      "m3u8" in media["protocol"]
      or "dash" in media["protocol"]
      or media_path.endswith((".m3u8", ".mpd"))
      or "mpegurl" in media["mime_type"].lower()
      or "dash+xml" in media["mime_type"].lower()
  )


def _video_quality_score(video):
  description = str(video.get("info") or "").lower()
  heights = [int(value) for value in re.findall(r"\b(\d{3,4})p\b", description)]
  heights.extend(
      int(value)
      for value in re.findall(r"\b\d{3,4}\s*x\s*(\d{3,4})\b", description)
  )
  rates = []
  for value, unit in re.findall(r"([\d,.]+)\s*(kbps|mbps)\b", description):
    try:
      rate = float(value.replace(",", ""))
    except ValueError:
      continue
    rates.append(rate * (1000 if unit == "mbps" else 1))
  return max(heights, default=0), max(rates, default=0)


def _select_detected_variant(candidates, preferred_height=None):
  if not preferred_height:
    return max(candidates, key=_video_quality_score)
  exact = [
      video for video in candidates
      if _video_quality_score(video)[0] == preferred_height
  ]
  if exact:
    return max(exact, key=_video_quality_score)
  below = [
      video for video in candidates
      if 0 < _video_quality_score(video)[0] <= preferred_height
  ]
  if below:
    return max(below, key=_video_quality_score)
  return min(
      candidates,
      key=lambda video: (
          abs(_video_quality_score(video)[0] - preferred_height),
          -_video_quality_score(video)[1],
      ),
  )


def _start_detected_video(tab_id, preferred_height=None):
  """Wait for XDM to parse the submitted manifest and start the chosen variant."""
  deadline = time.monotonic() + 45
  while time.monotonic() < deadline:
    try:
      response = xdm_http.get(
          f"{XDM_BASE_URL}/sync", timeout=(1, 2)
      )
      if response.status_code == 200:
        videos = response.json().get("videoList", [])
        candidates = [
            video for video in videos
            if isinstance(video, dict) and video.get("tabId") == tab_id
        ]
        if candidates:
          selected = _select_detected_variant(candidates, preferred_height)
          video_id = int(selected.get("id"))
          start_response = xdm_http.post(
              f"{XDM_BASE_URL}/vid",
              json={"vid": video_id},
              timeout=XDM_TIMEOUT,
          )
          if start_response.status_code != 200:
            raise CompanionError("XDM detectó el flujo, pero no pudo iniciar su descarga.")
          return
    except CompanionError:
      raise
    except (requests.RequestException, ValueError, TypeError):
      pass
    time.sleep(0.4)

  raise CompanionError(
      "XDM recibió el flujo, pero no detectó una variante descargable. "
      "Comprueba que su integración del navegador esté habilitada y vuelve a hacer clic en el video."
  )


@app.get("/health")
def health():
  return jsonify({"status": "ok"})


@app.get("/manifest/<token>.mpd")
def filtered_manifest(token):
  if not _is_loopback_request():
    return "Forbidden", 403
  if not re.fullmatch(r"[0-9a-f]{32}", token):
    return "Not found", 404
  proxy = get_manifest_proxy(token)
  if not proxy:
    return "Manifest expired", 410
  response = make_response(proxy["body"], 200)
  response.headers["Content-Type"] = "application/dash+xml; charset=utf-8"
  response.headers["Cache-Control"] = "no-store, max-age=0"
  response.headers["X-Content-Type-Options"] = "nosniff"
  return response


@app.post("/formats")
def formats():
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"status": "error", "message": "Solicitud de video no válida."}), 400
  try:
    page_url, cookies, user_agent, browser_title, audio_track, accept_language = _safe_browser_data(payload)
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 400

  try:
    _ensure_xdm_ready()
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 502

  try:
    _info, available, inventory, token, notice = _resolve_format_options(
        payload,
        page_url,
        cookies,
        user_agent,
        audio_track,
        accept_language,
    )
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 422

  return jsonify({
      "status": "ready",
      "session": token,
      "formats": [option for option, _media in available],
      "audio_track": audio_track,
      "notice": notice,
  })


@app.post("/playlist/start")
def start_playlist():
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"status": "error", "message": "Solicitud de lista no válida."}), 400
  try:
    page_url, cookies, user_agent, _title, _audio_track, accept_language = _safe_browser_data(payload)
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 400
  page_url = _canonical_playlist_url(page_url)
  _provider, playlist_config = _playlist_config(page_url)
  if not playlist_config:
    return jsonify({"status": "error", "message": "Este sitio no tiene habilitada la descarga de listas."}), 422
  with _playlist_jobs_lock:
    _prune_playlist_jobs_locked()
    existing_job_id = _active_playlist_urls.get(page_url)
    if existing_job_id and existing_job_id in _playlist_jobs:
      return jsonify({
          "status": "accepted",
          "job": _playlist_snapshot(_playlist_jobs[existing_job_id]),
      })
    active_jobs = [
        job for job in _playlist_jobs.values()
        if job.get("status") in {"enumerating", "processing"}
    ]
    if active_jobs:
      return jsonify({
          "status": "error",
          "message": "XDM Companion ya está procesando otra lista. Espera a que termine.",
      }), 409

    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "page_url": page_url,
        "title": str(payload.get("title") or "Lista de reproducción").strip()[:500],
        "status": "enumerating",
        "total": None,
        "downloadable_count": 0,
        "selection_limit": None,
        "processed": 0,
        "queued": 0,
        "skipped": 0,
        "failed": 0,
        "current_position": None,
        "current_title": "",
        "message": "Leyendo la lista de reproducción.",
        "issues": [],
        "created_at": time.time(),
        "finished_at": None,
    }
    _playlist_jobs[job_id] = job
    _active_playlist_urls[page_url] = job_id

  worker = threading.Thread(
      target=_preview_playlist_job,
      args=(job_id, page_url, cookies, user_agent, accept_language),
      daemon=True,
      name="xdm-companion-playlist",
  )
  try:
    worker.start()
  except RuntimeError:
    with _playlist_jobs_lock:
      job["status"] = "failed"
      job["message"] = "No se pudo iniciar el procesamiento de la lista."
      job["finished_at"] = time.time()
      _active_playlist_urls.pop(page_url, None)
    return jsonify({"status": "error", "message": job["message"]}), 503
  with _playlist_jobs_lock:
    snapshot = _playlist_snapshot(job)
  return jsonify({"status": "accepted", "job": snapshot})


@app.post("/playlist/queue")
def queue_playlist():
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"status": "error", "message": "Solicitud de lista no válida."}), 400
  try:
    page_url, cookies, user_agent, _title, _audio_track, accept_language = _safe_browser_data(payload)
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 400

  page_url = _canonical_playlist_url(page_url)
  job_id = str(payload.get("job_id") or "")
  if not re.fullmatch(r"[0-9a-f]{32}", job_id):
    return jsonify({"status": "error", "message": "La vista previa de la lista venció. Vuelve a abrirla."}), 400
  requested_limit = payload.get("limit")
  if requested_limit == "all":
    requested_limit = 0
  if type(requested_limit) is not int or requested_limit not in {0, 5, 10, 20, 30, 40}:
    return jsonify({"status": "error", "message": "La cantidad seleccionada no es válida."}), 400

  try:
    _ensure_xdm_ready()
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 502

  with _playlist_jobs_lock:
    _prune_playlist_jobs_locked()
    job = _playlist_jobs.get(job_id)
    if not job or job.get("page_url") != page_url:
      return jsonify({
          "status": "error",
          "message": "La vista previa de la lista venció. Vuelve a abrirla.",
      }), 410
    if job.get("status") == "processing":
      return jsonify({"status": "accepted", "job": _playlist_snapshot(job)})
    if job.get("status") != "awaiting_selection":
      return jsonify({
          "status": "error",
          "message": job.get("message") or "La lista todavía no está lista para enviarse a XDM.",
      }), 409

    active_jobs = [
        other for other_id, other in _playlist_jobs.items()
        if other_id != job_id and other.get("status") == "processing"
    ]
    if active_jobs:
      return jsonify({
          "status": "error",
          "message": "XDM Companion ya está procesando otra lista. Espera a que termine.",
      }), 409

    entries = list(job.get("_entries") or [])
    candidates = [entry for entry in entries if entry.get("url")]
    selected_entries = candidates if requested_limit == 0 else candidates[:requested_limit]
    if not selected_entries:
      return jsonify({"status": "error", "message": "La lista no contiene videos con enlaces disponibles."}), 422
    job["status"] = "processing"
    job["total"] = len(selected_entries)
    job["selection_limit"] = "all" if requested_limit == 0 else requested_limit
    job["processed"] = 0
    job["queued"] = 0
    job["skipped"] = 0
    job["failed"] = 0
    job["current_position"] = None
    job["current_title"] = ""
    job["message"] = "Resolviendo y enviando los videos seleccionados a XDM en orden."
    snapshot = _playlist_snapshot(job)

  worker = threading.Thread(
      target=_process_playlist_entries,
      args=(job_id, page_url, selected_entries, cookies, user_agent, accept_language),
      daemon=True,
      name="xdm-companion-playlist-download",
  )
  try:
    worker.start()
  except RuntimeError:
    with _playlist_jobs_lock:
      job["status"] = "awaiting_selection"
      job["message"] = "No se pudo iniciar la descarga. Puedes volver a intentarlo."
      job["selection_limit"] = None
      job["total"] = len(job.get("_entries") or [])
    return jsonify({"status": "error", "message": job["message"]}), 503
  return jsonify({"status": "accepted", "job": snapshot})


@app.post("/playlist/status")
def playlist_status():
  payload = request.get_json(silent=True)
  job_id = str((payload or {}).get("job_id") or "") if isinstance(payload, dict) else ""
  if not re.fullmatch(r"[0-9a-f]{32}", job_id):
    return jsonify({"status": "error", "message": "La sesión de lista no es válida."}), 400
  with _playlist_jobs_lock:
    _prune_playlist_jobs_locked()
    job = _playlist_jobs.get(job_id)
    if not job:
      return jsonify({
          "status": "error",
          "message": "La sesión de lista venció. Inicia de nuevo desde la página de la lista.",
      }), 410
    snapshot = _playlist_snapshot(job)
  return jsonify({"status": "ready", "job": snapshot})


@app.post("/select")
def select_format():
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({"status": "error", "message": "Solicitud de resolución no válida."}), 400
  token = payload.get("session")
  format_id = payload.get("format_id")
  if not isinstance(format_id, str) or not format_id or len(format_id) > 128:
    return jsonify({"status": "error", "message": "La resolución seleccionada no es válida."}), 400

  selection = _consume_format_selection(token, format_id)
  if not selection:
    return jsonify({
        "status": "error",
        "message": "La lista de resoluciones venció. Vuelve a abrir el menú de XDM.",
    }), 410

  option, media = selection
  try:
    _send_to_xdm(media, preferred_height=option["height"])
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 502

  return jsonify({
      "status": "queued",
      "message": f"El video {option['label']} se envió a XDM.",
      "format": option,
  })


@app.post("/intercept")
def intercept():
  payload = request.get_json(silent=True)
  if not isinstance(payload, dict):
    return jsonify({
        "status": "error",
        "message": "Se requiere un cuerpo JSON con la URL de la pestaña.",
    }), 400

  try:
    page_url, cookies, user_agent, browser_title, audio_track, accept_language = _safe_browser_data(payload)
  except CompanionError as error:
    return jsonify({"status": "error", "message": str(error)}), 400

  with _active_urls_lock:
    if page_url in _active_urls:
      return jsonify({
          "status": "processing",
          "message": "Este video ya se está enviando a XDM.",
      }), 409
    _active_urls.add(page_url)

  try:
    _ensure_xdm_ready()
  except CompanionError as error:
    with _active_urls_lock:
      _active_urls.discard(page_url)
    return jsonify({"status": "error", "message": str(error)}), 502

  try:
    _start_intercept_job(
        page_url, cookies, user_agent, browser_title, audio_track, accept_language,
    )
  except Exception:
    with _active_urls_lock:
      _active_urls.discard(page_url)
    app.logger.exception("No se pudo iniciar el procesamiento de %s", page_url)
    return jsonify({"status": "error", "message": "No se pudo iniciar el procesamiento del video."}), 500

  return jsonify({
      "status": "accepted",
      "message": "XDM Companion está preparando el video para XDM.",
  }), 202


if __name__ == "__main__":
  print("[*] XDM Companion disponible solo en este equipo (127.0.0.1:21121).")
  app.run(host="127.0.0.1", port=21121, debug=False, threaded=True)
