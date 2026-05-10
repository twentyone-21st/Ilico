# Ilico

Filtro inteligente de correos para Gmail. Clasifica automáticamente cada mensaje como HAM, SPAM o sospechoso, analiza cabeceras de autenticación (SPF, DKIM, DMARC) y aprende de las correcciones del usuario para mejorar con el tiempo.

## Funcionalidades

- Clasificación automática con modelo Naive Bayes entrenado en tiempo real
- Análisis de seguridad por cabeceras de autenticación del correo
- Tres bandejas: Principal, Archivados y Restringidos (spam)
- Notificaciones push en tiempo real vía Gmail API + Google Pub/Sub
- Panel de estadísticas con historial semanal y ranking de remitentes
- Interfaz web con modal de detalle, menú contextual y modo enseñanza

## Stack

Python · Flask · Gmail API · Google Cloud Run · Pub/Sub · GCS · Naive Bayes (scikit-learn)
