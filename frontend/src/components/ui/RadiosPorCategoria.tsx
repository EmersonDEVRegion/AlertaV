import { useEffect, useId, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { PushNotificationsState } from '@/hooks/usePushNotifications'
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import type { IconId } from '@/domain/emergencyIcons'
import { cn } from '@/lib/cn'
import {
  CATEGORIAS_RESPALDO,
  PASOS,
  formatRadio,
  metrosDePosicion,
  posicionDeMetros,
  radioAlEncender,
  radiosEfectivos,
} from '@/lib/radios'

/** El ícono de cada categoría de aviso: los mismos glifos del mapa. */
const ICONO_CATEGORIA: Readonly<Record<string, IconId>> = {
  fire: 'av-flame',
  traffic: 'av-crash',
  power: 'av-bolt',
  hydro: 'av-flood',
  other: 'av-alert',
  water: 'av-drop',
  transito: 'av-barrier',
}

const iconoDe = (clave: string): IconId => ICONO_CATEGORIA[clave] ?? 'av-alert'

/**
 * Las distancias en una línea de fichas (§D): lo que se mira a diario, sin
 * abrir el editor. Tocar una ficha abre el editor.
 */
export function ResumenRadios({
  push,
  onEditar,
}: {
  push: PushNotificationsState
  onEditar: () => void
}) {
  const categorias = push.server?.categorias?.length
    ? push.server.categorias
    : CATEGORIAS_RESPALDO
  const radios = radiosEfectivos(push.server?.radios_por_defecto, push.preferences.radios)
  return (
    <ul aria-label="Distancias de aviso" className="flex flex-wrap gap-1">
      {categorias.map(({ clave, etiqueta }) => {
        const metros = radios[clave] ?? 0
        const texto = metros > 0 ? formatRadio(metros) : 'No'
        return (
          <li key={clave}>
            <button
              type="button"
              onClick={onEditar}
              title={`${etiqueta}: ${metros > 0 ? texto : 'no avisar'}`}
              aria-label={`${etiqueta}: ${metros > 0 ? texto : 'no avisar'}`}
              className={cn(
                'inline-flex h-6 items-center gap-1 rounded-full bg-sunken px-2 text-[10.5px] font-semibold tabular-nums ring-1 ring-line transition-colors hover:bg-hover',
                metros > 0 ? 'text-ink' : 'text-ink-faint',
              )}
            >
              <GlyphIcon id={iconoDe(clave)} className="size-3 shrink-0" />
              {texto}
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** Cuánto esperar después del último movimiento antes de guardar. */
const GUARDAR_TRAS_MS = 700

/**
 * Un deslizador por categoría de aviso, una línea cada uno (§D): ícono,
 * nombre, barra delgada y valor. Antes eran dos líneas con una barra de 20 px
 * por categoría y el panel pasaba de 900 px.
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
    <fieldset className="mt-1.5 space-y-0.5 rounded-control bg-sunken px-2 py-1.5 ring-1 ring-line">
      <legend className="sr-only">Distancia de aviso por tipo de emergencia</legend>
      {categorias.map(({ clave, etiqueta }) => (
        <FilaRadio
          key={clave}
          icono={iconoDe(clave)}
          etiqueta={etiqueta}
          metros={borrador[clave] ?? guardados[clave] ?? 0}
          alEncender={radioAlEncender(clave, push.server?.radios_por_defecto)}
          onCambio={(metros) => cambiar(clave, metros)}
        />
      ))}
      <p className="pt-0.5 text-[9.5px] leading-snug text-ink-faint">
        Todo a la izquierda: no avisar. «Activar» la deja en su distancia de partida. Vale
        también para tus lugares guardados.
      </p>
    </fieldset>
  )
}

function FilaRadio({
  icono,
  etiqueta,
  metros,
  alEncender,
  onCambio,
}: {
  icono: IconId
  etiqueta: string
  metros: number
  /** Con cuánto queda al tocar «Activar» desde «No avisar». */
  alEncender: number
  onCambio: (metros: number) => void
}) {
  const id = useId()
  const texto = formatRadio(metros)
  const posicion = posicionDeMetros(metros)
  return (
    <div className="grid h-7 grid-cols-[0.875rem_minmax(0,6.5rem)_minmax(0,1fr)_2.75rem] items-center gap-2">
      <GlyphIcon
        id={icono}
        className={cn('size-3.5', metros > 0 ? 'text-ink-muted' : 'text-ink-faint')}
      />
      <label htmlFor={id} className="truncate text-[11px] font-medium text-ink" title={etiqueta}>
        {etiqueta}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={PASOS}
        step={1}
        value={posicion}
        aria-valuetext={texto}
        onChange={(event) => onCambio(metrosDePosicion(Number(event.target.value)))}
        className="range-thin w-full"
        style={{ '--pct': `${(posicion / PASOS) * 100}%` } as CSSProperties}
      />
      {metros > 0 ? (
        <span className="text-right text-[10.5px] font-semibold tabular-nums text-ink">
          {texto}
        </span>
      ) : (
        // Apagada, la barra parte en 300 m: «Activar» la deja en una distancia
        // que sirve (2 km para el tránsito) sin tener que arrastrar.
        <button
          type="button"
          onClick={() => onCambio(alEncender)}
          aria-label={`Activar avisos de ${etiqueta}: ${formatRadio(alEncender)}`}
          className="text-right text-[10.5px] font-semibold text-accent hover:underline
            focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          Activar
        </button>
      )}
    </div>
  )
}
