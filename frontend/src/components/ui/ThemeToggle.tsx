import type { Theme } from '@/hooks/useTheme'
import { cn } from '@/lib/cn'

interface ThemeToggleProps {
  theme: Theme
  onToggle: () => void
}

/**
 * Interruptor de tema.
 *
 * Muestra el ícono del tema al que se va a cambiar, no el actual: es la
 * convención que la gente ya conoce de otras aplicaciones, y el `aria-label`
 * dice explícitamente la acción para que no dependa del ícono.
 *
 * # Por qué se fue el emoji
 *
 * Era 🌙 / ☀️. Un emoji lo dibuja la fuente del sistema: distinto en Android,
 * en iOS y en Windows, con su propio color fijo que no responde al tema, sin
 * alineación fiable con el texto y sin forma de darle grosor de trazo. Estos
 * dos SVG heredan `currentColor`, miden siempre lo mismo y comparten grosor con
 * el resto de la iconografía de la aplicación.
 *
 * # La transición
 *
 * Los dos íconos están montados a la vez, superpuestos, y lo que se anima es su
 * rotación y su opacidad. Con un solo ícono intercambiado por `if` el cambio
 * sería un salto: no hay nada que interpolar entre dos nodos distintos.
 */
export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  const goingDark = theme === 'light'

  return (
    <button
      type="button"
      onClick={onToggle}
      role="switch"
      aria-checked={theme === 'dark'}
      aria-label={goingDark ? 'Activar modo oscuro' : 'Activar modo claro'}
      title={goingDark ? 'Modo oscuro' : 'Modo claro'}
      className={cn(
        'relative grid size-8 shrink-0 place-items-center overflow-hidden rounded-full',
        'bg-chrome-raised text-ink-on-chrome',
        'transition-[background-color,scale] duration-150 active:scale-[0.94]',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2',
        'focus-visible:outline-accent',
      )}
    >
      {/* Luna: entra girando desde la izquierda cuando se va a oscuro. */}
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={cn(
          'absolute size-4 transition-[opacity,rotate,scale] duration-300',
          goingDark ? 'rotate-0 scale-100 opacity-100' : '-rotate-90 scale-50 opacity-0',
        )}
      >
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>

      {/* Sol: el recorrido inverso. */}
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
        className={cn(
          'absolute size-4 transition-[opacity,rotate,scale] duration-300',
          goingDark ? 'rotate-90 scale-50 opacity-0' : 'rotate-0 scale-100 opacity-100',
        )}
      >
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    </button>
  )
}
