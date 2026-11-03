import os
import platform
import subprocess
import sys
import shutil
import re
import json
import hashlib
import tarfile
import tempfile
import urllib.request

from browser_bridge import install_linux_browser_bridges

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_command(cmd):
  print(f"[*] Ejecutando: {' '.join(cmd)}")
  subprocess.run(cmd, check=True)


def get_xdm_config_dir():
  home_dir = os.path.expanduser("~")
  return os.path.join(home_dir, ".xdman", "companion")


def install_persistent_uninstaller(xdm_config_dir):
  """Keep a user-level uninstall command after the downloaded archive is removed."""
  installer_dir = os.path.join(xdm_config_dir, "installer")
  os.makedirs(installer_dir, exist_ok=True)
  for filename in ("uninstall.py", "browser_bridge.py"):
    shutil.copy2(os.path.join(PROJECT_DIR, "installer", filename), installer_dir)

  local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
  os.makedirs(local_bin, exist_ok=True)
  command_path = os.path.join(local_bin, "xdm-companion-uninstall")
  command_content = """#!/usr/bin/env bash
set -euo pipefail
companion_dir="${HOME}/.xdman/companion"
python="${companion_dir}/venv/bin/python"
uninstaller="${companion_dir}/installer/uninstall.py"
if [[ ! -x "$python" || ! -f "$uninstaller" ]]; then
  printf '[!] No se encontró una instalación completa de XDM Companion.\\n' >&2
  exit 1
fi
exec "$python" "$uninstaller" "$@"
"""
  with open(command_path, "w", encoding="utf-8") as command_file:
    command_file.write(command_content)
  os.chmod(command_path, 0o755)
  print(f"[*] Desinstalador disponible en: {command_path}")


def install_windows_uninstaller():
  """Keep a PowerShell uninstaller outside the tree it removes."""
  app_data = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
  destination_dir = os.path.join(app_data, "XDM Companion")
  os.makedirs(destination_dir, exist_ok=True)
  source = os.path.join(PROJECT_DIR, "scripts", "uninstall.ps1")
  destination = os.path.join(destination_dir, "uninstall.ps1")
  shutil.copy2(source, destination)
  print(f"[*] Desinstalador disponible en: {destination}")


def browser_integrations_ready(browser_statuses):
  return bool(browser_statuses) and all(
      browser_integration_ready(browser, status)
      for browser, status in browser_statuses
  )


def browser_integration_ready(browser, status):
  if browser == "Chrome":
    return "integración instalada" in status
  if browser == "Firefox":
    return "política registrada" in status
  return False


def browser_integration_report(browser_statuses):
  """Return a precise, user-facing result for each browser integration."""
  report = []
  for browser, status in browser_statuses:
    if browser_integration_ready(browser, status):
      report.append((browser, True, "detección automática registrada"))
    elif browser == "Firefox" and "pendiente de firma de Mozilla" in status:
      report.append((
          browser,
          False,
          "requiere un XPI firmado por Mozilla; este paquete todavía no incluye esa firma",
      ))
    else:
      report.append((browser, False, status))
  return report


def installation_ready(js_runtime, browser_statuses, system=None):
  browser_setup_ready = system == "Windows" or browser_integrations_ready(browser_statuses)
  return bool(js_runtime) and browser_setup_ready


class SystemdUserBusUnavailable(RuntimeError):
  pass


def systemd_user_environment(environment=None, uid=None):
  """Recover the standard user-bus environment when the installer lacks it."""
  resolved = dict(os.environ if environment is None else environment)
  if uid is None:
    uid = os.getuid()

  runtime_dir = resolved.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
  if not resolved.get("XDG_RUNTIME_DIR") and os.path.isdir(runtime_dir):
    resolved["XDG_RUNTIME_DIR"] = runtime_dir
  if not resolved.get("DBUS_SESSION_BUS_ADDRESS"):
    bus_socket = os.path.join(runtime_dir, "bus")
    if os.path.exists(bus_socket):
      resolved["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_socket}"
  return resolved


def enable_systemd_user_service(service_path):
  wants_dir = os.path.join(os.path.dirname(service_path), "default.target.wants")
  os.makedirs(wants_dir, exist_ok=True)
  link_path = os.path.join(wants_dir, os.path.basename(service_path))
  relative_target = os.path.relpath(service_path, wants_dir)
  if os.path.lexists(link_path):
    if os.path.islink(link_path) and os.path.realpath(link_path) == os.path.realpath(service_path):
      return
    if os.path.isdir(link_path) and not os.path.islink(link_path):
      raise SystemExit(
          f"[!] No se puede habilitar el servicio: {link_path} ya existe como directorio."
      )
    os.remove(link_path)
  os.symlink(relative_target, link_path)


def run_systemctl_user(arguments, environment, allow_failure=False):
  command = ["systemctl", "--user", *arguments]
  print(f"[*] Ejecutando: {' '.join(command)}")
  result = subprocess.run(
      command,
      capture_output=True,
      text=True,
      env=environment,
      check=False,
  )
  output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
  if result.returncode != 0:
    lower_output = output.lower()
    bus_errors = (
        "failed to connect to user scope bus",
        "failed to connect to bus",
        "dbus_session_bus_address",
        "xdg_runtime_dir",
        "no medium found",
    )
    if any(marker in lower_output for marker in bus_errors):
      raise SystemdUserBusUnavailable(output)
    if arguments[:1] == ["reset-failed"] and "not loaded" in lower_output:
      return result
    if allow_failure:
      if output:
        print(output, file=sys.stderr)
      return result
    raise SystemExit(
        f"[!] Falló {' '.join(command)} (código {result.returncode})."
        + (f"\n{output}" if output else "")
    )
  if output:
    print(output)
  return result


def configure_systemd_user_service(service_path):
  environment = systemd_user_environment()
  has_user_bus = bool(
      environment.get("XDG_RUNTIME_DIR")
      and environment.get("DBUS_SESSION_BUS_ADDRESS")
  )
  if not has_user_bus:
    enable_systemd_user_service(service_path)
    print(
        "[!] No hay una sesión D-Bus de usuario activa. El servicio quedó "
        "habilitado y arrancará al iniciar sesión en Ubuntu."
    )
    return False

  try:
    run_systemctl_user(["daemon-reload"], environment)
    reset_result = run_systemctl_user(
        ["reset-failed", "xdm-companion.service"], environment, allow_failure=True
    )
    if reset_result.returncode != 0:
      print("[!] No se pudo limpiar el estado anterior de systemd; continuaré con el reinicio.")
    run_systemctl_user(["enable", "xdm-companion.service"], environment)
    run_systemctl_user(["restart", "xdm-companion.service"], environment)
    service_status = run_systemctl_user(
        ["is-active", "--quiet", "xdm-companion.service"],
        environment,
        allow_failure=True,
    )
    if service_status.returncode != 0:
      run_systemctl_user(
          ["--no-pager", "--full", "status", "xdm-companion.service"],
          environment,
          allow_failure=True,
      )
      raise SystemExit(
          "[!] El servicio XDM Companion no quedó activo. El estado de systemd anterior muestra el motivo."
      )
  except SystemdUserBusUnavailable:
    enable_systemd_user_service(service_path)
    print(
        "[!] Se perdió la conexión con D-Bus de usuario. El servicio quedó "
        "habilitado y arrancará al iniciar sesión en Ubuntu."
    )
    return False

  print("[*] Servicio de Systemd configurado y reiniciado con éxito.")
  return True


def linux_systemd_user_service_content(python_env, main_script_target, *, cgroup_v2=None):
  """Build the Linux unit with a soft memory-pressure threshold where supported."""
  if cgroup_v2 is None:
    cgroup_v2 = os.path.isfile("/sys/fs/cgroup/cgroup.controllers")
  memory_limit = "MemoryHigh=300M\n" if cgroup_v2 else ""
  return f"""[Unit]
Description=XDM Companion Service (Direct Python)
After=network.target graphical-session.target

[Service]
Type=simple
ExecStart={python_env} {main_script_target}
Restart=always
RestartSec=3
{memory_limit}
[Install]
WantedBy=default.target
"""


def get_supported_js_runtime():
  for executable, minimum_major, minimum_minor in (("deno", 2, 3), ("node", 22, 0)):
    runtime_path = shutil.which(executable)
    if not runtime_path:
      continue
    try:
      result = subprocess.run(
          [runtime_path, "--version"],
          capture_output=True,
          text=True,
          timeout=5,
          check=True,
      )
    except (OSError, subprocess.SubprocessError):
      continue
    match = re.search(r"(\d+)\.(\d+)", result.stdout or result.stderr)
    if match and (int(match.group(1)), int(match.group(2))) >= (
        minimum_major,
        minimum_minor,
    ):
      return {"name": executable, "path": runtime_path}
  return None


def find_user_uv():
  runtime_uv = os.path.join(get_xdm_config_dir(), "runtime", "uv")
  for candidate in (shutil.which("uv"), runtime_uv):
    if not candidate or not os.path.isfile(candidate):
      continue
    try:
      subprocess.run(
          [candidate, "--version"],
          capture_output=True,
          text=True,
          timeout=5,
          check=True,
      )
      return candidate
    except (OSError, subprocess.SubprocessError):
      continue
  return None


def install_user_uv():
  """Download the signed-by-checksum uv release into this user's XDM directory."""
  machine = platform.machine().lower()
  targets = {
      "x86_64": "x86_64-unknown-linux-gnu",
      "amd64": "x86_64-unknown-linux-gnu",
      "aarch64": "aarch64-unknown-linux-gnu",
      "arm64": "aarch64-unknown-linux-gnu",
  }
  target = targets.get(machine)
  if not target:
    raise SystemExit(f"[!] No hay un paquete de uv para la arquitectura {machine}.")

  api_url = "https://api.github.com/repos/astral-sh/uv/releases/latest"
  request = urllib.request.Request(
      api_url,
      headers={
          "Accept": "application/vnd.github+json",
          "User-Agent": "XDM-Companion-Installer",
      },
  )
  try:
    with urllib.request.urlopen(request, timeout=30) as response:
      release = json.loads(response.read().decode("utf-8"))
  except (OSError, ValueError) as error:
    raise SystemExit(f"[!] No se pudo consultar la versión oficial de uv: {error}") from error

  tag = str(release.get("tag_name") or "")
  if (
      not re.fullmatch(r"v?\d+\.\d+\.\d+", tag)
      or release.get("draft")
      or release.get("prerelease")
  ):
    raise SystemExit("[!] GitHub no devolvió una release estable válida de uv.")

  asset_name = f"uv-{target}.tar.gz"
  checksum_name = f"{asset_name}.sha256"
  base_url = f"https://github.com/astral-sh/uv/releases/download/{tag}/"
  assets = {
      item.get("name"): item
      for item in release.get("assets", [])
      if isinstance(item, dict)
  }
  urls = []
  for name in (asset_name, checksum_name):
    asset = assets.get(name)
    url = str(asset.get("browser_download_url") or "") if asset else ""
    if not url.startswith(base_url) or url.rsplit("/", 1)[-1] != name:
      raise SystemExit(f"La release de uv no contiene el archivo oficial {name}.")
    if int(asset.get("size") or 0) > 128 * 1024 * 1024:
      raise SystemExit(f"El archivo oficial de uv excede el tamaño permitido: {name}.")
    urls.append(url)

  print(f"[*] Preparando uv {tag} en la carpeta del usuario, sin permisos de administrador.")
  with tempfile.TemporaryDirectory(prefix="xdm-companion-uv-") as temp_dir:
    archive_path = os.path.join(temp_dir, asset_name)
    checksum_path = os.path.join(temp_dir, checksum_name)
    try:
      for url, destination in zip(urls, (archive_path, checksum_path)):
        with urllib.request.urlopen(url, timeout=120) as response, open(destination, "wb") as output:
          shutil.copyfileobj(response, output)
    except OSError as error:
      raise SystemExit(f"[!] No se pudo descargar uv desde su release oficial: {error}") from error

    with open(checksum_path, encoding="utf-8") as checksum_file:
      checksum_parts = checksum_file.read().strip().split()
    if (
        not checksum_parts
        or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum_parts[0])
        or (len(checksum_parts) > 1 and checksum_parts[-1].lstrip("*") != asset_name)
    ):
      raise SystemExit("[!] El checksum de uv tiene un formato no válido.")
    with open(archive_path, "rb") as archive_file:
      archive_digest = hashlib.sha256()
      for chunk in iter(lambda: archive_file.read(1024 * 1024), b""):
        archive_digest.update(chunk)
    digest = archive_digest.hexdigest()
    if digest.lower() != checksum_parts[0].lower():
      raise SystemExit("[!] El checksum SHA-256 del paquete oficial de uv no coincide.")

    uv_member = None
    with tarfile.open(archive_path, "r:gz") as archive:
      candidates = [
          member
          for member in archive.getmembers()
          if member.isfile() and member.name.rsplit("/", 1)[-1] == "uv"
      ]
      if len(candidates) != 1 or candidates[0].size > 128 * 1024 * 1024:
        raise SystemExit("El archivo oficial de uv no contiene el ejecutable esperado.")
      uv_member = archive.extractfile(candidates[0])
      if uv_member is None:
        raise SystemExit("No se pudo leer el ejecutable uv del archivo oficial.")
      runtime_dir = os.path.join(get_xdm_config_dir(), "runtime")
      os.makedirs(runtime_dir, exist_ok=True)
      staged_uv = os.path.join(temp_dir, "uv")
      with uv_member, open(staged_uv, "wb") as output:
        shutil.copyfileobj(uv_member, output)

    os.chmod(staged_uv, 0o755)
    installed_uv = os.path.join(runtime_dir, "uv")
    shutil.copy2(staged_uv, installed_uv)
    os.chmod(installed_uv, 0o755)

  return installed_uv


def create_production_venv(prod_venv_dir, system):
  command = [sys.executable, "-m", "venv", prod_venv_dir]
  result = subprocess.run(command, capture_output=True, text=True, check=False)
  if result.returncode == 0:
    if result.stdout:
      print(result.stdout, end="")
    return None

  error_output = "\n".join(part for part in (result.stdout, result.stderr) if part)
  if system == "Linux":
    if os.path.exists(prod_venv_dir):
      shutil.rmtree(prod_venv_dir)
    print("[*] Python no incluye ensurepip; usaré uv dentro de la carpeta del usuario.")
    uv_path = find_user_uv() or install_user_uv()
    uv_result = subprocess.run(
        [uv_path, "venv", "--seed", "--python", sys.executable, prod_venv_dir],
        capture_output=True,
        text=True,
        check=False,
    )
    if uv_result.returncode != 0:
      detail = (uv_result.stderr or uv_result.stdout or "").strip()
      raise SystemExit(
          "[!] No se pudo crear el entorno virtual en la carpeta del usuario."
          + (f"\n{detail}" if detail else "")
      )
    return uv_path

  if error_output:
    print(error_output, file=sys.stderr)
  raise SystemExit(
      f"[!] No se pudo crear el entorno virtual de producción (código "
      f"{result.returncode})."
  )


def main():
  system = platform.system()
  print(f"[*] ==================================================")
  print(f"[*] Instalador Robusto de XDM Companion (Modo Directo)")
  print(f"[*] Sistema detectado: {system}")
  print(f"[*] ==================================================")
  js_runtime = get_supported_js_runtime()
  if not js_runtime:
    print(
        "[!] YouTube requiere Deno 2.3+ o Node.js 22+ para que yt-dlp "
        "resuelva los retos actuales."
    )

  xdm_config_dir = get_xdm_config_dir()
  print(f"[*] Carpeta de XDM detectada automáticamente: {xdm_config_dir}")
  os.makedirs(xdm_config_dir, exist_ok=True)

  # 1. Crear o reparar el entorno virtual permanente dentro de la carpeta de XDM.
  prod_venv_dir = os.path.join(xdm_config_dir, "venv")

  if system == "Windows":
    python_env = os.path.join(prod_venv_dir, "Scripts", "python.exe")
  else:
    python_env = os.path.join(prod_venv_dir, "bin", "python")

  uv_path = None
  if not os.path.isfile(python_env):
    if os.path.exists(prod_venv_dir):
      print("[!] Se encontró un entorno virtual incompleto; se va a reconstruir.")
      shutil.rmtree(prod_venv_dir)
    print("[*] Creando entorno virtual de producción...")
    uv_path = create_production_venv(prod_venv_dir, system)
  if not os.path.isfile(python_env):
    raise SystemExit("[!] No se pudo crear el entorno virtual de XDM Companion.")

  # 2. Resolver pip desde el intérprete del entorno y evitar actualizar pip
  # por separado en cada reinstalación.
  print("[*] Instalando dependencias en el entorno de producción...")
  if uv_path:
    run_command([
        uv_path,
        "pip",
        "install",
        "--python",
        python_env,
        "-r",
        os.path.join(PROJECT_DIR, "requirements.txt"),
    ])
  else:
    run_command([
        python_env,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "-r",
        os.path.join(PROJECT_DIR, "requirements.txt"),
    ])

  # 3. Copiar el código fuente 'core' y 'domains.json' a la carpeta de XDM
  core_dst = os.path.join(xdm_config_dir, "core")
  if os.path.exists(core_dst):
    shutil.rmtree(core_dst)
  shutil.copytree(os.path.join(PROJECT_DIR, "core"), core_dst)
  if js_runtime:
    with open(os.path.join(core_dst, "runtime.json"), "w", encoding="utf-8") as f:
      json.dump(js_runtime, f)
  print(f"[*] Código fuente copiado en: {core_dst}")

  browser_statuses = []
  if system == "Linux":
    print("[*] Configurando la detección automática en Chrome y Firefox...")
    browser_statuses = install_linux_browser_bridges(xdm_config_dir)
    for browser, status in browser_statuses:
      print(f"[*] {browser}: {status}")
  elif system == "Windows":
    print("[*] El servicio está preparado; instala XDM Companion Bridge desde la tienda del navegador.")
  else:
    browser_statuses = [("Navegadores", "la instalación automática solo está preparada para Linux")]
    print(f"[!] {browser_statuses[0][0]}: {browser_statuses[0][1]}")

  extension_dst = os.path.join(xdm_config_dir, "browser-extension")
  if os.path.exists(extension_dst):
    shutil.rmtree(extension_dst)
    print("[*] Se retiraron los archivos de la antigua extensión independiente de XDM Companion.")

  # Archivo principal que ejecutará el servicio
  main_script_target = os.path.join(core_dst, "main.py")

  # 4. Configurar inicio automático a nivel de usuario (Systemd / Windows / macOS)
  home_dir = os.path.expanduser("~")
  service_active = True

  if system == "Linux":
    service_dir = os.path.join(home_dir, ".config", "systemd", "user")
    os.makedirs(service_dir, exist_ok=True)
    service_path = os.path.join(service_dir, "xdm-companion.service")

    # Systemd ejecutará directamente python apuntando al main.py dentro del venv de XDM
    service_content = linux_systemd_user_service_content(python_env, main_script_target)
    with open(service_path, "w", encoding="utf-8") as f:
      f.write(service_content)

    service_active = configure_systemd_user_service(service_path)

  elif system == "Windows":
    import winreg
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    pythonw_env = os.path.join(os.path.dirname(python_env), "pythonw.exe")
    startup_python = pythonw_env if os.path.isfile(pythonw_env) else python_env
    try:
      with winreg.CreateKeyEx(
          winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
      ) as reg_key:
        winreg.SetValueEx(
            reg_key,
            "XDMCompanion",
            0,
            winreg.REG_SZ,
            f'"{startup_python}" "{main_script_target}"',
        )
      print("[*] Inicio automático configurado en el Registro de Windows.")
    except OSError as error:
      raise SystemExit(f"[!] No se pudo configurar el inicio automático: {error}") from error

  elif system == "Darwin":  # macOS
    launch_agents_dir = os.path.join(home_dir, "Library", "LaunchAgents")
    os.makedirs(launch_agents_dir, exist_ok=True)
    plist_path = os.path.join(launch_agents_dir, "com.xdm.companion.plist")

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.xdm.companion</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_env}</string>
        <string>{main_script_target}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
"""
    with open(plist_path, "w", encoding="utf-8") as f:
      f.write(plist_content)

    run_command(["launchctl", "load", "-w", plist_path])
    print("[*] Agente de inicio de macOS configurado.")

  browser_ready = browser_integrations_ready(browser_statuses)
  all_ready = installation_ready(js_runtime, browser_statuses, system)
  if system == "Linux":
    install_persistent_uninstaller(xdm_config_dir)
  elif system == "Windows":
    install_windows_uninstaller()
  print("[*] ==================================================")
  if all_ready:
    if system == "Linux" and not service_active:
      print(
          "[*] Instalación completada. El servicio quedó habilitado y arrancará "
          "cuando inicies sesión en Ubuntu."
      )
    elif system == "Windows":
      print("[*] ¡Instalación completada! El servicio se inicia al iniciar sesión en Windows.")
      print("[*] Instala XDM Companion Bridge desde la tienda del navegador para habilitar la detección.")
    else:
      print("[*] ¡Instalación completada! El puente se activa al reiniciar Chrome y Firefox.")
  else:
    print("[!] Instalación parcial.")
    if system == "Linux":
      if service_active:
        print("[*] El servicio de XDM Companion está activo.")
      else:
        print("[*] El servicio arrancará cuando inicies sesión en Ubuntu.")
    if not browser_ready:
      print("[!] Estado de la detección automática por navegador:")
      for browser, ready, detail in browser_integration_report(browser_statuses):
        marker = "[*]" if ready else "[!]"
        print(f"{marker} {browser}: {detail}.")
    if not js_runtime:
      print("[!] Falta Deno 2.3+ o Node.js 22+ para resolver YouTube.")
  print(f"[*] Ubicación: {xdm_config_dir}")
  print("[*] ==================================================")
  return 0 if all_ready else 2


if __name__ == "__main__":
  raise SystemExit(main())
