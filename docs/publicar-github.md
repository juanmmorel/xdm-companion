# Publicar el repositorio y crear una release

Esta guía publica el código fuente en GitHub y prepara instaladores para descargar releases.

## 1. Crear el repositorio en GitHub

En GitHub, crea un repositorio público si quieres que cualquier persona pueda instalarlo sin iniciar sesión. También puede ser privado: en ese caso, cada usuario debe tener acceso al repositorio y autenticarse con GitHub CLI antes de instalarlo.

No marques las opciones para generar README, `.gitignore` o licencia: esos archivos ya están en el proyecto. El usuario de GitHub es `juanmmorel`; anota el nombre del repositorio como `REPO`.

## 2. Autorizar Git por SSH

Ya existe una clave ED25519 en `~/.ssh/id_ed25519`. Muestra **solo la clave pública**:

```bash
cat ~/.ssh/id_ed25519.pub
```

En GitHub abre **Settings → SSH and GPG keys → New SSH key**, elige **Authentication Key** y pega esa línea. Nunca compartas ni subas `~/.ssh/id_ed25519` (el archivo sin `.pub`): es la clave privada.

Comprueba la conexión:

```bash
ssh -T git@github.com
```

GitHub debe saludar a `juanmmorel` e indicar que la autenticación tuvo éxito. La primera vez puede pedir confirmar la huella de `github.com`; verifica que sea la huella publicada por GitHub antes de aceptarla.

## 3. Subir el código fuente

En una terminal, ejecuta:

```bash
cd /home/juanmorel/Proyectos/xdm-companion-package

# En este espacio de trabajo .git es un directorio vacío, no un repositorio.
rmdir .git
git init -b main
git add .
git status --short
```

Revisa la lista antes de continuar. Los entornos virtuales, cachés y archivos generados están excluidos por `.gitignore`; no agregues perfiles del navegador, cookies, claves ni otros datos personales.

```bash
git commit -m "Prepare XDM Companion repository"
git remote add origin git@github.com:juanmmorel/REPO.git
git push -u origin main
```

Sustituye `REPO` por el nombre que elegiste. Si Git te pide identificarte, configura `user.name` y `user.email` y repite el commit.

## 4. Publicar una nueva versión

La versión `v1.1.20` ya fue publicada antes de añadir el paquete de Windows. No vuelvas a usar ese tag: GitHub no actualizará automáticamente sus archivos. Para publicar estos cambios, asigna la misma versión nueva a los manifiestos de Chrome y Firefox; el tag debe coincidir con ella para que el empaquetador lo acepte:

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

Sustituye `X.Y.Z` por la versión nueva. El workflow **Release XDM Companion** ejecuta las verificaciones, genera los paquetes de Linux y Windows y crea una release con sus checksums. En GitHub, abre **Actions** y confirma que la release contiene `xdm-companion-linux.tar.gz`, `xdm-companion-linux.tar.gz.sha256`, `xdm-companion-windows.zip` y `xdm-companion-windows.zip.sha256`.

Para cada nueva versión, actualiza los dos manifiestos con el mismo valor, crea el tag `vX.Y.Z` correspondiente y súbelo.

## 5. Instalar desde la release

Cuando la release esté publicada, ejecuta en Linux:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh) --repo juanmmorel/REPO
```

Reemplaza `REPO` en los dos lugares. Este comando requiere Bash, conexión a GitHub y que el repositorio sea público. El instalador obtiene la última release estable; para fijar una versión usa `--version vX.Y.Z`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh) --repo juanmmorel/REPO --version vX.Y.Z
```

También puedes descargar y revisar el script antes de ejecutarlo:

```bash
curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh -o /tmp/xdm-companion-install.sh
less /tmp/xdm-companion-install.sh
bash /tmp/xdm-companion-install.sh --repo juanmmorel/REPO
```

En Windows, abre PowerShell y ejecuta:

```powershell
irm https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.ps1 | iex
```

Reemplaza `REPO` por el nombre real del repositorio. El paquete Windows se publica en la misma release. Este instalador configura el servicio en segundo plano; la extensión **XDM Companion Bridge** se instala por separado desde la tienda del navegador. Para desinstalar, ejecuta `& "$env:LOCALAPPDATA\XDM Companion\uninstall.ps1"`.

## Instalar desde un repositorio privado

La autenticación SSH que permite `git push` no autoriza las descargas HTTPS de `curl` o `irm`. Para evitar el 404 de GitHub en un repositorio privado, cada usuario autorizado debe instalar [GitHub CLI](https://cli.github.com/) e iniciar sesión. El instalador detecta la sesión de `gh` y la utiliza para consultar la release y descargar los paquetes privados; no guarda el token en el repositorio.

En Linux:

```bash
gh auth login --hostname github.com --web
installer="$(mktemp)"
gh api repos/juanmmorel/REPO/contents/scripts/install.sh -H 'Accept: application/vnd.github.raw+json' > "$installer" && bash "$installer" --repo juanmmorel/REPO
status=$?
rm -f "$installer"
test "$status" -eq 0
```

En Windows PowerShell:

```powershell
gh auth login --hostname github.com --web
$installer = Join-Path $env:TEMP "xdm-companion-install.ps1"
try {
  gh api repos/juanmmorel/REPO/contents/scripts/install.ps1 -H "Accept: application/vnd.github.raw+json" | Set-Content -LiteralPath $installer -Encoding UTF8
  if ($LASTEXITCODE -ne 0) { throw "No se pudo descargar el instalador. Comprueba el acceso al repositorio." }
  & $installer -Repository "juanmmorel/REPO"
} finally {
  Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
}
```

Sustituye `REPO` por el nombre real en cada lugar. Si una persona no tiene acceso al repositorio privado, GitHub seguirá devolviendo 404; el instalador no puede conceder permisos ni hacer público el repositorio.
