/**
 * Id del panel del radar. Vive aparte para que el botón (que va en la carga
 * inicial) pueda apuntarle con `aria-controls` sin arrastrar el panel entero,
 * que se carga recién cuando alguien lo abre.
 */
export const RADAR_PANEL_ID = 'vehicle-radar-panel'
