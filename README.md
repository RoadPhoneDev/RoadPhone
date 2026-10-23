# Road Phone

Programa para Windows que lee la telemetría de **Euro Truck Simulator 2** y la envía a tu app de móvil Road Phone.

> Estado: **Alpha**. Pueden aparecer errores; si ves alguno, usa *Ajustes → Copiar informe de diagnóstico* y envíalo.

## Instalación
1. Descarga el `.zip` de la última versión en la sección **Releases** (a la derecha).
2. Descomprime la carpeta `RoadPhone` donde quieras.
3. Abre `RoadPhone.exe`, inicia sesión y pulsa **Iniciar**.

Windows puede mostrar un aviso de SmartScreen porque el programa aún no está firmado: pulsa *Más información → Ejecutar de todas formas*.

## Actualizar
Desde la versión 0.3.0, abre *Ajustes → Road Phone* y pulsa **Buscar actualización**. Si hay una versión nueva, pulsa **Actualizar Road Phone**: la app se descarga, se verifica, se reemplaza y se vuelve a abrir sola (con copia de respaldo por si algo falla).

Si vienes de una versión anterior a la 0.3.0, descarga el zip del Release una vez a mano. Si la carpeta de Road Phone está en un lugar protegido (como *Archivos de programa*), muévela a Documentos o al Escritorio.

## Plugin de telemetría
Usa el plugin de código abierto [RenCloud/scs-sdk-plugin](https://github.com/RenCloud/scs-sdk-plugin) (licencia MIT).
