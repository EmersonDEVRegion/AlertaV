import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'alertav:theme'

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY)
    return value === 'dark' || value === 'light' ? value : null
  } catch {
    // Safari en navegación privada lanza al tocar localStorage. Que no se pueda
    // recordar la preferencia no puede impedir que la app arranque.
    return null
  }
}

/** Aplica la clase que lee la variante `dark:` de Tailwind. */
function applyTheme(theme: Theme): void {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  // `color-scheme` hace que el navegador pinte en oscuro los controles nativos
  // y las barras de scroll. Sin esto quedan blancos sobre un fondo negro.
  root.style.colorScheme = theme
}

/**
 * Tema global.
 *
 * Se inicializa desde la preferencia guardada y, si no hay ninguna, desde la del
 * sistema. Mientras el usuario no elija explícitamente, sigue al sistema: quien
 * pone el teléfono en oscuro al anochecer espera que la app acompañe.
 */
export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme())

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // Seguir al sistema sólo mientras no haya elección explícita.
  useEffect(() => {
    if (storedTheme() !== null) return
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (event: MediaQueryListEvent) =>
      setTheme(event.matches ? 'dark' : 'light')
    media.addEventListener('change', onChange)
    return () => media.removeEventListener('change', onChange)
  }, [])

  const toggle = useCallback(() => {
    setTheme((current) => {
      const next = current === 'dark' ? 'light' : 'dark'
      try {
        window.localStorage.setItem(STORAGE_KEY, next)
      } catch {
        /* sin persistencia: el tema dura lo que la sesión */
      }
      return next
    })
  }, [])

  return { theme, toggle, isDark: theme === 'dark' }
}
