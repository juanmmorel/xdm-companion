"""YouTube site adapter for player metadata, authenticated extraction, and DASH audio."""

import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from errors import CompanionError
from media_utils import (
    _accept_language_header, _cookie_domain_matches, _has_confirmed_drm,
    _is_original_audio_format, _manifest_source,
    _normalize_language,
)
from site_catalog import load_site_catalog, provider_for_url

SITE_CATALOG = load_site_catalog(Path(__file__).resolve().parents[1] / "domains.json")
ADAPTER_ID = Path(__file__).stem
PROVIDER = next(
    (provider for provider in SITE_CATALOG["providers"]
     if provider.get("backend", {}).get("adapter") == ADAPTER_ID),
    {},
)
BACKEND_CONFIG = PROVIDER.get("backend", {})
BROWSER_CONFIG = PROVIDER.get("browser", {})
MAX_URL_LENGTH = int(BACKEND_CONFIG.get("max_page_url_length", 8192))
MAX_MANIFEST_BYTES = int(BACKEND_CONFIG.get("manifest_max_bytes", 4194304))
MANIFEST_PROXY_TTL_SECONDS = int(BACKEND_CONFIG.get("manifest_proxy_ttl_seconds", 21600))
MAX_MANIFEST_PROXIES = int(BACKEND_CONFIG.get("manifest_proxy_limit", 64))
_manifest_proxies = {}
_manifest_proxies_lock = threading.Lock()

def _youtube_original_audio_track(info, audio_track=None):
  if isinstance(audio_track, dict) and audio_track.get("is_original") is True:
    return audio_track
  audio_formats = []
  for video_format in (info or {}).get("formats") or []:
    if not isinstance(video_format, dict):
      continue
    if str(video_format.get("acodec") or "").lower() in {"", "none"}:
      continue
    audio_formats.append(video_format)
    is_original = _is_original_audio_format(video_format)
    track_id = str(video_format.get("audio_track_id") or "").strip()[:128]
    language = _normalize_language(video_format.get("language"))
    if not language and track_id:
      language = _normalize_language(track_id.split(".", 1)[0])
    if is_original and language:
      return {
          "language": language,
          "id": track_id,
          "name": "",
          "is_original": True,
      }

  # Some YouTube clients omit both the explicit original marker and the DASH
  # master manifest. In that case yt-dlp can still expose the audio-only
  # renditions. Treat one unambiguous language/track as the original, but do
  # not guess when multiple tracks, an explicit non-default marker, or an
  # audio-description stream exists.
  languages = set()
  track_ids = set()
  has_non_default_marker = False
  has_descriptive_track = False
  for video_format in audio_formats:
    track_id = str(video_format.get("audio_track_id") or "").strip()[:128]
    language = _normalize_language(video_format.get("language"))
    if not language and track_id:
      language = _normalize_language(track_id.split(".", 1)[0])
    if language:
      languages.add(language.casefold())
    if track_id:
      track_ids.add(track_id.casefold())
    if video_format.get("audio_track_is_default") is False:
      has_non_default_marker = True
    try:
      if float(video_format.get("language_preference") or 0) <= -10:
        has_descriptive_track = True
    except (TypeError, ValueError):
      pass
  if (
      len(languages) == 1
      and len(track_ids) <= 1
      and not has_non_default_marker
      and not has_descriptive_track
  ):
    only_language = next(iter(languages))
    return {
        "language": only_language,
        "id": next(iter(track_ids), ""),
        "name": "",
        "is_original": True,
        "inferred": True,
    }
  return None

def _is_provider_url(page_url):
  provider = provider_for_url(page_url, SITE_CATALOG)
  return bool(provider and provider.get("backend", {}).get("adapter") == ADAPTER_ID)

def _youtube_video_id(page_url):
  try:
    parsed = urlparse(page_url)
  except (TypeError, ValueError):
    return ""
  player_config = BROWSER_CONFIG.get("player", {})
  short_domains = player_config.get("short_link_domains", [])
  if (parsed.hostname or "").lower().rstrip(".") in short_domains:
    candidate = parsed.path.strip("/").split("/", 1)[0]
  else:
    query = parse_qs(parsed.query)
    candidate = (query.get(str(player_config.get("video_id_query") or "v")) or [""])[0]
    if not candidate:
      for prefix in player_config.get("video_path_prefixes", []):
        if parsed.path.startswith(prefix):
          candidate = parsed.path[len(prefix):].split("/", 1)[0]
          break
  pattern = player_config.get("video_id_pattern") or "[A-Za-z0-9_-]{11}"
  return candidate if re.fullmatch(pattern, candidate) else ""

def _safe_youtube_playback(value, page_url, audio_track=None, cookies=None):
  """Normalize only the playable, non-DRM formats exposed by YouTube's player."""
  if not _is_provider_url(page_url) or not isinstance(value, dict):
    return None
  expected_video_id = _youtube_video_id(page_url)
  video_id = str(value.get("video_id") or "")[:32]
  if not video_id or (expected_video_id and video_id != expected_video_id):
    return None

  raw_manifest = str(value.get("dash_manifest_url") or "")[:MAX_URL_LENGTH]
  manifest_url = raw_manifest if raw_manifest and _youtube_manifest_url_is_safe(raw_manifest) else ""
  raw_formats = value.get("formats")
  if not isinstance(raw_formats, list):
    return None
  source_heights = set()
  raw_source_heights = value.get("source_heights")
  if isinstance(raw_source_heights, list):
    for value_height in raw_source_heights:
      try:
        height = int(value_height)
      except (TypeError, ValueError):
        continue
      if height > 0:
        source_heights.add(height)

  formats = []
  used_ids = set()
  for item in raw_formats:
    if not isinstance(item, dict):
      continue
    media_url = str(item.get("url") or "")[:MAX_URL_LENGTH]
    if not media_url or not _youtube_manifest_url_is_safe(media_url):
      continue
    raw_mime = str(item.get("mime_type") or "")[:256]
    mime_type = raw_mime.split(";", 1)[0].strip().lower()
    if not re.fullmatch(r"(?:audio|video)/(?:mp4|webm|3gpp|ogg)", mime_type):
      continue

    codec_match = re.search(r"codecs\s*=\s*[\"']?([^\"';]+)", raw_mime, re.IGNORECASE)
    codecs = [codec.strip().lower() for codec in (codec_match.group(1).split(",") if codec_match else [])]
    audio_prefixes = ("mp4a", "opus", "vorbis", "ac-3", "ec-3", "eac3", "flac", "alac")
    video_prefixes = ("avc", "av01", "vp8", "vp9", "hev", "hvc", "dvh", "theora")
    audio_codec = next((codec for codec in codecs if codec.startswith(audio_prefixes)), "")
    video_codec = next((codec for codec in codecs if codec.startswith(video_prefixes)), "")
    if mime_type.startswith("audio/"):
      audio_codec = audio_codec or mime_type.split("/", 1)[1]
      video_codec = "none"
    elif not video_codec:
      video_codec = mime_type.split("/", 1)[1]
    if not audio_codec:
      audio_codec = "none"

    track = item.get("audio_track") if isinstance(item.get("audio_track"), dict) else {}
    track_id = str(track.get("id") or "")[:128]
    track_is_default = track.get("is_default") is True
    track_default_known = track.get("is_default") is True or track.get("is_default") is False
    track_language = _normalize_language(track_id.split(".", 1)[0])
    if not track_language and track_is_default:
      track_language = _normalize_language((audio_track or {}).get("language")) if audio_codec != "none" else ""
    original_track_id = str((audio_track or {}).get("id") or "").strip()
    track_id_matches_original = bool(
        track_id and original_track_id and track_id.casefold() == original_track_id.casefold()
    )
    format_id = str(item.get("itag") or "")[:32]
    if not format_id:
      continue
    if track_id:
      safe_track_id = re.sub(r"[^A-Za-z0-9._-]", "", track_id)[:40]
      if safe_track_id:
        format_id = f"{format_id}-{safe_track_id}"
    if format_id in used_ids:
      continue
    used_ids.add(format_id)
    try:
      height = int(item.get("height") or 0)
    except (TypeError, ValueError):
      height = 0
    try:
      fps = float(item.get("fps") or 0)
    except (TypeError, ValueError):
      fps = 0
    try:
      bitrate = float(item.get("bitrate") or 0) / 1000
    except (TypeError, ValueError):
      bitrate = 0
    extension = {"video/mp4": "mp4", "audio/mp4": "m4a", "video/webm": "webm",
                 "audio/webm": "webm", "video/3gpp": "3gp", "audio/ogg": "ogg"}.get(mime_type, "mp4")
    normalized = {
        "format_id": format_id,
        "url": media_url,
        "height": height,
        "fps": fps,
        "tbr": bitrate,
        "vcodec": video_codec,
        "acodec": audio_codec,
        "ext": extension,
        "mime_type": raw_mime,
        "protocol": "https",
        "has_drm": item.get("drm") is True,
        "audio_is_default": track_is_default,
    }
    if track_id:
      normalized["audio_track_id"] = track_id
    if track_default_known:
      normalized["audio_track_is_default"] = track_is_default
    if track_id_matches_original:
      normalized["audio_is_default"] = True
    if track_language:
      normalized["language"] = track_language
    if manifest_url and video_codec != "none" and audio_codec == "none":
      normalized["manifest_url"] = manifest_url
    formats.append(normalized)

  if not formats:
    return None
  return {
      "title": str(value.get("title") or f"Video de {display_name()}").strip()[:500],
      "formats": formats,
      "availability": "public",
      "_xdm_companion_source_heights": sorted(source_heights, reverse=True),
      "_xdm_companion_diagnostics": {
          "source": "browser_player",
          "browser_account_cookies": _has_youtube_account_cookies(cookies or []),
          "yt_dlp_authenticated": None,
          "flags": [],
      },
  }

def _has_youtube_account_cookies(cookies):
  cookie_host = str(BACKEND_CONFIG.get("playback_cookie_host") or "")
  names = {
      str(cookie.get("name") or "").upper()
      for cookie in cookies
      if isinstance(cookie, dict)
      and _cookie_domain_matches(cookie_host, cookie)
      and bool(cookie.get("value"))
  }
  groups = [group for group in BROWSER_CONFIG.get("account_cookie_groups", []) if isinstance(group, list)]
  return bool(groups and all(
      {str(name).upper() for name in group}.intersection(names)
      for group in groups
  ))

def _safe_youtube_diagnostics(payload):
  """Keep only bounded counts/booleans from bridge diagnostics; never log cookie data."""
  payload_keys = BROWSER_CONFIG.get("payload_keys", {})
  browser = payload.get(payload_keys.get("browser_diagnostics"))
  player = payload.get(payload_keys.get("player_diagnostics"))
  browser = browser if isinstance(browser, dict) else {}
  player = player if isinstance(player, dict) else {}
  markers = browser.get("account_markers")
  markers = markers if isinstance(markers, dict) else {}

  def count(value):
    try:
      return max(0, min(100_000, int(value)))
    except (TypeError, ValueError):
      return 0

  def flag(value):
    return value is True

  query_results = []
  allowed_queries = {"page"}
  allowed_queries.update(
      str(item.get("kind") or "")
      for item in BROWSER_CONFIG.get("cookie_queries", [])
      if isinstance(item, dict)
  )
  for item in browser.get("queries") or []:
    if not isinstance(item, dict):
      continue
    kind = str(item.get("kind") or "")
    if kind not in allowed_queries:
      continue
    error = str(item.get("error") or "")
    error = error if re.fullmatch(r"[A-Za-z0-9_]{1,48}", error) else ""
    query_results.append({
        "kind": kind,
        "count": count(item.get("count")),
        "error": error,
    })
    if len(query_results) == 4:
      break

  heights = []
  for value in player.get("source_heights") or []:
    height = count(value)
    if 0 < height <= 16_000 and height not in heights:
      heights.append(height)
  heights.sort(reverse=True)
  return {
      "cookie_count": count(browser.get("cookie_count")),
      "account_markers": {
          name: flag(markers.get(name))
          for name in BROWSER_CONFIG.get("account_cookie_markers", {})
      },
      "queries": query_results,
      "player_found": flag(player.get("player_found")),
      "player_response_found": flag(player.get("response_found")),
      "player_video_id_matches": player.get("video_id_matches") is True,
      "player_formats": count(player.get("formats")),
      "player_adaptive_formats": count(player.get("adaptive_formats")),
      "player_video_formats": count(player.get("video_formats")),
      "player_direct_formats": count(player.get("direct_video_formats")),
      "player_encrypted_formats": count(player.get("encrypted_video_formats")),
      "player_drm_formats": count(player.get("drm_marked_formats")),
      "player_source_heights": heights,
      "player_dash_manifest": flag(player.get("dash_manifest")),
      "player_script_error": (
          str(player.get("script_error"))[:48]
          if re.fullmatch(r"[A-Za-z0-9_]{1,48}", str(player.get("script_error") or ""))
          else ""
      ),
  }

def _youtube_extraction_error(error, cookies, yt_dlp_authenticated=None, diagnostics=()):
  detail = str(error).lower()
  if any(str(term).lower() in detail for term in BACKEND_CONFIG.get("membership_error_terms", [])):
    if yt_dlp_authenticated is False:
      return (
          f"{display_name()} rechazó este video de membresía porque el extractor no reconoció una sesión activa. "
          "Comprueba que el puente esté instalado en el mismo perfil del navegador donde tienes la membresía."
      )
    if yt_dlp_authenticated is None and not _has_youtube_account_cookies(cookies):
      return (
          f"Este video es exclusivo para miembros, pero no se recibió una sesión activa de {display_name()}. "
          "Inicia sesión en el navegador con la cuenta que tiene la membresía y vuelve a abrir el video."
      )
    if "po_token_required" in diagnostics:
      return (
          f"El extractor recibió una sesión de {display_name()}, pero el proveedor no entregó el video de membresía: "
          "esta consulta requiere una validación de reproducción que XDM Companion no puede proporcionar. "
          "No se puede resolver mediante descifrado DRM."
      )
    return (
        f"El extractor recibió una sesión de {display_name()}, pero no confirmó acceso de membresía. "
        "Comprueba que sea la misma cuenta que tiene la membresía activa y recarga el video."
    )
  if any(str(term).lower() in detail for term in BACKEND_CONFIG.get("authentication_error_terms", [])):
    return f"{display_name()} rechazó la consulta. Recarga el video en el navegador e inténtalo de nuevo más tarde."
  return None

def _youtube_format_diagnostics(info, audio_track=None):
  """Summarize format shape for logs without exposing URLs, IDs, or titles."""
  if not isinstance(info, dict):
    return {}
  formats = info.get("formats") or []
  if not isinstance(formats, list):
    return {}
  original_track = _youtube_original_audio_track(info, audio_track)
  summary = {
      "formats": 0,
      "video": 0,
      "audio_only": 0,
      "video_with_url": 0,
      "video_with_audio": 0,
      "video_only_with_url": 0,
      "video_only_with_manifest": 0,
      "video_only_without_manifest": 0,
      "muxed_original": 0,
      "muxed_unknown_or_other": 0,
      "muxed_marked_nonoriginal": 0,
      "drm": 0,
      "original_track_known": bool(original_track),
      "audio_language_count": 0,
      "audio_track_id_count": 0,
      "audio_original_preference": 0,
      "audio_default_false": 0,
      "video_heights": set(),
      "video_with_url_heights": set(),
      "video_only_manifest_heights": set(),
  }
  audio_languages = set()
  audio_track_ids = set()
  for item in formats:
    if not isinstance(item, dict):
      continue
    summary["formats"] += 1
    if _has_confirmed_drm(item):
      summary["drm"] += 1
    video_codec = str(item.get("vcodec") or "").lower()
    audio_codec = str(item.get("acodec") or "").lower()
    has_video = video_codec not in {"", "none"}
    has_audio = audio_codec not in {"", "none"}
    if has_audio:
      language = _normalize_language(item.get("language"))
      track_id = str(item.get("audio_track_id") or "").strip()
      if not language and track_id:
        language = _normalize_language(track_id.split(".", 1)[0])
      if language:
        audio_languages.add(language.casefold())
      if track_id:
        audio_track_ids.add(track_id.casefold())
      try:
        preference = float(item.get("language_preference") or 0)
      except (TypeError, ValueError):
        preference = 0
      if preference > 0:
        summary["audio_original_preference"] += 1
      if item.get("audio_track_is_default") is False:
        summary["audio_default_false"] += 1
    has_url = isinstance(item.get("url"), str) and bool(item["url"])
    has_manifest = bool(_manifest_source(item))
    try:
      height = int(item.get("height") or 0)
    except (TypeError, ValueError):
      height = 0
    if has_video:
      summary["video"] += 1
      if height > 0:
        summary["video_heights"].add(height)
      if has_url:
        summary["video_with_url"] += 1
        if height > 0:
          summary["video_with_url_heights"].add(height)
      if has_audio:
        summary["video_with_audio"] += 1
        if has_audio and _is_original_audio_format(item, original_track):
          summary["muxed_original"] += 1
        elif item.get("audio_track_is_default") is False:
          summary["muxed_marked_nonoriginal"] += 1
        else:
          summary["muxed_unknown_or_other"] += 1
      elif has_url and has_manifest:
        summary["video_only_with_manifest"] += 1
        if height > 0:
          summary["video_only_manifest_heights"].add(height)
      elif has_url:
        summary["video_only_with_url"] += 1
        summary["video_only_without_manifest"] += 1
      elif has_manifest:
        summary["video_only_with_manifest"] += 1
        if height > 0:
          summary["video_only_manifest_heights"].add(height)
      else:
        summary["video_only_without_manifest"] += 1
    elif has_audio:
      summary["audio_only"] += 1
  for name in ("video_heights", "video_with_url_heights", "video_only_manifest_heights"):
    summary[name] = sorted(summary[name], reverse=True)
  summary["audio_language_count"] = len(audio_languages)
  summary["audio_track_id_count"] = len(audio_track_ids)
  return summary

def _youtube_manifest_url_is_safe(raw_url):
  try:
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    allowed_hosts = BACKEND_CONFIG.get("manifest_host_suffixes", [])
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and any(host == suffix or host.endswith("." + suffix) for suffix in allowed_hosts)
    )
  except (TypeError, ValueError):
    return False

def _canonical_language(value):
  language = _normalize_language(value).lower().replace("_", "-")
  if not language:
    return ""
  aliases = BACKEND_CONFIG.get("language_aliases", {})
  primary, *subtags = language.split("-")
  primary = aliases.get(primary, primary)
  return "-".join([primary, *subtags])

def _element_local_name(element):
  return element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""

def _is_dash_audio_adaptation(adaptation):
  content_type = str(adaptation.get("contentType") or "").lower()
  mime_type = str(adaptation.get("mimeType") or "").lower()
  if content_type == "audio" or mime_type.startswith("audio/"):
    return True
  for representation in adaptation:
    if _element_local_name(representation) != "Representation":
      continue
    representation_mime = str(representation.get("mimeType") or "").lower()
    if representation_mime.startswith("audio/") or representation.get("audioSamplingRate"):
      return True
  return False

def _resolve_dash_base_urls(root, manifest_url):
  """Make BaseURL references absolute before the manifest is served locally."""
  root_base_urls = [child for child in root if _element_local_name(child) == "BaseURL"]
  if not root_base_urls:
    base = ET.Element("BaseURL")
    base.text = manifest_url
    insert_at = next((
        index for index, child in enumerate(root)
        if _element_local_name(child) in {"Location", "Period", "Metrics"}
    ), len(root))
    root.insert(insert_at, base)

  def visit(element, inherited_base):
    child_bases = [child for child in element if _element_local_name(child) == "BaseURL"]
    resolved_bases = []
    for base_element in child_bases:
      value = (base_element.text or "").strip()
      if value:
        absolute = urljoin(inherited_base, value)
        base_element.text = absolute
        resolved_bases.append(absolute)
    effective_base = resolved_bases[0] if resolved_bases else inherited_base
    for child in element:
      if _element_local_name(child) == "Location" and (child.text or "").strip():
        child.text = urljoin(effective_base, child.text.strip())
      elif _element_local_name(child) != "BaseURL":
        visit(child, effective_base)

  visit(root, manifest_url)

def _filter_dash_manifest(manifest_body, manifest_url, preferred_language):
  if not preferred_language:
    raise CompanionError(f"{display_name()} no informó el idioma de audio que está reproduciendo el navegador.")
  if len(manifest_body) > MAX_MANIFEST_BYTES:
    raise CompanionError(f"El manifiesto de {display_name()} supera el tamaño permitido.")
  upper_body = manifest_body[:4096].upper()
  if b"<!DOCTYPE" in upper_body or b"<!ENTITY" in upper_body:
    raise CompanionError(f"El manifiesto de {display_name()} no tiene un formato XML seguro.")
  try:
    root = ET.fromstring(manifest_body)
  except ET.ParseError as error:
    raise CompanionError(f"{display_name()} no entregó un manifiesto DASH válido.") from error

  target = _canonical_language(preferred_language).split("-", 1)[0]
  found_audio = False
  retained_audio = 0
  for period in [element for element in root.iter() if _element_local_name(element) == "Period"]:
    period_audio = [
        element for element in period
        if _element_local_name(element) == "AdaptationSet" and _is_dash_audio_adaptation(element)
    ]
    if not period_audio:
      continue
    found_audio = True
    matching = []
    best_match_score = 0
    for adaptation in period_audio:
      language = _canonical_language(adaptation.get("lang"))
      if not language or language.split("-", 1)[0] != target:
        continue
      if language == _canonical_language(preferred_language):
        match_score = 3
      elif len(language.split("-")) == 1:
        match_score = 2
      else:
        match_score = 1
      if match_score > best_match_score:
        matching = [adaptation]
        best_match_score = match_score
      elif match_score == best_match_score:
        matching.append(adaptation)
    if not matching:
      raise CompanionError(
          f"El manifiesto de {display_name()} no contiene audio en {preferred_language}; "
          "no se envió a XDM para evitar descargar una pista en otro idioma."
      )
    retained_audio += len(matching)
    for adaptation in period_audio:
      if adaptation not in matching:
        period.remove(adaptation)

  if not found_audio or not retained_audio:
    raise CompanionError(
        f"{display_name()} no identificó las pistas de audio del manifiesto; no se envió a XDM "
        "para evitar elegir un idioma distinto."
    )

  _resolve_dash_base_urls(root, manifest_url)
  return ET.tostring(root, encoding="utf-8", xml_declaration=True)

def _read_youtube_manifest(manifest_url, headers, cookie):
  if not _youtube_manifest_url_is_safe(manifest_url):
    raise CompanionError(f"XDM Companion rechazó una URL de manifiesto de {display_name()} no válida.")
  remote_headers = {
      name: values[0]
      for name, values in (headers or {}).items()
      if name.lower() in {"user-agent", "referer", "accept-language", "accept"}
      and isinstance(values, list) and values and values[0]
  }
  if cookie:
    remote_headers["Cookie"] = cookie

  session = requests.Session()
  session.trust_env = False
  current_url = manifest_url
  response = None
  try:
    for _redirect in range(6):
      if not _youtube_manifest_url_is_safe(current_url):
        raise CompanionError(f"{display_name()} redirigió el manifiesto a un servidor no permitido.")
      response = session.get(
          current_url,
          headers=remote_headers,
          timeout=(5, 15),
          allow_redirects=False,
          stream=True,
      )
      if response.status_code in {301, 302, 303, 307, 308}:
        location = response.headers.get("Location")
        response.close()
        response = None
        if not location:
          raise CompanionError(f"{display_name()} devolvió una redirección de manifiesto incompleta.")
        current_url = urljoin(current_url, location)
        continue
      if response.status_code != 200:
        raise CompanionError(f"{display_name()} no permitió leer el manifiesto de audio del video.")
      chunks = []
      total = 0
      for chunk in response.iter_content(chunk_size=64 * 1024):
        if not chunk:
          continue
        total += len(chunk)
        if total > MAX_MANIFEST_BYTES:
          raise CompanionError(f"El manifiesto de {display_name()} supera el tamaño permitido.")
        chunks.append(chunk)
      final_url = response.url or current_url
      if not _youtube_manifest_url_is_safe(final_url):
        raise CompanionError(f"{display_name()} devolvió el manifiesto desde un servidor no permitido.")
      return b"".join(chunks), final_url
    raise CompanionError(f"{display_name()} redirigió demasiadas veces el manifiesto del video.")
  except requests.RequestException as error:
    raise CompanionError(f"No se pudo leer el manifiesto de audio de {display_name()}.") from error
  finally:
    if response is not None:
      response.close()
    session.close()

def _register_manifest_proxy(manifest_url, headers, cookie, preferred_language):
  if not preferred_language:
    raise CompanionError(
        f"{display_name()} no informó el idioma de audio que está reproduciendo el navegador. "
        "Recarga el video y vuelve a abrir el selector para evitar descargar otro idioma."
    )
  raw_body, final_url = _read_youtube_manifest(manifest_url, headers, cookie)
  filtered_body = _filter_dash_manifest(raw_body, final_url, preferred_language)
  token = uuid.uuid4().hex
  now = time.time()
  with _manifest_proxies_lock:
    expired = [
        key for key, value in _manifest_proxies.items()
        if value["expires_at"] <= now
    ]
    for key in expired:
      _manifest_proxies.pop(key, None)
    if len(_manifest_proxies) >= MAX_MANIFEST_PROXIES:
      oldest = min(_manifest_proxies, key=lambda key: _manifest_proxies[key]["created_at"])
      _manifest_proxies.pop(oldest, None)
    _manifest_proxies[token] = {
        "created_at": now,
        "expires_at": now + MANIFEST_PROXY_TTL_SECONDS,
        "body": filtered_body,
    }
  service_url = SITE_CATALOG.get("companion", {}).get("service_url", "http://127.0.0.1:21121")
  return f"{service_url}/manifest/{token}.mpd"


def get_manifest_proxy(token, now=None):
  """Return a live, one-time in-memory DASH manifest proxy entry."""
  now = time.time() if now is None else now
  with _manifest_proxies_lock:
    proxy = _manifest_proxies.get(token)
    if proxy and proxy["expires_at"] <= now:
      _manifest_proxies.pop(token, None)
      return None
    return proxy


def original_audio_track(info, audio_track=None):
  return _youtube_original_audio_track(info, audio_track)


def preferred_audio_languages(info, audio_track=None, accept_language=""):
  original = original_audio_track(info, audio_track)
  language = _normalize_language((original or {}).get("language"))
  return [language] if language else []


def media_audio_track(audio_track=None):
  return audio_track if (audio_track or {}).get("is_original") is True else None


def media_accept_language(audio_track=None, accept_language=""):
  return _accept_language_header(media_audio_track(audio_track), "")


def audio_format_is_original(video_format, original_track=None):
  return _is_original_audio_format(video_format, original_track)


def cookie_target_host():
  return str(BACKEND_CONFIG.get("playback_cookie_host") or "")


def display_name():
  return str(PROVIDER.get("display_name") or PROVIDER.get("id") or "video site")


def verbose_diagnostics():
  return BACKEND_CONFIG.get("verbose_diagnostics") is True


def checks_authenticated_session():
  return BACKEND_CONFIG.get("check_authenticated_session") is True


def extractor_key():
  return str(BACKEND_CONFIG.get("extractor_key") or "")


def extractor_args():
  value = BACKEND_CONFIG.get("extractor_args")
  return value if isinstance(value, dict) else {}


def runtime_requirement_message():
  name = display_name()
  return (
      f"Para extraer {name} instala Deno 2.3 o superior (recomendado) "
      "o Node.js 22 o superior y vuelve a intentarlo."
  )


def extraction_error(error, cookies, authenticated=None, diagnostics=()):
  return _youtube_extraction_error(error, cookies, authenticated, diagnostics)


def has_account_cookies(cookies):
  return _has_youtube_account_cookies(cookies)


def extraction_diagnostics(authenticated, flags):
  return {"yt_dlp_authenticated": authenticated, "flags": sorted(flags)}


def accepts_format(video_format, audio_preferences, original_audio_language,
                   is_dash_manifest, has_audio, is_original_audio):
  if not has_audio:
    return bool(is_dash_manifest and original_audio_language)

  format_track_id = str(video_format.get("audio_track_id") or "").strip()
  original_track_id = str((audio_preferences or {}).get("id") or "").strip()
  try:
    language_preference = float(video_format.get("language_preference"))
  except (TypeError, ValueError):
    language_preference = None
  exact_track_match = bool(
      format_track_id and original_track_id
      and format_track_id.casefold() == original_track_id.casefold()
  )
  known_non_original = (
      video_format.get("audio_track_is_default") is False
      or (language_preference is not None and language_preference <= -10)
      or bool(format_track_id and original_track_id and not exact_track_match)
  )
  actual_language = _normalize_language(video_format.get("language"))
  preferred = _normalize_language(original_audio_language)
  language_matches = bool(
      actual_language and preferred
      and actual_language.split("-", 1)[0].casefold()
      == preferred.split("-", 1)[0].casefold()
  )
  inferred_single_track = bool(
      (audio_preferences or {}).get("inferred") is True and not known_non_original
  )
  return bool(
      is_original_audio or exact_track_match
      or (language_matches and not known_non_original)
      or inferred_single_track
  )


def prepare_media_for_handoff(media):
  if (
      media.get("source_manifest_url")
      and "dash" in str(media.get("protocol") or "").lower()
  ):
    media["url"] = _register_manifest_proxy(
        media["source_manifest_url"],
        media.get("headers") or {},
        media.get("cookie"),
        media.get("audio_language") or "",
    )
  return media


def classify_extractor_log(message):
  """Translate extractor diagnostics into provider-owned, non-sensitive flags."""
  detail = str(message).lower()
  flags = set()
  markers = BACKEND_CONFIG.get("log_markers", {})
  token_marker = markers.get("playback_token", {})
  if str(token_marker.get("marker") or "").lower() in detail and any(
      str(term).lower() in detail for term in token_marker.get("required_terms", [])
  ):
    flags.add("po_token_required")
  drm_marker = markers.get("drm_filtered", {})
  if str(drm_marker.get("marker") or "").lower() in detail and any(
      str(term).lower() in detail for term in drm_marker.get("required_terms", [])
  ):
    flags.add("drm_formats_filtered")
  runtime_markers = markers.get("javascript_runtime", {}).get("terms", [])
  if any(str(term).lower() in detail for term in runtime_markers):
    flags.add("javascript_runtime_missing")
  return flags


def resolve_format_options(
    payload, page_url, cookies, user_agent, audio_track, accept_language,
    *, extract_info, available_formats, format_inventory, store_session, logger,
):
  """Resolve browser and extractor formats for this configured provider."""
  browser_key = BROWSER_CONFIG.get("payload_keys", {}).get("playback", "")
  browser_info = _safe_youtube_playback(
      payload.get(browser_key), page_url, audio_track, cookies,
  )
  available = available_formats(
      browser_info, page_url, cookies, user_agent, audio_track, accept_language,
  ) if browser_info else []
  browser_max = max((option[0]["height"] for option in available), default=0)
  player_max = max(
      (browser_info or {}).get("_xdm_companion_source_heights") or [0],
      default=0,
  )
  should_compare_extractor = not available or player_max > browser_max
  info = browser_info
  extractor_format_summary = {}
  if should_compare_extractor:
    try:
      extracted_info = extract_info(
          page_url, cookies, user_agent, audio_track, accept_language,
      )
    except CompanionError:
      if not available:
        raise
    else:
      extractor_format_summary = _youtube_format_diagnostics(extracted_info, audio_track)
      if browser_info:
        # Video-only extractor tiers can be paired with the original audio
        # metadata supplied by the playback page.
        info = dict(browser_info)
        info["formats"] = [
            *(browser_info.get("formats") or []),
            *(extracted_info.get("formats") or []),
        ]
        if "_has_drm" in extracted_info:
          info["_has_drm"] = extracted_info.get("_has_drm")
        if extracted_info.get("availability") in {
            "needs_auth", "subscriber_only", "premium_only",
        }:
          info["availability"] = extracted_info["availability"]
        browser_diagnostics = dict(browser_info.get("_xdm_companion_diagnostics") or {})
        extractor_diagnostics = extracted_info.get("_xdm_companion_diagnostics") or {}
        browser_diagnostics["source"] = "browser_player+yt_dlp"
        browser_diagnostics["yt_dlp_authenticated"] = extractor_diagnostics.get(
            "yt_dlp_authenticated",
        )
        browser_diagnostics["flags"] = sorted(set(
            browser_diagnostics.get("flags") or []
        ).union(extractor_diagnostics.get("flags") or []))
        info["_xdm_companion_diagnostics"] = browser_diagnostics
        available = available_formats(
            info, page_url, cookies, user_agent, audio_track, accept_language,
        )
      else:
        info = extracted_info
        available = available_formats(
            info, page_url, cookies, user_agent, audio_track, accept_language,
        )
  if info is None:
    info = extract_info(page_url, cookies, user_agent, audio_track, accept_language)
    available = available_formats(
        info, page_url, cookies, user_agent, audio_track, accept_language,
    )
  inventory = format_inventory(info, available)
  if not available:
    diagnostics = info.get("_xdm_companion_diagnostics") or {}
    safe_diagnostics = _safe_youtube_diagnostics(payload)
    combined_format_summary = _youtube_format_diagnostics(info, audio_track)
    logger.warning(
        "%s formats unavailable (cookie_count=%s, account_markers=%s, "
        "cookie_queries=%s, player_found=%s, player_formats=%s, "
        "player_direct_formats=%s, player_encrypted_formats=%s, "
        "player_heights=%s, player_dash_manifest=%s, session_authenticated=%s, "
        "extractor_flags=%s, extractor_formats=%s, extractor_summary=%s, "
        "combined_summary=%s, drm_formats_filtered=%s)",
        display_name(),
        safe_diagnostics["cookie_count"],
        safe_diagnostics["account_markers"],
        safe_diagnostics["queries"],
        safe_diagnostics["player_found"],
        safe_diagnostics["player_video_formats"],
        safe_diagnostics["player_direct_formats"],
        safe_diagnostics["player_encrypted_formats"],
        safe_diagnostics["player_source_heights"],
        safe_diagnostics["player_dash_manifest"],
        diagnostics.get("yt_dlp_authenticated"),
        sorted(set(diagnostics.get("flags") or [])),
        extractor_format_summary.get("formats", 0),
        extractor_format_summary,
        combined_format_summary,
        "drm_formats_filtered" in (diagnostics.get("flags") or []),
    )
    if info.get("availability") in {"needs_auth", "subscriber_only", "premium_only"}:
      if _has_youtube_account_cookies(cookies):
        raise CompanionError(
            "El proveedor reconoce que el video requiere acceso, pero no devolvió un flujo descargable "
            "para esta sesión. Confirma que la cuenta tenga acceso y recarga el video."
        )
      raise CompanionError(
          "Este video requiere una cuenta autorizada. Inicia sesión con una cuenta que tenga acceso."
      )
    flags = set(diagnostics.get("flags") or [])
    if (
        diagnostics.get("yt_dlp_authenticated") is False
        and not _has_youtube_account_cookies(cookies)
    ):
      raise CompanionError(
          "El puente del navegador no entregó una sesión autenticada al servicio local. "
          "Comprueba que ambos estén instalados en el mismo perfil del navegador."
      )
    if "po_token_required" in flags:
      raise CompanionError(
          "El proveedor requiere una validación adicional para esta reproducción. "
          "Las cookies de la cuenta no bastaron; XDM Companion no descifra ni elimina DRM."
      )
    if inventory["drm_filtered"]:
      raise CompanionError(
          "El proveedor solo devolvió flujos marcados como DRM. XDM Companion no los descifra."
      )
    if diagnostics.get("yt_dlp_authenticated") is True:
      if not combined_format_summary.get("original_track_known"):
        raise CompanionError(
            "La sesión fue reconocida, pero no se identificó el audio original de este video. "
            "XDM Companion no elegirá una pista al azar ni mostrará una calidad sin audio."
        )
      raise CompanionError(
          "La sesión fue reconocida, pero no se entregó una combinación completa de video y audio compatible con XDM."
      )
    raise CompanionError(
        "El proveedor no devolvió un formato completo compatible con XDM para esta sesión. "
        "Confirma que el navegador esté conectado a la cuenta autorizada y recarga la página."
    )

  best_height = available[0][0]["height"]
  notice = None
  diagnostics = info.get("_xdm_companion_diagnostics") or {}
  flags = set(diagnostics.get("flags") or [])
  highest_source = max(inventory["source_heights"], default=0)
  highest_compatible = max(inventory["compatible_heights"], default=0)
  if "po_token_required" in flags:
    notice = (
        f"El proveedor solo entregó {best_height}p; omitió otras calidades por sus requisitos actuales de reproducción. "
        "No se intentará descifrar contenido protegido."
    )
  elif inventory["drm_filtered"]:
    notice = (
        f"La mejor resolución compatible es {best_height}p. El proveedor también devolvió flujos marcados como DRM, "
        "que XDM Companion no descifra."
    )
  elif highest_source > highest_compatible:
    notice = (
        f"El proveedor anunció video hasta {highest_source}p, pero no entregó una pista completa que XDM pueda usar. "
        f"La mejor resolución compatible es {best_height}p."
    )
  elif len(inventory["compatible_heights"]) == 1:
    notice = (
        f"El proveedor solo devolvió una resolución descargable ({best_height}p) para esta sesión. "
        "Comprueba que el reproductor haya terminado de cargar y vuelve a intentarlo."
    )
  return info, available, inventory, store_session(available), notice
