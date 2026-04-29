# API REST

Todos los endpoints son servidos por Flask en `app.py`. Las respuestas son JSON salvo `GET /` y las rutas de autenticación que devuelven HTML o redirecciones.

---

## Autenticación

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/` | Interfaz principal |
| `GET` | `/auth/gmail` | Inicia el flujo OAuth2 con Google |
| `GET` | `/auth/callback` | Recibe el código OAuth y guarda el token en la sesión |
| `GET` | `/auth/logout` | Elimina el token y limpia el cache en memoria |

---

## Correos

### `GET /api/correos`
Devuelve los correos clasificados. Lanza una carga desde Gmail si el cache está vacío o vencido.

**Parámetros de query:**

| Parámetro | Valores | Descripción |
|-----------|---------|-------------|
| `categoria` | `principal` \| `archivados` \| `restringidos` | Bandeja a consultar |
| `refresh` | `0` \| `1` | Fuerza recarga ignorando el cache |

**Respuesta:**
```json
{
  "correos": [ { "id": "...", "asunto": "...", "clasificacion": "HAM", ... } ],
  "stats":   { "total": 42, "spam": 5, "ham": 35, "sospechoso": 2 },
  "loading": false,
  "desde_cache": true,
  "nuevos": 0
}
```

### `GET /api/correos/cache`
Devuelve el estado actual del cache sin disparar cargas. Usado por el polling del frontend cada 5 segundos.

**Respuesta adicional:**
```json
{ "stale": false, "vacio": false }
```

### `GET /api/correo/{mensaje_id}`
Devuelve el contenido completo de un correo, incluyendo el cuerpo HTML y la clasificación del cache.

---

## Acciones sobre correos

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/correo/{id}/archivar` | Quita el label INBOX (archiva) |
| `POST` | `/api/correo/{id}/desarchivar` | Añade el label INBOX de vuelta |
| `POST` | `/api/correo/{id}/restringir` | Mueve a Spam de Gmail |
| `POST` | `/api/correo/{id}/restaurar` | Restaura de Spam a la bandeja principal |
| `POST` | `/api/correo/{id}/eliminar` | Mueve a la Papelera de Gmail |
| `POST` | `/api/correos/limpiar` | Mueve en lote los correos SPAM indicados a Restringidos |

**Body de `/api/correos/limpiar`:**
```json
{ "ids": ["18f2a3b4c5d6", "18f2a3b4c5d7"] }
```

---

## Clasificación manual

### `POST /api/clasificar`
Clasifica un texto enviado manualmente por el usuario.

**Body:**
```json
{ "texto": "Tu cuenta ha sido bloqueada, haz clic aquí..." }
```

**Respuesta:**
```json
{
  "clasificacion": "SPAM",
  "confianza": 94.2,
  "prob_spam": 94.2,
  "prob_ham": 5.8,
  "ajustado": false,
  "razon": "Modelo NLP",
  "descripcion": "El análisis lingüístico detectó patrones asociados a mensajes no deseados: urgencia artificial, promesas exageradas o solicitudes inusuales típicas de correos fraudulentos."
}
```

---

## Correcciones del usuario

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/correcciones` | Lista las palabras de corrección guardadas |
| `POST` | `/api/feedback` | Añade palabras clave como spam o ham |
| `POST` | `/api/correcciones/sincronizar` | Fusiona correcciones del cliente con el servidor |
| `POST` | `/api/correcciones/editar` | Reemplaza una palabra existente |
| `POST` | `/api/correcciones/eliminar` | Elimina una palabra de la lista |

**Body de `/api/feedback`:**
```json
{
  "palabras": ["banco", "transferencia"],
  "tipo": "spam",
  "texto_clasificar": "...",
  "correo_id": "18f2a3b4c5d6"
}
```

---

## Estadísticas y perfil

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/perfil` | Email y total de mensajes del usuario autenticado |
| `GET` | `/api/stats` | Precisión del modelo y conteo de correcciones |
| `POST` | `/api/reentrenar` | Fuerza un reentrenamiento completo del modelo |
| `POST` | `/api/webhook/gmail` | Recibe notificaciones push de Gmail (sin acción activa) |
