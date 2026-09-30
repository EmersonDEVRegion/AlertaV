import { memo } from 'react'
import { Marker } from 'react-map-gl/maplibre'
import { PlaceGlyph } from '@/components/places/SavedPlaces'
import { usePlaces } from '@/lib/placesStore'

/**
 * Los lugares guardados en el mapa: una etiqueta chica con su ícono y nombre.
 *
 * Marcadores DOM y no una capa de MapLibre: son tres como mucho, llevan texto
 * con la tipografía de la interfaz y no necesitan agruparse. Leen el store
 * directamente, así que guardar o borrar un lugar no repinta el mapa.
 *
 * `pointer-events: none`: un toque sobre la etiqueta tiene que llegar al pin
 * del incendio que esté debajo, que es lo que importa.
 */
export const SavedPlaceMarkers = memo(function SavedPlaceMarkers() {
  const places = usePlaces()
  return (
    <>
      {places.map((place) => (
        <Marker
          key={place.id}
          longitude={place.lon}
          latitude={place.lat}
          anchor="bottom"
          style={{ pointerEvents: 'none' }}
        >
          <span className="flex flex-col items-center">
            <span className="flex items-center gap-1 rounded-full bg-raised py-0.5 pl-1 pr-2 text-[10.5px] font-semibold text-ink shadow-[var(--shadow-raised)] ring-1 ring-line">
              <span className="grid size-4 place-items-center rounded-full bg-accent text-accent-ink">
                <PlaceGlyph name={place.name} className="size-2.5" />
              </span>
              {place.name}
            </span>
            <span aria-hidden className="h-1.5 w-px bg-ink-muted" />
          </span>
        </Marker>
      ))}
    </>
  )
})
