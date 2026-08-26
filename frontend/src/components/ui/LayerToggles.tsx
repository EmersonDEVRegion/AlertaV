/**
 * @deprecated Renombrado a `SidePanel`. Este archivo sólo reexporta para no
 * romper importaciones antiguas; se puede borrar una vez migradas.
 */
export {
  DEFAULT_LAYER_VISIBILITY,
  DEFAULT_PROVIDER_VISIBILITY,
  SidePanel,
  SidePanel as LayerToggles,
} from './SidePanel'
export type { LayerVisibility, ProviderVisibility, SidePanelProps } from './SidePanel'
