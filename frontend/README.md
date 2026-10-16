# AlertaV — PWA

Cliente web instalable: el mapa de emergencias de la Región de Valparaíso.
Vite · React 19 · TypeScript 7 · MapLibre GL (react-map-gl) · TanStack Query ·
Tailwind CSS 4 · vite-plugin-pwa.

Decisiones de diseño (color, familias, edad del dato, offline):
[`docs/pwa.md`](../docs/pwa.md).

## Desarrollo

Requiere Node 22.

```bash
npm install
npm run dev            # http://localhost:5173
```

El dev server reenvía `/api` a `VITE_DEV_API_PROXY` (por defecto
`http://localhost:8000`, el backend local), así que no hay CORS en desarrollo.
Para probar contra producción: `VITE_DEV_API_PROXY=https://alertav-api.onrender.com`.

```bash
npm run check          # typecheck + lint + knip + tests (lo mismo que el CI)
npm run build          # tsc + build de producción con el service worker
npm run preview        # sirve dist/: la única forma de probar el service worker
npm run analyze        # qué pesa cada chunk (dist/stats.html)
npm run icons          # regenera los íconos desde public/icons/favicon.svg
npm run og             # regenera las imágenes de vista previa (public/og/)
```

**oxlint y no ESLint:** `typescript-eslint` no soporta TypeScript 7. oxlint trae
`react-hooks/exhaustive-deps`; las variables sin uso las ataja `tsc`
(`noUnusedLocals`). El service worker está apagado en `npm run dev` a propósito.

## Variables de entorno

Todas son públicas: Vite las copia al bundle, y `vite.config.ts` rechaza el
build si el nombre de una parece un secreto. La lista completa, con sus valores
por defecto, está en [`.env.example`](.env.example).

| Variable | Por defecto | Para qué |
|---|---|---|
| `VITE_API_BASE_URL` | `/api/v1` | Base de la API (en Vercel, la URL absoluta de Render) |
| `VITE_VEHICLE_RADAR` | encendido | Radar de vehículos (GBV); `off` lo apaga |
| `VITE_WATER_CUTS` | encendido | Cortes de agua (Esval) |
| `VITE_TURNSTILE_SITE_KEY` | vacío | Clave pública de Cloudflare Turnstile para los reportes |
| `VITE_MAP_STYLE`, `VITE_MAP_STYLE_DARK` | CARTO Positron / Dark Matter | Mapa base |
| `VITE_*_POLL_INTERVAL_MS` | 1–15 min | Cadencia de cada capa |

## Estructura

```text
src/
├── api/          un cliente por ruta, con validación de la respuesta (parse.ts)
├── domain/       reglas de presentación: familias, simbología, ventanas, etiquetas
├── hooks/        datos (react-query) y estado derivado
├── lib/          stores, rasterizado de capas, utilidades
├── config/       env tipado, mapa, CSP
├── components/
│   ├── map/      MapLibre y sus capas
│   ├── shell/    columna de escritorio y hoja del teléfono (ExplorePanel)
│   ├── feed/     historial y noticias
│   ├── incident/ ficha de incidente
│   ├── places/   «Mis lugares»
│   ├── report/   reporte ciudadano
│   ├── vehicles/ radar de vehículos
│   └── ui/       barra, menú, avisos, clima, primitivas
├── App.tsx
└── index.css     Tailwind v4 y tokens de diseño
public/           íconos, imágenes de vista previa, push-sw.js
scripts/          generadores (íconos, imágenes OG, comunas)
vercel.json       CSP, cabeceras y reenvío de /i/:code
```

## Producción

Vercel, con Root Directory `frontend`. Ver
[`docs/despliegue.md`](../docs/despliegue.md).
