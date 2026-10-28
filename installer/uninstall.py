import os
import platform
import subprocess
import shutil

from browser_bridge import uninstall_linux_browser_bridges


def run_command(cmd):
  try:
    subprocess.run(cmd, check=True, capture_output=True, text=True)
  except Exception:
    pass


def main():
  system = platform.system()
  print(f"[*] ==================================================")
  print(f"[*] Desinstalación Limpia de XDM Companion (Específica)")
  print(f"[*] Sistema detectado: {system}")
  print(f"[*] ==================================================")

  home_dir = os.path.expanduser("~")
  xdm_companion_dir = os.path.join(home_dir, ".xdman", "companion")
  preserve_bridge_files = False

  if system == "Linux":
    print("[*] Deteniendo y deshabilitando servicio Systemd...")
    run_command(["systemctl", "--user", "stop", "xdm-companion.service"])
    run_command(["systemctl", "--user", "disable", "xdm-companion.service"])

    service_path = os.path.join(
        home_dir, ".config", "systemd", "user", "xdm-companion.service"
    )
    wants_link = os.path.join(
        home_dir,
        ".config",
        "systemd",
        "user",
        "default.target.wants",
        "xdm-companion.service",
    )
    if os.path.lexists(wants_link) and os.path.islink(wants_link):
      os.remove(wants_link)
    if os.path.exists(service_path):
      os.remove(service_path)
      run_command(["systemctl", "--user", "daemon-reload"])

    for browser, status in uninstall_linux_browser_bridges(xdm_companion_dir):
      print(f"[*] {browser}: {status}")
      if "no se pudo retirar" in status:
        preserve_bridge_files = True

  elif system == "Windows":
    print("[*] Removiendo clave de inicio automático del Registro de Windows...")
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
      reg_key = winreg.OpenKey(
          winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
      )
      winreg.DeleteValue(reg_key, "XDMCompanion")
      winreg.CloseKey(reg_key)
    except Exception:
      pass

  elif system == "Darwin":  # macOS
    print("[*] Removiendo agente de inicio de macOS...")
    plist_path = os.path.join(
        home_dir, "Library", "LaunchAgents", "com.xdm.companion.plist"
    )
    if os.path.exists(plist_path):
      run_command(["launchctl", "unload", "-w", plist_path])
      os.remove(plist_path)

  # Borrar únicamente la carpeta exclusiva del complemento dentro de XDM
  if os.path.exists(xdm_companion_dir):
    if preserve_bridge_files:
      # Keep CRX/XPI files if a system registration still points at them.
      print("[!] Se conservará el puente del navegador porque quedó un registro del sistema activo.")
      for entry in os.scandir(xdm_companion_dir):
        if entry.name == "browser-bridge":
          continue
        if entry.is_dir(follow_symlinks=False):
          shutil.rmtree(entry.path)
        else:
          os.unlink(entry.path)
    else:
      print(f"[*] Eliminando exclusivamente la carpeta del complemento en: {xdm_companion_dir}")
      shutil.rmtree(xdm_companion_dir)
  else:
    print("[*] No se encontró la carpeta del complemento (ya estaba limpia).")

  # The entry point lives outside the companion folder so it can remove that folder.
  uninstall_command = os.path.join(home_dir, ".local", "bin", "xdm-companion-uninstall")
  try:
    os.remove(uninstall_command)
  except FileNotFoundError:
    pass

  print("[*] ==================================================")
  print("[*] ¡Desinstalación completada con éxito!")
  print("[*] La configuración general de XDM permanece intacta.")
  print("[*] ==================================================")


if __name__ == "__main__":
  main()
