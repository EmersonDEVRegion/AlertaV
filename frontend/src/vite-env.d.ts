/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

/**
 * Espejo de lo que lee `config/env.ts`. Todas son PÚBLICAS: Vite las hornea en
 * el bundle. `vite.config.ts` rechaza el build si alguien crea una con nombre
 * de secreto.
 */
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_POLL_INTERVAL_MS?: string
  readonly VITE_SEISMIC_POLL_INTERVAL_MS?: string
  readonly VITE_RAIN_POLL_INTERVAL_MS?: string
  readonly VITE_WEATHER_POLL_INTERVAL_MS?: string
  readonly VITE_ROAD_CLOSURE_POLL_INTERVAL_MS?: string
  readonly VITE_STALE_AFTER_MS?: string
  readonly VITE_MAP_STYLE?: string
  readonly VITE_MAP_STYLE_DARK?: string
  /** Sólo la lee `vite.config.ts` (proxy de desarrollo); no llega al bundle. */
  readonly VITE_DEV_API_PROXY?: string
  /** `on`/`off`. Por defecto: encendido en `npm run dev`, apagado en el build. */
  readonly VITE_VEHICLE_RADAR?: string
  readonly VITE_VEHICLE_POLL_INTERVAL_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
