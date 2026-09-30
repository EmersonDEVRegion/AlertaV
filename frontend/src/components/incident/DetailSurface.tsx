import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/cn'

/**
 * ¿La ficha va dentro de la columna (o de la hoja del teléfono) o flota sola?
 *
 * Con la columna única, la ficha de un incidente, un sismo o un corte de agua
 * reemplaza al historial en el mismo lugar, como en los sitios de este tipo.
 * Las tres tarjetas no saben dónde viven: piden su superficie acá, y el
 * contenedor que las monta dice si es incrustada o flotante.
 */
const EmbeddedContext = createContext(false)

export function DetailEmbed({ children }: { children: ReactNode }) {
  return <EmbeddedContext.Provider value>{children}</EmbeddedContext.Provider>
}

export function useDetailEmbedded(): boolean {
  return useContext(EmbeddedContext)
}

interface DetailSurfaceProps {
  label: string
  /** Clases de la versión flotante: posición fija, sombra, radios. */
  floating: string
  /** Clases de la versión incrustada: sólo el reparto del alto. */
  embedded: string
  children: ReactNode
}

export function DetailSurface({ label, floating, embedded, children }: DetailSurfaceProps) {
  const inside = useDetailEmbedded()
  return (
    <section
      // Flotando es un diálogo no modal; dentro de la columna es una región más.
      role={inside ? 'region' : 'dialog'}
      aria-label={label}
      className={cn(inside ? embedded : floating)}
    >
      {children}
    </section>
  )
}
