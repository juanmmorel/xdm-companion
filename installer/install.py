import os
import platform
import subprocess
import sys
import shutil
import re
import json

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

  if not os.path.isfile(python_env):
    if os.path.exists(prod_venv_dir):
      print("[!] Se encontró un entorno virtual incompleto; se va a reconstruir.")
      shutil.rmtree(prod_venv_dir)
    print("[*] Creando entorno virtual de producción...")
    run_command([sys.executable, "-m", "venv", prod_venv_dir])
  if not os.path.isfile(python_env):
    raise SystemExit("[!] No se pudo crear el entorno virtual de XDM Companion.")

  # 2. Resolver pip desde el intérprete del entorno y evitar actualizar pip
  # por separado en cada reinstalación.
  print("[*] Instalando dependencias en el entorno de producción...")
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

  if system == "Linux":
    service_dir = os.path.join(home_dir, ".config", "systemd", "user")
    os.makedirs(service_dir, exist_ok=True)
    service_path = os.path.join(service_dir, "xdm-companion.service")

    # Systemd ejecutará directamente python apuntando al main.py dentro del venv de XDM
    service_content = f"""[Unit]
Description=XDM Companion Service (Direct Python)
After=network.target graphical-session.target

[Service]
Type=simple
ExecStart={python_env} {main_script_target}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""
    with open(service_path, "w", encoding="utf-8") as f:
      f.write(service_content)

    # El gestor solo conoce el unit file después de recargar la configuración.
    run_command(["systemctl", "--user", "daemon-reload"])
    reset_result = subprocess.run(
        ["systemctl", "--user", "reset-failed", "xdm-companion.service"],
        capture_output=True,
        text=True,
        check=False,
    )
    if reset_result.returncode != 0 and "not loaded" not in (
        reset_result.stderr or ""
    ).lower():
      print("[!] No se pudo limpiar el estado anterior de systemd; continuaré con el reinicio.")
    run_command(["systemctl", "--user", "enable", "xdm-companion.service"])
    run_command(["systemctl", "--user", "restart", "xdm-companion.service"])

    service_status = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "xdm-companion.service"],
        check=False,
    )
    if service_status.returncode != 0:
      subprocess.run(
          ["systemctl", "--user", "--no-pager", "--full", "status", "xdm-companion.service"],
          check=False,
      )
      raise SystemExit(
          "[!] El servicio XDM Companion no quedó activo. El estado de systemd anterior muestra el motivo."
      )
    print("[*] Servicio de Systemd configurado y reiniciado con éxito.")

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
    if system == "Windows":
      print("[*] ¡Instalación completada! El servicio se inicia al iniciar sesión en Windows.")
      print("[*] Instala XDM Companion Bridge desde la tienda del navegador para habilitar la detección.")
    else:
      print("[*] ¡Instalación completada! El puente se activa al reiniciar Chrome y Firefox.")
  else:
    print("[!] Instalación parcial; el servicio quedó activo.")
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
