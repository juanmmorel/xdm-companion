"""Build and register the no-button browser bridge used by XDM Companion."""

import json
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = PACKAGE_DIR / "browser-bridge"
DOMAINS_FILE = PACKAGE_DIR / "core" / "domains.json"
CORE_DIR = PACKAGE_DIR / "core"
if str(CORE_DIR) not in sys.path:
  sys.path.insert(0, str(CORE_DIR))
from site_catalog import load_site_catalog, site_domains


class BrowserBridgeError(RuntimeError):
  pass


def load_domains(domains_file=DOMAINS_FILE):
  return site_domains(load_site_catalog(domains_file))


def _domain_patterns(domains):
  patterns = []
  for domain in domains:
    patterns.extend((f"*://{domain}/*", f"*://*.{domain}/*"))
  return patterns


def build_extension(source_dir, domains_file, output_dir, browser):
  """Create an unpacked extension directory with permissions limited to configured sites."""
  if browser not in {"chrome", "firefox"}:
    raise ValueError("browser must be chrome or firefox")
  domains = load_domains(domains_file)
  if not domains:
    raise BrowserBridgeError("No hay dominios configurados para el puente del navegador.")

  source_dir = Path(source_dir)
  output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)
  for filename in ("background.js", "content.js"):
    shutil.copy2(source_dir / filename, output_dir / filename)
  shutil.copytree(source_dir / "icons", output_dir / "icons", dirs_exist_ok=True)
  shutil.copytree(source_dir / "providers", output_dir / "providers", dirs_exist_ok=True)
  catalog = load_site_catalog(domains_file)
  browser_modules = []
  for provider in catalog.get("providers", []):
    browser_config = provider.get("browser", {})
    module = str(browser_config.get("module") or "")
    adapter = str(browser_config.get("adapter") or "")
    if not module or not adapter or module in browser_modules:
      continue
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.js", module):
      raise BrowserBridgeError(f"Módulo de navegador no válido para {provider['id']}.")
    if not (source_dir / "providers" / module).is_file():
      raise BrowserBridgeError(f"No se encontró el adaptador de navegador {module}.")
    browser_modules.append(module)
  (output_dir / "site-config.js").write_text(
      "globalThis.XDM_SITE_CATALOG = " + json.dumps(catalog, ensure_ascii=False) + ";\n",
      encoding="utf-8",
  )

  manifest_template = source_dir / f"manifest.{browser}.json"
  manifest = json.loads(manifest_template.read_text(encoding="utf-8"))
  patterns = _domain_patterns(domains)
  manifest.setdefault("host_permissions", []).extend(patterns)
  manifest["content_scripts"][0]["matches"] = patterns
  manifest["content_scripts"][0]["js"] = ["site-config.js", "content.js"]
  if browser == "firefox":
    manifest["background"]["scripts"] = [
        "site-config.js",
        *(f"providers/{module}" for module in browser_modules),
        "background.js",
    ]
  (output_dir / "manifest.json").write_text(
      json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
      encoding="utf-8",
  )
  return output_dir


def create_firefox_xpi(source_dir, domains_file, xpi_path):
  """Package Firefox source for Mozilla signing; Firefox Release rejects unsigned XPIs."""
  xpi_path = Path(xpi_path)
  xpi_path.parent.mkdir(parents=True, exist_ok=True)
  stage = xpi_path.with_suffix(".stage")
  if stage.exists():
    shutil.rmtree(stage)
  build_extension(source_dir, domains_file, stage, "firefox")
  try:
    with zipfile.ZipFile(
        xpi_path,
        "w",
        zipfile.ZIP_DEFLATED,
        strict_timestamps=False,
    ) as archive:
      for path in sorted(stage.rglob("*")):
        if path.is_file():
          archive.write(path, path.relative_to(stage).as_posix())
  finally:
    shutil.rmtree(stage, ignore_errors=True)
  return xpi_path


def create_chrome_store_zip(source_dir, domains_file, zip_path):
  """Create the source ZIP uploaded to Chrome Web Store (not a local CRX)."""
  zip_path = Path(zip_path)
  zip_path.parent.mkdir(parents=True, exist_ok=True)
  with tempfile.TemporaryDirectory(prefix="xdm-companion-chrome-store-") as temporary_dir:
    stage = Path(temporary_dir) / "extension"
    build_extension(source_dir, domains_file, stage, "chrome")
    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED,
        strict_timestamps=False,
    ) as archive:
      for path in sorted(stage.rglob("*")):
        if path.is_file():
          archive.write(path, path.relative_to(stage).as_posix())
  return zip_path


def contains_firefox_signature_metadata(xpi_path):
  """Check the JAR-signing files exist; Firefox remains the signature verifier."""
  try:
    with zipfile.ZipFile(xpi_path) as archive:
      names = {name.upper() for name in archive.namelist()}
    return "META-INF/MOZILLA.RSA" in names and "META-INF/MOZILLA.SF" in names
  except (OSError, zipfile.BadZipFile):
    return False


def chrome_extension_id(private_key_path, openssl="openssl", runner=subprocess.run):
  result = runner(
      [openssl, "pkey", "-in", str(private_key_path), "-pubout", "-outform", "DER"],
      check=True,
      capture_output=True,
      timeout=15,
  )
  digest = hashlib.sha256(result.stdout).digest()[:16]
  return "".join(chr(ord("a") + (nibble >> shift & 0x0F)) for nibble in digest for shift in (4, 0))


def package_chrome_extension(source_dir, domains_file, output_dir, chrome_binary, private_key_path, runner=subprocess.run):
  """Pack a CRX with the installed Chrome binary and keep its ID stable across upgrades."""
  output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)
  extension_dir = output_dir / "chrome"
  if extension_dir.exists():
    shutil.rmtree(extension_dir)
  build_extension(source_dir, domains_file, extension_dir, "chrome")

  private_key_path = Path(private_key_path)
  crx_path = output_dir / "xdm-companion-bridge.crx"
  generated_crx = extension_dir.with_suffix(".crx")
  generated_key = extension_dir.with_suffix(".pem")
  generated_crx.unlink(missing_ok=True)
  generated_key.unlink(missing_ok=True)

  with tempfile.TemporaryDirectory(prefix="xdm-companion-chrome-pack-") as profile_dir:
    command = [
        str(chrome_binary),
        "--no-sandbox",
        "--headless",
        "--disable-gpu",
        "--disable-crash-reporter",
        "--disable-breakpad",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile_dir}",
        f"--pack-extension={extension_dir}",
    ]
    if private_key_path.is_file():
      command.append(f"--pack-extension-key={private_key_path}")
    result = runner(command, check=False, capture_output=True, text=True, timeout=90)

  if result.returncode != 0 or not generated_crx.is_file():
    detail = (result.stderr or result.stdout or "Chrome no creó el paquete CRX.").strip()
    raise BrowserBridgeError(f"No se pudo empaquetar el puente para Chrome: {detail[-500:]}")
  if not private_key_path.exists():
    if not generated_key.is_file():
      raise BrowserBridgeError("Chrome no generó la clave estable para el puente.")
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(generated_key, private_key_path)
    private_key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
  shutil.move(generated_crx, crx_path)
  extension_id = chrome_extension_id(private_key_path)
  return crx_path, extension_id


def find_chrome_binary(which=shutil.which):
  for name in ("google-chrome-stable", "google-chrome", "google-chrome-beta", "google-chrome-unstable"):
    binary = which(name)
    if binary:
      return Path(binary).resolve()
  return None


def chrome_external_extension_directory(chrome_binary, home_dir=None, config_home=None):
  binary = Path(chrome_binary).resolve()
  path_parts = binary.parts
  if len(path_parts) >= 4 and path_parts[1:3] == ("opt", "google"):
    channel = binary.parent.name
    app_name = {
        "chrome-beta": "google-chrome-beta",
        "chrome-unstable": "google-chrome-unstable",
    }.get(channel, "google-chrome")
    config_dir = (
        Path(config_home)
        if config_home
        else Path(os.environ.get("XDG_CONFIG_HOME") or Path(home_dir or Path.home()) / ".config")
    )
    return config_dir / app_name / "External Extensions"
  if "chromium" in binary.parent.name:
    raise BrowserBridgeError(
        "Esta compilación de Chromium no ofrece un registro externo por usuario; "
        "el instalador no solicitará permisos de administrador."
    )
  raise BrowserBridgeError(f"No reconozco la ubicación de instalación de Chrome: {binary}")


def install_firefox_policy(signed_xpi, policy_file, runner=subprocess.run):
  """Force-install a Mozilla-signed Firefox bridge from a local XPI using enterprise policy."""
  signed_xpi = Path(signed_xpi).resolve()
  if not contains_firefox_signature_metadata(signed_xpi):
    raise BrowserBridgeError("Firefox Release solo acepta un XPI firmado por Mozilla.")
  policy_file = Path(policy_file)
  try:
    document = json.loads(policy_file.read_text(encoding="utf-8"))
  except FileNotFoundError:
    document = {"policies": {}}
  except (OSError, ValueError) as error:
    raise BrowserBridgeError(f"No se puede leer la política existente de Firefox: {error}") from error
  if not isinstance(document, dict):
    raise BrowserBridgeError("El archivo de políticas de Firefox no contiene un objeto JSON.")
  policies = document.setdefault("policies", {})
  if not isinstance(policies, dict):
    raise BrowserBridgeError("La sección policies de Firefox no es un objeto JSON.")
  extension_settings = policies.setdefault("ExtensionSettings", {})
  if not isinstance(extension_settings, dict):
    raise BrowserBridgeError("La política ExtensionSettings existente no es un objeto JSON.")
  extension_settings["xdm-companion-bridge@xdm.local"] = {
      "installation_mode": "force_installed",
      "install_url": signed_xpi.as_uri(),
  }
  _write_system_file(policy_file, json.dumps(document, ensure_ascii=False, indent=2) + "\n", runner)
  return policy_file


def remove_firefox_policy(policy_file, runner=subprocess.run):
  policy_file = Path(policy_file)
  try:
    document = json.loads(policy_file.read_text(encoding="utf-8"))
  except FileNotFoundError:
    return False
  except (OSError, ValueError) as error:
    raise BrowserBridgeError(f"No se puede leer la política existente de Firefox: {error}") from error
  policies = document.get("policies") if isinstance(document, dict) else None
  settings = policies.get("ExtensionSettings") if isinstance(policies, dict) else None
  if not isinstance(settings, dict) or "xdm-companion-bridge@xdm.local" not in settings:
    return False
  settings.pop("xdm-companion-bridge@xdm.local")
  if not settings:
    policies.pop("ExtensionSettings")
  if not policies:
    _remove_system_file(policy_file, runner)
    return True
  _write_system_file(policy_file, json.dumps(document, ensure_ascii=False, indent=2) + "\n", runner)
  return True


def _write_system_file(destination, contents, runner=subprocess.run):
  destination = Path(destination)
  with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", delete=False) as temp:
    temp.write(contents)
    source = Path(temp.name)
  try:
    if _can_create_under_user(destination.parent):
      destination.parent.mkdir(parents=True, exist_ok=True)
      temp_destination = destination.with_name(destination.name + ".tmp")
      shutil.copy2(source, temp_destination)
      temp_destination.chmod(0o644)
      temp_destination.replace(destination)
    else:
      raise BrowserBridgeError(
          "La política de Firefox está en una carpeta protegida; "
          "el instalador no solicitará permisos de administrador."
      )
  finally:
    source.unlink(missing_ok=True)


def remove_chrome_external_manifest(manifest_path, runner=subprocess.run):
  manifest_path = Path(manifest_path)
  if not manifest_path.exists():
    return False
  if _can_write(manifest_path.parent):
    manifest_path.unlink()
    return True
  raise BrowserBridgeError(
      "El registro antiguo de Chrome está en una carpeta protegida; "
      "el desinstalador no solicitará permisos de administrador."
  )


def _remove_system_file(path, runner=subprocess.run):
  path = Path(path)
  if _can_write(path.parent):
    path.unlink(missing_ok=True)
    return
  raise BrowserBridgeError(
      "La política antigua de Firefox está en una carpeta protegida; "
      "el desinstalador no solicitará permisos de administrador."
  )


def firefox_install_xpi_path(home_dir=None):
  """Store the policy-managed XPI outside dot-directories for Firefox Snap access."""
  home_dir = Path(home_dir) if home_dir else Path.home()
  return home_dir / "XDM Companion" / "browser-bridge" / "xdm-companion-bridge-firefox.xpi"


def install_linux_browser_bridges(
    companion_dir,
    chrome_binary=None,
    firefox_signed_xpi=None,
    firefox_payload_path=None,
):
  """Install Chrome automatically and Firefox when a Mozilla-signed XPI is packaged."""
  companion_dir = Path(companion_dir)
  bridge_dir = companion_dir / "browser-bridge"
  bridge_dir.mkdir(parents=True, exist_ok=True)
  state = {"chrome": None, "firefox": None}
  statuses = []

  chrome_binary = Path(chrome_binary).resolve() if chrome_binary else find_chrome_binary()
  if chrome_binary:
    try:
      crx, extension_id = package_chrome_extension(
          SOURCE_DIR,
          DOMAINS_FILE,
          bridge_dir,
          chrome_binary,
          companion_dir / "browser-bridge-key.pem",
      )
      extension_version = json.loads(
          (bridge_dir / "chrome" / "manifest.json").read_text(encoding="utf-8")
      )["version"]
      registration = write_chrome_external_manifest(
          extension_id,
          crx,
          extension_version,
          chrome_external_extension_directory(chrome_binary),
      )
      state["chrome"] = {"external_manifest": str(registration)}
      statuses.append(("Chrome", "integración instalada; se activa al abrir Chrome de nuevo"))
    except (OSError, subprocess.SubprocessError, BrowserBridgeError) as error:
      statuses.append(("Chrome", f"no se pudo instalar automáticamente: {error}"))
  else:
    statuses.append(("Chrome", "no se encontró una instalación compatible"))

  signed_xpi = Path(firefox_signed_xpi) if firefox_signed_xpi else SOURCE_DIR / "dist" / "xdm-companion-bridge-firefox.xpi"
  if signed_xpi.is_file() and contains_firefox_signature_metadata(signed_xpi):
    try:
      installed_xpi = Path(firefox_payload_path) if firefox_payload_path else firefox_install_xpi_path()
      installed_xpi.parent.mkdir(parents=True, exist_ok=True)
      shutil.copy2(signed_xpi, installed_xpi)
      policy_file = Path("/etc/firefox/policies/policies.json")
      install_firefox_policy(installed_xpi, policy_file)
      state["firefox"] = {
          "policy_file": str(policy_file),
          "xpi_file": str(installed_xpi),
      }
      statuses.append((
          "Firefox",
          "política registrada; Firefox verifica la firma y activa el puente al reiniciarse",
      ))
    except (OSError, BrowserBridgeError) as error:
      statuses.append(("Firefox", f"no se pudo instalar automáticamente: {error}"))
  else:
    try:
      unsigned_xpi = create_firefox_xpi(
          SOURCE_DIR,
          DOMAINS_FILE,
          bridge_dir / "xdm-companion-bridge-firefox-unlisted.xpi",
      )
      statuses.append((
          "Firefox",
          "pendiente de firma de Mozilla; el XPI para publicar está en " + str(unsigned_xpi),
      ))
    except (OSError, BrowserBridgeError) as error:
      statuses.append(("Firefox", f"no se pudo crear el paquete: {error}"))

  (bridge_dir / "install-state.json").write_text(
      json.dumps(state, ensure_ascii=False, indent=2) + "\n",
      encoding="utf-8",
  )
  return statuses


def uninstall_linux_browser_bridges(companion_dir):
  state_path = Path(companion_dir) / "browser-bridge" / "install-state.json"
  try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return []
  results = []
  chrome = state.get("chrome")
  if isinstance(chrome, dict) and chrome.get("external_manifest"):
    try:
      removed = remove_chrome_external_manifest(chrome["external_manifest"])
      results.append(("Chrome", "registro retirado" if removed else "sin registro"))
    except BrowserBridgeError as error:
      results.append(("Chrome", f"no se pudo retirar: {error}"))
  firefox = state.get("firefox")
  if isinstance(firefox, dict) and firefox.get("policy_file"):
    try:
      removed = remove_firefox_policy(firefox["policy_file"])
      if firefox.get("xpi_file"):
        Path(firefox["xpi_file"]).unlink(missing_ok=True)
      results.append(("Firefox", "política retirada" if removed else "sin política"))
    except BrowserBridgeError as error:
      results.append(("Firefox", f"no se pudo retirar: {error}"))
  return results


def chrome_external_manifest(extension_id, crx_path, version):
  return json.dumps(
      {"external_crx": str(Path(crx_path).resolve()), "external_version": version},
      separators=(",", ":"),
  ) + "\n"


def write_chrome_external_manifest(extension_id, crx_path, version, install_dir, runner=subprocess.run):
  """Register a Linux Chrome CRX using Chrome's supported external-extension mechanism."""
  install_dir = Path(install_dir)
  manifest = chrome_external_manifest(extension_id, crx_path, version)
  with tempfile.NamedTemporaryFile(
      mode="w", encoding="utf-8", suffix=".json", delete=False
  ) as temp_file:
    temp_file.write(manifest)
    temp_manifest = Path(temp_file.name)
  destination = install_dir / f"{extension_id}.json"
  try:
    if _can_create_under_user(destination.parent):
      destination.parent.mkdir(parents=True, exist_ok=True)
      shutil.copy2(temp_manifest, destination)
      destination.chmod(0o644)
    else:
      raise BrowserBridgeError(
          "No se puede escribir el registro en una carpeta del usuario; "
          "el instalador no solicitará permisos de administrador."
      )
  finally:
    temp_manifest.unlink(missing_ok=True)
  return destination


def _can_write(path):
  import os

  return os.access(path, os.W_OK)


def _can_create_under_user(path):
  path = Path(path)
  while not path.exists() and path != path.parent:
    path = path.parent
  return path.is_dir() and _can_write(path)
