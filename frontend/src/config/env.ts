/** Lectura única y tipada de las variables de entorno. */

function num(value: string | undefined, fallback: number): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

export const env = {
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? '/api/v1',
  /**
   * El worker de correlación del backend corre cada 120 s. Pedir más seguido
   * que eso gasta batería del teléfono sin traer datos nuevos.
   */
  pollIntervalMs: num(import.meta.env.VITE_POLL_INTERVAL_MS, 60_000),
  /** A partir de aquí la UI avisa que el dato puede no describir el presente. */
  staleAfterMs: num(import.meta.env.VITE_STALE_AFTER_MS, 180_000),
  mapStyle:
    import.meta.env.VITE_MAP_STYLE ??
    'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
} as const
