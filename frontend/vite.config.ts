import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

/**
 * Toda variable `VITE_*` se hornea en el bundle y la puede leer cualquiera que
 * abra la app. Hoy son todas públicas por diseño (una URL, cadencias y un
 * interruptor), y esta barrera existe para que siga así: el build falla si
 * alguien crea una con nombre de secreto, en vez de publicarla en silencio.
 */
const SECRET_NAME = /(SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE|SERVICE_ROLE|API_KEY|DATABASE_URL|DSN)/i

function assertNoSecretsInPublicEnv(env: Record<string, string>): void {
  const offenders = Object.keys(env).filter((name) => SECRET_NAME.test(name))
  if (offenders.length > 0) {
    throw new Error(
      `[AlertaV/env] ${offenders.join(', ')}: una variable VITE_ se publica en el bundle. ` +
        'Si es un secreto, va en el backend.',
    )
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  assertNoSecretsInPublicEnv(env)
  const apiProxyTarget = env.VITE_DEV_API_PROXY ?? 'http://localhost:8000'

  return {
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },

    optimizeDeps: {
      // maplibre-gl v6 resuelve la URL de su Web Worker en tiempo de ejecucion:
      //
      //   new URL('./maplibre-gl-worker.mjs', import.meta.url)
      //
      // Si Vite lo pre-empaqueta, `import.meta.url` pasa a apuntar a
      // node_modules/.vite/deps/, donde el worker no existe. El `new Worker()`
      // se construye igual, falla al cargar en segundo plano y nadie escucha ese
      // error: el dispatcher queda esperando para siempre, `load` no se dispara
      // nunca y el lienzo se queda en blanco sin una sola linea en consola.
      //
      // Excluirlo lo deja servido desde su carpeta real, donde el worker y su
      // dependencia `maplibre-gl-shared.mjs` viven como hermanos.
      //
      // Esto cubre solo dev. El build tenia el mismo modo de falla (la URL
      // caia en /assets/maplibre-gl-worker.mjs, inexistente) y lo resuelve
      // `src/lib/maplibreWorker.ts`, que fija la URL explicitamente.
      exclude: ['maplibre-gl'],
    },

    // maplibre instancia su worker con `new Worker(url, { type: 'module' })`.
    // El default de Vite es 'iife', que rompe si el bundle del worker necesita
    // dividirse. Ver src/lib/maplibreWorker.ts.
    worker: {
      format: 'es',
    },

    server: {
      port: 5173,
      // El backend ya trae http://localhost:5173 en CORS_ORIGINS, pero pasar por
      // el proxy evita el preflight y hace que dev y produccion usen exactamente
      // la misma URL relativa (/api/v1). Un problema menos que depurar.
      proxy: {
        '/api': { target: apiProxyTarget, changeOrigin: true },
      },
    },

    build: {
      target: 'es2022',
      // Sin mapas de fuente en el despliegue: son ~4 MB que nadie usa sin un
      // rastreador de errores. `npm run analyze` los genera cuando hacen falta.
      sourcemap: false,
      // maplibre-gl pesa cerca de 1 MB y no hay como evitarlo: es el motor de
      // render del mapa. El aviso por defecto (500 kB) solo agrega ruido.
      chunkSizeWarningLimit: 1100,
      rollupOptions: {
        output: {
          // Vite 8 usa rolldown: `manualChunks` fue reemplazado por
          // `codeSplitting`. Aislar maplibre evita invalidar toda la cache del
          // navegador cuando solo cambia el código de la aplicacion.
          codeSplitting: {
            groups: [
              { name: 'maplibre', test: /node_modules[\\/]maplibre-gl[\\/]/ },
              { name: 'query', test: /node_modules[\\/]@tanstack[\\/]/ },
            ],
          },
        },
      },
    },

    plugins: [
      react(),
      tailwindcss(),
      VitePWA({
        registerType: 'autoUpdate',
        injectRegister: 'auto',
        includeAssets: ['icons/favicon.svg', 'icons/apple-touch-icon.png'],

        manifest: {
          id: '/',
          name: 'AlertaV — Emergencias Región de Valparaíso',
          short_name: 'AlertaV',
          description:
            'Incidentes de emergencia de la Región de Valparaíso, correlacionados desde CONAF, SENAPRED y NASA FIRMS, con su nivel de confianza a la vista.',
          lang: 'es-CL',
          dir: 'ltr',
          start_url: '/',
          scope: '/',
          display: 'standalone',
          orientation: 'portrait-primary',
          background_color: '#0f172a',
          theme_color: '#0f172a',
          categories: ['utilities', 'news', 'navigation'],
          icons: [
            { src: '/icons/pwa-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
            { src: '/icons/pwa-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
            { src: '/icons/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
          ],
        },

        workbox: {
          // Avisos push. `generateSW` no acepta código propio en el service
          // worker, pero sí scripts importados: `public/push-sw.js` registra
          // los manejadores `push` y `notificationclick`. El navegador lo
          // guarda junto al service worker, así que funciona sin señal.
          importScripts: ['push-sw.js'],

          // `mjs` incluido a proposito: el bundle del worker de maplibre puede
          // emitirse con esa extension y sin precachearlo la app no abre el
          // mapa sin señal, que es justamente el caso de uso en terreno.
          globPatterns: ['**/*.{js,mjs,css,html,svg,png,ico,woff2}'],
          maximumFileSizeToCacheInBytes: 4 * 1024 * 1024,
          cleanupOutdatedCaches: true,
          clientsClaim: true,
          skipWaiting: true,
          navigateFallback: '/index.html',
          navigateFallbackDenylist: [/^\/api\//, /^\/docs/, /^\/redoc/],

          /*
           * Las reglas de la API se reconocen con FUNCIONES, no con RegExp.
           *
           * En producción la API vive en otro origen (Render) que la PWA
           * (Vercel). Workbox sólo aplica una RegExp a una URL de otro origen si
           * calza desde el primer carácter (`workbox-routing/RegExpRoute.js`), y
           * `/\/api\/v1\/incidents\//` calza en la mitad. Resultado: estas
           * reglas nunca guardaron nada en producción y la app no abría sin
           * señal. Un matcher de función no tiene esa restricción y sirve igual
           * con la API en el mismo origen (desarrollo) o en otro.
           *
           * Las funciones se serializan dentro de `sw.js`: no pueden usar nada
           * de este archivo.
           */
          runtimeCaching: [
            {
              // Incidentes: la red manda siempre. La cache solo existe para que
              // la app abra en una quebrada sin señal, y con fecha de vencimiento
              // corta: un incendio de hace una hora ya no describe el presente.
              // La UI además rotula la antiguedad (ver StalenessBanner).
              urlPattern: ({ url }) => url.pathname.startsWith('/api/v1/incidents/'),
              handler: 'NetworkFirst',
              options: {
                cacheName: 'alertav-incidents',
                networkTimeoutSeconds: 6,
                expiration: { maxEntries: 32, maxAgeSeconds: 60 * 10 },
                cacheableResponse: { statuses: [0, 200] },
                matchOptions: { ignoreVary: true },
              },
            },
            {
              // Radar de vehículos: misma lógica que los incidentes. Servir una
              // lista vieja sin red es seguro porque el cliente vuelve a filtrar
              // por la ventana de 48 h con su propio reloj, y todo lo que dice
              // «hace X» se calcula desde fechas absolutas de la respuesta.
              // Vence en 6 h: más allá, casi toda la lista cambió.
              urlPattern: ({ url }) => url.pathname.startsWith('/api/v1/feed/'),
              handler: 'NetworkFirst',
              options: {
                cacheName: 'alertav-feed',
                networkTimeoutSeconds: 6,
                expiration: { maxEntries: 8, maxAgeSeconds: 60 * 60 * 6 },
                cacheableResponse: { statuses: [0, 200] },
                matchOptions: { ignoreVary: true },
              },
            },
            {
              // Cortes de agua (Esval): misma lógica que los incidentes. Sin red
              // se ve la última lista, con su «visto hace X» a la vista. Vence
              // en 6 h: un corte de agua dura horas, no días.
              urlPattern: ({ url }) => url.pathname.startsWith('/api/v1/events/water-cuts/'),
              handler: 'NetworkFirst',
              options: {
                cacheName: 'alertav-water',
                networkTimeoutSeconds: 6,
                expiration: { maxEntries: 4, maxAgeSeconds: 60 * 60 * 6 },
                cacheableResponse: { statuses: [0, 200] },
                matchOptions: { ignoreVary: true },
              },
            },
            {
              /*
               * Amenaza sísmica: un modelo probabilístico que cambia cada varios
               * años. Se sirve la copia guardada al instante y se revalida
               * detrás: sin señal, la capa sigue disponible.
               */
              urlPattern: ({ url }) => url.pathname === '/api/v1/events/seismic/hazard',
              handler: 'StaleWhileRevalidate',
              options: {
                cacheName: 'alertav-hazard',
                expiration: { maxEntries: 2, maxAgeSeconds: 60 * 60 * 24 * 30 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
            {
              // Estilo, teselas, glifos y sprites del mapa base: inmutables,
              // cachear agresivo. El estilo está en `basemaps.cartocdn.com`,
              // pero las teselas y los glifos vienen de subdominios
              // (`tiles.basemaps.cartocdn.com`): la regla los cubre a todos.
              urlPattern: /^https:\/\/([a-z0-9-]+\.)*basemaps\.cartocdn\.com\/.*/i,
              handler: 'CacheFirst',
              options: {
                cacheName: 'alertav-basemap',
                expiration: { maxEntries: 600, maxAgeSeconds: 60 * 60 * 24 * 30 },
                cacheableResponse: { statuses: [0, 200] },
              },
            },
          ],
        },

        devOptions: {
          // Permite probar el service worker con `npm run dev`.
          enabled: false,
          type: 'module',
          navigateFallback: 'index.html',
        },
      }),
    ],
  }
})
