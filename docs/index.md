# Ilico — Filtro Inteligente de Correo

**Ilico** es una aplicación web que analiza y clasifica correos de Gmail usando Procesamiento de Lenguaje Natural (NLP) y Machine Learning. Desarrollada como proyecto académico en el ITLA.

---

## ¿Qué hace?

Ilico se conecta a tu cuenta de Gmail mediante OAuth2 y clasifica automáticamente cada correo en tres categorías:

| Clasificación | Significado |
|---------------|-------------|
| <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2ed573" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> **HAM** | Correo legítimo |
| <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ff4757" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="m4.9 4.9 14.2 14.2"/></svg> **SPAM** | Correo no deseado o peligroso |
| <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ffa502" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg> **SOSPECHOSO** | Requiere revisión manual |

---

## Características principales

- **Autenticación segura** con Google OAuth2
- **Clasificador NLP** basado en TF-IDF + Naive Bayes entrenado con miles de correos reales en español
- **Dataset interno amplio**: 10 categorías HAM y 10 categorías SPAM cubriendo banca, redes sociales, trabajo, educación, salud, gobierno, extorsión, hackers, fraudes y amenazas
- **Reglas inteligentes** por dominio: bancos dominicanos y servicios globales de confianza
- **Detección de estafas** mediante patrones léxicos de alto riesgo
- **Correcciones del usuario con prioridad absoluta**: el feedback del usuario nunca es ignorado por el sistema
- **Clasificación manual de textos** con descripción explicativa de por qué el sistema llegó a su conclusión
- **Análisis de seguridad** por correo: SPF/DKIM/DMARC y verificación de URLs con Google Safe Browsing
- **Panel en tiempo real** con polling automático cada 5 segundos
- **Sesión persistente de 30 días**: la cookie de sesión sobrevive reinicios del contenedor

---

## Stack tecnológico

```
Backend   Flask 3.0 · Python 3.11
ML/NLP    scikit-learn (TF-IDF + Multinomial Naive Bayes)
Gmail     Google API v1 · OAuth2
Frontend  HTML5 · CSS3 · JavaScript vanilla
Deploy    Google Cloud Run · Docker · Gunicorn
```

---

## Estructura del proyecto

```
ilico_app/
├── app.py                # Servidor Flask y API REST
├── classifier.py         # Motor de clasificación NLP
├── gmail_service.py      # Integración con la Gmail API
├── security_service.py   # Análisis SPF/DKIM/DMARC y Safe Browsing
├── requirements.txt      # Dependencias de producción
├── Dockerfile            # Imagen Docker para Google Cloud Run
├── .gcloudignore         # Exclusiones del contexto de build en Cloud Run
├── static/
│   ├── css/style.css     # Estilos del panel
│   └── js/script.js      # Lógica del frontend
└── templates/
    └── index.html        # Plantilla principal
```
