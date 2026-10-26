# Preparación para las tiendas de extensiones

## Archivos de carga

Desde la raíz del proyecto, ejecuta:

```bash
python3 installer/build_store_packages.py
```

El proceso crea y comprueba:

- `dist/store/xdm-companion-chrome-store.zip` para Chrome Web Store.
- `dist/store/xdm-companion-firefox-amo.xpi` para cargar en Mozilla Add-ons y que Mozilla lo firme.

El XPI generado no está firmado. Firefox Release solo instala una versión firmada por Mozilla. La decisión de aprobación y firma corresponde a AMO.

## Declaraciones de privacidad

Declara que el complemento procesa actividad de navegación, acciones de descarga, contenido del sitio y cookies de los sitios configurados. Explica que el complemento transfiere estos datos solo al servicio local de Companion en el mismo equipo para preparar formatos; el servicio consulta el proveedor y entrega el flujo seleccionado a la aplicación XDM local. Declara que no hay analítica ni transmisión de datos al desarrollador. Usa la política de [privacidad](privacy-policy.md) y publica antes una copia en una URL pública estable.

No declares que el complemento solo usa cookies sin mencionar las direcciones, metadatos y contenido del reproductor procesados. Evita afirmar que todos los sitios, cuentas o videos son compatibles.

## Permisos del paquete

| Permiso | Uso |
| --- | --- |
| `cookies` | Leer las cookies de un sitio configurado para la sesión de reproducción y la descarga solicitada. |
| `scripting` | Leer, en el contexto principal de la página, los formatos directos y la pista de audio que el reproductor expone. |
| Acceso a sitios configurados | Detectar reproductores y consultar el contenido expuesto por el sitio. Los dominios vienen de `core/domains.json`. |
| `127.0.0.1:21121` | Comunicarse con el servicio local instalado por XDM Companion. |

El manifiesto no declara botones de barra, analítica, almacenamiento del navegador ni ejecución de código remoto. Firefox requiere Manifest V3 y declara las categorías de datos para su solicitud de consentimiento.

## Revisión manual antes de enviar

1. Publica la política de privacidad y sustituye cualquier referencia de soporte genérica por un contacto real del responsable.
2. Comprueba que la ficha describa el servicio local, la precarga automática y el uso de cookies.
3. Sube los archivos generados a los portales respectivos y revisa sus validadores.
4. Para la revisión, proporciona instrucciones claras para instalar XDM Companion, iniciar el servicio local y reproducir una página de prueba permitida.
5. Revisa las observaciones de la tienda y corrige cualquier requisito que dependa de la ficha, cuenta de desarrollador o proceso de firma.

La preparación técnica no garantiza la aprobación. Las tiendas toman la decisión final tras revisar el comportamiento, los permisos, las declaraciones y la ficha publicada.
