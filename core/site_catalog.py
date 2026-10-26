"""Load the data-only registry shared by the service and browser package builder."""

import json
import re
from pathlib import Path
from urllib.parse import urlparse


SITE_CATALOG_FILE = Path(__file__).resolve().with_name("domains.json")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _normalize_domain(value):
  if not isinstance(value, str):
    return ""
  domain = value.strip().lower().rstrip(".")
  return domain if _DOMAIN_RE.fullmatch(domain) else ""


def load_site_catalog(path=SITE_CATALOG_FILE):
  """Read and validate site/provider data; old domain-only files remain readable."""
  try:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError, TypeError):
    return {"providers": [], "companion": {}}

  providers = document.get("providers") if isinstance(document, dict) else None
  if isinstance(providers, list):
    normalized = []
    seen_ids = set()
    for provider in providers:
      if not isinstance(provider, dict):
        continue
      provider_id = str(provider.get("id") or "").strip().lower()
      if not re.fullmatch(r"[a-z0-9_-]{1,48}", provider_id) or provider_id in seen_ids:
        continue
      domains = []
      for value in provider.get("domains", []):
        domain = _normalize_domain(value)
        if domain and domain not in domains:
          domains.append(domain)
      if not domains:
        continue
      seen_ids.add(provider_id)
      entry = dict(provider)
      entry["id"] = provider_id
      entry["domains"] = domains
      entry["browser"] = provider.get("browser") if isinstance(provider.get("browser"), dict) else {}
      entry["backend"] = provider.get("backend") if isinstance(provider.get("backend"), dict) else {}
      normalized.append(entry)
    companion = document.get("companion") if isinstance(document.get("companion"), dict) else {}
    return {"providers": normalized, "companion": companion}

  # Backwards compatibility for projects that still provide the former shape.
  legacy_domains = document.get("complex_domains", []) if isinstance(document, dict) else []
  domains = []
  for value in legacy_domains if isinstance(legacy_domains, list) else []:
    domain = _normalize_domain(value)
    if domain and domain not in domains:
      domains.append(domain)
  return {
      "providers": [{"id": "legacy", "domains": domains, "browser": {}, "backend": {}}]
      if domains else [],
      "companion": {},
  }


def site_domains(catalog):
  result = []
  for provider in (catalog or {}).get("providers", []):
    for domain in provider.get("domains", []):
      if domain not in result:
        result.append(domain)
  return result


def provider_for_url(raw_url, catalog=None):
  try:
    host = (urlparse(raw_url).hostname or "").lower().rstrip(".")
  except (TypeError, ValueError):
    return None
  if not host:
    return None
  for provider in (catalog or load_site_catalog()).get("providers", []):
    if any(host == domain or host.endswith("." + domain) for domain in provider.get("domains", [])):
      return provider
  return None
