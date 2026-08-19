import { LEVEL } from '@/domain/symbology'

interface AppHeaderProps {
  total: number
  /** Conteo por tramo de `confidence_level`. */
  byLevel: { unsafe: number; possible: number; confirmed: number }
  withAlert: number
  confirmedOnly: boolean
  onToggleConfirmedOnly: (value: boolean) => void
}

export function AppHeader({
  total,
  byLevel,
  withAlert,
  confirmedOnly,
  onToggleConfirmedOnly,
}: AppHeaderProps) {
  return (
    <header className="flex items-center gap-3 bg-slate-900 px-3 py-2.5 pt-[max(0.625rem,env(safe-area-inset-top))] text-white">
      <div className="min-w-0 flex-1">
        <h1 className="text-sm font-bold leading-tight">
          Alerta<span className="text-orange-400">V</span>
        </h1>
        {/* Desglose por tramo, con el punto de color de cada uno: el número
            suelto no dice nada si no se ve a qué color corresponde. */}
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-400">
          <span>{total} activos</span>
          {(['confirmed', 'possible', 'unsafe'] as const).map((key) => (
            <span key={key} className="inline-flex items-center gap-1">
              <span
                aria-hidden
                className="size-2 rounded-full"
                style={{ backgroundColor: LEVEL[key].color }}
              />
              <span className="tabular-nums">{byLevel[key]}</span>
              <span className="sr-only">{LEVEL[key].label}</span>
            </span>
          ))}
          <span>· {withAlert} con alerta</span>
        </p>
      </div>

      <label className="flex shrink-0 cursor-pointer items-center gap-2 rounded-full bg-slate-800 px-3 py-1.5 text-xs font-medium">
        <input
          type="checkbox"
          checked={confirmedOnly}
          onChange={(event) => onToggleConfirmedOnly(event.target.checked)}
          className="size-3.5 accent-orange-500"
        />
        {/* `confirmed_only` del backend filtra por verificación institucional
            (CONAF/Bomberos), no por el tramo `confirmed`. El texto lo dice. */}
        Verificados en terreno
      </label>
    </header>
  )
}
