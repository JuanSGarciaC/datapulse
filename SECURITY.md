# Política de seguridad

DataPulse es un proyecto de portafolio pensado para ejecutarse en una sola máquina (ver
[Antes de un entorno real](docs/deployment.md#4-antes-de-un-entorno-real)). Aun así, se agradece
cualquier reporte de seguridad.

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de GitHub:
[Security → Report a vulnerability](https://github.com/JuanSGarciaC/datapulse/security/advisories/new).

Incluye, si puedes:

- Qué componente afecta (API, dashboard, ingesta, migraciones, `docker-compose.yml`...).
- Pasos para reproducirlo y el impacto esperado.
- Versión o commit.

Responderé en unos días; es un proyecto personal y no hay un SLA formal.

## Qué está en alcance

- Fugas de secretos (en código, logs, errores o imágenes de Docker).
- Evasión de la autenticación por clave de la API o del mínimo privilegio de la base de datos.
- Inyección SQL, o escritura en `raw`/`marts` desde un rol que no debería poder hacerlo.

## Qué no está en alcance

Las limitaciones ya documentadas: sin TLS, sin login en el dashboard, Prefect sin autenticación
y sin límite de peticiones en la API. Todos los servicios solo escuchan en `127.0.0.1`.

## Datos y secretos

Los datos son 100 % sintéticos y `.env` nunca se versiona. Si encuentras un secreto real en el
repositorio o en su historial, repórtalo por el mismo canal privado.
