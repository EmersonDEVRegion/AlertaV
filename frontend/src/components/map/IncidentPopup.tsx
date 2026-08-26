import { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { Popup } from 'react-map-gl/maplibre'
import { Popup as MapLibrePopup } from 'maplibre-gl'
import type { Map as MapLibreMap, PopupOptions } from 'maplibre-gl'
import type { ReactNode, RefObject } from 'react'
import type { MapRef } from 'react-map-gl/maplibre'
import type { Incident } from '@/api/types'
import { EmergencyPopup } from './EmergencyPopup'

/**
 * Montaje del popup.
 *
 * # La distancia al pin
 *
 * `index.css` saca la flecha del flujo, así que los 20 px que separaban la
 * tarjeta del marcador desaparecieron con ella. `offset` los repone. El valor no
 * es estético: los cortes se dibujan con `<Marker anchor="bottom">` de 26 px de
 * alto, y los incidentes con círculos de capa cuyo radio crece con el zoom. 14
 * despeja al primero sin que la tarjeta flote lejos del segundo.
 */
export const POPUP_OFFSET = 14

/** Opciones comunes a las dos formas de montar. */
const BASE_OPTIONS = {
  // El nuestro vive en la cabecera de la tarjeta.
  closeButton: false,
  // Un clic en el mapa ya deselecciona a través de `onSelect(null)`; dejar que
  // MapLibre además cierre el popup por su cuenta produce dos fuentes de verdad
  // sobre lo mismo y un parpadeo al cambiar de incidente.
  closeOnClick: false,
  // MapLibre pone `max-width: 240px` en línea sobre `.maplibregl-popup`. La
  // tarjeta declara su propio ancho y su propio tope contra el viewport.
  maxWidth: 'none',
  offset: POPUP_OFFSET,
} satisfies PopupOptions

// ===========================================================================
// Forma 1 — declarativa. Es la que conviene usar.
// ===========================================================================

export interface IncidentPopupProps {
  /** `null` desmonta el popup. No hace falta cerrarlo a mano. */
  incident: Incident | null
  onClose: () => void
  onOpenDetail?: (code: string) => void
}

/**
 * Popup atado al árbol de React.
 *
 * # Por qué esta y no la imperativa
 *
 * `<Popup>` de react-map-gl crea el `maplibregl.Popup`, pero renderiza los hijos
 * con `createPortal`. Eso tiene dos consecuencias que importan:
 *
 *   1. **No hay una segunda raíz de React.** El contenido sigue siendo parte de
 *      este árbol, así que React lo desmonta solo cuando `incident` pasa a
 *      `null` o cuando el mapa se va. No hay nada que liberar a mano, que es la
 *      única manera segura de no filtrar nada.
 *   2. **El contexto atraviesa.** `QueryClientProvider`, el tema y cualquier
 *      proveedor que envuelva a `App` siguen visibles dentro del popup. Con una
 *      raíz aparte no lo estarían — ver la nota de `mountReactPopup`.
 *
 * Va dentro de `<Map>`, junto a las capas.
 */
export function IncidentPopup({
  incident,
  onClose,
  onOpenDetail,
}: IncidentPopupProps) {
  if (!incident) return null

  return (
    <Popup
      // Remontar al cambiar de incidente en vez de mover el popup existente:
      // sin `key`, MapLibre reposiciona el mismo nodo y la tarjeta se desliza
      // por el mapa hasta el destino, que se lee como un error de render.
      key={incident.code}
      longitude={incident.lon}
      latitude={incident.lat}
      anchor="bottom"
      onClose={onClose}
      {...BASE_OPTIONS}
    >
      <EmergencyPopup
        incident={incident}
        onClose={onClose}
        {...(onOpenDetail ? { onOpenDetail } : {})}
      />
    </Popup>
  )
}

// ===========================================================================
// Forma 2 — imperativa, sobre `new maplibregl.Popup()`
// ===========================================================================

/**
 * Monta un nodo de React dentro de un popup de MapLibre y devuelve su limpieza.
 *
 * Para cuando el popup no puede colgar del árbol: lo abre un handler de MapLibre
 * suelto, un control propio, o código que corre fuera de React.
 *
 * # Las tres fugas
 *
 * 1. **La raíz que nadie desmonta.** `createRoot` deja vivo un árbol de React
 *    con sus efectos, sus suscripciones y sus temporizadores. Que MapLibre borre
 *    el nodo del DOM no lo detiene: el `root` sigue existiendo y sigue
 *    corriendo. Por eso `dispose` llama a `root.unmount()`, y por eso se
 *    engancha a `close` — el usuario puede cerrar el popup con Esc sin que este
 *    código se entere de otra forma.
 *
 * 2. **El desmontaje sincrónico.** Si `close` se dispara durante un render de
 *    React —cierre desde un `onClick` del propio contenido, por ejemplo—,
 *    `root.unmount()` sincrónico rompe: React avisa que se intentó desmontar una
 *    raíz mientras estaba renderizando, y el árbol queda a medias.
 *    `queueMicrotask` lo posterga al final del turno actual, que es lo que
 *    recomienda la documentación de React 18 en adelante y sigue valiendo en 19.
 *
 * 3. **El doble cierre.** `popup.remove()` emite `close`, que vuelve a llamar a
 *    `dispose`, que vuelve a llamar a `remove()`. La bandera `disposed` corta el
 *    ciclo, y además hace que la función sea segura de llamar dos veces — que es
 *    justo lo que pasa cuando un `useEffect` limpia un popup que el usuario ya
 *    había cerrado.
 *
 * # La advertencia del contexto
 *
 * Una raíz nueva no ve NINGÚN proveedor del árbol principal. Un componente que
 * llame a `useQuery`, al tema o a cualquier contexto va a fallar en tiempo de
 * ejecución, no de compilación. Si hace falta, el `node` tiene que venir ya
 * envuelto:
 *
 *     mountReactPopup(map, [lon, lat],
 *       <QueryClientProvider client={queryClient}>
 *         <EmergencyPopup … />
 *       </QueryClientProvider>)
 *
 * Duplicar proveedores por cada popup es exactamente el costo que la forma
 * declarativa no paga.
 */
export function mountReactPopup(
  map: MapLibreMap,
  lngLat: [number, number],
  node: ReactNode,
  options: PopupOptions = {},
): () => void {
  const container = document.createElement('div')
  const root = createRoot(container)
  root.render(node)

  const popup = new MapLibrePopup({ ...BASE_OPTIONS, ...options })
    .setLngLat(lngLat)
    .setDOMContent(container)
    .addTo(map)

  let disposed = false
  const dispose = () => {
    if (disposed) return
    disposed = true
    popup.off('close', dispose)
    queueMicrotask(() => root.unmount())
    popup.remove()
  }

  popup.on('close', dispose)
  return dispose
}

/**
 * Envoltura de `mountReactPopup` para usarla desde un componente.
 *
 * La limpieza del efecto ES el `dispose` que devuelve el montaje, que es lo que
 * garantiza que un cambio de incidente cierre el popup anterior antes de abrir
 * el nuevo, y que desmontar el mapa no deje ninguna raíz corriendo.
 */
export function useImperativePopup(
  mapRef: RefObject<MapRef | null>,
  incident: Incident | null,
  onClose: () => void,
): void {
  useEffect(() => {
    const map = mapRef.current?.getMap()
    if (!map || !incident) return

    return mountReactPopup(
      map,
      [incident.lon, incident.lat],
      <EmergencyPopup incident={incident} onClose={onClose} />,
    )
  }, [mapRef, incident, onClose])
}
