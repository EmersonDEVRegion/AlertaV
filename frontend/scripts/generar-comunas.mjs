// Genera `src/domain/comunas.ts` desde los polígonos oficiales que carga la
// migración 0015 (`backend/migrations/data/comunas_v_region.geojson`).
//
// El buscador de la barra sólo necesita, por comuna, un nombre, un punto
// dentro de ella y su caja: no los 11 mil vértices. Este script los calcula
// una vez y el resultado entra al repo; no corre en el build.
//
//   node scripts/generar-comunas.mjs
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const src = resolve(here, '../../backend/migrations/data/comunas_v_region.geojson')
const out = resolve(here, '../src/domain/comunas.ts')

const geojson = JSON.parse(readFileSync(src, 'utf8'))

/** Área con signo de un anillo (fórmula del cordón), en grados². */
function ringArea(ring) {
  let a = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1])
  }
  return a / 2
}

function ringCentroid(ring) {
  let x = 0
  let y = 0
  let a = 0
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
    x += (ring[j][0] + ring[i][0]) * f
    y += (ring[j][1] + ring[i][1]) * f
    a += f
  }
  return [x / (3 * a), y / (3 * a)]
}

function inside([px, py], ring) {
  let hit = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

/**
 * Un punto que cae DENTRO de la comuna. El centroide de una comuna con forma
 * de medialuna (Valparaíso, Concón) puede caer afuera, en el mar o en la
 * vecina; en ese caso se toma el medio del tramo más ancho de la línea
 * horizontal que pasa por el centroide.
 */
function labelPoint(ring) {
  const c = ringCentroid(ring)
  if (inside(c, ring)) return c
  const xs = []
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if (yi > c[1] !== yj > c[1]) xs.push(((xj - xi) * (c[1] - yi)) / (yj - yi) + xi)
  }
  xs.sort((a, b) => a - b)
  let best = null
  for (let k = 0; k + 1 < xs.length; k += 2) {
    if (!best || xs[k + 1] - xs[k] > best[1] - best[0]) best = [xs[k], xs[k + 1]]
  }
  return best ? [(best[0] + best[1]) / 2, c[1]] : c
}

const round = (n) => Math.round(n * 10000) / 10000

const comunas = geojson.features
  .map((f) => {
    const polys = f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates : [f.geometry.coordinates]
    let west = Infinity
    let south = Infinity
    let east = -Infinity
    let north = -Infinity
    let largest = null
    for (const poly of polys) {
      const outer = poly[0]
      for (const [x, y] of outer) {
        west = Math.min(west, x)
        east = Math.max(east, x)
        south = Math.min(south, y)
        north = Math.max(north, y)
      }
      if (!largest || Math.abs(ringArea(outer)) > Math.abs(ringArea(largest))) largest = outer
    }
    const [lon, lat] = labelPoint(largest)
    return {
      cut: f.properties.cut,
      nombre: f.properties.nombre,
      provincia: f.properties.provincia,
      punto: [round(lon), round(lat)],
      caja: [round(west), round(south), round(east), round(north)],
    }
  })
  .sort((a, b) => a.nombre.localeCompare(b.nombre, 'es'))

const body = `// Archivo generado por \`scripts/generar-comunas.mjs\` desde
// \`backend/migrations/data/comunas_v_region.geojson\`. No editar a mano.

interface Comuna {
  cut: number
  nombre: string
  provincia: string
  /** Un punto dentro de la comuna, [lon, lat]. */
  punto: [number, number]
  /** Caja que la contiene: [oeste, sur, este, norte]. */
  caja: [number, number, number, number]
}

export const COMUNAS: readonly Comuna[] = [
${comunas
  .map((c) => '  ' + JSON.stringify(c).replace(/"(\w+)":/g, '$1: ').replace(/"/g, "'").replace(/,(?=\S)/g, ', '))
  .join(',\n')},
]
`
writeFileSync(out, body)
console.log(`${comunas.length} comunas → ${out}`)
