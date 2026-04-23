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

| Fuente | Tipo | Ejemplos |
|--------|------|----------|
| Dataset interno | Correos bancarios RD, phishing, spam general | ~1 500 |
| Feedback del usuario | Correos reales etiquetados por el usuario | Variable |
| Hugging Face (en background) | SMS Spam Multilingüe | ~5 000+ |

El dataset interno incluye ejemplos específicos del contexto dominicano: alertas de Banco Popular, Banreservas, Qik, Claro RD y patrones de estafa locales.

---

## Ajuste por correcciones del usuario

Cuando el usuario enseña palabras al sistema, estas modifican las probabilidades del modelo en tiempo real:

```
P(spam)_final = P(spam)_modelo + Σ(0.15 × palabras_spam_presentes) − Σ(0.15 × palabras_ham_presentes)
```

El ajuste máximo por dirección es **45 %** para evitar sobreescribir completamente el modelo.

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
