/*
 * AlertaV — avisos push dentro del service worker.
 *
 * El service worker de la PWA lo genera workbox (vite-plugin-pwa en modo
 * `generateSW`) y no admite código propio. Este archivo se le inyecta con
 * `workbox.importScripts` (ver vite.config.ts): se carga una vez, al instalar
 * el service worker, y registra los dos manejadores que workbox no tiene.
 *
 * Es JavaScript plano, sin build, a propósito: corre en el contexto del service
 * worker, donde no hay módulos de la app ni variables de Vite. Todo lo que
 * necesita viaja dentro del mensaje (ver `app/services/push/messages.py`).
 *
 * Lo que NO hace: renovar la suscripción cuando el navegador la rota
 * (`pushsubscriptionchange`). Para eso haría falta la URL de la API y la
 * ubicación, que este contexto no tiene. La PWA reenvía su suscripción cada vez
 * que se abre, y eso cubre el caso.
 */

/* global self, clients */

self.addEventListener('push', (event) => {
  let data = {}
  if (event.data) {
    try {
      data = event.data.json()
    } catch {
      // Un mensaje que no es JSON sólo puede venir de una prueba manual desde
      // las herramientas del navegador. Se muestra tal cual, sin inventar nada.
      data = { title: 'AlertaV', body: event.data.text() }
    }
  }

  const title = typeof data.title === 'string' && data.title ? data.title : 'AlertaV'
  const urgent = data.kind === 'incident' || data.kind === 'seismic'

  /** @type {NotificationOptions} */
  const options = {
    body: typeof data.body === 'string' ? data.body : '',
    // El folio del incidente, o el sismo. Un mensaje nuevo con el mismo tag
    // reemplaza al anterior en la bandeja en vez de apilarse.
    tag: typeof data.tag === 'string' ? data.tag : undefined,
    icon: '/icons/pwa-192.png',
    // Android pinta el badge en la barra de estado usando sólo su canal alfa:
    // tiene que ser un glifo blanco sobre transparente, no el ícono a color.
    badge: '/icons/badge-96.png',
    lang: 'es-CL',
    dir: 'ltr',
    timestamp: typeof data.ts === 'number' ? data.ts : Date.now(),
    vibrate: urgent ? [220, 120, 220] : undefined,
    data: {
      url: typeof data.url === 'string' ? data.url : '/',
      kind: data.kind || 'other',
    },
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const raw = (event.notification.data && event.notification.data.url) || '/'
  // Sólo rutas del propio sitio: el mensaje viaja cifrado desde nuestro
  // servidor, pero abrir una URL externa desde una notificación es una puerta
  // que no hace falta tener.
  const target = new URL(raw, self.location.origin)
  const url = target.origin === self.location.origin ? target.href : self.location.origin + '/'

  event.waitUntil(
    (async () => {
      const windows = await clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const client of windows) {
        if (new URL(client.url).origin !== self.location.origin) continue
        // La app ya está abierta: se le pide que muestre el incidente sin
        // recargar, que en una conexión mala es la diferencia entre verlo al
        // instante y mirar una pantalla en blanco.
        client.postMessage({ type: 'alertav:navigate', url })
        if ('focus' in client) await client.focus()
        return
      }
      await clients.openWindow(url)
    })(),
  )
})
