# Cómo contribuir

¡Gracias por el interés! DataPulse es un proyecto de portafolio, pero las mejoras y los reportes
de errores son bienvenidos.

## Antes de empezar

1. Abre un issue describiendo el cambio (o el error) para acordar el enfoque.
2. Las decisiones de diseño relevantes se documentan en [docs/adr/](docs/adr): si tu cambio
   contradice una, propón un ADR nuevo en lugar de saltártela.

## Entorno de desarrollo

Requisitos: Git, Docker Desktop y [uv](https://docs.astral.sh/uv/). La guía completa está en el
[README](README.md#desarrollo-local-windows-powershell).

```powershell
Copy-Item .env.example .env          # y cambia las contraseñas
.\scripts\dev.ps1 setup
docker compose up -d postgres
.\scripts\dev.ps1 migrate
.\scripts\dev.ps1 createtestdb
.\scripts\dev.ps1 check              # lint + tipos + pruebas + seguridad (como el CI)
```

En Linux/macOS usa `make check`. `check` completo tarda unos minutos (en Windows, cerca de 17);
mientras desarrollas basta con `.\scripts\dev.ps1 test-unit`.

## Reglas del proyecto

- **El CI debe quedar en verde:** ruff, mypy `strict`, bandit, pip-audit, gitleaks y cobertura
  ≥ 90 %.
- **Toda corrección lleva una prueba** que falle sin ella; todo comportamiento nuevo, pruebas.
- **Nada de secretos:** `.env` no se versiona y los valores de ejemplo usan `change-me-*`.
  Los datos son sintéticos y los correos usan `example.com`.
- **Finales de línea LF** (lo fuerza `.gitattributes`). Los `.ps1` con acentos se guardan **con
  BOM** para que PowerShell 5.1 los lea bien.
- Si cambias `data/samples`, regénéralos con `.\scripts\dev.ps1 generate`: una prueba exige que
  coincidan con `manifest.json`.

## Commits y pull requests

- Mensajes en español con prefijo convencional: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Un commit por cambio lógico; la descripción explica el **porqué**.
- Rellena la plantilla del pull request e indica cómo lo verificaste.

## Seguridad

Las vulnerabilidades se reportan en privado: ver [SECURITY.md](SECURITY.md).
