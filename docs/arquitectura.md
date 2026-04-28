# Arquitectura del sistema

Ilico sigue una arquitectura cliente-servidor clásica con cuatro módulos Python en el backend y una interfaz de usuario de página única (SPA) en el frontend.

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
        ├── security_service.py  (SPF/DKIM/DMARC + Safe Browsing)
        │
        └── correcciones_usuario.json  (palabras del usuario)
```

---

## app.py — Servidor Flask

Es el punto de entrada de la aplicación. Sus responsabilidades son:

- Servir la interfaz HTML mediante la ruta `GET /`
- Gestionar el flujo OAuth2 con Google (`/auth/gmail`, `/auth/callback`, `/auth/logout`)
- Mantener un **cache en memoria** de tres categorías de correos (`principal`, `archivados` y `cuarentena`) con TTL de 5 minutos
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

Cada resultado incluye un campo `descripcion` con una explicación en lenguaje natural de por qué el sistema llegó a su conclusión.

---

## security_service.py — Análisis de seguridad

Se ejecuta sobre cada lote de correos clasificados y añade el campo `seguridad` a cada uno:

- **Autenticación**: extrae el resultado de SPF, DKIM y DMARC de los headers del correo
- **URLs**: extrae hasta 20 URLs del cuerpo del correo y las verifica contra Google Safe Browsing API v4 en una sola llamada batch
- **Nivel**: calcula un nivel consolidado (`peligro`, `advertencia` o `seguro`) combinando los fallos de autenticación, las amenazas de URL y la clasificación del modelo

---

## gmail_service.py — Integración Gmail

Gestiona toda la comunicación con la Gmail API v1:

- **OAuth2**: crea el flujo de autorización y renueva el token automáticamente si está vencido
- **Descarga paralela**: usa `ThreadPoolExecutor` con 5 hilos para obtener los detalles de múltiples correos simultáneamente
- **Parseo MIME**: extrae asunto, remitente, fecha, texto plano y HTML del payload anidado
- **Ordenación cronológica**: convierte las fechas RFC-2822 a Unix timestamp y ordena del más reciente al más antiguo
- **Acciones**: archiva, desarchiva, mueve a cuarentena, restaura y elimina correos mediante la modificación de etiquetas de Gmail
