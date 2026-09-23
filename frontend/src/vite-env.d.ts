/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_POLL_INTERVAL_MS?: string
  readonly VITE_SEISMIC_POLL_INTERVAL_MS?: string
  readonly VITE_STALE_AFTER_MS?: string
  readonly VITE_MAP_STYLE?: string
  readonly VITE_MAP_STYLE_DARK?: string
  readonly VITE_DEV_API_PROXY?: string
  /** `on`/`off`. Por defecto: encendido en `npm run dev`, apagado en el build. */
  readonly VITE_VEHICLE_RADAR?: string
  readonly VITE_VEHICLE_POLL_INTERVAL_MS?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
