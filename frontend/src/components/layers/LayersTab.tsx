import type { ReactNode } from 'react'
import type { CollectorsHealth, HealthStatus } from '@/api/health'
import type { OutageProvider } from '@/api/types'
import { LegendBody } from '@/components/map/MapLegend'
import { GlyphIcon } from '@/components/ui/GlyphIcon'
import { LayerHealth } from '@/components/ui/LayerHealth'
import { SectionToggle } from '@/components/ui/SectionToggle'
import { Chip } from '@/components/ui/primitives'
import type { IncidentLayerKey } from '@/domain/families'
import {
  DEFAULT_LAYER_VISIBILITY,
  DEFAULT_PROVIDER_VISIBILITY,
  EMERGENCY_LAYERS,
  hiddenFilterCount,
  type EmergencyLayerKey,
  type LayerVisibility,
  type ProviderVisibility,
} from '@/domain/layers'
import { PROVIDER, PROVIDER_ORDER } from '@/domain/powerSymbology'
import { SEISMIC_FILTER_OPTIONS, type SeismicFilterKey } from '@/domain/seismicFilter'
import { useSectionOpen } from '@/lib/sectionStore'
import { LayerGroupHeading, LayerTile } from './LayerTile'
import { ReferenceLayers, type ReferenceLayersProps } from './ReferenceLayers'

/**
 * La pestaña «Capas» de la columna (escritorio) y de la hoja (teléfono).
 *
 * Tres grupos con el mismo rótulo que las secciones de «Ahora»:
 *
 *   - **Emergencias**: un mosaico de azulejos, uno por capa, con el glifo y el
 *     color del mapa. Debajo, los filtros finos como fichas: empresa de luz,
 *     relevancia de los sismos y «sólo verificados».
 *   - **Referencia**: amenaza sísmica, cortes de ruta y cuarteles
 *     (`ReferenceLayers`).
 *   - **Leyenda**, plegada: es consulta, no control.
 *
 * Ya no hay lista de incidentes por capa: era la misma de «Ahora», escondida
 * detrás de un contador.
 */

/** La salud de la fuente de los cortes de agua: viene en su propia respuesta. */
export interface WaterLayerStatus {
  status: HealthStatus | undefined
  detail: string | null
}

export interface EmergencyLayersProps {
  visibility: LayerVisibility
  onChange: (next: LayerVisibility) => void
  /** Lo que la capa tiene en el mapa ahora. */
  counts: Record<EmergencyLayerKey, number>
  seismicFilter: SeismicFilterKey
  onSeismicFilterChange: (filter: SeismicFilterKey) => void
  providers: ProviderVisibility
  onProvidersChange: (next: ProviderVisibility) => void
  /** Cortes de luz en el mapa por empresa. */
  providerCounts: Record<OutageProvider, number>
  /**
   * Salud por familia. `undefined` mientras carga o si falló: no saber si una
   * capa ve no autoriza a afirmar que está ciega.
   */
  health?: CollectorsHealth
  /** Sin esto no hay azulejo de agua: el backend todavía no leyó a Esval. */
  water?: WaterLayerStatus | null
  /**
   * Sólo lo verificado por quien fue al lugar (CONAF, Bomberos, la
   * distribuidora). Es `confirmed_only` del backend, no el tramo `confirmed`.
   */
  confirmedOnly?: boolean
  onConfirmedOnlyChange?: (next: boolean) => void
}

function isIncidentLayer(key: EmergencyLayerKey): key is IncidentLayerKey {
  return key !== 'seismic' && key !== 'water'
}

function FilterRow({ label, children, radio = false }: { label: string; children: ReactNode; radio?: boolean }) {
  return (
    <div className="flex items-start gap-2 px-1.5">
      <span className="w-[4.5rem] shrink-0 pt-1.5 text-[10.5px] text-ink-faint">{label}</span>
      <div
        {...(radio ? { role: 'radiogroup', 'aria-label': label } : { role: 'group', 'aria-label': label })}
        className="flex min-w-0 flex-1 flex-wrap gap-1"
      >
        {children}
      </div>
    </div>
  )
}

function EmergencyLayers({
  visibility,
  onChange,
  counts,
  seismicFilter,
  onSeismicFilterChange,
  providers,
  onProvidersChange,
  providerCounts,
  health,
  water,
  confirmedOnly = false,
  onConfirmedOnlyChange,
}: EmergencyLayersProps) {
  const hidden = hiddenFilterCount(visibility, providers, confirmedOnly)

  const showAll = () => {
    onChange(DEFAULT_LAYER_VISIBILITY)
    onProvidersChange(DEFAULT_PROVIDER_VISIBILITY)
    onConfirmedOnlyChange?.(false)
  }

  const healthFor = (key: EmergencyLayerKey) => {
    if (key === 'water') return water ? { status: water.status, detail: water.detail } : null
    // Los sismos no llevan marca: vienen de `/events/seismic`, con su propia
    // cadencia, y no son una familia de `collector_health`.
    if (!isIncidentLayer(key)) return null
    return {
      status: health?.by_family[key],
      detail: health?.collectors.find((c) => c.families.includes(key) && c.status !== 'ok')?.detail,
    }
  }

  return (
    <section aria-labelledby="capas-emergencias">
      <LayerGroupHeading
        id="capas-emergencias"
        action={
          hidden > 0 && (
            <button
              type="button"
              onClick={showAll}
              className="text-[10.5px] font-semibold text-accent underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              Mostrar todo
            </button>
          )
        }
      >
        Emergencias
      </LayerGroupHeading>

      <div className="grid grid-cols-2 gap-1.5">
        {EMERGENCY_LAYERS.map((layer) => {
          if (layer.key === 'water' && !water) return null
          const on = visibility[layer.key]
          const count = counts[layer.key]
          const source = healthFor(layer.key)
          return (
            <LayerTile
              key={layer.key}
              label={layer.label}
              icon={<GlyphIcon id={layer.icon} className="size-3.5" />}
              color={layer.color}
              on={on}
              onToggle={() => onChange({ ...visibility, [layer.key]: !on })}
              corner={
                <span className="count count-3 pt-0.5 text-[11px] font-semibold text-ink-muted">
                  {count}
                </span>
              }
              detail={
                source ? (
                  <LayerHealth status={source.status} count={count} detail={source.detail} />
                ) : null
              }
            />
          )
        })}
      </div>

      <div className="mt-2.5 space-y-1.5">
        {/* Los filtros de una capa sólo existen con la capa encendida. */}
        {visibility.power && (
          <FilterRow label="Empresas">
            {PROVIDER_ORDER.map((provider) => (
              <Chip
                key={provider}
                on={providers[provider]}
                onToggle={() => onProvidersChange({ ...providers, [provider]: !providers[provider] })}
                swatch={PROVIDER[provider].color}
                count={providerCounts[provider]}
              >
                {PROVIDER[provider].label}
              </Chip>
            ))}
          </FilterRow>
        )}

        {visibility.seismic && (
          <FilterRow label="Sismos" radio>
            {SEISMIC_FILTER_OPTIONS.map((option) => (
              <Chip
                key={option.key}
                radio
                on={seismicFilter === option.key}
                onToggle={() => onSeismicFilterChange(option.key)}
                title={option.hint}
              >
                {option.label}
              </Chip>
            ))}
          </FilterRow>
        )}

        {onConfirmedOnlyChange && (
          <FilterRow label="Fuente">
            <Chip
              on={confirmedOnly}
              onToggle={() => onConfirmedOnlyChange(!confirmedOnly)}
              title="Confirmados por CONAF, Bomberos o la distribuidora"
            >
              Sólo verificados en terreno
            </Chip>
            {/* Encendido, dice qué está escondiendo: sin esto, «verificados»
                no significaba nada para nadie. */}
            {confirmedOnly && (
              <p className="w-full pt-0.5 text-[10px] leading-tight text-ink-muted">
                Sólo lo que CONAF, Bomberos o la distribuidora confirmaron en el lugar.
              </p>
            )}
          </FilterRow>
        )}
      </div>
    </section>
  )
}

function Legend() {
  const [open, toggle] = useSectionOpen('capas-leyenda', false)
  return (
    <section aria-label="Leyenda">
      <SectionToggle label="Leyenda" open={open} onToggle={toggle} controls="capas-leyenda-cuerpo" />
      {open && (
        <div id="capas-leyenda-cuerpo" className="px-1.5 pb-1 pt-1.5">
          <LegendBody />
        </div>
      )}
    </section>
  )
}

export function LayersTab({
  emergencies,
  reference,
}: {
  emergencies: EmergencyLayersProps
  reference: ReferenceLayersProps
}) {
  return (
    <div className="space-y-4 pt-0.5">
      <EmergencyLayers {...emergencies} />
      <ReferenceLayers {...reference} />
      <Legend />
    </div>
  )
}
