# AlertaV — backend

API y workers: recolección de fuentes, motor de correlación y avisos push.
Python 3.12 · FastAPI · SQLAlchemy 2 async · PostgreSQL + PostGIS · Alembic.

Cómo está hecho y por qué: [`docs/arquitectura.md`](../docs/arquitectura.md).
Rutas: [`docs/api.md`](../docs/api.md). Fuentes: [`docs/fuentes.md`](../docs/fuentes.md).

## Desarrollo

```bash
docker compose up -d db                      # PostGIS 17 en localhost:5432 (alertav/alertav)
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload                # http://localhost:8000/docs
```

Sin `.env`, la configuración por defecto apunta a esa base local. Las fuentes
con clave (`FIRMS_MAP_KEY`, `GEMINI_API_KEY`, `APIFY_*`) se definen en un
`backend/.env` que no se versiona; la lista de producción está en
[`.env.production.example`](.env.production.example) y cada ajuste está
documentado en `app/core/config.py`.

### Workers

```bash
python -m app.workers                                   # todo, como en producción
python -m app.collectors.runner                         # una pasada de cada collector
python -m app.collectors.runner --collector conaf_incendios
python -m app.collectors.runner --show-schedule         # cadencia configurada
python -m app.services.correlation.runner               # una pasada del motor
python -m app.services.correlation.runner --radius-m 2000 --window-hours 3
```

### Verificación

```bash
ruff check .
pytest                                                  # sin red ni base
ALERTAV_PG_INTEGRACION=1 pytest tests/test_integracion_pg.py   # contra la base local
```

El CI (`.github/workflows/backend.yml`) corre `ruff`, las migraciones de ida y
vuelta y `pytest` con los tests de integración contra un PostGIS desechable.

## Estructura

```text
app/
├── main.py                 FastAPI, CORS, middlewares, /static
├── workers.py              recolección + correlación + push en un proceso
├── api/v1/endpoints/       rutas (events, incidents, collectors, feed, push, share, moderacion, apify, health)
├── core/                   configuración, base de datos, logging, identidad HTTP, límites
├── models/                 ORM (señales, incidentes, sismos, push, comunas, grilla)
├── schemas/                contratos Pydantic de entrada y salida
├── repositories/           todo el SQL
├── services/
│   ├── correlation/        motor: confianza, comunas, perfiles, orquestación
│   ├── push/               avisos: reglas, mensajes, Web Push (RFC 8291 + VAPID)
│   └── …                   ingesta, webhook de Apify, feeds, clima, salud
└── collectors/             una carpeta por fuente; base.py, registry.py, runner.py
migrations/versions/        Alembic (fuente única del esquema)
scripts/                    herramientas de operación (ver docs/operacion.md)
static/geo/                 capas estáticas: cuarteles y amenaza sísmica
tests/
```

**Agregar una fuente:** implementar `fetch()` y `normalize()` sobre
`BaseCollector` y registrarla en `collectors/registry.py`. La idempotencia, la
traza en `collector_runs` y el manejo de errores ya están en `run()`.
`normalize()` es pura: se prueba con fixtures reales, sin red.

## Producción

Render (Docker, plan Free) con `start.sh`: migraciones, API y workers
supervisados. Ver [`docs/despliegue.md`](../docs/despliegue.md).
