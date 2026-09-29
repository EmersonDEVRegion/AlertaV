/**
 * Simbología de los cortes de agua (Esval).
 *
 * # Un solo color, cian oscuro
 *
 * Hay una sola empresa (Esval es la sanitaria de toda la V Región), así que el
 * color no tiene nada que distinguir entre empresas, como en la luz. Lo que
 * tiene que hacer es NO confundirse con lo que ya hay en el mapa:
 *
 *   CGE             azul marino   #1e3a8a  (mismo disco, otro glifo)
 *   SENAPRED        azul          #3b82f6
 *   lluvia          celeste       #0ea5e9 / #0369a1
 *   accidentes      cian claro    #22d3ee
 *   otras           teal          #0d9488
 *
 * `#0e7490` (cyan-700) cae entre el teal y el celeste sin coincidir con
 * ninguno, y la gota blanca lo termina de separar del rayo de CGE.
 *
 * # Por qué no hay escala
 *
 * Esval es la autoridad sobre su propia red: no hay duda de que el corte existe
 * y no hay tramo de confianza que codificar. Tampoco publica clientes
 * afectados. Lo único que varía y le importa a un vecino es si es de
 * emergencia o programado, y eso lo dice el texto, no el tono.
 */
export const WATER = {
  color: '#0e7490',
  onColor: '#ffffff',
  label: 'Cortes de agua',
} as const
