/**
 * Ícono del radar: dos anillos, un barrido y un eco.
 *
 * Vectorial y en `currentColor` por lo mismo que el triángulo del botón de
 * reporte: un emoji lo dibuja la fuente del sistema, con color fijo y sin
 * alineación fiable con el texto.
 */
export function RadarGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <circle cx="12" cy="12" r="9" opacity="0.45" />
      <circle cx="12" cy="12" r="4.5" opacity="0.7" />
      <path d="M12 12 18.4 5.6" />
      <circle cx="15.6" cy="9.4" r="1.4" fill="currentColor" stroke="none" />
    </svg>
  )
}
