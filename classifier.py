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

MODEL_CACHE = Path(__file__).parent / "modelo_spam.pkl"
FEEDBACK_CORREOS_FILE = Path(__file__).parent / "feedback_correos.json"

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

PALABRAS_PROMOCIONAL = {
    "oferta", "promocion", "descuento", "gratis", "gana", "premio",
    "exclusivo", "especial", "limitado", "ahorra", "rebaja",
    "porcentaje de descuento", "black friday", "cyber monday",
    "no te pierdas", "aprovecha", "solo por hoy", "ultimas unidades",
    "haz clic", "click aqui", "ver oferta", "compra ahora",
    "registrate", "suscribete",
}

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
    if not remitente:
        return ""
    match = re.search(r'<([^>]+)>', remitente)
    email = match.group(1) if match else remitente.strip()
    if "@" in email:
        dominio = email.split("@")[-1].lower().strip()
        return dominio
    return ""


def es_dominio_confianza(dominio: str) -> tuple:
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
    texto_lower = texto.lower()

    score_transaccional = sum(1 for p in PALABRAS_TRANSACCIONAL if p in texto_lower)
    score_promocional   = sum(1 for p in PALABRAS_PROMOCIONAL   if p in texto_lower)

    if score_transaccional > score_promocional and score_transaccional >= 2:
        return "transaccional"
    elif score_promocional > score_transaccional and score_promocional >= 2:
        return "promocional"
    return "neutro"


def preprocesar(texto: str) -> str:
    texto = texto.lower()
    texto = re.sub(r'http\S+|www\S+', ' url_enlace ', texto)
    texto = re.sub(r'\$[\d,\.]+',  ' monto_dinero ',  texto)
    texto = re.sub(r'\d{4}',       ' codigo_cuatro ',  texto)
    texto = re.sub(r'\d+',         ' numero ',         texto)
    texto = re.sub(r'[^\w\s]',     ' ',                texto)
    tokens = [t for t in texto.split() if t not in STOPWORDS_ES and len(t) > 2]
    return " ".join(tokens)


def _normalizar_busqueda(texto: str) -> str:
    t = texto.lower()
    for a, b in (
        ("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
        ("ñ", "n"),
    ):
        t = t.replace(a, b)
    return t


def _es_estafa_coercion_alto_riesgo(texto: str) -> bool:
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
    if not texto.strip():
        return {
            "clasificacion": "INDETERMINADO", "confianza": 0,
            "prob_spam": 0, "prob_ham": 0, "ajustado": False,
            "razon": "Mensaje vacío"
        }

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
        }

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
                "razon":         f"Dominio de confianza ({tipo_dom}) + contenido transaccional"
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
                "razon":         f"Dominio de confianza ({tipo_dom}) + contenido promocional"
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
                "razon":         f"Dominio de confianza ({tipo_dom})"
            }

    probs  = modelo.predict_proba([preprocesar(texto)])[0]
    clases = list(modelo.classes_)
    p_ham  = float(probs[clases.index("ham")])
    p_spam = float(probs[clases.index("spam")])

    texto_l = texto.lower()
    aj_spam = min(0.45, sum(0.15 for w in spam_usr if w in texto_l))
    aj_ham  = min(0.45, sum(0.15 for w in ham_usr  if w in texto_l))

    p_spam_f = min(1.0, max(0.0, p_spam + aj_spam - aj_ham))
    p_ham_f  = 1.0 - p_spam_f
    ajustado = aj_spam > 0 or aj_ham > 0

    if   p_spam_f >= 0.55: clas = "SPAM";       conf = p_spam_f
    elif p_spam_f <= 0.45: clas = "HAM";        conf = p_ham_f
    else:                  clas = "SOSPECHOSO"; conf = max(p_spam_f, p_ham_f)

    return {
        "clasificacion": clas,
        "confianza":     round(conf * 100, 1),
        "prob_spam":     round(p_spam * 100, 1),
        "prob_ham":      round(p_ham  * 100, 1),
        "prob_spam_f":   round(p_spam_f * 100, 1),
        "prob_ham_f":    round(p_ham_f  * 100, 1),
        "ajustado":      ajustado,
        "razon":         "Modelo NLP" + (" + correcciones del usuario" if ajustado else "")
    }


def _construir_pipeline() -> Pipeline:
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
    logger.info("  Descargando dataset desde Hugging Face...")
    dataset = load_dataset("ashu0311/SMS_Spam_Multilingual_Collection_Dataset")
    datos   = dataset[list(dataset.keys())[0]]

    textos, etiquetas = [], []
    for r in datos:
        txt = str(r.get("text_es") or "").strip()
        lbl = str(r.get("labels")  or "").strip().lower()
        if txt and lbl in ["ham", "spam"]:
            textos.append(preprocesar(txt))
            etiquetas.append(lbl)

    t2, e2 = _dataset_interno()
    return textos + t2, etiquetas + e2


def entrenar_modelo(forzar: bool = False) -> tuple:
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
    ] * 8

    ham_seguridad = [
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
    ] * 8

    ham_personal = [
        "Hola, te confirmo la reunión del viernes a las 3pm en la oficina central",
        "Adjunto el informe mensual de ventas para tu revisión antes de la junta",
        "Buenos días, necesito que me envíes el contrato firmado antes del mediodía",
        "Te recordamos que mañana hay mantenimiento del sistema de 10pm a 2am",
        "Convocatoria: Asamblea ordinaria de accionistas el próximo 20 de abril",
        "Su cita médica está confirmada para el martes 16 de abril a las 9:00am",
        "Estimado cliente, su vehículo está listo para ser retirado del taller",
    ] * 5

    spam_phishing = [
        "URGENTE: Su cuenta bancaria será suspendida. Verifique ahora haciendo clic aquí inmediatamente",
        "Su tarjeta de crédito ha sido bloqueada por actividad sospechosa llame ya 1-800-FRAUDE",
        "Ganó RD$ 500,000 en el sorteo del Banco Central. Para reclamar envíe sus datos personales",
        "Actualice sus datos bancarios o su cuenta será cancelada en 24 horas haga clic aquí",
        "El banco le informa que necesitamos verificar su cuenta urgente ingrese su clave",
        "Felicidades su préstamo fue pre-aprobado sin requisitos ni buró de crédito llame hoy",
        "ALERTA: Movimiento sospechoso en su cuenta. Confirme sus datos en este enlace urgente",
        "Su cuenta Paypal fue limitada verifique su identidad haciendo clic en el siguiente enlace",
        "Estimado cliente su cuenta será desactivada si no actualiza la información de inmediato",
    ] * 10

    spam_general = [
        "Gana dinero fácil desde casa sin inversión trabajando solo 2 horas diarias garantizado",
        "FELICIDADES fue seleccionado ganador de un iPhone 15 haga clic para reclamar su premio gratis",
        "Inversion en criptomonedas con rendimiento garantizado del 300% en 30 dias registrese ya",
        "Oferta exclusiva solo hoy 90% de descuento en todos los productos compra ahora antes que se agoten",
        "Conoce personas solteras en tu area gratis registrate sin costo y empieza a chatear ahora",
        "Medicamentos sin receta Viagra Cialis envio discreto a domicilio precio especial descuento",
        "Su computadora tiene virus llame a soporte tecnico ahora mismo numero gratuito urgente",
        "Loteria nacional ganador verificar premio llamar urgente numero ganador seleccionado",
        "Oportunidad de negocio multinivel gane comisiones ilimitadas reclute amigos ya empiece",
        "Promo especial casino online 100 giros gratis deposita y duplica tu dinero hoy mismo",
        "Baje de peso en 7 dias sin dieta sin ejercicio pastilla milagrosa natural garantizado",
        "Trabajo desde casa empacando productos gana RD$ 15000 semanales llame inscribase hoy",
    ] * 10

    todos_ham  = ham_bancario + ham_seguridad + ham_personal
    todos_spam = spam_phishing + spam_general

    textos    = [preprocesar(t) for t in todos_ham + todos_spam]
    etiquetas = ["ham"] * len(todos_ham) + ["spam"] * len(todos_spam)
    return textos, etiquetas
