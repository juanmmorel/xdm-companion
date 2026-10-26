# Publicar el repositorio y crear la primera release

Esta guía publica el código fuente en GitHub y prepara el instalador para descargar la primera versión desde una release pública.

## 1. Crear el repositorio en GitHub

En GitHub, crea un repositorio **público**. El instalador remoto consulta las releases sin autenticación, por lo que un repositorio privado no sirve para el comando de instalación mostrado abajo.

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

## 4. Publicar la primera versión

Ahora ambos manifiestos tienen la versión `1.1.19`. El nombre del tag debe coincidir con esa versión para que el empaquetador lo acepte:

```bash
git tag v1.1.19
git push origin v1.1.19
```

El workflow **Release XDM Companion** ejecuta las verificaciones, genera el paquete Linux y crea una release pública con el archivo y su checksum. En GitHub, abre **Actions** y espera que termine correctamente; luego confirma en **Releases** que `v1.1.19` incluye `xdm-companion-linux.tar.gz` y `xdm-companion-linux.tar.gz.sha256`.

Para futuras versiones, actualiza la versión en los manifiestos Chrome y Firefox al mismo valor, crea el tag correspondiente y súbelo. Por ejemplo, `v1.1.20` debe coincidir con `1.1.20` en ambos manifiestos.

## 5. Instalar desde la release

Cuando la release esté publicada, ejecuta en Linux:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh) --repo juanmmorel/REPO
```

Reemplaza `REPO` en los dos lugares. Este comando requiere Bash, conexión a GitHub y un repositorio público. El instalador obtiene la última release estable; para fijar una versión usa `--version v1.1.19`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh) --repo juanmmorel/REPO --version v1.1.19
```

También puedes descargar y revisar el script antes de ejecutarlo:

```bash
curl -fsSL https://raw.githubusercontent.com/juanmmorel/REPO/main/scripts/install.sh -o /tmp/xdm-companion-install.sh
less /tmp/xdm-companion-install.sh
bash /tmp/xdm-companion-install.sh --repo juanmmorel/REPO
```
