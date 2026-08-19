interface MapOverlayStateProps {
  title: string
  detail: string
}

/** Estado vacío o de carga, superpuesto al mapa sin taparlo del todo. */
export function MapOverlayState({ title, detail }: MapOverlayStateProps) {
  return (
    <div className="pointer-events-none absolute inset-x-0 top-1/2 z-10 flex -translate-y-1/2 justify-center px-6">
      <div className="max-w-xs rounded-2xl bg-white/95 px-4 py-3 text-center shadow-xl ring-1 ring-slate-900/10 backdrop-blur dark:bg-slate-900/95 dark:ring-white/10">
        <p className="text-sm font-semibold text-slate-900 dark:text-slate-100">{title}</p>
        <p className="mt-1 text-xs leading-snug text-slate-600 dark:text-slate-400">{detail}</p>
      </div>
    </div>
  )
}
