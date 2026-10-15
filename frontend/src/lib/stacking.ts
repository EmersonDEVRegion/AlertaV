/**
 * Orden de apilamiento del cromo, de abajo hacia arriba.
 *
 * # Por qué existe este archivo (§I, 15-10-2026)
 *
 * La barra superior y la hoja inferior del teléfono tenían las dos `z-20`, y
 * viven en el mismo contexto de apilamiento (`<main>` no crea uno propio). Con
 * empate gana lo que va después en el DOM: la hoja. Los paneles que cuelgan de
 * la barra —avisos, clima, menú «⋯», sugerencias del buscador— tienen `z-30`,
 * pero ese `z-30` vale **dentro** del contexto de la barra, así que la hoja los
 * cortaba igual. En el iPhone, el panel de avisos con los deslizadores por
 * categoría quedaba tapado desde «Otras emergencias» hacia abajo.
 *
 * La barra va un escalón sobre la hoja y las fichas flotantes, y bajo el radar
 * (`z-40`) y el modal de reporte (`z-50`, en portal), que sí deben taparla.
 *
 * Son clases literales a propósito: Tailwind sólo genera las clases que
 * encuentra escritas tal cual en el código.
 */
export const STACK = {
  /** La hoja inferior y las fichas flotantes del teléfono. */
  sheet: 'z-20',
  /** La barra superior y todo lo que cuelga de ella. */
  header: 'z-30',
} as const
