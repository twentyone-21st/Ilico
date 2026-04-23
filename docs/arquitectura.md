# Arquitectura del sistema

Ilico sigue una arquitectura cliente-servidor clásica con tres módulos Python en el backend y una interfaz de usuario de página única (SPA) en el frontend.

---

## Diagrama general

```
Navegador (script.js)
        │  fetch() → JSON
        ▼
  Flask (app.py)          ──→  Gmail API v1
        │                           │
        ├── classifier.py ◄─────────┘ texto del correo
        │       │
        │   modelo_spam.pkl  (TF-IDF + Naive Bayes)
        │
        └── correcciones_usuario.json  (palabras del usuario)
```

---

## app.py — Servidor Flask

Es el punto de entrada de la aplicación. Sus responsabilidades son:

- Servir la interfaz HTML mediante la ruta `GET /`
- Gestionar el flujo OAuth2 con Google (`/auth/gmail`, `/auth/callback`, `/auth/logout`)
- Mantener un **cache en memoria** de dos categorías de correos (`principal` y `archivados`) con TTL de 5 minutos
- Exponer la **API REST** que consume el frontend
- Arrancar el modelo en un **hilo daemon** al iniciar, sin bloquear la primera respuesta
- Cargar y persistir las **palabras de corrección** del usuario en `correcciones_usuario.json`

### Flujo de carga de correos

```
GET /api/correos
    │
    ├─ ¿Cache vacío o vencido?
    │       │
    │       ├── Sí → Carga 30 correos rápidos (primer plano)
    │       │         + Ampliación a 1000 en background (hilo)
    │       │
    │       └── No → Devuelve cache inmediatamente
    │
    └─ Polling cada 5 s → GET /api/correos/cache  (sin disparar cargas)
```

---

## classifier.py — Motor de clasificación

Implementa cuatro capas de clasificación en orden de prioridad:

### Capa 0: Correcciones del usuario (prioridad absoluta)
Antes de cualquier otra lógica, comprueba si el texto contiene palabras que el usuario ha enseñado al sistema. Si hay más palabras HAM que SPAM, devuelve HAM inmediatamente. Si hay más SPAM que HAM, devuelve SPAM. Esto garantiza que el feedback del usuario nunca sea ignorado, incluso frente a correos de bancos o patrones de estafa.

```
hits_ham  = palabras HAM del usuario presentes en el correo
hits_spam = palabras SPAM del usuario presentes en el correo

hits_ham >= hits_spam > 0  →  HAM  (confianza: 80–98 %)
hits_spam > hits_ham       →  SPAM (confianza: 80–98 %)
ninguna coincidencia       →  continúa a Capa 1
```

### Capa 1: Detección de estafas
Comprueba el texto contra patrones léxicos de alto riesgo (solicitud de datos bancarios + amenaza). Si coincide, devuelve SPAM con 96 % de confianza sin consultar el modelo.

### Capa 2: Reglas por dominio
Verifica si el remitente pertenece a las listas de confianza:

- **Bancos dominicanos**: Banco Popular, BHD León, Banreservas, Scotiabank, APAP, etc.
- **Servicios globales**: Google, Apple, Microsoft, Amazon, Netflix, Spotify, etc.

Si el dominio es confiable, analiza la intención del contenido:

| Intención | Resultado |
|-----------|-----------|
| Transaccional | HAM (98 %) |
| Promocional | SOSPECHOSO (65 %) |
| Neutro | HAM (85 %) |

### Capa 3: Modelo NLP
Usa el pipeline TF-IDF + Naive Bayes entrenado. Las probabilidades se ajustan con las palabras que el usuario ha enseñado al sistema (+15 % por cada palabra coincidente, máximo 45 %).

---

## app.py — Comportamiento tras nuevo deploy

Al arrancar el servidor, `app.py` comprueba si existe un `token.json` cuya fecha de creación sea anterior al inicio del proceso actual. Si es así, lo elimina automáticamente. Esto garantiza que tras cada nuevo deploy en Railway el usuario sea redirigido a iniciar sesión, evitando estados inconsistentes donde la app aparece autenticada pero con la memoria vacía.

---

## gmail_service.py — Integración Gmail

Gestiona toda la comunicación con la Gmail API v1:

- **OAuth2**: carga, refresco y persistencia del token en `token.json`
- **Descarga paralela**: usa `ThreadPoolExecutor` con 5 hilos para obtener los detalles de múltiples correos simultáneamente
- **Parseo MIME**: extrae asunto, remitente, fecha, texto plano y HTML del payload anidado
- **Ordenación cronológica**: convierte las fechas RFC-2822 a Unix timestamp y ordena del más reciente al más antiguo
