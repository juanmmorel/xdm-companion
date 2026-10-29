# Publicar una release

Los manifiestos de Chrome y Firefox son la fuente de la versión. No escribas el tag a mano ni prepares una release con versiones distintas.

## Preparar la versión

Actualiza primero `main` y ejecuta el incremento que corresponda:

```bash
git switch main
git pull --ff-only origin main
python3 scripts/release.py bump patch
```

El script consulta los tags existentes, calcula la próxima versión disponible, crea una rama `release/vX.Y.Z` y actualiza ambos manifiestos juntos. Usa `minor` o `major` en vez de `patch` cuando corresponda.

Confirma únicamente los manifiestos, sube la rama y abre un PR hacia `main`:

```bash
git add browser-bridge/manifest.chrome.json browser-bridge/manifest.firefox.json
git diff --cached --check
git commit -m "Prepare release"
git push -u origin "$(git branch --show-current)"
```

Fusiona el PR después de que pasen las verificaciones.

## Publicar los paquetes

Después de la fusión, actualiza `main` y ejecuta:

```bash
git switch main
git pull --ff-only origin main
python3 scripts/release.py publish
```

El script comprueba que el checkout corresponda a `origin/main`, ejecuta las pruebas, crea y verifica los paquetes de Linux y Windows, y deriva el tag de las versiones sincronizadas. Solo entonces publica el tag. GitHub Actions vuelve a comprobar el tag antes de ejecutar la validación de Windows y crear la release con los dos paquetes y sus checksums.

Si un tag ya existe, el script no lo mueve ni lo reutiliza; el incremento selecciona una versión posterior disponible.
