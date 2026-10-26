# Publicar el repositorio y crear la primera release

Esta guía publica el código fuente en GitHub y prepara el instalador para descargar la primera versión desde una release pública.

## 1. Crear el repositorio en GitHub

En GitHub, crea un repositorio **público**. El instalador remoto consulta las releases sin autenticación, por lo que un repositorio privado no sirve para el comando de instalación mostrado abajo.

No marques las opciones para generar README, `.gitignore` o licencia: esos archivos ya están en el proyecto. Anota el nombre como `OWNER/REPO`.

## 2. Subir el código fuente

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
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

Sustituye `OWNER/REPO` por la cuenta y el nombre que elegiste. Si Git te pide identificarte, configura `user.name` y `user.email` y repite el commit.

## 3. Publicar la primera versión

Ahora ambos manifiestos tienen la versión `1.1.18`. El nombre del tag debe coincidir con esa versión para que el empaquetador lo acepte:

```bash
git tag v1.1.18
git push origin v1.1.18
```

El workflow **Release XDM Companion** ejecuta las verificaciones, genera el paquete Linux y crea una release pública con el archivo y su checksum. En GitHub, abre **Actions** y espera que termine correctamente; luego confirma en **Releases** que `v1.1.18` incluye `xdm-companion-linux.tar.gz` y `xdm-companion-linux.tar.gz.sha256`.

Para futuras versiones, actualiza la versión en los manifiestos Chrome y Firefox al mismo valor, crea el tag correspondiente y súbelo. Por ejemplo, `v1.1.19` debe coincidir con `1.1.19` en ambos manifiestos.

## 4. Instalar desde la release

Cuando la release esté publicada, ejecuta en Linux:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install.sh) --repo OWNER/REPO
```

Reemplaza `OWNER/REPO` en los dos lugares. Este comando requiere Bash, conexión a GitHub y un repositorio público. El instalador obtiene la última release estable; para fijar una versión usa `--version v1.1.18`:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install.sh) --repo OWNER/REPO --version v1.1.18
```

También puedes descargar y revisar el script antes de ejecutarlo:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/install.sh -o /tmp/xdm-companion-install.sh
less /tmp/xdm-companion-install.sh
bash /tmp/xdm-companion-install.sh --repo OWNER/REPO
```
