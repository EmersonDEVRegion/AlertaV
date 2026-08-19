/**
 * Las tres categorías que puede elegir una persona al reportar.
 *
 * Espejo de `ReportCategory` en `backend/app/schemas/event.py`. El backend
 * traduce cada categoría a una señal de dominio (`smoke`, `accident`, `other`)
 * y de ahí a una familia; el cliente no necesita saber nada de eso, sólo mandar
 * la categoría.
 *
 * Por qué los textos viven acá y no dentro del modal
 * ---------------------------------------------------
 * Hasta este hito el formulario decía siempre "llama al 132" — el número de
 * Bomberos — porque la aplicación nació siendo un mapa de incendios. Mandar a
 * llamar a Bomberos por un choque sin fuego retrasa la respuesta correcta:
 * quien atiende un accidente vial es Carabineros (133), y si hay heridos, el
 * SAMU (131).
 *
 * Tener la tabla completa en un solo lugar hace que agregar una categoría sea
 * una entrada más y no una cacería de textos por los componentes. Es la misma
 * razón por la que `VERIFYING_SOURCES` y `EMERGENCY_CONTACT` viven en
 * `families.ts`: cuando el texto depende del dominio, se declara junto al
 * dominio.
 */

export const REPORT_CATEGORIES = ['fire', 'traffic_accident', 'other'] as const
export type ReportCategory = (typeof REPORT_CATEGORIES)[number]

export interface EmergencyLine {
  number: string
  service: string
}

export interface ReportCategoryInfo {
  /** Lo que se lee en el radio button. */
  label: string
  /** Ayuda breve bajo la etiqueta: qué entra en esta categoría y qué no. */
  hint: string
  emoji: string
  /**
   * A quién llamar. Puede haber más de un número: en un accidente con heridos
   * hacen falta Carabineros y SAMU, y el orden importa — primero quien despeja
   * la vía y toma el parte, después quien atiende al herido si lo hay.
   */
  lines: EmergencyLine[]
  /** Marcador del textarea, orientado a lo que ayuda a ubicar el hecho. */
  placeholder: string
}

export const REPORT_CATEGORY_INFO: Record<ReportCategory, ReportCategoryInfo> = {
  fire: {
    label: 'Incendio',
    hint: 'Humo, llamas o quema en cerro, casa o vehículo.',
    emoji: '🔥',
    lines: [{ number: '132', service: 'Bomberos' }],
    placeholder:
      'Ej: columna de humo negro en el cerro sobre la Ruta 68, altura del peaje Zapata. Se ve desde la carretera.',
  },
  traffic_accident: {
    label: 'Accidente automovilístico',
    hint: 'Choque, volcamiento o atropello en la vía.',
    emoji: '🚗',
    lines: [
      { number: '133', service: 'Carabineros' },
      { number: '131', service: 'SAMU, si hay heridos' },
    ],
    placeholder:
      'Ej: choque entre dos autos en Av. España con Uno Norte, sentido a Valparaíso. Una pista bloqueada.',
  },
  other: {
    label: 'Otra emergencia',
    hint: 'Inundación, derrumbe, u otra situación de riesgo.',
    emoji: '⚠️',
    lines: [
      { number: '133', service: 'Carabineros' },
      { number: '132', service: 'Bomberos' },
    ],
    placeholder:
      'Ej: derrumbe de tierra sobre la calzada en el camino a Laguna Verde, a la altura del mirador.',
  },
}

/** Frase de "esto no reemplaza una llamada", con los números que corresponden. */
export function emergencyCallHint(category: ReportCategory | null): string {
  if (!category) {
    return 'Esto no reemplaza a una llamada de emergencia. Si hay riesgo para una persona, llama al 133.'
  }
  const lines = REPORT_CATEGORY_INFO[category].lines
    .map((line) => `${line.number} (${line.service})`)
    .join(' o al ')
  return `Esto no reemplaza a una llamada de emergencia. Si hay riesgo para una persona, llama al ${lines}.`
}

/** Recordatorio corto del acuse de recibo, ya enviado el reporte. */
export function successCallHint(category: ReportCategory): string {
  const primary = REPORT_CATEGORY_INFO[category].lines[0]!
  return `Si hay riesgo para alguien, llama al ${primary.number} (${primary.service}).`
}
