/**
 * El componente se renombró a `SidePanel`. `LayerToggles.tsx` quedó como
 * reexportación para no romper importaciones antiguas de golpe.
 *
 * Este test existe para que el puente no se rompa en silencio: si alguien borra
 * el shim mientras todavía queda un `import` viejo, la compilación avisa, pero
 * si lo vacía sin borrarlo la app quedaría importando `undefined`.
 */

import { describe, expect, it } from 'vitest'
import * as shim from './LayerToggles'
import { SidePanel } from './SidePanel'

describe('shim de compatibilidad', () => {
  it('reexporta el componente bajo los dos nombres', () => {
    expect(shim.SidePanel).toBe(SidePanel)
    expect(shim.LayerToggles).toBe(SidePanel)
  })

  it('reexporta los valores por defecto que consume App', () => {
    expect(shim.DEFAULT_LAYER_VISIBILITY.fire).toBe(true)
    expect(shim.DEFAULT_PROVIDER_VISIBILITY.chilquinta).toBe(true)
  })
})
