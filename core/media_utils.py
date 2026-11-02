"""Shared URL, cookie, and audio helpers used by configured site providers."""

import re
import time
from urllib.parse import urlparse

_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")

def _normalize_language(value):
  value = str(value or "").strip()
  if not value or len(value) > 40 or not _LANGUAGE_TAG_RE.fullmatch(value):
    return ""
  normalized = value.replace("_", "-")
  return "" if normalized.lower() == "und" else normalized

def _safe_audio_track(value):
  if not isinstance(value, dict):
    return None
  raw_id = str(value.get("id") or "").strip()[:128]
  language = _normalize_language(value.get("language"))
  if not language and raw_id:
    language = _normalize_language(raw_id.split(".", 1)[0])
  name = str(value.get("name") or "").strip()[:100]
  is_original = value.get("is_original") is True
  inferred = value.get("inferred") is True
  if not language and not raw_id and not name and not is_original and not inferred:
    return None
  track = {
      "language": language,
      "id": raw_id,
      "name": name,
      "is_original": is_original,
  }
  if inferred:
    track["inferred"] = True
  return track

def _safe_accept_language(value):
  if not isinstance(value, str):
    return ""
  languages = []
  for item in value.split(",")[:8]:
    # Drop quality parameters and accept only language tags; this value is
    # copied into HTTP headers sent to third-party media hosts.
    language = _normalize_language(item.split(";", 1)[0])
    if language and language.lower() not in {entry.lower() for entry in languages}:
      languages.append(language)
  return ",".join(languages)

def _preferred_audio_languages(audio_track=None, accept_language=""):
  result = []
  active_language = _normalize_language((audio_track or {}).get("language"))
  if active_language:
    result.append(active_language)
  for language in _safe_accept_language(accept_language).split(","):
    if language and language.lower() not in {entry.lower() for entry in result}:
      result.append(language)
  return result

def _accept_language_header(audio_track=None, accept_language=""):
  languages = _preferred_audio_languages(audio_track, accept_language)
  if not languages:
    return ""
  return ",".join(
      language if index == 0 else f"{language};q={max(0.1, 1 - index * 0.1):.1f}"
      for index, language in enumerate(languages[:8])
  )

def _cookie_domain_matches(host, cookie):
  original_domain = str(cookie.get("domain") or "").strip().lower()
  domain = original_domain.lstrip(".")
  host_only = cookie.get("hostOnly")
  if host_only is None:
    host_only = not original_domain.startswith(".")
  if not domain or any(char in domain for char in "/:@"):
    return False
  return host == domain if host_only else host == domain or host.endswith("." + domain)

def _cookie_header_for_url(cookies, media_url):
  parsed = urlparse(media_url)
  host = (parsed.hostname or "").lower()
  path = parsed.path or "/"
  if not host:
    return None
  is_https = parsed.scheme.lower() == "https"
  now = time.time()
  matches = []
  for cookie in cookies:
    if not isinstance(cookie, dict) or not _cookie_domain_matches(host, cookie):
      continue
    cookie_path = str(cookie.get("path") or "/")
    if not cookie_path.startswith("/"):
      cookie_path = "/"
    if not path.startswith(cookie_path):
      continue
    if len(path) > len(cookie_path) and not cookie_path.endswith("/") and path[len(cookie_path)] != "/":
      continue
    if cookie.get("secure") and not is_https:
      continue
    try:
      expiration = float(cookie.get("expirationDate") or 0)
    except (TypeError, ValueError):
      expiration = 0
    if not cookie.get("session") and expiration and expiration <= now:
      continue
    name = str(cookie.get("name") or "")
    value = str(cookie.get("value") or "")
    if name and not any(char in name + value for char in ";\r\n"):
      matches.append(f"{name}={value}")
  return "; ".join(matches) or None

def _is_original_audio_format(video_format, original_track=None):
  format_track_id = str(video_format.get("audio_track_id") or "").strip()
  original_track_id = str((original_track or {}).get("id") or "").strip()
  if format_track_id and original_track_id:
    return format_track_id.casefold() == original_track_id.casefold()
  if video_format.get("audio_is_default") is True:
    return True
  try:
    return float(video_format.get("language_preference") or 0) > 0
  except (TypeError, ValueError):
    return False

def _manifest_source(video_format):
  """Return a usable master manifest URL and protocol for a format, if any."""
  manifest_url = video_format.get("manifest_url")
  if not isinstance(manifest_url, str) or not manifest_url:
    return None
  parsed = urlparse(manifest_url)
  if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    return None
  path = parsed.path.lower()
  protocol = str(video_format.get("protocol") or "").lower()
  if (
      "dash" in protocol
      or path.endswith(".mpd")
      or "/manifest/dash/" in path
  ):
    return manifest_url, "http_dash_segments"
  if "m3u8" in protocol or path.endswith(".m3u8"):
    return manifest_url, protocol or "m3u8_native"
  return None

def _has_confirmed_drm(video_format):
  drm_flag = video_format.get("has_drm")
  return drm_flag is True or (
      isinstance(drm_flag, str) and drm_flag.strip().lower() in {"true", "yes"}
  )
