import { useSyncExternalStore } from 'react'

/**
 * Punto de quiebre del riel flotante.
 *
 * # Por qué esto se decide en JavaScript y no sólo en CSS
 *
 * La colisión de paneles en teléfono no era un problema de estilo: eran **dos
 * superficies flotantes de 15 rem cada una** ancladas a bordes opuestos, y a
 * 430 px de ancho no caben ni ocultándose una detrás de la otra. Se podía
 * arreglar con `hidden md:block` sobre cada una, pero eso deja el teléfono sin
 * los controles, no con otros.
 *
 * La solución es que en teléfono **exista otra interfaz**: una barra de fichas
 * que abre un panel a la vez. Y eso no es un cambio de aspecto sino de árbol —
 * distintos componentes, distinto estado, distinta semántica de accesibilidad—,
 * así que el punto de quiebre tiene que llegar a React.
 *
 * Montar los dos árboles y esconder uno con CSS tendría un costo real: la lista
 * de incidentes se renderizaría dos veces, y el lector de pantalla vería dos
 * juegos de controles con las mismas etiquetas.
 *
 * # Por qué `useSyncExternalStore` y no `useState` + `useEffect`
 *
 * Es exactamente el caso para el que existe: una fuente de verdad **fuera** de
 * React que hay que leer de forma consistente durante el renderizado. Con
 * `useState` inicializado en un efecto, el primer pintado usa siempre el valor
 * por defecto y el layout salta en cuanto el efecto corre — en un teléfono eso
 * es ver la interfaz de escritorio durante un cuadro. `getSnapshot` se consulta
 * antes de pintar y no hay salto.
 *
 * El tercer argumento es el instantáneo del servidor: no hay SSR en esta
 * aplicación, pero `useSyncExternalStore` lo exige y devolver el valor de
 * escritorio es la respuesta correcta para cualquier entorno sin `matchMedia`
 * —jsdom incluido, donde los tests montan los componentes de escritorio—.
 */

/** `md` de Tailwind. El mismo número que usan las variantes `md:` del cromo. */
export const COMPACT_BREAKPOINT = '(max-width: 767.98px)'

function subscribe(query: string, onChange: () => void): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const list = window.matchMedia(query)
  list.addEventListener('change', onChange)
  return () => list.removeEventListener('change', onChange)
}

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => subscribe(query, onChange),
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  )
}

/**
 * ¿Estamos en la medida donde los dos paneles flotantes no caben?
 *
 * Nombrada por la CONSECUENCIA y no por el dispositivo: no es «es un teléfono»
 * —una ventana estrecha en un escritorio tiene el mismo problema— sino «no hay
 * ancho para dos superficies a la vez».
 */
export function useIsCompact(): boolean {
  return useMediaQuery(COMPACT_BREAKPOINT)
}
