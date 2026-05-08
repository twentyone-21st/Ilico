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
│  • Bandeja Restringidos ├────────────────────────┤
│  • Clasificar texto     │  CONTENIDO PRINCIPAL   │
│  • Enseñar al sistema   │  (sección activa)      │
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
5. `selTipo('spam')` — selecciona el tipo de feedback inicial
6. `prepararLogout()` — enlaza el botón de cierre de sesión
7. `cargarDesdeCache()` — intenta mostrar correos desde el cache del servidor antes de hacer la carga completa

### Sistema de polling
Una vez cargados los primeros correos, `iniciarAutoRefresh()` crea un `setInterval` de 5 segundos que consulta `/api/correos/cache`. Si llegan correos nuevos, re-renderiza la tabla automáticamente.

### Modal del correo
Al hacer clic en una fila, `abrirCorreo(id)` carga el contenido completo desde `/api/correo/{id}`. El cuerpo HTML se renderiza en un `<iframe sandbox="allow-same-origin">` para aislar posibles scripts externos. Tras 500 ms aparece el **panel FPM** (verificación de clasificación).

### Sección "Clasificar texto"
Diseño de dos columnas: panel de texto a la izquierda y tarjeta de resultado a la derecha.

- **Estado vacío**: icono circular neutro con el mensaje *"Aún no has introducido un texto para clasificar"*
- **Estado de carga**: spinner animado mientras el servidor analiza el texto
- **Estado de resultado**: icono y etiqueta coloreada (SPAM / HAM / SOSPECHOSO), seguidos de una explicación en lenguaje natural generada por el backend (`descripcion`) que justifica la conclusión del sistema
- **Botón Aceptar**: bloquea la entrada hasta que el usuario lo pulsa, luego limpia el área y vuelve al estado vacío

### Menú contextual
El clic derecho (o pulsación larga en móvil) sobre una fila abre un menú con acciones: Archivar/Desarchivar, Restringir, Quitar restricción y Eliminar. Las opciones varían según la categoría activa.

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

### Efectos visuales por clasificación
La tarjeta de resultado en "Clasificar texto" aplica un resplandor radial de color según el resultado:

| Clasificación | Color del resplandor |
|---------------|----------------------|
| SPAM | Rojo `rgba(255,71,87,…)` |
| HAM | Verde `rgba(46,213,115,…)` |
| SOSPECHOSO | Amarillo `rgba(255,165,2,…)` |

Implementado con el pseudo-elemento `::before` del `.result-card` usando `radial-gradient`.
