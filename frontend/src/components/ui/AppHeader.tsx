interface AppHeaderProps {
  total: number
  confirmed: number
  withAlert: number
  confirmedOnly: boolean
  onToggleConfirmedOnly: (value: boolean) => void
}

export function AppHeader({
  total,
  confirmed,
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
        <p className="truncate text-[11px] text-slate-400">
          {total} activos · {confirmed} confirmados · {withAlert} con alerta
        </p>
      </div>

      <label className="flex shrink-0 cursor-pointer items-center gap-2 rounded-full bg-slate-800 px-3 py-1.5 text-xs font-medium">
        <input
          type="checkbox"
          checked={confirmedOnly}
          onChange={(event) => onToggleConfirmedOnly(event.target.checked)}
          className="size-3.5 accent-orange-500"
        />
        Solo confirmados
      </label>
    </header>
  )
}
