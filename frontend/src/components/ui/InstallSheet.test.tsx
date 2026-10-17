import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { InstallSheet } from './InstallSheet'

describe('InstallSheet (§Q)', () => {
  it('muestra los pasos del dispositivo y deja ver los de otro', () => {
    render(<InstallSheet onClose={vi.fn()} />)
    expect(screen.getByRole('dialog', { name: 'Instalar AlertaV' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: 'iPhone' }))
    expect(screen.getByText(/Abrir como app web/)).toBeInTheDocument()
    expect(screen.getByText('Agregar a pantalla de inicio')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: 'Mac' }))
    expect(screen.getByText('Agregar al Dock')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('radio', { name: 'Android' }))
    expect(screen.getByText('Instalar app')).toBeInTheDocument()
  })

  it('Escape y «Cerrar» la cierran', () => {
    const onClose = vi.fn()
    render(<InstallSheet onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar' }))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
