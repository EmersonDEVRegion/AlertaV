import { memo } from 'react'
import { ICON_GLYPHS, type IconId } from '@/domain/emergencyIcons'

interface GlyphIconProps {
  id: IconId
  className?: string
  /** Color del relleno. Por defecto, el del texto que lo rodea. */
  color?: string
}

/**
 * Los glifos del mapa, fuera del mapa.
 *
 * El lienzo dibuja estos mismos trazos como iconos SDF (`lib/iconRaster.ts`).
 * Acá se pintan como SVG para la leyenda y las listas, así que un glifo se
 * define UNA vez —en `domain/emergencyIcons.ts`— y se ve igual en todas partes.
 * El relleno es `evenodd`, igual que en el rasterizador: los recortes
 * interiores de la llama o del salvavidas dependen de eso.
 */
export const GlyphIcon = memo(function GlyphIcon({ id, className, color }: GlyphIconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden
      focusable="false"
      className={className}
      fill={color ?? 'currentColor'}
    >
      {ICON_GLYPHS[id].paths.map((d) => (
        <path key={d} d={d} fillRule="evenodd" />
      ))}
    </svg>
  )
})
