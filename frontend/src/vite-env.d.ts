/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string
  readonly VITE_POLL_INTERVAL_MS?: string
  readonly VITE_SEISMIC_POLL_INTERVAL_MS?: string
  readonly VITE_STALE_AFTER_MS?: string
  readonly VITE_MAP_STYLE?: string
  readonly VITE_DEV_API_PROXY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
