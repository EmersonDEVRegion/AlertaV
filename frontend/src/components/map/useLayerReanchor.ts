import { useEffect } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'

/**
 * Vuelve a poner un grupo de capas debajo de su ancla tras un cambio de estilo.
 *
 * # Qué problema resuelve
 *
 * Cambiar de tema llama a `map.setStyle()`, que **vacía el arreglo de capas**.
 * react-map-gl vuelve a añadir cada `<Layer>` al recibir `styledata`, en el
 * orden en que están montados los componentes. Eso normalmente basta, pero el
 * orden de reconstrucción de MapLibre no es un contrato público y el precio de
 * equivocarse no se ve: la capa de contexto queda ENCIMA de los pines de
 * emergencia, la jerarquía del mapa se invierte, y sólo ocurre después de que
 * alguien toque el interruptor de tema.
 *
 * La comprobación cuesta un `indexOf` sobre un arreglo de identificadores.
 *
 * # Por qué el ancla es una LISTA y no un identificador
 *
 * Porque la capa de referencia que va más abajo —la amenaza sísmica— tiene que
 * quedar por debajo de la lluvia, y la lluvia puede no estar montada. Con una
 * lista en orden de preferencia, la amenaza pide «debajo de la lluvia si
 * existe; si no, debajo del cono», que es la regla real. Con un identificador
 * único habría que elegir entre un ancla que a veces no existe —y MapLibre
 * descarta la capa entera— o una posición equivocada la mitad del tiempo.
 */
export function useLayerReanchor(
  instance: MapLibreMap | null,
  layerIds: readonly string[],
  /** Anclas candidatas, de la más deseable a la de respaldo. */
  anchors: readonly string[],
): void {
  useEffect(() => {
    if (!instance) return

    const reanchor = () => {
      const order = instance.getLayersOrder()

      // La primera que exista de verdad y no sea una de las nuestras: anclarse
      // a una capa del propio grupo sería pedirle a MapLibre que se ordene
      // respecto de sí misma.
      const anchor = anchors.find(
        (id) => !layerIds.includes(id) && order.includes(id),
      )
      if (!anchor) return

      const anchorAt = order.indexOf(anchor)
      for (const id of layerIds) {
        const position = order.indexOf(id)
        // Sólo si quedó POR ENCIMA del ancla. Mover una capa que ya está en su
        // sitio marcaría el estilo como sucio y forzaría un repintado inútil.
        if (position !== -1 && position > anchorAt) instance.moveLayer(id, anchor)
      }
    }

    instance.on('styledata', reanchor)
    return () => {
      instance.off('styledata', reanchor)
    }
    // `layerIds` y `anchors` son constantes de módulo en todos los sitios de
    // llamada; listarlas obligaría a memorizarlas en cada uno sin ganar nada.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instance])
}
