# Política de privacidad de XDM Companion Bridge

**Versión:** 1.1.22

**Última actualización:** 25 de octubre de 2026

## Datos que procesa

El puente detecta reproductores en los sitios que aparecen en el catálogo de XDM Companion. Para preparar las resoluciones puede procesar la dirección de la página, el título, el idioma del navegador y del audio, el agente del navegador, los datos de reproducción que el sitio expone y las cookies que el navegador permite leer para ese sitio. Las cookies pueden incluir una sesión iniciada.

El puente envía esos datos únicamente al servicio local de XDM Companion en el mismo equipo (`127.0.0.1`). El servicio los usa para consultar el sitio de origen y sus servidores de medios; cuando corresponde, envía a ese mismo proveedor las cookies de su dominio para autenticar la reproducción. Tras la selección, el servicio entrega a la aplicación XDM local la dirección del flujo y los encabezados o cookies necesarios para que la descarga funcione. El proyecto no recibe esos datos en un servidor remoto ni los comparte con un servicio de analítica. XDM Companion no descifra ni elude DRM.

## Conservación y control

El puente no mantiene un historial ni usa analítica. La página puede mantener en memoria la lista de calidades durante cuatro minutos y el servicio conserva la sesión seleccionable durante cinco minutos. Las cookies se escriben temporalmente en un archivo local para el análisis y el archivo se elimina al terminar esa operación. El manifiesto filtrado puede permanecer en memoria hasta seis horas para que XDM pueda leerlo. Estos datos caducan y no se guardan en el almacenamiento del navegador.

La detección y precarga se realizan en los sitios configurados para que el selector esté listo al abrirlo. La instalación del complemento solicita los permisos del navegador y la autorización de procesamiento de datos requerida por Firefox; sin ella, la función no puede operar. El usuario puede retirar el puente desde el navegador o desinstalar XDM Companion para detener el procesamiento. Cerrar sesión en el sitio revoca la sesión que aporta el navegador.

## Seguridad

El servicio local escucha solo en `127.0.0.1`. El puente limita la comunicación a ese servicio y el servicio valida el origen del complemento y el sitio configurado antes de procesar la solicitud. Mantén actualizado el navegador, XDM y XDM Companion.

al.
