/**
 * Diagnóstico del mapa, disponible también en producción.
 *
 * Por qué existe
 * --------------
 * El peor modo de falla de MapLibre es el lienzo en blanco sin una sola línea
 * en consola. Ocurre cuando el Web Worker no carga: el dispatcher se queda
 * esperando, el evento `load` no se dispara nunca y `map.on('error')` no recibe
 * nada, porque el fallo pasa en el `new Worker()` y nadie lo escucha. Desde
 * afuera se ve idéntico a un problema de WebGL, de encuadre o de estilo.
 *
 * El diagnóstico anterior vivía dentro de `onLoad` y detrás de
 * `import.meta.env.DEV`. Eso lo dejaba inútil justo en los dos casos que
 * importan: producción, y el fallo en el que `load` nunca ocurre.
 *
 * Cómo se usa
 * -----------
 * Abrir la app con `?debug=1` (o `?debug=map`). En desarrollo va siempre
 * encendido. Sin el flag el costo es un `URLSearchParams` al arrancar y nada
 * más: no se registra ni un listener.
 */

import { getWorkerCount, getWorkerUrl } from 'maplibre-gl'
import type { Map as MapLibreMap } from 'maplibre-gl'

/** Si `load` no llegó en este plazo, algo se colgó y hay que reportarlo. */
const WATCHDOG_MS = 8_000

export const MAP_DEBUG: boolean = (() => {
  if (import.meta.env.DEV) return true
  if (typeof window === 'undefined') return false
  const flag = new URLSearchParams(window.location.search).get('debug')
  return flag === '1' || flag === 'true' || flag === 'map'
})()

const tag = '[AlertaV/mapa]'

function webglReport(canvas: HTMLCanvasElement): Record<string, unknown> {
  const gl =
    canvas.getContext('webgl2') ??
    (canvas.getContext('webgl') as WebGLRenderingContext | null)

  if (!gl) {
    return {
      contexto: 'NINGUNO — el navegador no entregó WebGL, el lienzo no puede pintar',
    }
  }

  const debugInfo = gl.getExtension('WEBGL_debug_renderer_info')
  return {
    contexto: canvas.getContext('webgl2') ? 'webgl2' : 'webgl1',
    perdido: gl.isContextLost(),
    renderer: debugInfo
      ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
      : gl.getParameter(gl.RENDERER),
  }
}

/**
 * El worker se pide por red. Si su URL devuelve 404 —el caso que arregló
 * `maplibreWorker.ts`— esto lo dice con todas las letras en vez de dejar el
 * lienzo mudo.
 */
async function probeWorker(): Promise<Record<string, unknown>> {
  let workerUrl: string | undefined
  try {
    workerUrl = getWorkerUrl()
  } catch {
    /* la versión instalada podría no exponerlo */
  }

  if (!workerUrl) {
    return {
      url: '(no declarada — maplibre la derivará de import.meta.url, que en el build apunta a un archivo inexistente)',
      workersActivos: getWorkerCount(),
    }
  }

  const resuelta = new URL(workerUrl, window.location.href).href
  try {
    const response = await fetch(resuelta, { method: 'GET', cache: 'no-store' })
    return {
      url: resuelta,
      estado: `${response.status} ${response.statusText}`,
      tipoContenido: response.headers.get('content-type'),
      alcanzable: response.ok,
      workersActivos: getWorkerCount(),
    }
  } catch (cause) {
    return { url: resuelta, alcanzable: false, error: String(cause) }
  }
}

/**
 * Registra las sondas sobre un mapa recién creado. Devuelve la función de
 * limpieza que hay que llamar al desmontar.
 */
export function attachMapDiagnostics(map: MapLibreMap): () => void {
  if (!MAP_DEBUG) return () => {}

  const inicio = performance.now()
  let cargó = false
  let tilesPedidos = 0
  let tilesListos = 0

  const transcurrido = () => `${Math.round(performance.now() - inicio)} ms`

  const onDataLoading = (event: { dataType?: string }) => {
    if (event.dataType === 'source') tilesPedidos += 1
  }

  const onData = (event: { dataType?: string; tile?: unknown }) => {
    if (event.dataType === 'source' && event.tile) tilesListos += 1
  }

  const onLoad = () => {
    cargó = true
    const canvas = map.getCanvas()
    const style = map.getStyle()

    console.info(`${tag} load en ${transcurrido()}`, {
      estiloCargado: map.isStyleLoaded(),
      capasDelEstilo: style?.layers?.length ?? 0,
      fuentes: Object.keys(style?.sources ?? {}),
      centro: map.getCenter().toArray(),
      zoom: Number(map.getZoom().toFixed(2)),
      canvasCSS: [canvas.clientWidth, canvas.clientHeight],
      canvasBuffer: [canvas.width, canvas.height],
      webgl: webglReport(canvas),
      tiles: { pedidos: tilesPedidos, listos: tilesListos },
    })

    if (canvas.clientWidth === 0 || canvas.clientHeight === 0) {
      console.error(
        `${tag} el contenedor mide 0 px. El mapa cargó pero no tiene dónde dibujarse: ` +
          `revisa que el ancestro posicionado tenga alto.`,
      )
    }
  }

  // El caso importante: `load` que no llega. Un temporizador es la única forma
  // de observarlo, porque no hay evento que anuncie su propia ausencia.
  const watchdog = window.setTimeout(() => {
    if (cargó) return
    console.error(
      `${tag} el evento "load" no llegó en ${WATCHDOG_MS} ms. ` +
        `Firma típica de un Web Worker que no cargó: el dispatcher espera para siempre.`,
      {
        estiloCargado: map.isStyleLoaded(),
        tiles: { pedidos: tilesPedidos, listos: tilesListos },
      },
    )
    void probeWorker().then((report) =>
      console.error(`${tag} sonda del worker`, report),
    )
  }, WATCHDOG_MS)

  const onError = (event: { error?: unknown }) => {
    console.error(`${tag} error a los ${transcurrido()}`, event.error ?? event)
  }

  map.on('dataloading', onDataLoading)
  map.on('data', onData)
  map.on('load', onLoad)
  map.on('error', onError)

  // Instantánea temprana del worker: si su URL da 404 se sabe de inmediato,
  // sin esperar el watchdog.
  void probeWorker().then((report) => {
    if (report['alcanzable'] === false) {
      console.error(
        `${tag} el worker de MapLibre NO es alcanzable. El mapa quedará en blanco.`,
        report,
      )
    } else {
      console.info(`${tag} worker`, report)
    }
  })

  return () => {
    window.clearTimeout(watchdog)
    map.off('dataloading', onDataLoading)
    map.off('data', onData)
    map.off('load', onLoad)
    map.off('error', onError)
  }
}
