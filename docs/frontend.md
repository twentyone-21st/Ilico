# Frontend

El frontend es una **Single Page Application (SPA)** construida con HTML5, CSS3 y JavaScript puro, sin frameworks externos. Toda la lógica reside en `static/js/script.js` y el diseño en `static/css/style.css`.

---

## Estructura de la interfaz

```
┌─────────────────────────────────────────────────┐
│  SIDEBAR (260px fijo)   │  TOPBAR                │
│  • Logo Ilico           │  • Título de sección   │
│  • Bandeja Principal    │  • Email del usuario   │
│  • Bandeja Archivados   │  • Botón cerrar sesión │
│  • Clasificar texto     ├────────────────────────┤
│  • Enseñar al sistema   │  CONTENIDO PRINCIPAL   │
│                         │  (sección activa)      │
└─────────────────────────────────────────────────┘
```

---

## script.js — Lógica del frontend

### Estado global
El archivo mantiene variables de estado en el ámbito global del módulo:

```javascript
let todosLosCorreos = [];    // correos de la categoría activa
let categoriaActiva = 'principal';
let intervaloAuto   = null;  // referencia al polling
let _mapaCorreos    = {};    // índice id→correo para el modal
```

### Ciclo de vida al cargar la página
Al dispararse `DOMContentLoaded` se ejecutan en orden:

1. `cargarPerfil()` — muestra el email del usuario en la topbar
2. `cargarStats()` — carga contadores de palabras enseñadas
3. `sincronizarCorreccionesAlServidor()` — recupera correcciones del localStorage
4. `cargarChips()` — renderiza las listas de palabras SPAM/HAM
5. `cargarDesdeCache()` — intenta mostrar correos desde el cache del servidor antes de hacer la carga completa

### Sistema de polling
Una vez cargados los primeros correos, `iniciarAutoRefresh()` crea un `setInterval` de 5 segundos que consulta `/api/correos/cache`. Si llegan correos nuevos, re-renderiza la tabla y muestra un toast de notificación.

### Modal del correo
Al hacer clic en una fila, `abrirCorreo(id)` carga el contenido completo desde `/api/correo/{id}`. El cuerpo HTML se renderiza en un `<iframe sandbox="allow-same-origin">` para aislar posibles scripts externos. Tras 500 ms aparece el **panel FPM** (verificación de clasificación).

---

## style.css — Sistema de diseño

El CSS usa **custom properties** (variables CSS) definidas en `:root` para mantener coherencia en todo el sistema:

```css
--bg-0 … --bg-3     /* Escala de grises oscuros */
--spam / --ham      /* Rojo y verde para clasificaciones */
--accent            /* Azul índigo para elementos interactivos */
--font-display      /* Syne — títulos */
--font-body         /* Manrope — texto general */
--font-mono         /* DM Mono — badges y código */
```

### Barra de nivel de confianza
Cada correo tiene una barra de color que representa qué tan seguro es:

| Nivel | Color | Rango |
|-------|-------|-------|
| Arriesgado | Rojo oscuro | 0–20 % |
| Cuestionable | Rojo claro | 21–40 % |
| Ambiguo | Amarillo | 41–60 % |
| Fiable | Verde | 61–80 % |
| Seguro | Verde oscuro | 81–100 % |

Al pasar el cursor sobre la barra aparece un **tooltip** con una descripción en lenguaje natural del nivel de riesgo.
