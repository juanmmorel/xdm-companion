"""Build upload archives for Chrome Web Store and Mozilla Add-ons."""

import json
import zipfile
from pathlib import Path

from browser_bridge import (
    DOMAINS_FILE,
    SOURCE_DIR,
    create_chrome_store_zip,
    create_firefox_xpi,
)


PACKAGE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PACKAGE_DIR / "dist" / "store"


def validate_archive(path):
  with zipfile.ZipFile(path) as archive:
    names = set(archive.namelist())
    manifest = json.loads(archive.read("manifest.json"))
    if manifest.get("manifest_version") != 3:
      raise RuntimeError(f"{path.name}: el paquete no usa Manifest V3.")
    if "content.js" not in names or "background.js" not in names:
      raise RuntimeError(f"{path.name}: faltan archivos del puente.")
    for content_script in manifest.get("content_scripts", []):
      for script in content_script.get("js", []):
        if script not in names:
          raise RuntimeError(f"{path.name}: falta el script de página {script}.")
      for stylesheet in content_script.get("css", []):
        if stylesheet not in names:
          raise RuntimeError(f"{path.name}: falta la hoja de estilos {stylesheet}.")
    for icon in (manifest.get("icons") or {}).values():
      if icon not in names:
        raise RuntimeError(f"{path.name}: falta el icono {icon}.")
    if manifest.get("background", {}).get("scripts"):
      for script in manifest["background"]["scripts"]:
        if script not in names:
          raise RuntimeError(f"{path.name}: falta el módulo {script}.")
    worker = manifest.get("background", {}).get("service_worker")
    if worker and worker not in names:
      raise RuntimeError(f"{path.name}: falta el proceso de fondo {worker}.")
  return manifest["version"]


def main():
  OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
  chrome_zip = create_chrome_store_zip(
      SOURCE_DIR, DOMAINS_FILE, OUTPUT_DIR / "xdm-companion-chrome-store.zip",
  )
  firefox_xpi = create_firefox_xpi(
      SOURCE_DIR, DOMAINS_FILE, OUTPUT_DIR / "xdm-companion-firefox-amo.xpi",
  )
  for archive in (chrome_zip, firefox_xpi):
    version = validate_archive(archive)
    print(f"{archive} (Manifest V3, versión {version})")
  print("Firefox firma el XPI durante la revisión y publicación en AMO.")


if __name__ == "__main__":
  main()
