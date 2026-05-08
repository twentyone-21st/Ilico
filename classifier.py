"""
@file classifier.py
@brief Motor de clasificación de correos de Ilico. Combina reglas basadas en dominios,
       detección de estafas y un modelo TF-IDF + Naive Bayes entrenado con datos reales.
"""
import re
import os
import json
import pickle
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["DATASETS_VERBOSITY"]     = "error"

try:
    from datasets import load_dataset, disable_progress_bar, logging as ds_logging
    ds_logging.set_verbosity_error()
    disable_progress_bar()
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False

from sklearn.naive_bayes import MultinomialNB
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

# Ruta del modelo serializado para evitar reentrenar en cada arranque
MODEL_CACHE = Path(__file__).parent / "modelo_spam.pkl"
FEEDBACK_CORREOS_FILE = Path(__file__).parent / "feedback_correos.json"

# Dominios de bancos dominicanos conocidos; sus correos se clasifican con alta confianza
DOMINIOS_BANCARIOS_RD = {
    "bancopopular.com.do", "popular.com.do",
    "qik.com.do", "qikbanco.com.do",
    "banreservas.com.do", "banreservas.com",
    "scotiabank.com.do", "scotiabank.com",
    "bhdleon.com.do", "bhd.com.do",
    "apap.com.do",
    "bancamaestro.com.do",
    "promerica.com.do",
    "vimenca.com.do",
    "alaver.com.do",
    "bancalopez.com.do",
    "citibank.com.do", "citi.com",
    "bancoempresa.com.do",
    "lafise.com.do",
    "motorbank.com.do",
    "caribe.com.do",
    "coopnama.coop",
    "coopebarnica.coop",
    "bancosantacr.com.do",
    "cardnet.com.do",
    "azul.com.do",
    "visard.com.do",
    "mastercard.com",
}

# Servicios globales de confianza (Google, Apple, Amazon, etc.)
DOMINIOS_SERVICIOS_GLOBALES = {
    "google.com", "accounts.google.com", "no-reply.accounts.google.com",
    "googleplay.com", "play.google.com",
    "youtube.com",
    "gmail.com",
    "apple.com", "id.apple.com", "appleid.apple.com",
    "microsoft.com", "live.com", "outlook.com", "hotmail.com",
    "microsoftonline.com",
    "amazon.com", "amazon.com.mx", "amazon.es",
    "paypal.com",
    "uber.com", "uber.receipts.com", "uber-trip-receipts.com",
    "ubereats.com",
    "duolingo.com",
    "coursera.org",
    "netflix.com", "spotify.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "linkedin.com",
    "claro.com.do", "altice.com.do", "viva.com.do",
    "tricom.net",
    "edesur.com.do", "edenorte.com.do", "edeeste.com.do",
    "caasd.org.do",
}

TODOS_LOS_DOMINIOS_CONFIANZA = DOMINIOS_BANCARIOS_RD | DOMINIOS_SERVICIOS_GLOBALES

# Palabras que indican que un correo de un remitente confiable es transaccional (recibos, alertas)
PALABRAS_TRANSACCIONAL = {
    "transaccion", "transferencia", "deposito", "retiro", "pago",
    "compra", "consumo", "balance", "estado de cuenta", "factura",
    "debito", "credito", "tarjeta", "cuenta", "monto", "rdo",
    "autorizacion", "confirmacion", "recibo", "comprobante",
    "itbis", "impuesto", "cuota",
    "verificacion", "validacion", "autenticacion", "codigo",
    "contrasena", "clave", "acceso", "inicio de sesion", "sesion",
    "dispositivo", "ubicacion", "actividad", "alerta de seguridad",
    "cambio de contrasena", "factor de autenticacion",
    "nombre", "apellido", "cedula", "rnc", "direccion",
    "viaje", "trip", "orden", "pedido", "entrega", "suscripcion",
    "renovacion", "compra exitosa", "pago procesado", "recibo de",
    "descargaste", "instalaste", "actualizaste",
}

# Palabras que indican contenido promocional (ofertas, descuentos, llamadas a acción)
PALABRAS_PROMOCIONAL = {
    "oferta", "promocion", "descuento", "gratis", "gana", "premio",
    "exclusivo", "especial", "limitado", "ahorra", "rebaja",
    "porcentaje de descuento", "black friday", "cyber monday",
    "no te pierdas", "aprovecha", "solo por hoy", "ultimas unidades",
    "haz clic", "click aqui", "ver oferta", "compra ahora",
    "registrate", "suscribete",
}

# Stopwords en español eliminadas antes del vectorizado para reducir ruido
STOPWORDS_ES = {
    "de","la","el","en","y","a","los","se","del","que","un","una","es",
    "por","con","no","su","al","para","como","mas","pero","sus","le","ya",
    "o","este","si","porque","esta","entre","cuando","muy","sin","sobre",
    "tambien","me","hasta","hay","donde","han","lo","todo","ni","contra",
    "ese","mi","tu","te","nos","les","fue","era","ser","has","he","u","r",
    "esto","eso","aqui","alli","hoy","ayer","ahora","antes","despues",
    "solo","bien","mal","asi","tan","menos","tanto","cuanto","cada",
    "otro","otra","estos","estas","esos","esas","cual","cuales"
}


def extraer_dominio(remitente: str) -> str:
    """
    @brief Extrae el dominio del campo 'From' de un correo en formato 'Nombre <email@dominio.com>'.
    @param remitente Cadena con el remitente del correo.
    @return Dominio en minúsculas, o cadena vacía si no se puede extraer.
    """
    if not remitente:
        return ""
    match = re.search(r'<([^>]+)>', remitente)
    email = match.group(1) if match else remitente.strip()
    if "@" in email:
        dominio = email.split("@")[-1].lower().strip()
        return dominio
    return ""


def es_dominio_confianza(dominio: str) -> tuple:
    """
    @brief Verifica si un dominio pertenece a las listas de confianza de bancos o servicios globales.
    @param dominio Dominio a verificar (ej: 'bancopopular.com.do').
    @return Tupla (es_confiable: bool, tipo: str) donde tipo es 'banco', 'servicio' o ''.
    """
    if not dominio:
        return False, ""

    if dominio in DOMINIOS_BANCARIOS_RD:
        return True, "banco"
    if dominio in DOMINIOS_SERVICIOS_GLOBALES:
        return True, "servicio"

    for d_confianza in DOMINIOS_BANCARIOS_RD:
        if dominio.endswith("." + d_confianza) or dominio == d_confianza:
            return True, "banco"
    for d_confianza in DOMINIOS_SERVICIOS_GLOBALES:
        if dominio.endswith("." + d_confianza) or dominio == d_confianza:
            return True, "servicio"

    return False, ""


def analizar_intencion(texto: str) -> str:
    """
    @brief Determina si el contenido de un correo es transaccional, promocional o neutro.
    @param texto Texto del correo a analizar.
    @return 'transaccional', 'promocional' o 'neutro'.
    """
    texto_lower = texto.lower()

    score_transaccional = sum(1 for p in PALABRAS_TRANSACCIONAL if p in texto_lower)
    score_promocional   = sum(1 for p in PALABRAS_PROMOCIONAL   if p in texto_lower)

    if score_transaccional > score_promocional and score_transaccional >= 2:
        return "transaccional"
    elif score_promocional > score_transaccional and score_promocional >= 2:
        return "promocional"
    return "neutro"


def preprocesar(texto: str) -> str:
    """
    @brief Normaliza y tokeniza texto para el vectorizador TF-IDF: minúsculas, URLs, montos, stopwords.
    @param texto Texto crudo del correo.
    @return Cadena de tokens limpios separados por espacios.
    """
    texto = texto.lower()
    texto = re.sub(r'http\S+|www\S+', ' url_enlace ', texto)
    texto = re.sub(r'\$[\d,\.]+',  ' monto_dinero ',  texto)
    texto = re.sub(r'\d{4}',       ' codigo_cuatro ',  texto)
    texto = re.sub(r'\d+',         ' numero ',         texto)
    texto = re.sub(r'[^\w\s]',     ' ',                texto)
    tokens = [t for t in texto.split() if t not in STOPWORDS_ES and len(t) > 2]
    return " ".join(tokens)


def _normalizar_busqueda(texto: str) -> str:
    """
    @brief Elimina tildes y convierte a minúsculas para comparaciones insensibles a acentos.
    @param texto Texto a normalizar.
    @return Texto sin tildes en minúsculas.
    """
    t = texto.lower()
    for a, b in (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ñ", "n"),
    ):
        t = t.replace(a, b)
    return t


def _es_estafa_coercion_alto_riesgo(texto: str) -> bool:
    """
    @brief Detecta patrones de estafa o coerción: solicitud de datos bancarios combinada con amenazas.
    @param texto Texto del correo a analizar.
    @return True si el correo coincide con un patrón de alto riesgo conocido.
    """
    t = _normalizar_busqueda(texto)

    pide_datos = any(
        p in t for p in (
            "datos bancarios",
            "datos de tu tarjeta",
            "datos de la tarjeta",
            "tarjeta de credito",
            "tarjeta de debito",
            "tarjetas de debito",
            "tarjetas de credito",
            "todas tus tarjetas",
            "clave del cajero",
            "pin de tu tarjeta",
            "cvv",
            "codigo de seguridad de tu tarjeta",
            "cuenta bancaria y clave",
            "compartes todos tus datos",
            "envia los datos de",
            "envia tu clave",
        )
    )
    amenaza = any(
        p in t for p in (
            "homicidio",
            "demanda",
            "denunciare",
            "te denunciare",
            "denuncia penal",
            "extorsion",
            "secuestro",
            "carcel",
            "multa",
            "evitar una posible",
            "consecuencias legales",
            "si no pagas",
            "pagar o",
            "testimonio falso",
        )
    )
    if pide_datos and amenaza:
        return True
    if any(p in t for p in (
        "actualice sus datos bancarios o su cuenta sera cancelada",
        "verifique su cuenta urgente ingrese su clave",
        "gano rd$",
        "gane rd$",
    )):
        return True
    return False


def clasificar(texto: str, modelo, spam_usr: list, ham_usr: list,
               remitente: str = "") -> dict:
    """
    @brief Clasifica un correo como SPAM, HAM o SOSPECHOSO usando cuatro capas: correcciones
           explícitas del usuario, reglas de estafa, dominios de confianza y modelo TF-IDF + Naive Bayes.
    @param texto     Contenido del correo a clasificar.
    @param modelo    Pipeline scikit-learn entrenado.
    @param spam_usr  Lista de palabras que el usuario marcó como indicadores de spam.
    @param ham_usr   Lista de palabras que el usuario marcó como indicadores de ham.
    @param remitente Campo 'From' del correo para extraer y verificar el dominio.
    @return Dict con clasificacion, confianza, prob_spam, prob_ham, ajustado y razon.
    """
    if not texto.strip():
        return {
            "clasificacion": "INDETERMINADO", "confianza": 0,
            "prob_spam": 0, "prob_ham": 0, "ajustado": False,
            "razon": "Mensaje vacío", "descripcion": ""
        }

    # Capa 0: las correcciones del usuario tienen prioridad absoluta sobre cualquier otra regla
    texto_l   = texto.lower()
    hits_ham  = sum(1 for w in ham_usr  if w in texto_l)
    hits_spam = sum(1 for w in spam_usr if w in texto_l)

    if hits_ham > 0 and hits_ham >= hits_spam:
        conf = min(98.0, 70.0 + hits_ham * 10.0)
        return {
            "clasificacion": "HAM",
            "confianza":     conf,
            "prob_spam":     round(100.0 - conf, 1),
            "prob_ham":      conf,
            "prob_spam_f":   round(100.0 - conf, 1),
            "prob_ham_f":    conf,
            "ajustado":      True,
            "razon":         f"Corrección del usuario ({hits_ham} palabra(s) HAM)",
            "descripcion":   "Encontré en este texto palabras que tú mismo identificaste como indicadores de un mensaje legítimo. Estas coincidencias tienen prioridad sobre cualquier análisis automático.",
        }
    if hits_spam > 0 and hits_spam > hits_ham:
        conf = min(98.0, 70.0 + hits_spam * 10.0)
        return {
            "clasificacion": "SPAM",
            "confianza":     conf,
            "prob_spam":     conf,
            "prob_ham":      round(100.0 - conf, 1),
            "prob_spam_f":   conf,
            "prob_ham_f":    round(100.0 - conf, 1),
            "ajustado":      True,
            "razon":         f"Corrección del usuario ({hits_spam} palabra(s) SPAM)",
            "descripcion":   "Encontré en este texto palabras que tú mismo marcaste como indicadores de spam. Estas coincidencias tienen prioridad sobre cualquier análisis automático.",
        }

    # Capa 1: detección de estafas de alto riesgo por patrones léxicos
    if _es_estafa_coercion_alto_riesgo(texto):
        return {
            "clasificacion": "SPAM",
            "confianza":     96.0,
            "prob_spam":     96.0,
            "prob_ham":      4.0,
            "prob_spam_f":   96.0,
            "prob_ham_f":    4.0,
            "ajustado":      False,
            "razon":         "Patrón de estafa o coerción (datos sensibles + amenaza)",
            "descripcion":   "El texto combina solicitudes de datos sensibles —tarjetas, claves o cuentas bancarias— con lenguaje de amenaza o coacción. Este patrón es característico de fraudes, estafas y mensajes de extorsión.",
        }

    # Capa 2: reglas por dominio confiable + análisis de intención
    dominio              = extraer_dominio(remitente)
    confiable, tipo_dom  = es_dominio_confianza(dominio)

    if confiable:
        intencion = analizar_intencion(texto)

        if intencion == "transaccional":
            return {
                "clasificacion": "HAM",
                "confianza":     98.0,
                "prob_spam":     2.0,
                "prob_ham":      98.0,
                "prob_spam_f":   2.0,
                "prob_ham_f":    98.0,
                "ajustado":      False,
                "razon":         f"Dominio de confianza ({tipo_dom}) + contenido transaccional",
                "descripcion":   "El remitente pertenece a un dominio verificado de confianza y el contenido corresponde a un mensaje transaccional legítimo: confirmaciones de movimientos, alertas de cuenta, códigos de verificación o recibos.",
            }
        elif intencion == "promocional":
            return {
                "clasificacion": "SOSPECHOSO",
                "confianza":     65.0,
                "prob_spam":     35.0,
                "prob_ham":      65.0,
                "prob_spam_f":   35.0,
                "prob_ham_f":    65.0,
                "ajustado":      False,
                "razon":         f"Dominio de confianza ({tipo_dom}) + contenido promocional",
                "descripcion":   "Aunque el remitente pertenece a un dominio confiable, el contenido tiene un marcado carácter promocional. Puede ser legítimo, pero conviene revisar antes de hacer clic en cualquier enlace u oferta.",
            }
        else:
            return {
                "clasificacion": "HAM",
                "confianza":     85.0,
                "prob_spam":     15.0,
                "prob_ham":      85.0,
                "prob_spam_f":   15.0,
                "prob_ham_f":    85.0,
                "ajustado":      False,
                "razon":         f"Dominio de confianza ({tipo_dom})",
                "descripcion":   "El remitente proviene de un dominio reconocido y confiable. No se detectaron señales de alerta en el contenido del mensaje.",
            }

    # Capa 3: predicción del modelo NLP con ajuste por palabras del usuario
    probs  = modelo.predict_proba([preprocesar(texto)])[0]
    clases = list(modelo.classes_)
    p_ham  = float(probs[clases.index("ham")])
    p_spam = float(probs[clases.index("spam")])

    aj_spam = min(0.45, hits_spam * 0.15)
    aj_ham  = min(0.45, hits_ham  * 0.15)

    p_spam_f = min(1.0, max(0.0, p_spam + aj_spam - aj_ham))
    p_ham_f  = 1.0 - p_spam_f
    ajustado = aj_spam > 0 or aj_ham > 0

    if   p_spam_f >= 0.55: clas = "SPAM";       conf = p_spam_f
    elif p_spam_f <= 0.45: clas = "HAM";        conf = p_ham_f
    else:                  clas = "SOSPECHOSO"; conf = max(p_spam_f, p_ham_f)

    _desc_nlp = {
        "SPAM":       "El análisis lingüístico detectó patrones asociados a mensajes no deseados: urgencia artificial, promesas exageradas o solicitudes inusuales típicas de correos fraudulentos.",
        "HAM":        "El texto presenta características propias de mensajes legítimos. No se detectaron patrones de fraude, urgencia injustificada ni solicitudes sospechosas.",
        "SOSPECHOSO": "El texto contiene señales mixtas: algunas características son propias de mensajes legítimos, pero otras coinciden con patrones de spam. Conviene verificar el remitente y el contexto antes de actuar.",
    }
    return {
        "clasificacion": clas,
        "confianza":     round(conf * 100, 1),
        "prob_spam":     round(p_spam * 100, 1),
        "prob_ham":      round(p_ham  * 100, 1),
        "prob_spam_f":   round(p_spam_f * 100, 1),
        "prob_ham_f":    round(p_ham_f  * 100, 1),
        "ajustado":      ajustado,
        "razon":         "Modelo NLP" + (" + correcciones del usuario" if ajustado else ""),
        "descripcion":   _desc_nlp.get(clas, ""),
    }


def _construir_pipeline() -> Pipeline:
    """
    @brief Construye el pipeline scikit-learn con TF-IDF bigramas y Naive Bayes multinomial.
    @return Pipeline listo para entrenar.
    """
    return Pipeline([
        ('tfidf', TfidfVectorizer(
            sublinear_tf=True,
            ngram_range=(1, 2),
            min_df=2,
            max_df=0.95,
            strip_accents='unicode'
        )),
        ('nb', MultinomialNB(alpha=0.1))
    ])


def _cargar_feedback_correos_desde_json():
    """
    @brief Carga los correos reales etiquetados por el usuario para incluirlos en el entrenamiento.
    @return Tupla (textos, etiquetas) con los ejemplos del historial de feedback.
    """
    if not FEEDBACK_CORREOS_FILE.exists():
        return [], []
    try:
        raw = json.loads(FEEDBACK_CORREOS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"  No se pudo leer feedback_correos.json: {e}")
        return [], []

    if isinstance(raw, dict) and "correos" in raw:
        items = raw["correos"]
    elif isinstance(raw, list):
        items = raw
    else:
        return [], []

    textos, etiquetas = [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        txt = str(item.get("texto_clasificar") or "").strip()
        lbl = str(item.get("etiqueta") or item.get("tipo") or "").strip().lower()
        if txt and lbl in ("spam", "ham"):
            textos.append(preprocesar(txt))
            etiquetas.append(lbl)

    if textos:
        logger.info(f"  Ejemplos de feedback de correos reales: {len(textos)}")
    return textos, etiquetas


def _cargar_desde_huggingface():
    """
    @brief Descarga múltiples datasets de HuggingFace (spam multilingüe, phishing, fraude) y los combina.
    @return Tupla (textos, etiquetas) con todos los datos ampliados.
    """
    textos, etiquetas = [], []

    # Dataset 1: SMS spam multilingüe (incluye español)
    try:
        logger.info("  [HF] Cargando SMS spam multilingüe...")
        ds = load_dataset("ashu0311/SMS_Spam_Multilingual_Collection_Dataset")
        for r in ds[list(ds.keys())[0]]:
            txt = str(r.get("text_es") or "").strip()
            lbl = str(r.get("labels")  or "").strip().lower()
            if txt and lbl in ["ham", "spam"]:
                textos.append(preprocesar(txt))
                etiquetas.append(lbl)
        logger.info(f"  [HF] SMS multilingüe: {len(textos)} ejemplos")
    except Exception as e:
        logger.warning(f"  [HF] SMS multilingüe no disponible: {e}")

    # Dataset 2: Phishing emails (en inglés, útil para patrones universales de fraude)
    n_antes = len(textos)
    try:
        logger.info("  [HF] Cargando dataset de phishing...")
        ds2 = load_dataset("cybersectony/phishing-email-detection-v2.4.1")
        split = list(ds2.keys())[0]
        for r in ds2[split]:
            txt = str(r.get("text") or r.get("email_text") or "").strip()[:600]
            lbl_raw = str(r.get("label") or r.get("label_num") or "").strip()
            if not txt:
                continue
            if lbl_raw in ("1", "phishing", "spam"):
                lbl = "spam"
            elif lbl_raw in ("0", "legitimate", "ham"):
                lbl = "ham"
            else:
                continue
            textos.append(preprocesar(txt))
            etiquetas.append(lbl)
        logger.info(f"  [HF] Phishing dataset: {len(textos) - n_antes} ejemplos")
    except Exception as e:
        logger.warning(f"  [HF] Phishing dataset no disponible: {e}")

    # Dataset 3: Email spam general
    n_antes = len(textos)
    try:
        logger.info("  [HF] Cargando dataset email spam general...")
        ds3 = load_dataset("mshenoda/email-spam")
        split = list(ds3.keys())[0]
        for r in ds3[split]:
            txt = str(r.get("text") or "").strip()[:600]
            lbl_raw = str(r.get("label") or "").strip().lower()
            if not txt:
                continue
            if lbl_raw in ("1", "spam"):
                lbl = "spam"
            elif lbl_raw in ("0", "ham", "not spam"):
                lbl = "ham"
            else:
                continue
            textos.append(preprocesar(txt))
            etiquetas.append(lbl)
        logger.info(f"  [HF] Email spam general: {len(textos) - n_antes} ejemplos")
    except Exception as e:
        logger.warning(f"  [HF] Email spam general no disponible: {e}")

    t2, e2 = _dataset_interno()
    textos.extend(t2)
    etiquetas.extend(e2)
    return textos, etiquetas


def entrenar_modelo(forzar: bool = False) -> tuple:
    """
    @brief Carga el modelo desde cache o entrena uno nuevo; lanza mejora con Hugging Face en background.
    @param forzar Si True, ignora el cache y reentrena aunque exista un modelo guardado.
    @return Tupla (modelo, accuracy) con el pipeline entrenado y su precisión en el set de prueba.
    """
    if not forzar and MODEL_CACHE.exists():
        logger.info("  Modelo cargado desde caché.")
        try:
            with open(MODEL_CACHE, "rb") as f:
                datos = pickle.load(f)
            return datos["modelo"], datos["accuracy"]
        except Exception:
            logger.warning("  pkl corrupto, reentrenando...")

    logger.info("  Entrenando con dataset interno (arranque rápido)...")
    textos_b, etiquetas_b = _dataset_interno()
    tf, ef = _cargar_feedback_correos_desde_json()
    textos_b.extend(tf)
    etiquetas_b.extend(ef)

    # Incorpora el dataset Enron si está disponible en disco
    from pathlib import Path as _P
    enron_path = _P(__file__).parent / "enron_dataset.csv"
    if enron_path.exists():
        try:
            import csv
            with open(enron_path, encoding='utf-8', errors='ignore') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    txt = str(row.get('text') or row.get('mensaje') or '').strip()
                    lbl = str(row.get('label') or row.get('etiqueta') or '').strip().lower()
                    if txt and lbl in ('spam','ham'):
                        textos_b.append(preprocesar(txt))
                        etiquetas_b.append(lbl)
            logger.info(f"  Dataset Enron cargado: {enron_path}")
        except Exception as e:
            logger.warning(f"  No se pudo cargar Enron dataset: {e}")

    X_train, X_test, y_train, y_test = train_test_split(
        textos_b, etiquetas_b, test_size=0.20, random_state=42, stratify=etiquetas_b
    )
    modelo_base = _construir_pipeline()
    modelo_base.fit(X_train, y_train)
    acc_base = accuracy_score(y_test, modelo_base.predict(X_test))
    logger.info(f"  Modelo base listo — {acc_base*100:.1f}% ({len(textos_b):,} ejemplos)")

    try:
        with open(MODEL_CACHE, "wb") as f:
            pickle.dump({"modelo": modelo_base, "accuracy": acc_base}, f)
    except Exception:
        pass

    # Mejora opcional en background con dataset ampliado de Hugging Face
    if HF_AVAILABLE:
        import threading as _t
        def _mejorar_con_hf():
            try:
                logger.info("  [HF] Descargando dataset ampliado en background...")
                textos_hf, etiquetas_hf = _cargar_desde_huggingface()
                tf2, ef2 = _cargar_feedback_correos_desde_json()
                textos_hf.extend(tf2)
                etiquetas_hf.extend(ef2)
                Xtr, Xte, ytr, yte = train_test_split(
                    textos_hf, etiquetas_hf, test_size=0.20, random_state=42, stratify=etiquetas_hf
                )
                m2  = _construir_pipeline()
                m2.fit(Xtr, ytr)
                a2  = accuracy_score(yte, m2.predict(Xte))
                logger.info(f"  [HF] Modelo mejorado — {a2*100:.1f}% ({len(textos_hf):,} ejemplos)")
                with open(MODEL_CACHE, "wb") as f:
                    pickle.dump({"modelo": m2, "accuracy": a2}, f)
            except Exception as e:
                logger.warning(f"  [HF] No se pudo mejorar el modelo: {e}")
        _t.Thread(target=_mejorar_con_hf, daemon=True).start()

    return modelo_base, acc_base


def _dataset_interno():
    """
    @brief Dataset base en español con ejemplos de 10 categorías HAM y 10 categorías SPAM.
    @return Tupla (textos_preprocesados, etiquetas) lista para entrenamiento.
    """

    # ── HAM ──────────────────────────────────────────────────────────────────

    ham_bancario = [
        "Su tarjeta terminada en 4521 fue utilizada por RD$ 2,350.00 en Supermercados Nacional el 12/04/2026",
        "Transferencia recibida de Juan Perez por RD$ 5,000.00 a su cuenta corriente 001-234567-8",
        "Su pago de RD$ 1,200.00 a Claro RD fue procesado exitosamente. Comprobante: TXN-98234",
        "Alerta de seguridad: Se detectó un inicio de sesión desde un nuevo dispositivo en su cuenta Banco Popular",
        "Su estado de cuenta del mes de marzo está disponible. Saldo disponible: RD$ 45,230.00",
        "Código de verificación para su transacción: 847293. No comparta este código con nadie",
        "Su solicitud de préstamo personal ha sido aprobada. Monto: RD$ 150,000.00 a 24 meses",
        "Recordatorio: Su cuota de préstamo vence el 15 de abril. Monto: RD$ 8,500.00",
        "Tarjeta de crédito: Pago mínimo de RD$ 3,200.00 vence el 20/04/2026",
        "Transacción declinada: Su tarjeta 4521 fue rechazada en Amazon por fondos insuficientes",
        "Su cuenta ha sido desbloqueada exitosamente tras verificación de identidad",
        "Actualización de sus datos de contacto registrada: nuevo número 809-555-1234",
        "Qik: Recibiste RD$ 500.00 de Maria Rodriguez. Tu saldo actual es RD$ 2,340.00",
        "BHD León: Su tarjeta fue usada en el exterior. Si no reconoce esta transacción llame al 809-243-3232",
        "Banreservas: Pago de servicios CAASD por RD$ 890.00 procesado. Referencia: 2024039821",
        "Scotiabank: Depósito de nómina recibido RD$ 35,000.00 empresa Grupo Leon Jimenes",
        "APAP: Su certificado financiero vence en 30 días. Monto: RD$ 200,000.00",
        "Visacard: Su pago de RD$ 4,500.00 fue recibido. Gracias por mantenerse al día",
        "Promerica: Apertura de cuenta de ahorros confirmada. Número de cuenta: 0045-89231",
        "Vimenca: Cambio de divisa procesado. Vendiste USD$ 200.00 a RD$ 11,800.00",
    ] * 7

    ham_seguridad_digital = [
        "Tu código de verificación de Google es 591847. No compartas este código",
        "Alguien intentó acceder a tu cuenta de Gmail desde Chrome en Windows. Si fuiste tú ignora este mensaje",
        "Tu suscripción de Netflix se renovó exitosamente por $15.99 USD el 12 de abril",
        "Recibo de Uber: Tu viaje del jueves por la noche costó RD$ 450.00",
        "Google Play: Compraste Minecraft por $6.99. Si no reconoces esta compra contacta soporte",
        "Apple ID: Tu contraseña fue cambiada el 12 de abril desde un iPhone en Santo Domingo",
        "Amazon: Tu pedido #112-3456789-0123456 ha sido enviado. Llega el 15 de abril",
        "Tu cuenta de Duolingo llevaba 30 días activa. Racha completada",
        "Microsoft: Se agregó un nuevo método de inicio de sesión a tu cuenta",
        "Spotify Premium: Pago de $9.99 procesado. Próximo cobro: 12 de mayo",
        "PayPal: Enviaste $50.00 USD a merchant@tienda.com",
        "Claro RD: Tu recarga de RD$ 200.00 fue aplicada exitosamente. Saldo: RD$ 245.00",
        "Altice: Tu factura del mes de abril por RD$ 2,100.00 está disponible",
        "WhatsApp: Tu código de verificación es 724-519. No lo compartas con nadie",
        "Dropbox: Alguien accedió a tus archivos desde un dispositivo nuevo en República Dominicana",
        "GitHub: Nueva clave SSH añadida a tu cuenta. Si no fuiste tú revoca el acceso ahora",
        "Zoom: Tu reunión del lunes a las 10am ha sido confirmada. ID: 842-901-337",
    ] * 7

    ham_personal = [
        # Mensajes formales y de coordinación
        "Hola, te confirmo la reunión del viernes a las 3pm en la oficina central",
        "Adjunto el informe mensual de ventas para tu revisión antes de la junta",
        "Buenos días, necesito que me envíes el contrato firmado antes del mediodía",
        "Te recordamos que mañana hay mantenimiento del sistema de 10pm a 2am",
        "Convocatoria: Asamblea ordinaria de accionistas el próximo 20 de abril",
        "Su cita médica está confirmada para el martes 16 de abril a las 9:00am",
        "Estimado cliente, su vehículo está listo para ser retirado del taller",
        "Hola primo, te mando los archivos que me pediste para la presentación del lunes",
        "Estimado, se le recuerda que el pago del alquiler vence el día 5 de cada mes",
        "Buenas tardes, ¿podemos reagendar la llamada para el miércoles por la tarde?",
        "Te escribo para informarte que cambié mi número de teléfono al 849-312-5678",
        "Feliz cumpleaños, que tengas un excelente día rodeado de tus seres queridos",
        # Mensajes casuales entre amigos
        "Oye, ¿nos reunimos el sábado para ver la película? Avísame si puedes ir",
        "Bro, ¿vas a la fiesta de Carlos el viernes? Dime para ir juntos y buscarte",
        "Hola amiga, hace mucho tiempo que no nos vemos. ¿Cómo estás? Cuéntame todo",
        "¿Qué vas a hacer este fin de semana? Podríamos salir a comer algo por ahí",
        "Oye, ¿viste el partido anoche? Estuvo increíble, el Licey ganó en la novena",
        "Sam, ¿te acuerdas de mí? Estudiamos juntos en el ITLA. Te vi en Instagram",
        "Hola, te invito a mi cumpleaños el próximo viernes. Espero que puedas venir",
        "¿Puedes traer palomitas esta noche? Vamos a ver una peli. Empieza a las 8pm",
        "Hace mucho que no hablamos, ¿cómo te ha ido? Cuéntame qué hay de nuevo",
        "Oye compa, ¿me puedes dar el teléfono del profe? Necesito hablar con él hoy",
        "¿Viste lo que pusieron en el grupo de WhatsApp? Dicen que mañana no hay clases",
        "Te cuento que me fue muy bien en el examen final, saqué un 95. Gracias por todo",
        "¿Qué tal si hacemos una videollamada esta semana para ponernos al día?",
        "Hola vecina, ¿sabe si mañana habrá agua? Estoy llenando el tanque por si acaso",
        "El grupo de la universidad se va a reunir el jueves a las 6pm. ¿Confirmas?",
        # Mensajes de familia
        "Mami, ya salí del trabajo. Paso por el supermercado y llego en unos 20 minutos",
        "Prima, te quiero mucho. Cuídate y nos vemos el domingo en casa de la abuela",
        "Hola amor, ¿ya llegaste bien a la casa? Avísame cuando puedas",
        "Ma, llegué bien. El viaje estuvo tranquilo. Te llamo mañana con más calma",
        "Hola tío, feliz día del padre. Gracias por todo lo que has hecho por nosotros",
        "Gordo, te extrañamos. Recupérate pronto que aquí te estamos esperando",
        "Mi amor, ya estoy en camino. ¿Quieres que traiga algo de comer para la cena?",
        "Papá, ¿cómo está la rodilla? Espero que el médico te haya dado buenas noticias",
        "Buenas noches familia. Que todos descansen y mañana nos vemos tempranito",
        "Hermano, ¿puedes pasar a recogerme en la UASD a las 5pm? Gracias de antemano",
        "Mariana, ¿qué dices si nos reunimos mañana en tu casa para hacer la tarea?",
        "Podemos hacer palomitas y beber refresco. Hace mucho tiempo que no nos vemos",
        "Disculpa que no te había contestado, estaba en clases todo el día. ¿Qué pasó?",
        "Hola compadre, ¿cómo está la familia? Aquí todos bien gracias a Dios. Saludos",
        "Buenas, ¿sabes si el profe canceló la clase de mañana? No vi el aviso por ningún lado",
    ] * 7

    ham_redes_sociales = [
        "Facebook: Carlos Méndez te envió una solicitud de amistad",
        "Instagram: Tu publicación recibió 47 me gusta en la última hora",
        "TikTok: Tu video alcanzó 1,000 reproducciones. Sigue así",
        "LinkedIn: Tienes 3 nuevas solicitudes de conexión de profesionales de tu sector",
        "Twitter: @usuario_rd mencionó tu cuenta en un tweet",
        "YouTube: Un nuevo comentario en tu video: gracias por el contenido, muy útil",
        "Facebook: Tienes un recuerdo de hace 3 años. Míralo y compártelo si quieres",
        "Instagram: @moda_rd comenzó a seguirte",
        "LinkedIn: Tu publicación sobre desarrollo web obtuvo 120 reacciones",
        "TikTok: Tienes 5 nuevos seguidores esta semana",
        "Facebook Groups: Hay 12 publicaciones nuevas en el grupo Desarrolladores RD",
        "Pinterest: 8 personas guardaron tu pin de recetas dominicanas esta semana",
        "Reddit: Tu comentario fue votado positivamente 34 veces en r/programming",
        "Twitch: El streamer que sigues está en vivo ahora mismo",
        "Discord: Tienes 4 mensajes sin leer en el servidor de tu comunidad",
    ] * 5

    ham_trabajo = [
        "RR.HH: Tu solicitud de vacaciones del 20 al 27 de abril fue aprobada",
        "Recursos Humanos: El depósito de quincena se realizará el viernes 15 de abril",
        "Tu evaluación de desempeño trimestral está disponible en el portal de empleados",
        "Convocatoria a capacitación obligatoria: Nuevas normas de cumplimiento el jueves 18 a las 9am",
        "Gerencia: La reunión de equipo del lunes se pospone al martes a las 11am",
        "IT Soporte: Tu nueva laptop corporativa está lista para retiro en el piso 3",
        "Nomina: Se procesó tu bono de productividad correspondiente al primer trimestre",
        "RR.HH: Tu contrato de trabajo ha sido renovado por un año adicional",
        "Oferta laboral confirmada: Analista de Datos, inicio el 2 de mayo, salario RD$ 75,000",
        "Tu solicitud de trabajo remoto los viernes fue aprobada a partir de la próxima semana",
        "Compañía: El seguro médico colectivo fue renovado, cubre dependientes directos",
        "RR.HH: Tus días de vacaciones acumulados son 15. Solicítalos antes del 31 de diciembre",
        "Agenda corporativa: Cena de fin de año el sábado 14 de diciembre en el Hotel Embajador",
        "Tu reporte de gastos del mes de marzo fue aprobado y reembolsado",
        "Felicitaciones: Fuiste seleccionado como empleado del mes de abril",
    ] * 5

    ham_ecommerce = [
        "MercadoLibre: Tu paquete fue entregado hoy a las 2:14pm. Califica tu experiencia",
        "Amazon: Tu pedido de auriculares inalámbricos fue enviado. Número de rastreo: 1Z999AA",
        "AliExpress: Tu pedido llegará entre el 18 y 25 de abril según el transportista",
        "Zara Online: Tu devolución de RD$ 3,200.00 fue procesada. Verás el reembolso en 5 días",
        "MercadoLibre: Alguien hizo una oferta por el artículo que estás vendiendo",
        "Amazon Prime: Tu período de prueba gratuito vence en 3 días",
        "Carrefour: Tu pedido de supermercado en línea fue confirmado para entrega mañana",
        "Claro Tienda: Tu nuevo iPhone 15 está listo para retirar en la tienda de Sambil",
        "Netflix: Se añadió un nuevo dispositivo a tu cuenta. Si no fuiste tú, cambia tu clave",
        "Booking.com: Tu reserva en Hotel Barceló fue confirmada para el 15 de mayo",
        "Airbnb: Tu anfitrión confirmó tu reserva en Cabarete del 20 al 23 de junio",
        "DiDi: Califica tu viaje reciente y ayuda a mejorar el servicio",
    ] * 5

    ham_educacion = [
        "ITLA: Tu matrícula para el cuatrimestre mayo-agosto fue procesada exitosamente",
        "UASD: Tienes calificaciones pendientes de revisar en el portal estudiantil",
        "PUCMM: Tu beca fue renovada para el período académico 2026-2027",
        "INTEC: La defensa de tu tesis fue programada para el 28 de mayo a las 10am",
        "Ministerio de Educación: Los resultados de las pruebas nacionales están disponibles",
        "O&M: El horario de clases del próximo cuatrimestre ya está publicado",
        "Biblioteca UASD: El libro que solicitaste está disponible para retiro",
        "Coursera: Completaste el 75% del curso de Python. Sigue así para obtener tu certificado",
        "ITLA: El taller de inteligencia artificial es el sábado 19 a las 9am, asistencia obligatoria",
        "Udemy: Tu certificado de Desarrollo Web está listo para descargar",
        "edX: El curso de ciencia de datos comienza el lunes. Accede al material de introducción",
        "Google Digital: Felicitaciones, obtuviste la certificación Google Analytics",
    ] * 4

    ham_salud = [
        "Centro Médico UCE: Su cita con el Dr. Ramírez es el martes 16 a las 10:30am",
        "Laboratorio Referencia: Sus resultados de exámenes están listos para ser retirados",
        "ARS Humano: Su reclamación de reembolso por RD$ 8,400.00 fue aprobada",
        "Farmacia Carol: Su medicamento Metformina 500mg está disponible para retiro",
        "Dr. Peña Consultorio: Recordatorio de cita de seguimiento mañana a las 3pm",
        "ARS Universal: Su plan de salud fue renovado. Nueva tarjeta disponible en sucursales",
        "Hospital General Plaza: Sus resultados de imagen (rayos X) están disponibles en línea",
        "Clínica Abreu: Le recordamos su vacuna de refuerzo contra el COVID programada para el viernes",
        "ARS Reservas: La cirugía del 22 de abril fue preautorizada. Lleve este número: PRE-44821",
        "Nutricionista Dra. López: Su plan alimenticio actualizado fue enviado a su correo",
    ] * 5

    ham_gobierno = [
        "DGII: Su declaración jurada de impuestos del 2025 fue recibida correctamente",
        "TSS: Su cotización de seguridad social del mes de marzo fue procesada",
        "JCE: Su cédula de identidad está lista para ser retirada en la oficina de su demarcación",
        "Dirección General de Pasaportes: Su pasaporte está listo. Pase a retirarlo con su recibo",
        "DIGESETT: Infracciones de tránsito registradas a su nombre: 0. Récord limpio",
        "Ministerio de Trabajo: Su solicitud de certificado de trabajo fue aprobada",
        "INDOTEL: Su queja contra el proveedor de internet fue recibida. Referencia: QJ-20241",
        "Pro Consumidor: Su denuncia fue tramitada. Le notificaremos el resultado en 15 días",
        "Superintendencia de Bancos: Aviso de nueva regulación para tarjetas de crédito vigente desde mayo",
        "Ministerio de Salud: Campaña de vacunación gratuita disponible en todos los centros de salud",
        "SIUBEN: Su hogar fue actualizado en el registro de beneficiarios sociales",
        "Ayuntamiento SDN: Su solicitud de permiso de construcción fue aprobada. Ref: PC-2026-0392",
    ] * 4

    ham_noticias = [
        "El Listín Diario: Resumen de las noticias más importantes del día en República Dominicana",
        "Diario Libre: Economía dominicana creció 5.2% en el primer trimestre del año",
        "ESPN Deportes: El Licey venció al Escogido 4-2 en el juego de apertura de la temporada",
        "Bloomberg: Los mercados bursátiles cerraron al alza impulsados por datos de empleo",
        "TechCrunch: Apple anunció nuevos modelos de iPhone para septiembre con mejoras en IA",
        "National Geographic: Descubren nueva especie de rana en la cordillera central dominicana",
        "CNN en Español: Cumbre de líderes latinoamericanos aborda migración y cambio climático",
        "Forbes: Las 10 empresas más innovadoras de Latinoamérica en 2026",
        "Acento.com.do: El Banco Central mantiene tasa de interés estable para el segundo trimestre",
        "El País: Avances en inteligencia artificial transforman el mercado laboral en Latinoamérica",
    ] * 4

    # ── SPAM ─────────────────────────────────────────────────────────────────

    spam_phishing_bancario = [
        "URGENTE: Su cuenta bancaria será suspendida. Verifique ahora haciendo clic aquí inmediatamente",
        "Su tarjeta de crédito ha sido bloqueada por actividad sospechosa llame ya 1-800-FRAUDE",
        "Ganó RD$ 500,000 en el sorteo del Banco Central. Para reclamar envíe sus datos personales",
        "Actualice sus datos bancarios o su cuenta será cancelada en 24 horas haga clic aquí",
        "El banco le informa que necesitamos verificar su cuenta urgente ingrese su clave",
        "Felicidades su préstamo fue pre-aprobado sin requisitos ni buró de crédito llame hoy",
        "ALERTA: Movimiento sospechoso en su cuenta. Confirme sus datos en este enlace urgente",
        "Su cuenta PayPal fue limitada verifique su identidad haciendo clic en el siguiente enlace",
        "Estimado cliente su cuenta será desactivada si no actualiza la información de inmediato",
        "Banco Popular le informa que su cuenta está en riesgo ingrese ahora sus datos para protegerla",
        "Qik: Recibiste una transferencia de RD$ 25,000 haz clic para aceptarla antes de que expire",
        "BHD León urgente: detectamos fraude en su tarjeta confirme número completo y cvv aquí",
    ] * 9

    spam_general = [
        "Gana dinero fácil desde casa sin inversión trabajando solo 2 horas diarias garantizado",
        "FELICIDADES fue seleccionado ganador de un iPhone 15 haga clic para reclamar su premio gratis",
        "Inversión en criptomonedas con rendimiento garantizado del 300% en 30 días regístrese ya",
        "Oferta exclusiva solo hoy 90% de descuento en todos los productos compra ahora antes que se agoten",
        "Conoce personas solteras en tu área gratis regístrate sin costo y empieza a chatear ahora",
        "Medicamentos sin receta envío discreto a domicilio precio especial descuento exclusivo",
        "Su computadora tiene virus llame a soporte técnico ahora mismo número gratuito urgente",
        "Lotería nacional ganador verificar premio llamar urgente número ganador seleccionado",
        "Oportunidad de negocio multinivel gane comisiones ilimitadas reclute amigos ya empiece",
        "Promo especial casino online 100 giros gratis deposita y duplica tu dinero hoy mismo",
        "Baje de peso en 7 días sin dieta sin ejercicio pastilla milagrosa natural garantizado",
        "Trabajo desde casa empacando productos gana RD$ 15,000 semanales llame inscríbase hoy",
        "Eres el visitante número un millón de esta página haz clic para reclamar tu recompensa",
        "Accede a miles de películas y series gratis sin pagar nunca descarga la app ahora mismo",
    ] * 8

    spam_extorsion = [
        "Tengo acceso a tu cámara y grabé imágenes comprometedoras tuyas. Paga o las publico",
        "Hackeé tu dispositivo hace semanas y tengo todo tu historial de navegación privado págame",
        "Sé dónde vives y lo que haces cada día. Deposita RD$ 50,000 o hablaré con tu familia",
        "Tus contraseñas han sido comprometidas tengo acceso a tus cuentas págame en Bitcoin ahora",
        "Tengo fotos tuyas en situaciones comprometedoras. Transfiere dinero o las envío a tus contactos",
        "He infectado tu teléfono con spyware y grabé todo lo que haces. Paga para que lo elimine",
        "Tu información personal está en mi poder. Si no pagas la publicaré en la dark web",
        "Conozco tu dirección exacta y la de tu familia. Paga o las consecuencias serán graves",
        "Grabé tu pantalla durante meses tengo evidencia de todo lo que hiciste págame ahora mismo",
        "Si no transfieres el monto indicado publicaré tus mensajes privados y fotos comprometedoras",
        "Tengo acceso a tu correo desde hace meses sé todos tus secretos deposita para que lo cierre",
        "Compré tus datos en la dark web y tengo acceso total a tu vida digital paga para borrarlo",
        "Tu webcam fue hackeada y tengo grabaciones íntimas pages o las distribuyo a tus contactos",
        "Soy hacker tengo tus contraseñas de todas tus redes sociales y cuentas banco págame ya",
        "Enviaré este archivo a todos tus contactos si no depositas RD$ 30,000 en las próximas horas",
    ] * 8

    spam_hackers = [
        "Tu cuenta de Gmail fue comprometida haz clic aquí para recuperar el acceso de inmediato",
        "Detectamos actividad inusual descarga esta herramienta para proteger tu dispositivo ahora",
        "Tu contraseña fue filtrada en una brecha de seguridad actualízala ahora en este enlace",
        "Virus detectado en tu teléfono descarga este antivirus gratuito para eliminarlo ya",
        "Tu IP fue registrada accediendo a contenido ilegal. Paga la multa para evitar consecuencias",
        "Tu cuenta de Instagram fue hackeada ingresa aquí para recuperarla antes de que la pierdan",
        "Alerta de seguridad crítica: tu dispositivo está infectado con ransomware llama ahora",
        "Tus datos de inicio de sesión fueron robados en una filtración masiva actúa inmediatamente",
        "Hemos detectado que alguien más usa tu cuenta descarga la actualización de seguridad urgente",
        "Tu número de cédula está siendo usado para fraudes verifica tu identidad en este portal",
        "Sistema comprometido tu teléfono envía datos a servidores externos descarga el parche ya",
        "Fotos y documentos de tu dispositivo fueron accedidos remotamente paga para recuperar acceso",
        "Tu router fue hackeado todos tus datos están en riesgo llama al soporte técnico urgente",
        "Detectamos inicio de sesión desde Rusia en tu cuenta de WhatsApp verifica aquí",
        "Malware instalado silenciosamente en tu equipo la semana pasada paga para su eliminación",
    ] * 8

    spam_fraude_laboral = [
        "Oferta de trabajo desde casa gana USD$ 500 diarios sin experiencia ni título universitario",
        "Empresa internacional busca representantes en RD comisiones del 40% sin horario fijo",
        "Trabajo de modelo publicitario para redes sociales gana RD$ 80,000 al mes sin experiencia",
        "Contratamos encuestadores en línea gana RD$ 2,500 por encuesta completada desde tu casa",
        "Empleo urgente en crucero de lujo sin experiencia salario USD$ 3,000 mensuales aplica ya",
        "Digitadores de datos para empresa en EE.UU. gana USD$ 25 por hora desde República Dominicana",
        "Asistente virtual para celebridad gana RD$ 120,000 al mes trabajo 100% remoto aplica hoy",
        "Reclutamos distribuidores de productos naturales gana sin límite desde tu teléfono celular",
        "Oferta real: empresa canadiense paga USD$ 800 semanales por revisar correos electrónicos",
        "Trabajo de mystery shopper compra en tiendas y te reembolsamos más una comisión generosa",
        "Influencer pagado busca personas para reseñar productos en redes sociales sin seguidores",
        "Gana dinero tomando fotos con tu celular empresa paga USD$ 50 por cada foto aprobada",
    ] * 7

    spam_fraude_romantico = [
        "Hola soy militar de EE.UU. en misión en Africa busco una persona sincera con quien compartir",
        "Soy viuda de un empresario con una gran herencia necesito ayuda para transferir los fondos",
        "Conocí tu perfil y me enamoré necesito que me ayudes a salir del país con mi fortuna",
        "Soy doctora en misión humanitaria enamorada de ti necesito dinero para el pasaje de regreso",
        "Tengo USD$ 4 millones bloqueados en el banco necesito un socio de confianza para liberarlos",
        "Me robaron la cartera en España y necesito que me prestes dinero para el vuelo de regreso",
        "Somos almas gemelas nunca sentí esto por nadie necesito solo RD$ 15,000 para visitarte",
        "Soy ingeniero trabajando en plataforma petrolera offshore busco amor verdadero envíame dinero",
        "Mi hija está en el hospital necesito que me prestes dinero urgente te devuelvo con intereses",
        "Ganamos juntos la lotería internacional para cobrar necesitas depositar los impuestos primero",
        "Quiero casarme contigo pero necesito dinero para los papeles de migración ayúdame por favor",
        "Tengo sentimientos profundos por ti solo necesito un pequeño préstamo para la emergencia médica",
    ] * 5

    spam_fraude_inversion = [
        "Invierte en Bitcoin y duplica tu dinero en 48 horas garantizado por expertos financieros",
        "Robot de trading automático genera USD$ 500 diarios sin que hagas nada regístrate gratis",
        "Sistema de apuestas deportivas infalible ganamos el 95% de las predicciones únete ahora",
        "Proyecto inmobiliario en metaverso inversión mínima de USD$ 100 gana USD$ 5,000 al mes",
        "Club de inversión exclusivo solo 100 miembros rendimiento garantizado del 50% mensual",
        "Copy trading copia las operaciones de traders millonarios gana sin saber nada del mercado",
        "NFT que se revaloriza 10x en 30 días oportunidad única entra antes de que cierre el cupo",
        "Forex señales VIP 98% de efectividad suscripción de prueba gratis por 7 días únete hoy",
        "Pirámide de ahorro solidario todos ganan únete con RD$ 5,000 y recibe RD$ 50,000 en un mes",
        "Criptomoneda nueva antes del lanzamiento público invierte ahora y multiplica tu capital",
        "Plan de ahorro multinivel cada persona que refieras te genera ingresos pasivos de por vida",
        "Empresa de inversión en oro con sede en Suiza rendimiento mensual del 8% comprobado únete",
    ] * 7

    spam_redes_falsas = [
        "Compra 10,000 seguidores reales en Instagram solo USD$ 15 entrega en 24 horas garantizado",
        "5,000 likes en tu publicación de Facebook por solo RD$ 500 pago por transferencia",
        "Aumenta tus visitas en TikTok con nuestro bot 100,000 views por solo USD$ 25",
        "Hackeamos cualquier cuenta de Instagram o Facebook pago contra entrega resultados reales",
        "Recuperamos cuentas hackeadas de WhatsApp o redes sociales pago al confirmar resultado",
        "Vendo cuentas de Instagram verificadas con miles de seguidores precios desde USD$ 50",
        "Servicio de reseñas positivas para tu negocio en Google Maps 50 reseñas por RD$ 3,000",
        "Monetiza tu canal de YouTube comprando suscriptores reales cumple los requisitos ya",
        "Aumenta tu engagement en redes sociales con comentarios reales de personas reales hoy",
        "Falsifica seguidores para parecer influencer y cobrar a marcas sin invertir tiempo",
    ] * 6

    spam_gobierno_falso = [
        "DGII: Tiene una deuda pendiente de RD$ 45,230 debe pagar hoy o enfrentará cargos legales",
        "TSS urgente: su cuenta de seguridad social fue suspendida verifique sus datos ahora",
        "JCE: Su cédula de identidad fue reportada como perdida actualice sus datos en este enlace",
        "Ministerio de Trabajo: Tiene una demanda laboral pendiente preséntese o pague la multa",
        "Policía Nacional: Tiene una citación judicial urgente presencia obligatoria mañana a las 8am",
        "Hacienda: Declaró impuestos incorrectamente debe un reintegro o enfrenta proceso penal",
        "Migración RD: Su estatus migratorio presenta irregularidades regule su situación hoy mismo",
        "Procuraduría: Fue mencionado en una investigación penal contacte a su abogado urgente",
        "DIGESETT: Tiene 5 multas de tránsito impagadas su licencia será suspendida mañana",
        "Superintendencia de Bancos falsa: su cuenta bancaria fue reportada por lavado de dinero",
        "INDOTEL: Su línea telefónica será suspendida por uso inapropiado actualice sus datos hoy",
        "Intendencia de Aduanas: Tiene un paquete retenido pague los aranceles para liberarlo urgente",
    ] * 7

    spam_amenazas = [
        "Te voy a matar a ti y a tu familia si no me pagas lo que me debes tienes 24 horas",
        "Sé dónde estudian tus hijos más te vale pagar o les va a pasar algo muy malo",
        "Tengo hombres vigilando tu casa si no cooperas esto terminará muy mal para todos",
        "Eres un objetivo confirmado tienes una semana para salir del país o sufrirás las consecuencias",
        "El cobro de esta deuda será por las buenas o por las malas la decisión es tuya",
        "No te dormir tranquilo sabiendo lo que sé de ti mejor página o las cosas se complican",
        "Última advertencia deposita el dinero en esta cuenta o publicamos todo lo que sabemos",
        "Matones contratados para hacerte daño pueden cancelarse si pagas antes del domingo",
        "Grupo de extorsión: hemos tomado control de tu información pages o se la damos a tus jefes",
        "Somos peligrosos y sabemos dónde encontrarte páganos o verás lo que pasa con tu negocio",
        "Tengo fotos tuyas con menores de edad falsificadas las publicaré si no me depositas ya",
        "Tu empresa será destruida reputacionalmente si no pagas la tarifa de protección mensual",
    ] * 8

    # ── Combinar y devolver ───────────────────────────────────────────────────
    todos_ham = (
        ham_bancario + ham_seguridad_digital + ham_personal +
        ham_redes_sociales + ham_trabajo + ham_ecommerce +
        ham_educacion + ham_salud + ham_gobierno + ham_noticias
    )
    todos_spam = (
        spam_phishing_bancario + spam_general + spam_extorsion +
        spam_hackers + spam_fraude_laboral + spam_fraude_romantico +
        spam_fraude_inversion + spam_redes_falsas + spam_gobierno_falso +
        spam_amenazas
    )

    textos    = [preprocesar(t) for t in todos_ham + todos_spam]
    etiquetas = ["ham"] * len(todos_ham) + ["spam"] * len(todos_spam)
    return textos, etiquetas
