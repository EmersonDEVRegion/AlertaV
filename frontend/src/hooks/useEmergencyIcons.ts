import { useEffect, useState } from 'react'
import type { Map as MapLibreMap } from 'maplibre-gl'
import { ICON_IDS } from '@/domain/emergencyIcons'
import { buildAllIcons } from '@/lib/iconRaster'

/**
 * Registra los iconos SDF en el estilo del mapa.
 *
 * # El problema que casi nadie ve venir
 *
 * Cambiar de tema llama a `map.setStyle()`, y **eso borra las imágenes
 * registradas junto con el resto del estilo**. Un `addImage` hecho una sola vez
 * en `load` sobrevive hasta que alguien toca el interruptor de tema; a partir de
 * ahí todas las capas `symbol` quedan sin su `icon-image`.
 *
 * El modo de falla es el peor de MapLibre: no lanza. Emite un `error` en
 * consola por cada icono ausente y deja los símbolos sin dibujar. En un mapa con
 * varias capas encima es perfectamente posible no notarlo hasta producción.
 *
 * Por eso el registro se engancha a `styledata` y no a `load`, y comprueba
 * `hasImage` antes de escribir: `addImage` con un identificador ya existente
 * también emite error.
 *
 * # El coste, y por qué no bloquea el primer cuadro
 *
 * Son siete transformadas de distancia sobre lienzos de 64×64. `buildAllIcons`
 * cede el hilo entre glifo y glifo, y todo el trabajo arranca DESPUÉS del
 * primer `styledata`, así que el mapa ya pintó su primer cuadro cuando esto
 * empieza. Los símbolos aparecen unos milisegundos más tarde que el terreno,
 * que es exactamente el orden deseable.
 *
 * Las imágenes se construyen **una vez** y se reutilizan en cada reinstalación:
 * lo que el cambio de tema invalida es el registro en el estilo, no los píxeles.
 */
export function useEmergencyIcons(instance: MapLibreMap | null): boolean {
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!instance) return

    let cancelled = false
    // Caché de los píxeles: sobrevive a los cambios de estilo.
    let cache: Awaited<ReturnType<typeof buildAllIcons>> | null = null

    const install = () => {
      if (cancelled || !cache) return
      let installed = 0
      for (const { id, image } of cache) {
        // `addImage` sobre un id existente emite error y no reemplaza nada.
        if (instance.hasImage(id)) {
          installed += 1
          continue
        }
        try {
          instance.addImage(id, image, { sdf: true })
          installed += 1
        } catch (error) {
          // Un icono que no entra no puede tumbar a los demás ni al mapa.
          console.error('[AlertaV/iconos] no se pudo registrar', id, error)
        }
      }
      setReady(installed > 0)
    }

    const boot = async () => {
      cache = await buildAllIcons(ICON_IDS)
      install()
    }

    /*
     * `styledata` se dispara muchas veces y también tras cada `setStyle`, que es
     * justo cuando hay que reinstalar. `install` es idempotente gracias a
     * `hasImage`, así que llamarlo de más no cuesta nada.
     */
    const onStyleData = () => install()
    instance.on('styledata', onStyleData)

    void boot()

    return () => {
      cancelled = true
      instance.off('styledata', onStyleData)
      /*
       * No se quitan las imágenes al desmontar. `removeImage` mientras una capa
       * `symbol` todavía las referencia deja el estilo inconsistente, y el mapa
       * se destruye completo de todos modos: es memoria que el navegador
       * recupera sola.
       */
    }
  }, [instance])

  return ready
}
