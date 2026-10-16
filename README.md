# AlertaV

[![backend](https://github.com/EmersonDEVRegion/AlertaV/actions/workflows/backend.yml/badge.svg)](https://github.com/EmersonDEVRegion/AlertaV/actions/workflows/backend.yml)
[![frontend](https://github.com/EmersonDEVRegion/AlertaV/actions/workflows/frontend.yml/badge.svg)](https://github.com/EmersonDEVRegion/AlertaV/actions/workflows/frontend.yml)
[![licencia: AGPL-3.0](https://img.shields.io/badge/licencia-AGPL--3.0-blue.svg)](LICENSE)

Mapa ciudadano de emergencias de la **Región de Valparaíso**: incendios,
accidentes, rescates, cortes de luz, agua y ruta, sismos, lluvia y alertas
oficiales, en una PWA instalable y gratuita.

**→ [alertav.vercel.app](https://alertav.vercel.app)**

> AlertaV no reemplaza al 132 (Bomberos), 131 (SAMU), 133 (Carabineros) ni a
> SENAPRED. Ante una emergencia, llama.

## Cómo funciona

AlertaV no depende de una sola fuente. Recolecta **señales** independientes
—satélite, CONAF, SENAPRED, centrales de Bomberos, empresas de servicios,
prensa, vecinos— y un motor de correlación las agrupa en **incidentes**, cada
uno con su nivel de confianza y la lista de fuentes que lo respaldan.

```text
 Fuentes ──► collectors ──┐
 Apify (X) ► webhook ─────┼──► raw_events ──► motor de correlación ──► incidents
 Vecinos ──► reporte ─────┘   (PostGIS)        (cada 2 min)               │
                                                        ┌─────────────────┼──────────────┐
                                                        ▼                 ▼              ▼
                                                    API REST        avisos push    /i/{code}
                                                        │
                                                        ▼
                                                PWA (React + MapLibre)
```

Dos reglas guían todo el diseño: **una señal no es un incidente** (una anomalía
térmica de FIRMS nunca se muestra como incendio confirmado) y **cada afirmación
lleva su fuente**.

## Fuentes

NASA FIRMS · CONAF · SENAPRED · centrales de Bomberos en X (Valparaíso, Viña
del Mar, Los Andes, Quilpué, Quillota) · SIG Bomberos · CSN · USGS · Chilquinta
· CGE · Esval · Transporte Informa (MTT) · MOP Vialidad · Open-Meteo · prensa
regional · GBV · reportes ciudadanos. Detalle en [`docs/fuentes.md`](docs/fuentes.md).

## Stack

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 async, Alembic, httpx |
| Base | PostgreSQL 17 + PostGIS (Supabase) |
| Frontend | React 19, TypeScript 7, Vite, MapLibre GL, TanStack Query, Tailwind CSS 4 |
| Avisos | Web Push con VAPID propio (RFC 8291) |
| Texto libre | Gemini para extraer calles; un índice propio de esquinas de OSM y Nominatim para geocodificar |
| Infraestructura | Render, Vercel, Supabase, Apify, Oracle Cloud; todo en planes gratuitos |

## Repositorio

```text
backend/     API, workers (collectors, motor, avisos), migraciones y tests
frontend/    PWA
docs/        arquitectura, API, fuentes, PWA, despliegue y operación
infra/       Apify (Tasks de X) y el proxy de salida en Chile (Oracle)
.github/     CI del backend y del frontend; keepalive de la API
```

## Desarrollo

```bash
# Backend: PostGIS local, dependencias, esquema y API
cd backend
docker compose up -d db
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload            # http://localhost:8000/docs

# Frontend, en otra terminal
cd frontend
npm install
npm run dev                              # http://localhost:5173
```

Más en [`backend/README.md`](backend/README.md) y
[`frontend/README.md`](frontend/README.md).

## Documentación

| Documento | Qué cuenta |
|---|---|
| [Arquitectura](docs/arquitectura.md) | Procesos, esquema, motor de correlación y confianza |
| [API](docs/api.md) | Rutas públicas, de operación e integraciones |
| [Fuentes](docs/fuentes.md) | Qué se lee de cada fuente y cómo no fallar en silencio |
| [PWA](docs/pwa.md) | Decisiones de diseño de la interfaz |
| [Meteorología](docs/meteorologia.md) | Lluvia, grilla y la capa táctica multiamenaza |
| [Avisos push](docs/notificaciones-push.md) | Reglas, formato y puesta en marcha |
| [Despliegue](docs/despliegue.md) | Supabase, Render, Vercel, Apify y Oracle |
| [Operación](docs/operacion.md) | Salud, rutas de operador, scripts y palancas |
| [Postmortems](docs/postmortems/) | Incidentes técnicos y lo que se aprendió |

## Privacidad

AlertaV guarda lo mínimo: para los avisos, la suscripción del navegador, la
última ubicación (redondeada) y los lugares guardados, que se borran al
desactivarlos; para un reporte ciudadano, el punto, la categoría, el texto y
huellas con sal, nunca la IP. El detalle está en «Privacidad», dentro del menú
de la app.

## Licencia y autoría

[AGPL-3.0](LICENSE). Proyecto sin fines de lucro de **Emerson** y **Claude**, de
uso público, con crédito a cada fuente.
Contacto: [emerson.alcorce.silva@gmail.com](mailto:emerson.alcorce.silva@gmail.com).
