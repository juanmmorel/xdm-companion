"""Run yt-dlp metadata extraction outside the long-lived Companion process."""

import json
import math
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from itertools import islice
from pathlib import Path


_FORMAT_FIELDS = (
    "format_id", "url", "manifest_url", "protocol", "mime_type", "ext",
    "height", "resolution", "fps", "tbr", "acodec", "vcodec", "language",
    "language_preference", "audio_track_id", "audio_track_name", "audio_track",
    "audio_track_is_default", "audio_is_default", "filesize", "filesize_approx",
    "has_drm", "http_headers",
)
_INFO_FIELDS = (
    "_type", "title", "url", "protocol", "mime_type", "ext", "height", "fps",
    "format_id", "http_headers", "_has_drm", "availability",
)
_MAX_WORKER_INPUT_BYTES = 2 * 1024 * 1024
_MAX_ERROR_LENGTH = 8_000
_MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024


class YtdlpWorkerError(RuntimeError):
  def __init__(self, message, authenticated=None, flags=()):
    super().__init__(str(message or "No se pudo extraer la información del video.")[:_MAX_ERROR_LENGTH])
    self.authenticated = authenticated if isinstance(authenticated, bool) else None
    self.flags = frozenset(str(flag) for flag in flags if isinstance(flag, str))


class _LazyYtDlp:
  """Allow unit tests to inject a fake API without importing yt-dlp in the service."""

  _module = None

  def __getattr__(self, name):
    module = self._module
    if module is None:
      import yt_dlp as module
      self._module = module
    return getattr(module, name)


yt_dlp = _LazyYtDlp()


class _LogCollector:
  def __init__(self, adapter=None):
    self.adapter = adapter
    self.flags = set()

  def _record(self, message):
    classify = getattr(self.adapter, "classify_extractor_log", None)
    if classify:
      self.flags.update(classify(message))
    elif "javascript runtime" in str(message).lower() or "yt-dlp-ejs" in str(message).lower():
      self.flags.add("javascript_runtime_missing")

  def debug(self, message):
    self._record(message)

  def info(self, _message):
    pass

  def warning(self, message):
    self._record(message)

  def error(self, message):
    self._record(message)


def _json_value(value):
  if value is None or isinstance(value, (str, bool, int)):
    return value
  if isinstance(value, float):
    return value if math.isfinite(value) else None
  return None


def _compact_mapping(value, *, scalar_keys=None):
  if not isinstance(value, dict):
    return None
  result = {}
  for key, item in value.items():
    if not isinstance(key, str):
      continue
    if scalar_keys is not None and key not in scalar_keys:
      continue
    scalar = _json_value(item)
    if scalar is not None:
      result[key] = scalar
  return result


def _compact_format(value):
  if not isinstance(value, dict):
    return None
  result = {}
  for key in _FORMAT_FIELDS:
    if key not in value:
      continue
    item = value[key]
    if key == "http_headers":
      headers = _compact_mapping(item)
      if headers:
        result[key] = headers
    elif key == "audio_track":
      track = _compact_mapping(item)
      if track:
        result[key] = track
    else:
      scalar = _json_value(item)
      if scalar is not None:
        result[key] = scalar
  return result


def _compact_formats(value, limit=None):
  if not isinstance(value, (list, tuple)) and not hasattr(value, "__iter__"):
    return []
  try:
    iterable = iter(value)
  except TypeError:
    return []
  if limit is not None:
    iterable = islice(iterable, limit)
  return [compact for item in iterable if (compact := _compact_format(item)) is not None]


def _compact_video_info(info):
  if not isinstance(info, dict):
    return info
  result = {}
  for key in _INFO_FIELDS:
    if key not in info:
      continue
    value = info[key]
    if key == "http_headers":
      headers = _compact_mapping(value)
      if headers:
        result[key] = headers
    else:
      scalar = _json_value(value)
      if scalar is not None:
        result[key] = scalar
  result["formats"] = _compact_formats(info.get("formats"))
  requested = info.get("requested_downloads")
  if isinstance(requested, (list, tuple)):
    result["requested_downloads"] = _compact_formats(requested, limit=1)
  return result


def _compact_playlist_info(info, max_items):
  if not isinstance(info, dict):
    return info
  entries = info.get("entries")
  if entries is None:
    compact_entries = None
  else:
    try:
      iterator = iter(entries)
    except TypeError:
      compact_entries = None
    else:
      compact_entries = []
      for entry in islice(iterator, max_items + 1):
        if not isinstance(entry, dict):
          compact_entries.append(None)
          continue
        compact = {}
        for key in ("id", "title", "url", "webpage_url", "original_url"):
          scalar = _json_value(entry.get(key))
          if scalar is not None:
            compact[key] = scalar
        compact_entries.append(compact)
  return {
      "_type": _json_value(info.get("_type")),
      "title": _json_value(info.get("title")),
      "playlist_count": _json_value(info.get("playlist_count")),
      "entries": compact_entries,
  }


def execute_request(command, *, ytdlp_module=None):
  """Execute one extraction and return a compact, JSON-safe result."""
  if not isinstance(command, dict):
    return {"ok": False, "error": "Solicitud del proceso auxiliar no válida.", "flags": []}
  operation = command.get("operation")
  if operation not in {"video", "playlist"}:
    return {"ok": False, "error": "Operación de extracción no válida.", "flags": []}
  options = command.get("options")
  if not isinstance(options, dict):
    return {"ok": False, "error": "Opciones de extracción no válidas.", "flags": []}

  authenticated = None
  logger = None
  try:
    from providers.registry import adapter_for_url

    adapter = adapter_for_url(str(command.get("url") or ""))
    logger = _LogCollector(adapter)
    extractor = ytdlp_module or yt_dlp
    options = dict(options)
    options["logger"] = logger
    with extractor.YoutubeDL(options) as downloader:
      extractor_key = str(command.get("extractor_key") or "")
      if (
          operation == "video"
          and extractor_key
          and command.get("check_authenticated_session") is True
      ):
        site_extractor = downloader.get_info_extractor(extractor_key)
        site_extractor.initialize()
        authenticated = bool(site_extractor.is_authenticated)
      info = downloader.extract_info(
          str(command.get("url") or ""),
          download=False,
      )

    if operation == "video":
      compact_info = _compact_video_info(info)
    else:
      max_items = command.get("max_items")
      if type(max_items) is not int or not 1 <= max_items <= 100_000:
        return {"ok": False, "error": "Límite de lista no válido.", "flags": sorted(logger.flags)}
      compact_info = _compact_playlist_info(info, max_items)
    return {
        "ok": True,
        "info": compact_info,
        "authenticated": authenticated,
        "flags": sorted(logger.flags),
    }
  except Exception as error:
    return {
        "ok": False,
        "error": str(error)[:_MAX_ERROR_LENGTH],
        "authenticated": authenticated,
        "flags": sorted(logger.flags) if logger else [],
    }


def invoke_extractor_worker(command):
  """Run one short-lived Python worker so yt-dlp's heap dies with the child."""
  try:
    input_text = json.dumps(command, ensure_ascii=False, allow_nan=False)
  except (TypeError, ValueError) as error:
    raise YtdlpWorkerError("No se pudieron preparar los datos para yt-dlp.") from error

  if len(input_text.encode("utf-8")) > _MAX_WORKER_INPUT_BYTES:
    raise YtdlpWorkerError("La solicitud de extracción supera el tamaño permitido.")

  worker_path = Path(__file__).resolve()
  run_options = {
      "args": [sys.executable, str(worker_path)],
      "input": input_text,
      "stderr": subprocess.DEVNULL,
      "text": True,
      "encoding": "utf-8",
      "errors": "replace",
      "cwd": str(worker_path.parent),
      "check": False,
  }
  if os.name == "nt":
    run_options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

  try:
    with tempfile.TemporaryFile(mode="w+b") as worker_output:
      completed = subprocess.run(stdout=worker_output, **run_options)
      worker_output.seek(0, os.SEEK_END)
      output_size = worker_output.tell()
      if output_size > _MAX_WORKER_OUTPUT_BYTES:
        raise YtdlpWorkerError("yt-dlp devolvió más información de la permitida.")
      worker_output.seek(0)
      output = worker_output.read(_MAX_WORKER_OUTPUT_BYTES + 1).decode(
          "utf-8", errors="replace",
      )
  except YtdlpWorkerError:
    raise
  except (OSError, subprocess.SubprocessError) as error:
    raise YtdlpWorkerError("No se pudo iniciar el proceso aislado de yt-dlp.") from error

  if len(output.encode("utf-8", errors="replace")) > _MAX_WORKER_OUTPUT_BYTES:
    raise YtdlpWorkerError("yt-dlp devolvió más información de la permitida.")
  try:
    result = json.loads(output)
  except (TypeError, ValueError) as error:
    raise YtdlpWorkerError("El proceso aislado de yt-dlp devolvió una respuesta no válida.") from error
  if not isinstance(result, dict):
    raise YtdlpWorkerError("El proceso aislado de yt-dlp devolvió una respuesta no válida.")
  if completed.returncode != 0 and result.get("ok") is True:
    raise YtdlpWorkerError("El proceso aislado de yt-dlp terminó de forma inesperada.")
  if result.get("ok") is not True:
    raise YtdlpWorkerError(
        result.get("error"),
        authenticated=result.get("authenticated"),
        flags=result.get("flags") or (),
    )
  return result


def _worker_main():
  raw_request = sys.stdin.buffer.read(_MAX_WORKER_INPUT_BYTES + 1)
  if len(raw_request) > _MAX_WORKER_INPUT_BYTES:
    result = {"ok": False, "error": "La solicitud supera el tamaño permitido.", "flags": []}
  else:
    try:
      command = json.loads(raw_request.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
      result = {"ok": False, "error": "Solicitud del proceso auxiliar no válida.", "flags": []}
    else:
      with redirect_stdout(sys.stderr):
        result = execute_request(command)
  sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
  sys.stdout.write("\n")
  return 0


if __name__ == "__main__":
  raise SystemExit(_worker_main())
