/**
 * Fija la URL del Web Worker de maplibre-gl para que sobreviva al build.
 *
 * El problema
 * -----------
 * maplibre-gl 6.4.0 resuelve la URL de su worker en tiempo de ejecucion
 * (`dist/maplibre-gl.mjs`, funcion minificada `wi()`):
 *
 *   const file = import.meta.url.endsWith('-dev.mjs')
 *     ? 'maplibre-gl-worker-dev.mjs'
 *     : 'maplibre-gl-worker.mjs'
 *   return new URL(`./${file}`, import.meta.url).href
 *
 * El nombre se arma con un template literal, asi que ningun bundler puede
 * detectarlo estaticamente ni emitir el worker como asset. En produccion
 * `import.meta.url` es el chunk construido (`/assets/maplibre-<hash>.js`), asi
 * que la URL resultante es `/assets/maplibre-gl-worker.mjs` — un archivo que no
 * existe. El `new Worker()` se crea igual, falla al cargar en segundo plano y
 * nadie escucha ese error: el dispatcher espera para siempre, `load` nunca se
 * dispara y el lienzo queda en blanco sin una sola linea en consola.
 *
 * La solucion
 * -----------
 * `?worker&url` le pide a Vite que compile el worker como bundle propio
 * (resolviendo de paso su hermano `maplibre-gl-shared.mjs`, que de otro modo
 * tambien faltaria) y nos devuelva la URL con hash del asset emitido.
 * `setWorkerUrl` la escribe en `config.WORKER_URL`, que maplibre prefiere por
 * sobre la derivacion de `import.meta.url`. Funciona igual en dev y en build.
 *
 * Requiere `worker: { format: 'es' }` en vite.config.ts: maplibre instancia el
 * worker con `new Worker(url, { type: 'module' })`.
 *
 * Este modulo solo tiene efectos secundarios. Importarlo antes de montar el
 * mapa basta; ver `components/map/IncidentMap.tsx`.
 */
import { setWorkerUrl } from 'maplibre-gl'
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'

setWorkerUrl(maplibreWorkerUrl)
