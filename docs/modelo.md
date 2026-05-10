# Modelo NLP

El clasificador de Ilico combina reglas deterministas y un modelo de aprendizaje automático para lograr alta precisión tanto en correos en español como en inglés.

---

## Pipeline de scikit-learn

```
Texto crudo
    │
    ▼
preprocesar()          # minúsculas, URLs→token, montos→token, stopwords
    │
    ▼
TfidfVectorizer        # bigramas, sublinear_tf, min_df=2, max_df=0.95
    │
    ▼
MultinomialNB(α=0.1)   # clasificador Naive Bayes multinomial
    │
    ▼
predict_proba()        # [P(ham), P(spam)]
```

### TF-IDF con bigramas
El vectorizador convierte el texto en una matriz de frecuencias ponderadas. Al usar bigramas `(1,2)` captura frases de dos palabras como "datos bancarios" o "haz clic", que son fuertes indicadores de spam.

### Naive Bayes multinomial
Calcula la probabilidad de que un documento pertenezca a cada clase dado el vector TF-IDF. El parámetro `alpha=0.1` aplica suavizado de Laplace para evitar probabilidades cero en palabras no vistas durante el entrenamiento.

---

## Dataset de entrenamiento

El modelo se entrena con tres fuentes combinadas:

| Fuente | Tipo | Ejemplos aprox. |
|--------|------|-----------------|
| Dataset interno | 10 categorías HAM + 10 categorías SPAM en español | ~4 500 |
| Feedback del usuario | Correos reales etiquetados por el usuario | Variable |
| Hugging Face (en background) | SMS multilingüe + phishing + email spam | ~15 000+ |

### Categorías del dataset interno

El dataset cubre ampliamente los tipos de correo que un usuario dominicano puede recibir:

**HAM (correos legítimos)**

| Categoría | Ejemplos incluidos |
|-----------|-------------------|
| Bancario | Alertas Banco Popular, BHD, Banreservas, Qik, APAP, Scotiabank |
| Seguridad digital | Códigos 2FA de Google, Apple, WhatsApp, GitHub |
| Personal | Mensajes de trabajo, familia, recordatorios |
| Redes sociales | Notificaciones de Facebook, Instagram, TikTok, LinkedIn |
| Trabajo | RR.HH., nómina, capacitaciones, ofertas laborales reales |
| E-commerce | Amazon, MercadoLibre, AliExpress, confirmaciones de pedido |
| Educación | ITLA, UASD, PUCMM, Coursera, Udemy |
| Salud | Citas médicas, resultados de laboratorio, ARS |
| Gobierno | DGII, TSS, JCE, Migración, Policía Nacional |
| Noticias | El Listín, Diario Libre, ESPN, Bloomberg, TechCrunch |

**SPAM (correos no deseados o peligrosos)**

| Categoría | Ejemplos incluidos |
|-----------|-------------------|
| Phishing bancario | Suplantación de bancos dominicanos, PayPal, Qik |
| Spam general | Premios falsos, casinos, adelgazantes milagrosos |
| Extorsión | Sextorsión, chantaje, amenazas de publicar datos |
| Hackers | Malware, robo de credenciales, alertas falsas de seguridad |
| Fraude laboral | Trabajos desde casa, reclutamiento falso, mystery shopper |
| Fraude romántico | Estafas de citas, militares falsos, herencias |
| Fraude de inversión | Crypto garantizado, robots de trading, pirámides |
| Redes sociales falsas | Venta de seguidores, bots, hackeo de cuentas |
| Gobierno falso | DGII, TSS, JCE, Policía suplantados |
| Amenazas directas | Extorsión violenta, intimidación personal |

---

## Ajuste por correcciones del usuario

Cuando el usuario enseña palabras al sistema, estas modifican las probabilidades del modelo en tiempo real:

```
P(spam)_final = P(spam)_modelo + Σ(0.15 × palabras_spam_presentes) − Σ(0.15 × palabras_ham_presentes)
```

El ajuste máximo por dirección es **45 %** para evitar sobreescribir completamente el modelo.

---

### Datasets de Hugging Face (background)

Al entrenar, se descargan hasta tres datasets adicionales en background:

1. **`ashu0311/SMS_Spam_Multilingual_Collection_Dataset`** — SMS spam con versión en español
2. **`cybersectony/phishing-email-detection-v2.4.1`** — emails de phishing reales en inglés
3. **`mshenoda/email-spam`** — dataset general de email spam

Cada uno tiene manejo de errores independiente: si uno no está disponible, los demás continúan cargándose sin detener el proceso.

---

## Ciclo de entrenamiento

```
Arranque del servidor
    │
    ├─ ¿modelo_spam.pkl existe?
    │       │
    │       ├── Sí  → Carga en < 1 segundo
    │       │
    │       └── No  → Entrena con dataset interno (~10 s)
    │                  Guarda modelo_spam.pkl
    │                  Lanza mejora con Hugging Face en background
    │
    └─ Servidor listo para clasificar
```

El evento `threading.Event` (`_MODELO_LISTO`) bloquea las primeras peticiones de clasificación hasta que el modelo esté disponible, con un timeout de 90 segundos.

---

## Precisión reportada

El modelo se evalúa con un split 80/20 durante el entrenamiento:

- **Dataset interno solo**: ~92–95 %
- **Con dataset de Hugging Face**: ~96–98 %

Los valores exactos se muestran en la sección **Enseñar al sistema** de la interfaz.
