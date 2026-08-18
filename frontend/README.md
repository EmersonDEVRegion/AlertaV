# AlertaV — PWA (Fase 4)

Cliente web instalable para el mapa de incidentes de la Región de Valparaíso.
Consume el motor de correlación del backend a través de `/api/v1/incidents/*`.

Stack: **Vite 8 + React 19 + TypeScript 7 + MapLibre GL JS 6 (vía react-map-gl 8)
+ TanStack Query 5 + Tailwind CSS 4 + vite-plugin-pwa**.

---

## Cómo correrlo

Requiere Node 20.19+ o 22.12+ (verificado con Node 22.23).

```bash
cd frontend
cp .env.example .env
npm install
npm run dev            # http://localhost:5173
```

En otra terminal, el backend:

```bash
cd backend
uvicorn app.main:app --reload            # http://localhost:8000
python -m app.services.correlation.runner --loop   # el worker que llena /active
```

Sin el worker corriendo, `/incidents/active` responde `[]` y la PWA muestra el
estado vacío correctamente — no es un error del frontend.

**No hace falta tocar CORS.** El dev server proxea `/api` hacia
`VITE_DEV_API_PROXY` (por defecto `http://localhost:8000`), así que el navegador
nunca cruza de origen y la URL de la API es la misma relativa (`/api/v1`) en
desarrollo y en producción.

Otros comandos:

```bash
npm run typecheck   # tsc --noEmit
npm run build       # typecheck + build de producción (genera el service worker)
npm run preview     # sirve dist/ — es la única forma de probar el service worker
```

El service worker está desactivado en `npm run dev` a propósito: cachear durante
el desarrollo es una fuente inagotable de confusión. Para probarlo, usá
`npm run build && npm run preview`, o poné `devOptions.enabled: true` en
`vite.config.ts`.

---

## Estructura

```
frontend/
├── public/icons/            # iconos PWA (192, 512, maskable, apple-touch, favicon)
├── src/
│   ├── api/
│   │   ├── types.ts         # espejo TS de app/schemas/incident.py — fuente de verdad del contrato
│   │   ├── client.ts        # fetch + ApiError + armado de query strings
│   │   └── incidents.ts     # una función por endpoint
│   ├── domain/
│   │   ├── symbology.ts     # ← el núcleo: reglas de color de los dos ejes
│   │   └── labels.ts        # enums del backend → castellano
│   ├── hooks/
│   │   ├── useActiveIncidents.ts   # polling de /incidents/active
│   │   ├── useIncidentDetail.ts    # /incidents/{code}, sólo con la ficha abierta
│   │   ├── useFreshness.ts         # edad real del dato en pantalla
│   │   └── useOnlineStatus.ts
│   ├── components/
│   │   ├── map/
│   │   │   ├── IncidentMap.tsx     # MapLibre + fuente GeoJSON
│   │   │   ├── incidentLayers.ts   # las 4 capas de círculos
│   │   │   └── MapLegend.tsx
│   │   ├── incident/
│   │   │   ├── IncidentSheet.tsx   # BottomSheet en móvil, panel lateral en desktop
│   │   │   ├── ConfidenceBar.tsx   # una barra por eje, rotulada
│   │   │   ├── ConfidenceAudit.tsx # despliegue de confidence_breakdown
│   │   │   ├── SourceChips.tsx
│   │   │   └── AlertBadge.tsx
│   │   └── ui/                     # AppHeader, StalenessBanner, MapOverlayState
│   ├── lib/                        # queryClient, geojson, format (es-CL)
│   ├── config/                     # env tipado, encuadre y límites del mapa
│   ├── App.tsx
│   └── index.css                   # Tailwind v4 + tokens
├── vite.config.ts                  # proxy, chunking, manifest y workbox
└── tsconfig.json
```

---

## Las tres decisiones que explican el diseño

### 1. El color codifica dos ejes, no uno

El backend separa deliberadamente `confidence` (¿existe el fenómeno?) de
`alert_level` / `alert_confidence` (¿qué declaró SENAPRED?). Son independientes:
**puede haber alerta roja vigente sobre un incidente que nadie confirmó en
terreno**, y al revés. Colapsarlos en un solo color obligaría a mentir en uno de
los dos.

- **Relleno = el hecho.** Rojo si `is_official_confirmed` (CONAF o Bomberos
  fueron al lugar), naranjo si hay varias fuentes sin confirmación, amarillo si
  hay una sola señal, gris si el incidente ya está cerrado.
- **Anillo = la alerta oficial.** Sólo aparece cuando hay `alert_level`.

El relleno usa `is_official_confirmed`, **nunca un umbral sobre `confidence`**.
El backend expone ese booleano justamente para que el cliente no tenga que
inventarse un corte propio.

Todas las reglas viven en `src/domain/symbology.ts` y de ahí salen tanto las
expresiones de MapLibre como la leyenda y la ficha. El mapa y la leyenda no
pueden discrepar porque leen la misma tabla.

### 2. Offline sí, pero con la edad del dato a la vista

El service worker cachea el app shell y los tiles, y sirve `/incidents/*` con
`NetworkFirst` (timeout 6 s, expiración 10 min). Eso permite abrir la app en una
quebrada sin señal.

La contraparte obligatoria es `StalenessBanner`: si la respuesta tiene más de
`VITE_STALE_AFTER_MS` (3 min por defecto) o no hay conexión, aparece una franja
ámbar con la antigüedad exacta del dato. Sin ese cartel, cachear una app de
emergencias sería una forma de desinformar.

### 3. Una sola fuente de datos

El backend ofrece `/incidents/active` (JSON tipado) y `/incidents/geojson`. La
PWA usa **sólo `/active`** y arma el GeoJSON en el cliente
(`src/lib/geojson.ts`). Motivo: la ficha necesita el objeto completo de todos
modos, y mantener dos vistas del mismo mundo es una manera segura de que se
desincronicen.

Al GeoJSON sólo viajan propiedades escalares — MapLibre serializa los arreglos
anidados, así que `sources` se lee del objeto tipado, no del feature.

---

## Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Base de la API |
| `VITE_DEV_API_PROXY` | `http://localhost:8000` | Destino del proxy de desarrollo |
| `VITE_POLL_INTERVAL_MS` | `60000` | Cadencia del polling |
| `VITE_STALE_AFTER_MS` | `180000` | Umbral del aviso de antigüedad |
| `VITE_MAP_STYLE` | CARTO Positron | Estilo del mapa base |

El polling está en 60 s porque el worker de correlación corre cada 120 s
(`CORRELATION_POLL_INTERVAL_SECONDS`). Pedir más seguido gasta batería sin traer
datos nuevos. Si cambia esa constante en el backend, cambiala acá también.

TanStack Query pausa el polling cuando la pestaña pierde el foco y refresca al
volver, así que la app no consume batería en el bolsillo.

---

## Pendientes antes de producción

- **Mapa base.** CARTO Positron no pide API key y sirve para desarrollo, pero su
  uso gratuito tiene límites. Para producción conviene MapTiler, Protomaps
  autoalojado o tiles propios. Se cambia sólo con `VITE_MAP_STYLE`.
- **CORS.** Si la PWA no se sirve desde el mismo origen que la API, agregá el
  dominio a `CORS_ORIGINS` del backend y apuntá `VITE_API_BASE_URL` a la URL
  absoluta.
- **`POST /incidents/correlate`** debe quedar detrás de autenticación de
  operador; la PWA no lo llama.
- **Web Push**, reportes ciudadanos, historial y filtros por comuna quedan para
  la siguiente iteración (ver `context.md`).
- **Accesibilidad.** Los objetivos táctiles y `prefers-reduced-motion` están
  cubiertos; falta una pasada de contraste sobre el amarillo `single_signal`
  cuando se usa como fondo de chip.

---

## Verificaciones hechas

- `tsc --noEmit` limpio (TypeScript 7, `strict` + `noUncheckedIndexedAccess`).
- `vite build` limpio; service worker con 19 entradas precacheadas y las dos
  reglas de runtime caching presentes en `dist/sw.js`.
- Contraste automático de los 23 campos de `IncidentRead`, los 14 de
  `IncidentEventLink`, los 7 de `IncidentStats` y los 3 enums contra
  `backend/app/schemas/incident.py` y `backend/app/models/enums.py`: coinciden
  exactamente.
- Reglas de simbología ejercitadas contra seis incidentes representativos,
  incluido el caso crítico *alerta roja + sin confirmar* → relleno naranjo con
  anillo rojo.
- Dev server arrancado contra un backend simulado: proxy, orden `lon,lat` y
  serialización del listado verificados de punta a punta.
