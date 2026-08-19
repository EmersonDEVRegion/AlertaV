import type { Theme } from '@/hooks/useTheme'

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
      className="grid size-8 shrink-0 place-items-center rounded-full bg-slate-800 text-sm text-slate-200 transition hover:bg-slate-700 dark:bg-slate-700 dark:hover:bg-slate-600"
    >
      <span aria-hidden>{goingDark ? '🌙' : '☀️'}</span>
    </button>
  )
}
