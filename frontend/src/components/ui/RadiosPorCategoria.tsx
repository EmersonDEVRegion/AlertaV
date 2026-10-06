import { useEffect, useId, useRef, useState } from 'react'
import type { PushNotificationsState } from '@/hooks/usePushNotifications'
import {
  CATEGORIAS_RESPALDO,
  PASOS,
  formatRadio,
  metrosDePosicion,
  posicionDeMetros,
  radiosEfectivos,
} from '@/lib/radios'

/** Cuánto esperar después del último movimiento antes de guardar. */
const GUARDAR_TRAS_MS = 700

/**
 * Un deslizador por categoría de aviso (§K).
 *
 * Libre de 300 m a 20 km en escala logarítmica —los primeros kilómetros ocupan
 * la mayor parte del recorrido— y, en el extremo izquierdo, «No avisar». Lo que
 * se mueve se guarda solo, 700 ms después de soltar: un aviso por cada pixel
 * del recorrido sería una petición al servidor por pixel.
 */
export function RadiosPorCategoria({ push }: { push: PushNotificationsState }) {
  const categorias = push.server?.categorias?.length
    ? push.server.categorias
    : CATEGORIAS_RESPALDO
  const guardados = radiosEfectivos(push.server?.radios_por_defecto, push.preferences.radios)
  const [borrador, setBorrador] = useState<Record<string, number>>({})
  const borradorRef = useRef<Record<string, number>>({})
  const pendiente = useRef<ReturnType<typeof setTimeout> | null>(null)
  // El guardado corre 700 ms después: tiene que ver las preferencias de ese
  // momento, no las del render en que se programó.
  const ultimo = useRef(push)
  useEffect(() => {
    ultimo.current = push
  }, [push])

  useEffect(
    () => () => {
      if (pendiente.current) clearTimeout(pendiente.current)
    },
    [],
  )

  const programarGuardado = () => {
    if (pendiente.current) clearTimeout(pendiente.current)
    pendiente.current = setTimeout(() => {
      pendiente.current = null
      const actual = ultimo.current
      // Si todavía se está guardando otra cosa, `setPreferences` descartaría
      // ésta: se espera otra vuelta en vez de perder el cambio.
      if (actual.step !== null) {
        programarGuardado()
        return
      }
      const elegidos = { ...actual.preferences.radios, ...borradorRef.current }
      borradorRef.current = {}
      setBorrador({})
      void actual.setPreferences({ ...actual.preferences, radios: elegidos })
    }, GUARDAR_TRAS_MS)
  }

  const cambiar = (clave: string, metros: number) => {
    borradorRef.current = { ...borradorRef.current, [clave]: metros }
    setBorrador(borradorRef.current)
    programarGuardado()
  }

  return (
    <fieldset className="mt-2 space-y-1.5 rounded-control bg-sunken p-2 ring-1 ring-line">
      <legend className="sr-only">Distancia de aviso por tipo de emergencia</legend>
      <p className="text-[10.5px] font-semibold uppercase tracking-wide text-ink-muted">
        Avisar a qué distancia
      </p>
      {categorias.map(({ clave, etiqueta }) => (
        <FilaRadio
          key={clave}
          etiqueta={etiqueta}
          metros={borrador[clave] ?? guardados[clave] ?? 0}
          onCambio={(metros) => cambiar(clave, metros)}
        />
      ))}
      <p className="text-[9.5px] leading-snug text-ink-faint">
        Vale para tu ubicación y para tus lugares guardados. Todo a la izquierda: no avisar.
      </p>
    </fieldset>
  )
}

function FilaRadio({
  etiqueta,
  metros,
  onCambio,
}: {
  etiqueta: string
  metros: number
  onCambio: (metros: number) => void
}) {
  const id = useId()
  const texto = formatRadio(metros)
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-[11px] font-semibold text-ink">
          {etiqueta}
        </label>
        <span
          className={`shrink-0 text-[10.5px] tabular-nums ${metros > 0 ? 'text-ink' : 'text-ink-faint'}`}
        >
          {texto}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={0}
        max={PASOS}
        step={1}
        value={posicionDeMetros(metros)}
        aria-valuetext={texto}
        onChange={(event) => onCambio(metrosDePosicion(Number(event.target.value)))}
        className="mt-0.5 h-5 w-full cursor-pointer accent-[var(--accent)]"
      />
    </div>
  )
}
