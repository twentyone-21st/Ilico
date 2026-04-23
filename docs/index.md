# Ilico — Filtro Inteligente de Correo

**Ilico** es una aplicación web que analiza y clasifica correos de Gmail usando Procesamiento de Lenguaje Natural (NLP) y Machine Learning. Desarrollada como proyecto académico en el ITLA.

---

## ¿Qué hace?

Ilico se conecta a tu cuenta de Gmail mediante OAuth2 y clasifica automáticamente cada correo en tres categorías:

| Clasificación | Significado |
|---------------|-------------|
| ✅ **HAM** | Correo legítimo |
| 🚫 **SPAM** | Correo no deseado o peligroso |
| ⚠️ **SOSPECHOSO** | Requiere revisión manual |

---

## Características principales

- **Autenticación segura** con Google OAuth2
- **Clasificador NLP** basado en TF-IDF + Naive Bayes entrenado con miles de correos reales en español
- **Dataset interno amplio**: 10 categorías HAM y 10 categorías SPAM cubriendo banca, redes sociales, trabajo, educación, salud, gobierno, extorsión, hackers, fraudes y amenazas
- **Reglas inteligentes** por dominio: bancos dominicanos y servicios globales de confianza
- **Detección de estafas** mediante patrones léxicos de alto riesgo
- **Correcciones del usuario con prioridad absoluta**: el feedback del usuario nunca es ignorado por el sistema
- **Panel en tiempo real** con polling automático cada 5 segundos
- **Nivel de confianza** visual por correo (Arriesgado → Seguro)
- **Sesión limpia tras cada deploy**: Railway cierra la sesión automáticamente al publicar cambios

---

## Stack tecnológico

```
Backend   Flask 3.0 · Python 3.11
ML/NLP    scikit-learn (TF-IDF + Multinomial Naive Bayes)
Gmail     Google API v1 · OAuth2
Frontend  HTML5 · CSS3 · JavaScript vanilla
Deploy    Railway · Docker · Gunicorn
```

---

## Estructura del proyecto

```
ilico_app/
├── app.py              # Servidor Flask y API REST
├── classifier.py       # Motor de clasificación NLP
├── gmail_service.py    # Integración con la Gmail API
├── requirements.txt    # Dependencias de producción
├── Dockerfile          # Imagen Docker para Railway
├── static/
│   ├── css/style.css   # Estilos del panel
│   └── js/script.js    # Lógica del frontend
└── templates/
    └── index.html      # Plantilla principal
```
