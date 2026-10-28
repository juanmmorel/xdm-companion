#!/usr/bin/env bash

# Installs XDM Companion from a local checkout or the latest published GitHub release.
if [ -z "${BASH_VERSION:-}" ]; then
  printf '[!] Este instalador requiere Bash; ejecútalo con bash, no con sh.\n' >&2
  exit 2
fi

set -Eeuo pipefail
umask 077

ASSET_NAME="xdm-companion-linux.tar.gz"
REPOSITORY="${XDM_COMPANION_REPOSITORY:-}"
REMOTE_INSTALL="false"
[[ -z "$REPOSITORY" ]] || REMOTE_INSTALL="true"
RELEASE_VERSION="latest"
DO_UNINSTALL="false"
PYTHON_BIN="${XDM_COMPANION_PYTHON:-python3}"
WORK_DIR=""
LOG_FILE="${XDG_STATE_HOME:-${HOME}/.local/state}/xdm-companion/install.log"
GITHUB_TOKEN_VALUE="${XDM_COMPANION_GITHUB_TOKEN:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
GITHUB_CLI_AUTHENTICATED="false"

if [[ -z "$GITHUB_TOKEN_VALUE" ]] && command -v gh >/dev/null 2>&1; then
  GITHUB_TOKEN_VALUE="$(gh auth token --hostname github.com 2>/dev/null || true)"
fi
if [[ -n "$GITHUB_TOKEN_VALUE" ]] && command -v gh >/dev/null 2>&1; then
  GITHUB_CLI_AUTHENTICATED="true"
fi

gh_cli() {
  if [[ -n "$GITHUB_TOKEN_VALUE" ]]; then
    GH_TOKEN="$GITHUB_TOKEN_VALUE" gh "$@"
  else
    gh "$@"
  fi
}

usage() {
  cat <<'USAGE'
Uso:
  bash scripts/install.sh [--yes]                         Instalar desde el checkout local
  XDM_COMPANION_REPOSITORY=OWNER/REPO bash install.sh    Instalar la última release de GitHub
  bash install.sh --repo OWNER/REPO [--version vX.Y.Z]  Elegir repositorio o versión
  bash install.sh --uninstall                            Desinstalar la instalación del usuario

Opciones:
  --repo OWNER/REPO   Repositorio de GitHub
  --version TAG       Release que se instalará; por defecto, la última release estable
  --yes               Alias de compatibilidad; no solicita permisos de administrador
  --uninstall         Ejecutar el desinstalador instalado
  --help              Mostrar esta ayuda

Para un repositorio privado, inicia sesión con GitHub CLI (`gh auth login`) antes de ejecutar el instalador.
El paquete de release debe contener xdm-companion-linux.tar.gz y su SHA-256.
USAGE
}

log() { printf '[*] %s\n' "$*"; }
fail() { printf '[!] %s\n' "$*" >&2; exit 1; }

on_error() {
  local code=$?
  trap - ERR
  printf '[!] Falló la instalación (código %s). Registro: %s\n' "$code" "$LOG_FILE" >&2
  exit "$code"
}
trap on_error ERR

while (($#)); do
  case "$1" in
    --repo)
      (($# >= 2)) || fail "Falta el valor de --repo."
      REPOSITORY="$2"
      REMOTE_INSTALL="true"
      shift 2
      ;;
    --version)
      (($# >= 2)) || fail "Falta el valor de --version."
      RELEASE_VERSION="$2"
      REMOTE_INSTALL="true"
      shift 2
      ;;
    --yes|-y)
      shift
      ;;
    --uninstall)
      DO_UNINSTALL="true"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "Opción desconocida: $1. Usa --help para ver las opciones."
      ;;
  esac
done

if [[ "$DO_UNINSTALL" == "true" ]]; then
  installed_uninstaller="${HOME}/.local/bin/xdm-companion-uninstall"
  [[ -x "$installed_uninstaller" ]] || fail "No se encontró el desinstalador. Instala XDM Companion primero."
  exec "$installed_uninstaller"
fi

[[ "$(uname -s)" == "Linux" ]] || fail "Este instalador automatizado está preparado para Linux."

install_missing_dependencies() {
  local missing=()
  local command_name
  for command_name in curl tar sha256sum tee mktemp; do
    command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
  done
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || missing+=("python3")
  ((${#missing[@]} == 0)) && return 0
  fail "Faltan herramientas necesarias (${missing[*]}). Instálalas en el sistema y vuelve a ejecutar el instalador; XDM Companion no solicitará credenciales de administrador."
}

install_missing_dependencies
mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1
export PATH="${HOME}/.xdman/companion/runtime:${HOME}/.local/bin:${PATH}"

if [[ "$(uname -m)" != "x86_64" && "$(uname -m)" != "aarch64" && "$(uname -m)" != "arm64" ]]; then
  fail "Arquitectura no compatible: $(uname -m)."
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/xdm-companion-install.XXXXXXXX")"
cleanup() { [[ -z "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT

has_supported_js_runtime() {
  local executable minimum_major minimum_minor
  for executable in deno node; do
    command -v "$executable" >/dev/null 2>&1 || continue
    if [[ "$executable" == "deno" ]]; then
      minimum_major=2
      minimum_minor=3
    else
      minimum_major=22
      minimum_minor=0
    fi
    if "$PYTHON_BIN" - "$executable" "$minimum_major" "$minimum_minor" <<'PY'
import re
import subprocess
import sys

executable, minimum_major, minimum_minor = sys.argv[1:]
try:
  result = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=5, check=True)
except (OSError, subprocess.SubprocessError):
  raise SystemExit(1)
match = re.search(r"(\d+)\.(\d+)", (result.stdout or "") + " " + (result.stderr or ""))
if not match or (int(match.group(1)), int(match.group(2))) < (int(minimum_major), int(minimum_minor)):
  raise SystemExit(1)
PY
    then
      return 0
    fi
  done
  return 1
}

install_user_deno() {
  local target asset api_url runtime_dir
  case "$(uname -m)" in
    x86_64) target="x86_64-unknown-linux-gnu" ;;
    aarch64|arm64) target="aarch64-unknown-linux-gnu" ;;
    *) fail "Deno no tiene una descarga automática configurada para $(uname -m)." ;;
  esac
  asset="deno-${target}.zip"
  api_url="https://api.github.com/repos/denoland/deno/releases/latest"
  log "No hay Deno 2.3+ ni Node.js 22+; preparando Deno para este usuario."
  curl --fail --silent --show-error --location --retry 3 \
    --header "Accept: application/vnd.github+json" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    "$api_url" --output "$WORK_DIR/deno-release.json"

  mapfile -t DENO_INFO < <("$PYTHON_BIN" - "$WORK_DIR/deno-release.json" "$asset" <<'PY'
import json
import re
import sys

release_path, asset_name = sys.argv[1:]
document = json.loads(open(release_path, encoding="utf-8").read())
tag = str(document.get("tag_name") or "")
if not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or document.get("draft") or document.get("prerelease"):
  raise SystemExit("GitHub no devolvió una release estable de Deno.")
base = f"https://github.com/denoland/deno/releases/download/{tag}/"
assets = {item.get("name"): item for item in document.get("assets", []) if isinstance(item, dict)}
for name in (asset_name, f"{asset_name}.sha256sum"):
  item = assets.get(name)
  url = str(item.get("browser_download_url") or "") if item else ""
  if not url.startswith(base) or url.rsplit("/", 1)[-1] != name:
    raise SystemExit(f"La release de Deno no contiene el archivo oficial {name}.")
  print(url)
print(tag)
PY
  )
  [[ "${#DENO_INFO[@]}" -eq 3 ]] || fail "No se pudieron validar los archivos de la release de Deno."
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 \
    "${DENO_INFO[0]}" --output "$WORK_DIR/$asset"
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 \
    "${DENO_INFO[1]}" --output "$WORK_DIR/$asset.sha256sum"
  (cd "$WORK_DIR" && sha256sum --check --strict "$asset.sha256sum")
  "$PYTHON_BIN" - "$WORK_DIR/$asset" "$WORK_DIR/deno" <<'PY'
import pathlib
import shutil
import stat
import sys
import zipfile

archive_path, output_path = map(pathlib.Path, sys.argv[1:])
with zipfile.ZipFile(archive_path) as archive:
  files = [item for item in archive.infolist() if not item.is_dir()]
  if len(files) != 1 or files[0].filename != "deno":
    raise SystemExit("El ZIP oficial de Deno no contiene únicamente el ejecutable esperado.")
  item = files[0]
  mode = item.external_attr >> 16
  if stat.S_ISLNK(mode) or item.file_size > 512 * 1024 * 1024:
    raise SystemExit("El ejecutable de Deno tiene un tipo o tamaño no permitido.")
  with archive.open(item) as source, output_path.open("wb") as destination:
    shutil.copyfileobj(source, destination)
PY
  runtime_dir="${HOME}/.xdman/companion/runtime"
  mkdir -p "$runtime_dir"
  cp "$WORK_DIR/deno" "$runtime_dir/.deno.new.$$"
  chmod 0755 "$runtime_dir/.deno.new.$$"
  mv -f -- "$runtime_dir/.deno.new.$$" "$runtime_dir/deno"
  log "Deno ${DENO_INFO[2]} instalado en $runtime_dir/deno."
}

LOCAL_PROJECT=""
SCRIPT_PATH="${BASH_SOURCE[0]}"
if [[ -f "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
  CANDIDATE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
  if [[ -f "$CANDIDATE_ROOT/installer/install.py" && -f "$CANDIDATE_ROOT/core/domains.json" ]]; then
    LOCAL_PROJECT="$CANDIDATE_ROOT"
  fi
fi

run_installer() {
  local project_dir="$1"
  [[ -f "$project_dir/installer/install.py" ]] || fail "El paquete no contiene installer/install.py."
  [[ -f "$project_dir/core/domains.json" ]] || fail "El paquete no contiene core/domains.json."
  [[ -f "$project_dir/requirements.txt" ]] || fail "El paquete no contiene requirements.txt."
  log "Instalando XDM Companion desde: $project_dir"
  "$PYTHON_BIN" "$project_dir/installer/install.py"
}

finish_install() {
  local project_dir="$1" label="$2" installer_status=0
  run_installer "$project_dir" || installer_status=$?
  if [[ "$installer_status" -eq 0 ]]; then
    log "$label se instaló correctamente. Registro: $LOG_FILE"
  elif [[ "$installer_status" -eq 2 ]]; then
    log "Instalación parcial: el servicio quedó activo, pero alguna integración del navegador requiere atención."
    log "Consulta el detalle en: $LOG_FILE"
  else
    return "$installer_status"
  fi
  log "Desinstalación: $HOME/.local/bin/xdm-companion-uninstall"
  return "$installer_status"
}

if [[ -n "$LOCAL_PROJECT" && "$REMOTE_INSTALL" != "true" ]]; then
  if ! has_supported_js_runtime; then
    install_user_deno
  fi
  has_supported_js_runtime || fail "No se encontró una versión válida de Deno o Node.js después de la preparación."
  log "Usando los archivos del proyecto local."
  if finish_install "$LOCAL_PROJECT" "XDM Companion"; then
    exit 0
  else
    exit "$?"
  fi
fi

[[ "$REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || fail \
  "No se conoce el repositorio. Usa --repo OWNER/REPO o define XDM_COMPANION_REPOSITORY."
[[ "$RELEASE_VERSION" == "latest" || "$RELEASE_VERSION" =~ ^v?[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || fail \
  "Etiqueta de release no válida: $RELEASE_VERSION"

if ! has_supported_js_runtime; then
  install_user_deno
fi
has_supported_js_runtime || fail "No se encontró una versión válida de Deno o Node.js después de la preparación."

if [[ "$RELEASE_VERSION" == "latest" ]]; then
  RELEASE_API="https://api.github.com/repos/${REPOSITORY}/releases/latest"
else
  RELEASE_API="https://api.github.com/repos/${REPOSITORY}/releases/tags/${RELEASE_VERSION}"
fi
log "Consultando release de $REPOSITORY ($RELEASE_VERSION)."
if [[ "$GITHUB_CLI_AUTHENTICATED" == "true" ]]; then
  if [[ "$RELEASE_VERSION" == "latest" ]]; then
    RELEASE_ENDPOINT="repos/${REPOSITORY}/releases/latest"
  else
    RELEASE_ENDPOINT="repos/${REPOSITORY}/releases/tags/${RELEASE_VERSION}"
  fi
  if ! gh_cli api "$RELEASE_ENDPOINT" > "$WORK_DIR/release.json"; then
    fail "No se pudo leer la release de $REPOSITORY. Confirma que tu cuenta tenga acceso al repositorio y a sus releases."
  fi
else
  if ! curl --fail --silent --show-error --location --retry 3 \
    --header "Accept: application/vnd.github+json" \
    --header "X-GitHub-Api-Version: 2022-11-28" \
    "$RELEASE_API" --output "$WORK_DIR/release.json"; then
    fail "GitHub no permitió leer la release. Si el repositorio es privado, instala GitHub CLI, ejecuta 'gh auth login' con una cuenta autorizada y vuelve a ejecutar este instalador. Un repositorio privado solo está disponible para sus usuarios autorizados."
  fi
fi

mapfile -t RELEASE_INFO < <("$PYTHON_BIN" - "$WORK_DIR/release.json" "$REPOSITORY" "$RELEASE_VERSION" "$ASSET_NAME" <<'PY'
import json
import re
import sys

release_path, repository, requested_version, asset_name = sys.argv[1:]
document = json.loads(open(release_path, encoding="utf-8").read())
tag = str(document.get("tag_name") or "")
if not re.fullmatch(r"v?[A-Za-z0-9][A-Za-z0-9._-]{0,63}", tag):
  raise SystemExit("GitHub devolvió una etiqueta no válida.")
if document.get("draft") or document.get("prerelease"):
  raise SystemExit("La release seleccionada no es una release estable publicada.")
if requested_version != "latest" and tag != requested_version:
  raise SystemExit(f"GitHub respondió con {tag}, no con {requested_version}.")

base = f"https://github.com/{repository}/releases/download/{tag}/"
assets = {item.get("name"): item for item in document.get("assets", []) if isinstance(item, dict)}
for name in (asset_name, f"{asset_name}.sha256"):
  item = assets.get(name)
  url = str(item.get("browser_download_url") or "") if item else ""
  if not url.startswith(base) or url.rsplit("/", 1)[-1] != name:
    raise SystemExit(f"La release {tag} no contiene el asset esperado {name}.")
  print(url)
print(tag)
PY
)
[[ "${#RELEASE_INFO[@]}" -eq 3 ]] || fail "La release no tiene todos los archivos necesarios."
ARCHIVE_URL="${RELEASE_INFO[0]}"
CHECKSUM_URL="${RELEASE_INFO[1]}"
RELEASE_TAG="${RELEASE_INFO[2]}"

log "Descargando XDM Companion $RELEASE_TAG."
if [[ "$GITHUB_CLI_AUTHENTICATED" == "true" ]]; then
  if ! gh_cli release download "$RELEASE_TAG" --repo "$REPOSITORY" \
    --pattern "$ASSET_NAME" --pattern "$ASSET_NAME.sha256" --dir "$WORK_DIR"; then
    fail "No se pudieron descargar los archivos de la release. Confirma que tu cuenta tenga permiso para leer sus assets."
  fi
else
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 \
    "$ARCHIVE_URL" --output "$WORK_DIR/$ASSET_NAME"
  curl --fail --silent --show-error --location --retry 3 --connect-timeout 20 \
    "$CHECKSUM_URL" --output "$WORK_DIR/$ASSET_NAME.sha256"
fi
[[ -f "$WORK_DIR/$ASSET_NAME" && -f "$WORK_DIR/$ASSET_NAME.sha256" ]] || fail \
  "GitHub no entregó ambos archivos de la release. Revisa el acceso al repositorio y sus assets."
(cd "$WORK_DIR" && sha256sum --check --strict "$ASSET_NAME.sha256")

"$PYTHON_BIN" - "$WORK_DIR/$ASSET_NAME" <<'PY'
import pathlib
import sys
import tarfile

archive_path = pathlib.Path(sys.argv[1])
with tarfile.open(archive_path, "r:gz") as archive:
  required = {"installer/install.py", "core/domains.json", "requirements.txt"}
  found = set()
  for member in archive.getmembers():
    path = pathlib.PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
      raise SystemExit(f"El paquete incluye una ruta insegura: {member.name}")
    if not (member.isdir() or member.isfile()):
      raise SystemExit(f"El paquete incluye un tipo de archivo no permitido: {member.name}")
    normalized = str(path).removeprefix("./")
    if normalized in required:
      found.add(normalized)
  if found != required:
    raise SystemExit(f"El paquete está incompleto; faltan: {sorted(required - found)}")
PY

mkdir -p "$WORK_DIR/source"
tar --extract --gzip --file "$WORK_DIR/$ASSET_NAME" --directory "$WORK_DIR/source" \
  --no-same-owner --no-same-permissions
if finish_install "$WORK_DIR/source" "XDM Companion $RELEASE_TAG"; then
  exit 0
else
  exit "$?"
fi
