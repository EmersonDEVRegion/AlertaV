import { useEffect, useState } from 'react'
import { currentRegionCamera, type RegionCamera } from '@/lib/regionCamera'

function sameCamera(a: RegionCamera, b: RegionCamera): boolean {
  return (
    Math.abs(a.minZoom - b.minZoom) < 1e-6 &&
    a.maxBounds.every((value, i) => Math.abs(value - (b.maxBounds[i] ?? NaN)) < 1e-9)
  )
}

/**
 * Zoom mínimo y límites del mapa para la ventana actual (`lib/regionCamera`).
 *
 * Se recalcula al cambiar el tamaño de la ventana, una vez por cuadro; si el
 * resultado no cambia se devuelve el mismo objeto, para que react-map-gl no
 * vuelva a fijar los límites de balde.
 */
export function useRegionCamera(): RegionCamera {
  const [camera, setCamera] = useState(currentRegionCamera)

  useEffect(() => {
    let frame = 0
    const update = () => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const next = currentRegionCamera()
        setCamera((prev) => (sameCamera(prev, next) ? prev : next))
      })
    }
    window.addEventListener('resize', update)
    return () => {
      cancelAnimationFrame(frame)
      window.removeEventListener('resize', update)
    }
  }, [])

  return camera
}
