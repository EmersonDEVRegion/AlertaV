# Notificaciones push — emergencias cercanas y temblores

**Fecha:** 2026-09-23
**Backend:** `app/services/push/`, `app/repositories/push_repository.py`, `app/api/v1/endpoints/push.py`, migración `0012_push_subscriptions`
**Frontend:** `public/push-sw.js`, `src/hooks/usePushNotifications.ts`, `src/components/ui/NotificationBell.tsx`, `src/lib/push.ts`

---

## 1. Qué hace

Dos avisos al teléfono, aunque la app esté cerrada:

| Aviso | A quién | Cuándo |
|---|---|---|
| **Emergencia cercana** (incendio, accidente, corte de luz, inundación…) | Suscripciones a menos de 5 km del incidente | El incidente está `active`, apareció hace menos de 3 h, y además: lo confirmó CONAF/Bomberos, **o** es un corte de luz, **o** tiene confianza ≥ 30 % con al menos 2 fuentes distintas |
| **Temblor** | Suscripciones dentro del radio de percepción del sismo | Magnitud ≥ 3,5 (según la versión del CSN si existe) y ocurrido hace menos de 30 min |

Cada aviso lleva el tipo y la distancia en el título («Incendio forestal a 1,2 km»,
«Sismo de magnitud 4,7 a 32 km») y en el cuerpo la comuna, la hora, cuánto sabemos (o quién lo
confirmó) y, si hay, la alerta de SENAPRED. Un corte de luz lleva distribuidora, clientes
afectados y reposición estimada. Tocar el aviso abre la app en el incidente.

**Una sola fuente no basta.** Un píxel de FIRMS (0,40) o una alerta de Waze (0,40) ya superan
el 30 %; sin la regla de las dos fuentes, la chimenea de Ventanas despertaría a Quintero cada
noche. `PUSH_INCIDENT_MIN_SOURCES=1` la desactiva.

**Los temblores no son alerta temprana.** El CSN y el USGS publican minutos después y los
collectors pasan cada 5. El aviso sirve para saber qué se sintió, no para anticiparse. La PWA lo
dice en el panel de avisos, junto a la instrucción de evacuar la costa si el sismo impide
mantenerse en pie.

---

## 2. Cómo funciona

```
PWA ── POST /push/subscriptions (endpoint + claves + ubicación) ──► push_subscriptions
                                                                        │
motor de correlación ─► incidents ──┐                                   │
collectors CSN/USGS ─► raw_events ──┼─► notificador (cada 60 s) ◄───────┘
                                    │        │ reserva en push_deliveries
                                    │        ▼
                                    └── Web Push cifrado (RFC 8291 + VAPID) ─► FCM / Mozilla / Apple ─► teléfono
```

* **Web Push estándar, sin Firebase.** Funciona en Chrome/Android, Firefox, Edge y en Safari de
  iPhone **sólo si AlertaV está instalada en la pantalla de inicio** (iOS 16.4+). La PWA lo
  explica en el panel cuando detecta un iPhone sin instalar.
* **La ubicación es la última conocida.** Con la app cerrada el navegador no la entrega. La PWA
  la reenvía cada vez que se abre (si se movió más de 250 m o pasaron 12 h), sin volver a pedir
  permiso. Se guarda redondeada a 3 decimales (~110 m) y se borra al desactivar.
* **El notificador es un tercer motor** dentro de `app.workers`, al lado de la recolección y la
  correlación. No se engancha a la correlación porque esa pasada es una transacción atómica y
  porque el umbral suele cruzarse pasadas después de que nace el incidente.
* **Nunca avisa dos veces lo mismo.** Cada envío se reserva en `push_deliveries` con un índice
  único `(suscripción, tipo, asunto)` antes de salir. Un incidente absorbido por otro no genera un
  segundo aviso; un sismo publicado por el CSN y el USGS se avisa una vez (con la versión del CSN).
* **Suscripciones muertas se borran solas:** 404/410 del servicio de push la borran al tiro; 10
  fallos seguidos de otro tipo, también.
* **Sólo servicios de push conocidos.** El servidor hace POST a la URL que registra el navegador;
  para que eso no se use contra servicios internos, se aceptan sólo los dominios de
  `PUSH_ALLOWED_ENDPOINT_HOSTS` (FCM, Mozilla, Apple, Windows).

---

## 3. Puesta en marcha

1. **Generar las claves VAPID, una sola vez:**

   ```powershell
   cd backend
   .\.venv\Scripts\python.exe scripts\generate_vapid_keys.py
   ```

   Imprime `VAPID_PRIVATE_KEY=…`. **No la regeneres después**: cada suscripción queda atada a la
   clave con que se creó, y cambiarla deja mudos a todos los teléfonos hasta que abran la app.

2. **Render → Environment** (servicio del backend):

   | Variable | Valor |
   |---|---|
   | `VAPID_PRIVATE_KEY` | la que imprimió el script (Secret) |
   | `VAPID_SUBJECT` | `mailto:<tu correo>` — obligatorio, los servicios de push lo exigen |

   El resto tiene valores por defecto (ver `.env.production.example`). La clave pública **no se
   configura**: el backend la deriva y la PWA la pide a `GET /api/v1/push/status`.

3. **Migración:** `alembic upgrade head` (o `RUN_MIGRATIONS=1` en Render) crea las dos tablas.

4. **Frontend:** no necesita variables nuevas. Basta con el deploy en Vercel.

5. **Probar:** abrir la app desde el teléfono, tocar la campana → «Activar avisos» → «Enviar aviso
   de prueba». En escritorio, Chrome no permite push en ventanas de incógnito.

Sin `VAPID_PRIVATE_KEY` nada se rompe: `/push/status` responde `enabled: false` con el motivo, la
campana lo muestra, y el notificador se queda dormido sin tumbar a los otros motores.

---

## 4. Variables

| Variable | Defecto | Para qué |
|---|---|---|
| `VAPID_PRIVATE_KEY` | — | Clave de firma. Sin ella, push apagado. |
| `VAPID_SUBJECT` | — | Contacto `mailto:` o `https://`. |
| `PUSH_ENABLED` | `true` | Pausa los envíos sin perder a los suscritos. |
| `ENABLE_PUSH` | `1` | (start.sh) No levantar el notificador. |
| `PUSH_POLL_INTERVAL_SECONDS` | `60` | Cadencia del notificador. |
| `PUSH_INCIDENT_RADIUS_M` | `5000` | Radio de aviso para suscripciones nuevas. |
| `PUSH_INCIDENT_MIN_CONFIDENCE` | `0.30` | Umbral de confianza. |
| `PUSH_INCIDENT_MIN_SOURCES` | `2` | Fuentes distintas exigidas si no hay confirmación oficial. |
| `PUSH_INCIDENT_MAX_AGE_MINUTES` | `180` | Un incidente más viejo ya no se avisa. |
| `PUSH_SEISMIC_MIN_MAGNITUDE` | `3.5` | Magnitud mínima. |
| `PUSH_SEISMIC_MAX_AGE_MINUTES` | `30` | Un sismo más viejo ya no se avisa. |
| `PUSH_SEISMIC_MAX_REACH_KM` | `400` | Tope del radio de percepción (igual al del mapa). |
| `PUSH_MAX_CONSECUTIVE_FAILURES` | `10` | Fallos seguidos antes de borrar una suscripción. |
| `PUSH_DELIVERY_RETENTION_DAYS` | `30` | Cuánto se guarda el registro de envíos. |
| `PUSH_TEST_MIN_INTERVAL_SECONDS` | `60` | Pruebas por IP. |
| `PUSH_ALLOWED_ENDPOINT_HOSTS` | FCM, Mozilla, Apple, Windows | Servicios de push aceptados. |

---

## 5. Rutas

| Ruta | Qué hace |
|---|---|
| `GET /api/v1/push/status` | ¿Hay push? Clave pública y umbrales vigentes. |
| `POST /api/v1/push/subscriptions` | Alta o actualización (idempotente por `endpoint`). |
| `POST /api/v1/push/unsubscribe` | Baja: borra la suscripción, su ubicación y su registro. |
| `POST /api/v1/push/test` | Aviso de prueba a una suscripción ya registrada (1 por minuto por IP). |

---

## 6. Verificación

* `pytest tests/test_webpush_crypto.py tests/test_push_*.py` — el cifrado reproduce byte a byte
  el ejemplo del RFC 8291; reglas, redacción, notificador y API sin base de datos.
* `python scripts/smoke_push.py` — el SQL de verdad contra PostGIS (radio en `geography`,
  anti-join, fusiones, deduplicación de sismos, borrado en cascada). Limpia lo que crea.
* `npm test` en el frontend — detección de soporte (iPhone), resincronización, enlaces y panel.

Lo que no se puede probar sin un teléfono: la entrega real a través de FCM o Apple. Es el paso 5
de la puesta en marcha.
