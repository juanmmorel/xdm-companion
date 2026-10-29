#!/usr/bin/env python3
"""Keep Chrome/Firefox versions aligned and publish releases from that version."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    ROOT / "browser-bridge" / "manifest.chrome.json",
    ROOT / "browser-bridge" / "manifest.firefox.json",
)
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TAG_PATTERN = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ReleaseError(RuntimeError):
  pass


def parse_version(value):
  match = VERSION_PATTERN.fullmatch(value or "")
  if not match:
    raise ReleaseError(f"Versión no válida: {value!r}; usa MAJOR.MINOR.PATCH.")
  return tuple(int(part) for part in match.groups())


def read_manifest_versions(paths=None):
  if paths is None:
    paths = MANIFESTS
  versions = []
  for path in paths:
    try:
      with path.open(encoding="utf-8") as manifest_file:
        version = json.load(manifest_file)["version"]
      parse_version(version)
    except (OSError, KeyError, json.JSONDecodeError) as error:
      raise ReleaseError(f"No se pudo leer la versión de {path}: {error}") from error
    versions.append(version)
  return versions


def validate_tag(tag, versions):
  match = TAG_PATTERN.fullmatch(tag or "")
  if not match:
    raise ReleaseError(f"Tag no válido: {tag!r}; el formato esperado es vMAJOR.MINOR.PATCH.")
  if len(set(versions)) != 1:
    raise ReleaseError(f"Las versiones de Chrome y Firefox no coinciden: {versions}.")
  version = versions[0]
  parse_version(version)
  if tag != f"v{version}":
    raise ReleaseError(
        f"El tag {tag} no coincide con la versión {version} de los manifiestos. "
        "Actualiza ambas versiones antes de crear el tag."
    )
  return version


def next_version(current_version, tags, level):
  current = parse_version(current_version)
  stable_tags = []
  for tag in tags:
    match = TAG_PATTERN.fullmatch(tag)
    if match:
      stable_tags.append(tuple(int(part) for part in match.groups()))
  base = max([current, *stable_tags])
  major, minor, patch = base
  if level == "major":
    return f"{major + 1}.0.0"
  if level == "minor":
    return f"{major}.{minor + 1}.0"
  if level == "patch":
    return f"{major}.{minor}.{patch + 1}"
  raise ReleaseError(f"Tipo de incremento no válido: {level!r}.")


def update_manifest_versions(paths, version):
  parse_version(version)
  prepared = []
  for path in paths:
    try:
      with path.open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
      manifest["version"] = version
      rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
      prepared.append((path, rendered))
    except (OSError, json.JSONDecodeError) as error:
      raise ReleaseError(f"No se pudo preparar {path}: {error}") from error

  temporary_files = []
  try:
    for path, rendered in prepared:
      descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
      temporary = Path(temp_name)
      temporary_files.append((path, temporary))
      with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
      os.chmod(temporary, path.stat().st_mode)
    for path, temporary in temporary_files:
      os.replace(temporary, path)
  finally:
    for _, temporary in temporary_files:
      temporary.unlink(missing_ok=True)


def run_git(*arguments, capture_output=False, check=True):
  return subprocess.run(
      ["git", *arguments],
      cwd=ROOT,
      check=check,
      text=True,
      capture_output=capture_output,
  )


def bump(level):
  branch = run_git("branch", "--show-current", capture_output=True).stdout.strip()
  if branch != "main":
    raise ReleaseError("Ejecuta el incremento desde main; el script creará la rama release/vX.Y.Z.")
  if run_git("diff", "--quiet", check=False).returncode or run_git(
      "diff", "--cached", "--quiet", check=False,
  ).returncode:
    raise ReleaseError("Hay cambios rastreados sin confirmar; guárdalos antes de preparar la versión.")

  manifest_paths = [str(path.relative_to(ROOT)) for path in MANIFESTS]
  dirty = run_git("status", "--porcelain", "--", *manifest_paths, capture_output=True).stdout.strip()
  if dirty:
    raise ReleaseError("Los manifiestos tienen cambios locales; guárdalos antes de calcular la versión.")
  run_git("fetch", "origin", "--tags")
  head = run_git("rev-parse", "HEAD", capture_output=True).stdout.strip()
  remote_main = run_git("rev-parse", "origin/main", capture_output=True).stdout.strip()
  if head != remote_main:
    raise ReleaseError("main no coincide con origin/main; ejecuta git pull --ff-only origin main.")

  versions = read_manifest_versions()
  if len(set(versions)) != 1:
    raise ReleaseError(f"Las versiones de Chrome y Firefox no coinciden: {versions}.")
  tags = run_git("tag", "--list", "v*", capture_output=True).stdout.splitlines()
  version = next_version(versions[0], tags, level)
  release_branch = f"release/v{version}"
  local_branch = run_git("branch", "--list", release_branch, capture_output=True).stdout.strip()
  remote_branch = run_git(
      "branch", "--remotes", "--list", f"origin/{release_branch}", capture_output=True,
  ).stdout.strip()
  if local_branch or remote_branch:
    raise ReleaseError(f"La rama {release_branch} ya existe; revisa o elimina esa rama antes de continuar.")
  run_git("switch", "-c", release_branch)
  update_manifest_versions(MANIFESTS, version)
  print(f"Manifiestos de Chrome y Firefox actualizados a {version}.")
  print(f"Rama creada: {release_branch}. Confirma los manifiestos y abre un PR hacia main.")


def publish():
  branch = run_git("branch", "--show-current", capture_output=True).stdout.strip()
  if branch != "main":
    raise ReleaseError("Publica desde main después de fusionar el PR de versión.")
  if run_git("diff", "--quiet", check=False).returncode or run_git(
      "diff", "--cached", "--quiet", check=False,
  ).returncode:
    raise ReleaseError("Hay cambios rastreados sin confirmar; confirma o descártalos antes de publicar.")

  untracked = run_git(
      "ls-files", "--others", "--exclude-standard", "--",
      "core", "installer", "browser-bridge", "scripts", "README.md", "LICENSE", "requirements.txt",
      capture_output=True,
  ).stdout.strip()
  if untracked:
    raise ReleaseError(f"Hay archivos fuente sin seguimiento que no entrarían en la release:\n{untracked}")

  run_git("fetch", "origin", "--tags")
  head = run_git("rev-parse", "HEAD", capture_output=True).stdout.strip()
  remote_main = run_git("rev-parse", "origin/main", capture_output=True).stdout.strip()
  if head != remote_main:
    raise ReleaseError("main no coincide con origin/main; actualízala antes de publicar.")

  versions = read_manifest_versions()
  version = versions[0] if len(set(versions)) == 1 else ""
  if not version:
    raise ReleaseError(f"Las versiones de Chrome y Firefox no coinciden: {versions}.")
  tag = f"v{version}"
  validate_tag(tag, versions)
  existing_tags = run_git("tag", "--list", tag, capture_output=True).stdout.splitlines()
  if existing_tags:
    raise ReleaseError(
        f"El tag {tag} ya existe. No se reescriben tags; calcula una versión nueva con "
        "python3 scripts/release.py bump patch."
    )

  subprocess.run(
      ["bash", "-n", "scripts/install.sh", "scripts/package-release.sh"],
      cwd=ROOT,
      check=True,
  )
  subprocess.run(
      [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
      cwd=ROOT,
      check=True,
  )
  subprocess.run(["node", "integration_tests/browser_bridge.test.js"], cwd=ROOT, check=True)

  with tempfile.TemporaryDirectory(prefix="xdm-release-preflight-") as temporary:
    subprocess.run(
        ["bash", "scripts/package-release.sh", tag, temporary],
        cwd=ROOT,
        check=True,
    )
    release_dir = Path(temporary) / tag
    subprocess.run(
        [
            "sha256sum", "--check", "--strict",
            "xdm-companion-linux.tar.gz.sha256",
            "xdm-companion-windows.zip.sha256",
        ],
        cwd=release_dir,
        check=True,
    )

  run_git("tag", "-a", tag, "-m", f"Release {tag}")
  run_git("push", "origin", tag)
  print(f"Tag {tag} enviado. GitHub Actions generará y publicará los paquetes.")


def main():
  parser = argparse.ArgumentParser(
      description="Versiona y publica releases de XDM Companion sin escribir tags manualmente.",
      epilog=(
          "Flujo: crea una rama desde main; ejecuta 'bump patch|minor|major'; "
          "confirma y fusiona el PR; luego ejecuta 'publish' desde main."
      ),
  )
  commands = parser.add_subparsers(dest="command", required=True)
  bump_parser = commands.add_parser("bump", help="Calcula y sincroniza la próxima versión en ambos manifiestos.")
  bump_parser.add_argument("level", choices=("patch", "minor", "major"))
  check_parser = commands.add_parser("check", help="Comprueba que un tag coincida con ambos manifiestos.")
  check_parser.add_argument("tag")
  commands.add_parser("publish", help="Prepara paquetes, valida y publica el tag derivado de los manifiestos.")
  arguments = parser.parse_args()

  try:
    if arguments.command == "bump":
      bump(arguments.level)
    elif arguments.command == "check":
      version = validate_tag(arguments.tag, read_manifest_versions())
      print(f"Tag {arguments.tag} coincide con los manifiestos ({version}).")
    else:
      publish()
  except (ReleaseError, subprocess.CalledProcessError) as error:
    print(f"[!] {error}", file=sys.stderr)
    return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
