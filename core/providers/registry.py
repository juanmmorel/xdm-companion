"""Resolve configured adapters without provider-specific request branches."""

import importlib
import re

from site_catalog import load_site_catalog, provider_for_url


_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def adapter_for_url(raw_url, catalog=None):
  provider = provider_for_url(raw_url, catalog)
  if not provider:
    return None
  adapter_name = str(provider.get("backend", {}).get("adapter") or "")
  if not _ADAPTER_NAME.fullmatch(adapter_name):
    return None
  try:
    return importlib.import_module(f".{adapter_name}", package=__package__)
  except ModuleNotFoundError as error:
    if error.name == f"{__package__}.{adapter_name}":
      return None
    raise


def provider_config_for_url(raw_url, catalog=None):
  return provider_for_url(raw_url, catalog)


def get_manifest_proxy(token, now=None, catalog=None):
  """Find a provider-owned local manifest proxy without naming the provider."""
  for provider in load_site_catalog()["providers"] if catalog is None else catalog.get("providers", []):
    module = adapter_for_url(
        next((f"https://{domain}/" for domain in provider.get("domains", [])), ""),
        catalog or {"providers": [provider]},
    )
    getter = getattr(module, "get_manifest_proxy", None) if module else None
    if getter:
      value = getter(token, now=now)
      if value:
        return value
  return None
