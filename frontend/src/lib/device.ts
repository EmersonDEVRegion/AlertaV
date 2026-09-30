/**
 * ¿Esto es un teléfono (o una tableta)?
 *
 * Por el puntero y no por el ancho ni el `userAgent`: un computador con la
 * ventana angosta sigue siendo un computador, y lo que cambia para los avisos
 * —la ubicación por GPS o por red, la app cerrada o el navegador abierto— va
 * con el aparato, no con el tamaño de la ventana.
 */
export function isHandheld(): boolean {
  if (typeof window === 'undefined') return false
  return window.matchMedia?.('(pointer: coarse)').matches ?? false
}

/** «este teléfono» / «este computador». */
export function thisDevice(): string {
  return isHandheld() ? 'este teléfono' : 'este computador'
}
