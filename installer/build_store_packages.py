"""Build upload archives for Chrome Web Store and Mozilla Add-ons."""

import argparse
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
    corrupt_member = archive.testzip()
    if corrupt_member:
      raise RuntimeError(f"{path.name}: archivo ZIP dañado en {corrupt_member}.")
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


def create_firefox_source_zip(output_path):
  output_path = Path(output_path)
  output_path.parent.mkdir(parents=True, exist_ok=True)
  source_files = [PACKAGE_DIR / "LICENSE", PACKAGE_DIR / "README.md"]
  source_files.extend(sorted(path for path in SOURCE_DIR.rglob("*") if path.is_file()))
  source_files.extend((
      PACKAGE_DIR / "core" / "domains.json",
      PACKAGE_DIR / "core" / "site_catalog.py",
      PACKAGE_DIR / "installer" / "browser_bridge.py",
      PACKAGE_DIR / "installer" / "build_store_packages.py",
  ))
  with zipfile.ZipFile(
      output_path, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False,
  ) as archive:
    for source_file in source_files:
      if not source_file.is_file():
        raise RuntimeError(f"Falta un archivo fuente requerido: {source_file}.")
      archive.write(source_file, source_file.relative_to(PACKAGE_DIR).as_posix())
  return output_path


def validate_source_archive(path):
  required_files = {
      "LICENSE",
      "README.md",
      "browser-bridge/background.js",
      "browser-bridge/content.js",
      "browser-bridge/manifest.chrome.json",
      "browser-bridge/manifest.firefox.json",
      "core/domains.json",
      "core/site_catalog.py",
      "installer/browser_bridge.py",
      "installer/build_store_packages.py",
  }
  with zipfile.ZipFile(path) as archive:
    corrupt_member = archive.testzip()
    if corrupt_member:
      raise RuntimeError(f"{path.name}: archivo ZIP dañado en {corrupt_member}.")
    names = set(archive.namelist())
    missing_files = required_files - names
    if missing_files:
      raise RuntimeError(f"{path.name}: faltan fuentes: {sorted(missing_files)}.")
    versions = {
        json.loads(archive.read(name))["version"]
        for name in (
            "browser-bridge/manifest.chrome.json",
            "browser-bridge/manifest.firefox.json",
        )
    }
    if len(versions) != 1:
      raise RuntimeError(f"{path.name}: las versiones de Chrome y Firefox no coinciden.")
  return versions.pop()


def build_packages(output_dir=OUTPUT_DIR):
  output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)
  chrome_zip = create_chrome_store_zip(
      SOURCE_DIR, DOMAINS_FILE, output_dir / "xdm-companion-chrome-store.zip",
  )
  firefox_xpi = create_firefox_xpi(
      SOURCE_DIR, DOMAINS_FILE, output_dir / "xdm-companion-firefox-amo.xpi",
  )
  firefox_sources = create_firefox_source_zip(
      output_dir / "xdm-companion-firefox-source.zip",
  )
  chrome_version = validate_archive(chrome_zip)
  firefox_version = validate_archive(firefox_xpi)
  if chrome_version != firefox_version:
    raise RuntimeError("Los paquetes de Chrome y Firefox no tienen la misma versión.")
  for archive, version in ((chrome_zip, chrome_version), (firefox_xpi, firefox_version)):
    print(f"{archive} (Manifest V3, versión {version})")
  source_version = validate_source_archive(firefox_sources)
  if source_version != firefox_version:
    raise RuntimeError("Las fuentes de Firefox y el XPI no tienen la misma versión.")
  print(f"{firefox_sources} (fuentes de Firefox, versión {source_version})")
  print("Firefox firma el XPI durante la revisión y publicación en AMO.")
  return chrome_zip, firefox_xpi, firefox_sources


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--output-dir",
      type=Path,
      default=OUTPUT_DIR,
      help="Directorio para guardar los paquetes generados.",
  )
  arguments = parser.parse_args()
  build_packages(arguments.output_dir)


if __name__ == "__main__":
  main()
