import { useEffect, useState } from 'react'

/**
 * La hora actual, refrescada cada `intervalMs`.
 *
 * Existe para lo que envejece sin que llegue nada nuevo: el «hace 3 h» de una
 * tarjeta y la ventana de 48 h del radar. Sin esto, una pestaña abierta toda la
 * tarde seguiría diciendo «hace 5 min» de un aviso de hace horas, y seguiría
 * mostrando uno que ya salió de la ventana.
 *
 * Un minuto y no un segundo, como `useFreshness`: acá la unidad más fina que se
 * muestra es el minuto, y repintar más seguido no cambiaría nada en pantalla.
 */
export function useNow(intervalMs = 60_000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])

  return now
}
