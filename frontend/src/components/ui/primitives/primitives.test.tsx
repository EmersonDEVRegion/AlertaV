/**
 * Tests de las primitivas extraídas.
 *
 * `switch-geometry.test.ts` cubre la aritmética del riel; esto cubre el
 * comportamiento y la accesibilidad, que es lo que se pierde al reescribir un
 * control a mano.
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, Checkbox, Switch } from './index'

describe('Switch', () => {
  it('expone rol y estado para el lector de pantalla', () => {
    render(<Switch checked={false} onCheckedChange={vi.fn()} label="Amenaza sísmica" />)
    const el = screen.getByRole('switch', { name: 'Amenaza sísmica' })
    expect(el).toHaveAttribute('aria-checked', 'false')
  })

  it('es operable con teclado, no sólo con el puntero', async () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onChange} label="Lluvia" />)

    await userEvent.tab()
    expect(screen.getByRole('switch')).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('aplica el acento sólo cuando está encendido', () => {
    const { rerender } = render(
      <Switch checked={false} onCheckedChange={vi.fn()} label="x" accentColor="#7c3aed" />,
    )
    // Apagado usa el token neutro, no el color de la capa.
    expect(screen.getByRole('switch')).not.toHaveStyle({ backgroundColor: '#7c3aed' })

    rerender(<Switch checked onCheckedChange={vi.fn()} label="x" accentColor="#7c3aed" />)
    expect(screen.getByRole('switch')).toHaveStyle({ backgroundColor: '#7c3aed' })
  })

  it('no dispara el cambio si está deshabilitado', async () => {
    const onChange = vi.fn()
    render(<Switch checked={false} onCheckedChange={onChange} label="x" disabled />)
    await userEvent.click(screen.getByRole('switch'))
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('Checkbox', () => {
  it('sigue siendo un input nativo', () => {
    render(<Checkbox checked onCheckedChange={vi.fn()} label="Incendios" />)
    const el = screen.getByRole('checkbox', { name: 'Incendios' })
    // Conserva la semántica del sistema: estado, foco y comportamiento móvil.
    expect(el.tagName).toBe('INPUT')
    expect(el).toBeChecked()
  })

  it('informa el nuevo valor, no el evento', async () => {
    const onChange = vi.fn()
    render(<Checkbox checked={false} onCheckedChange={onChange} label="Sismos" />)
    await userEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})

describe('Button', () => {
  it('la variante urgente no tiñe la sombra con su propio color', () => {
    render(<Button variant="urgent" size="fab">Reportar</Button>)
    const cls = screen.getByRole('button').className

    // Una sombra roja se derrama sobre el mapa y compite con los marcadores de
    // emergencia, que son rojos de verdad.
    expect(cls).toContain('bg-urgent')
    expect(cls).not.toMatch(/shadow-\[.*220[,_]38[,_]38/)
    expect(cls).toMatch(/shadow-\[0_2px_8px_rgb\(0_0_0/)
  })

  it('el relieve de la variante urgente no codifica un rojo concreto', () => {
    render(<Button variant="urgent" size="fab">Reportar</Button>)
    const cls = screen.getByRole('button').className

    // El degradado es luz sobre `bg-urgent`, no una pareja de rojos: fijar
    // `from-red-500`/`to-red-600` serviría a un tema y rompería el otro.
    expect(cls).toContain('bg-linear-to-b')
    expect(cls).not.toMatch(/(from|to)-red-\d/)
    // El extremo invisible tiene que ser blanco con alfa cero ESCRITO A MANO.
    // `to-transparent` y `to-white/0` computan los dos a negro con alfa cero, y
    // en el respaldo sRGB del `@supports` de Tailwind —sin premultiplicar— el
    // tramo medio del degradado se ensucia de gris.
    expect(cls).toContain('to-[rgb(255_255_255/0)]')
    expect(cls).not.toMatch(/\bto-transparent\b|\bto-white\/0\b/)
  })

  it('da respuesta táctil sin disparar layout', () => {
    render(<Button variant="urgent">x</Button>)
    const cls = screen.getByRole('button').className
    // `scale` sólo toca el compositor; `width`/`padding` obligarían a relayout
    // en cada pulsación, con un mapa repintando debajo.
    expect(cls).toContain('active:scale-[0.97]')
  })

  it('la transición nombra las propiedades que Tailwind v4 escribe de verdad', () => {
    render(<Button variant="urgent">x</Button>)
    const cls = screen.getByRole('button').className

    // En v4 `scale-*` compila a `scale:` y `translate-*` a `translate:`; sólo
    // v3 las componía en `transform`. Nombrar `transform` acá era listar una
    // propiedad que nadie declara, y el pulsado saltaba sin interpolar.
    expect(cls).toContain('transition-[background-color,box-shadow,translate,scale,filter]')
    expect(cls).not.toMatch(/transition-\[[^\]]*\btransform\b/)
  })

  it('el FAB es una píldora con altura táctil que se eleva sólo si hay movimiento permitido', () => {
    render(<Button variant="urgent" size="fab">x</Button>)
    const cls = screen.getByRole('button').className
    expect(cls).toContain('rounded-full')
    expect(cls).toContain('h-12')
    // La regla global de `prefers-reduced-motion` sólo acorta la duración: el
    // desplazamiento seguiría ocurriendo, instantáneo. `motion-safe` lo retira.
    expect(cls).toContain('motion-safe:hover:-translate-y-0.5')
  })

  it('la elevación es del tamaño flotante, no de la variante urgente', () => {
    render(<Button variant="urgent">x</Button>)
    // Un botón urgente dentro de un formulario no debe despegarse del panel.
    expect(screen.getByRole('button').className).not.toContain('-translate-y-0.5')
  })
})
