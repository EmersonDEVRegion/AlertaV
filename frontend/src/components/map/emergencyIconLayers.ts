/**
 * Capas de símbolo: el icono de cada emergencia.
 *
 * # Qué reemplaza y qué NO
 *
 * En la fuente de incidentes el icono **sustituye al disco de color**: era una
 * mancha que sólo decía «acá hay algo», y el glifo dice además qué es.
 *
 * En sismos y cortes de ruta el icono se monta ENCIMA, sin quitar nada. Ahí el
 * radio del círculo y el tamaño del rombo codifican magnitud y severidad, y un
 * icono de tamaño fijo perdería ese dato. `icon-size` variable no es un buen
 * reemplazo: el ojo compara áreas de círculos con bastante precisión y siluetas
 * escaladas mucho peor.
 *
 * # El color
 *
 * Al ser SDF, el color no viene en la imagen: lo pone `icon-color`, atado a las
 * mismas expresiones de severidad que ya usaban los rellenos. El halo va en el
 * color del fondo de la aplicación, que es lo que separa el glifo del terreno
 * en los dos temas sin recurrir a una placa opaca detrás.
 */

import type { ExpressionSpecification, SymbolLayerSpecification } from 'maplibre-gl'
import {
  CLOSURE_ICON,
  FALLBACK_ICON,
  INCIDENT_TYPE_ICON,
  SEISMIC_ICON,
} from '@/domain/emergencyIcons'
import { LEVEL_COLOR_EXPRESSION } from '@/domain/palette'

export type SymbolLayer = Omit<SymbolLayerSpecification, 'source'>

export const INCIDENT_ICON_LAYER_ID = 'incidents-icon'
export const SEISMIC_ICON_LAYER_ID = 'seismic-icon'
export const CLOSURE_ICON_LAYER_ID = 'closure-icon'

/** Fondo del tema, para el halo. Separa el glifo del terreno sin placa opaca. */
const HALO: Record<'light' | 'dark', string> = {
  light: '#f1f5f9',
  dark: '#020617',
}

/**
 * `match` de tipo a icono, generado desde el diccionario.
 *
 * Se genera y no se escribe a mano para que agregar un tipo en
 * `emergencyIcons.ts` baste: una lista escrita dos veces se desincroniza, y el
 * síntoma sería que un tipo nuevo cae en el respaldo sin que nadie se entere.
 */
const ICON_BY_TYPE = [
  'match',
  ['get', 'type'],
  ...Object.entries(INCIDENT_TYPE_ICON).flatMap(([type, icon]) => [type, icon]),
  FALLBACK_ICON,
] as unknown as ExpressionSpecification

/**
 * Tamaño del icono.
 *
 * El lienzo es de 64 px y el glifo ocupa 48, así que `icon-size: 0.5` da unos
 * 24 px en pantalla. Crece con el zoom igual que crecían los discos, para que
 * el mapa no cambie de densidad visual al acercarse.
 */
const ICON_SIZE: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['zoom'],
  7,
  0.3,
  11,
  0.45,
  15,
  0.6,
]

export function incidentIconLayer(theme: 'light' | 'dark'): SymbolLayer {
  return {
    id: INCIDENT_ICON_LAYER_ID,
    type: 'symbol',
    layout: {
      'icon-image': ICON_BY_TYPE,
      'icon-size': ICON_SIZE,
      /*
       * `icon-allow-overlap` en `true` y `ignore-placement` también.
       *
       * Con la colisión activada MapLibre esconde los símbolos que se pisan, y
       * en un mapa de emergencias eso significa ocultar incidentes reales
       * porque hay otro cerca. Un racimo apretado es información —ahí está
       * pasando algo—, no ruido que convenga adelgazar.
       */
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
    paint: {
      'icon-color': LEVEL_COLOR_EXPRESSION as unknown as ExpressionSpecification,
      'icon-halo-color': HALO[theme],
      'icon-halo-width': 1.4,
      // Los incidentes cerrados se atenúan, igual que hacía el disco.
      'icon-opacity': ['case', ['get', 'is_closed'], 0.55, 1],
    },
  }
}

/**
 * Sismos. Un solo glifo: la fuente entera son terremotos.
 *
 * El color sigue la banda de magnitud que ya usaba el anillo, así que el icono
 * y el círculo que tiene debajo hablan del mismo dato.
 */
export function seismicIconLayer(
  theme: 'light' | 'dark',
  bandColor: ExpressionSpecification,
): SymbolLayer {
  return {
    id: SEISMIC_ICON_LAYER_ID,
    type: 'symbol',
    layout: {
      'icon-image': SEISMIC_ICON,
      // Más chico que el de incidentes: va dentro del círculo de magnitud, y
      // taparlo anularía justo el dato que ese círculo transmite.
      'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.22, 14, 0.36],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
    paint: {
      'icon-color': bandColor,
      'icon-halo-color': HALO[theme],
      'icon-halo-width': 1.2,
    },
  }
}

/** Cortes de ruta. El color sigue la severidad del MOP. */
export function closureIconLayer(
  theme: 'light' | 'dark',
  severityColor: ExpressionSpecification,
): SymbolLayer {
  return {
    id: CLOSURE_ICON_LAYER_ID,
    type: 'symbol',
    layout: {
      'icon-image': CLOSURE_ICON,
      'icon-size': ['interpolate', ['linear'], ['zoom'], 7, 0.24, 14, 0.4],
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
    },
    paint: {
      'icon-color': severityColor,
      'icon-halo-color': HALO[theme],
      'icon-halo-width': 1.2,
    },
  }
}
