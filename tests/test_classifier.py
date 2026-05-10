"""
@file tests/test_classifier.py
@brief Tests unitarios e integración del motor de clasificación de Ilico.

Cubre las cuatro capas del clasificador:
  Capa 0 — Correcciones del usuario
  Capa 1 — Patrones de estafa/coerción
  Capa 2 — Reglas de dominio confiable
  Capa 3 — Modelo ML (SGDClassifier + TF-IDF)

Ejecutar con: pytest tests/
"""
import pytest
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier

from classifier import (
    clasificar,
    preprocesar,
    extraer_dominio,
    es_dominio_confianza,
    analizar_intencion,
    _es_estafa_coercion_alto_riesgo,
    _metricas_por_clase,
)


# ---------------------------------------------------------------------------
# Fixture: modelo mínimo entrenado en ejemplos básicos (rápido, sin HF)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def modelo():
    """Pipeline mínimo entrenado con ejemplos representativos de cada clase."""
    spam_samples = [
        "gana dinero fácil desde casa sin inversión garantizado gratis",
        "urgente su cuenta bancaria será suspendida verifique ahora clic aquí",
        "felicidades ganó un iPhone haga clic para reclamar su premio",
        "inversión en criptomonedas rendimiento garantizado 300 por ciento",
        "tengo acceso a tu cámara págame bitcoin o publico las fotos",
        "oferta exclusiva solo hoy 90 por ciento de descuento compra ahora",
        "trabajo desde casa gana 15000 semanales sin experiencia llame ya",
        "virus detectado en tu teléfono descarga este antivirus gratis urgente",
        "tu contraseña fue filtrada actualízala ahora en este enlace urgente",
        "robot trading automático genera 500 diarios sin hacer nada regístrate",
    ] * 30

    ham_samples = [
        "su tarjeta terminada en 4521 fue utilizada por RD 2350 en supermercado",
        "transferencia recibida de Juan Pérez por RD 5000 a su cuenta corriente",
        "hola cómo estás nos vemos el viernes para almorzar en el restaurante",
        "tu código de verificación de Google es 591847 no lo compartas",
        "reunión del equipo reprogramada para el martes a las 11am confirmado",
        "su cita médica está confirmada para el martes 16 a las 9am",
        "recibo de Uber tu viaje costó RD 450 califica tu experiencia",
        "hola primo te mando los archivos que pediste para la presentación",
        "estado de cuenta del mes de marzo disponible saldo 45230",
        "tu suscripción de Netflix fue renovada exitosamente gracias",
    ] * 30

    textos    = [preprocesar(t) for t in spam_samples + ham_samples]
    etiquetas = ["spam"] * len(spam_samples) + ["ham"] * len(ham_samples)

    m = Pipeline([
        ("tfidf", TfidfVectorizer(sublinear_tf=True, ngram_range=(1, 2), min_df=1)),
        ("clf",   SGDClassifier(loss="modified_huber", random_state=42,
                                max_iter=300, class_weight="balanced")),
    ])
    m.fit(textos, etiquetas)
    return m


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

class TestExtraerDominio:
    def test_formato_con_angulos(self):
        assert extraer_dominio("Banco Popular <noreply@bancopopular.com.do>") == "bancopopular.com.do"

    def test_email_directo(self):
        assert extraer_dominio("user@gmail.com") == "gmail.com"

    def test_vacio(self):
        assert extraer_dominio("") == ""

    def test_sin_arroba(self):
        assert extraer_dominio("nombre sin email") == ""

    def test_subdominio(self):
        assert extraer_dominio("Alerts <alerts.security@accounts.google.com>") == "accounts.google.com"


class TestEsDominioConfianza:
    def test_banco_dominicano(self):
        ok, tipo = es_dominio_confianza("bancopopular.com.do")
        assert ok is True and tipo == "banco"

    def test_servicio_global(self):
        ok, tipo = es_dominio_confianza("google.com")
        assert ok is True and tipo == "servicio"

    def test_dominio_desconocido(self):
        ok, tipo = es_dominio_confianza("estafa-falsa.com")
        assert ok is False and tipo == ""

    def test_vacio(self):
        ok, _ = es_dominio_confianza("")
        assert ok is False

    def test_subdominio_banco(self):
        ok, tipo = es_dominio_confianza("notificaciones.banreservas.com.do")
        assert ok is True and tipo == "banco"


class TestAnalizarIntencion:
    def test_transaccional(self):
        texto = "Su tarjeta fue utilizada por RD 2000. Comprobante de transferencia"
        assert analizar_intencion(texto) == "transaccional"

    def test_promocional(self):
        texto = "Oferta exclusiva descuento especial solo hoy aprovecha ya"
        assert analizar_intencion(texto) == "promocional"

    def test_neutro(self):
        texto = "Hola, cómo estás, espero que bien."
        assert analizar_intencion(texto) == "neutro"


class TestEstafaCoercion:
    def test_datos_bancarios_con_amenaza(self):
        texto = "Envía datos de tu tarjeta de crédito o enfrentarás consecuencias legales"
        assert _es_estafa_coercion_alto_riesgo(texto) is True

    def test_cvv_con_demanda(self):
        texto = "Necesito el CVV de tu tarjeta o te denunciaré ante las autoridades"
        assert _es_estafa_coercion_alto_riesgo(texto) is True

    def test_sin_amenaza(self):
        texto = "Por favor envía los datos de tu tarjeta para completar la compra"
        assert _es_estafa_coercion_alto_riesgo(texto) is False

    def test_sin_datos_bancarios(self):
        texto = "Si no pagas te llevaré a juicio por daños y perjuicios"
        assert _es_estafa_coercion_alto_riesgo(texto) is False


class TestPreprocesar:
    def test_normaliza_a_minusculas(self):
        # El texto debe quedar en minúsculas (las mayúsculas también generan el token de feature,
        # pero la palabra base se normaliza igual)
        resultado = preprocesar("Urgente")
        assert "urgente" in resultado

    def test_elimina_urls(self):
        resultado = preprocesar("visita http://ejemplo.com para más info")
        assert "http" not in resultado
        assert "url_enlace" in resultado

    def test_elimina_stopwords(self):
        resultado = preprocesar("el gato de la casa")
        tokens = resultado.split()
        assert "de" not in tokens
        assert "la" not in tokens

    def test_token_urgencia(self):
        resultado = preprocesar("urgente oferta gratis premio")
        assert "__feature_lenguaje_urgente__" in resultado

    def test_token_mayusculas(self):
        resultado = preprocesar("URGENTE VERIFIQUE SU CUENTA AHORA MISMO SEÑOR")
        assert "__feature_muchas_mayusculas__" in resultado


class TestMetricasPorClase:
    def test_perfecto(self):
        y_t = ["spam", "ham", "spam", "ham"]
        y_p = ["spam", "ham", "spam", "ham"]
        m = _metricas_por_clase(y_t, y_p)
        assert m["spam"]["f1"] == 100.0
        assert m["ham"]["f1"] == 100.0

    def test_keys_presentes(self):
        y_t = ["spam", "ham"]
        y_p = ["spam", "spam"]
        m = _metricas_por_clase(y_t, y_p)
        for clase in ("spam", "ham"):
            assert "precision" in m[clase]
            assert "recall" in m[clase]
            assert "f1" in m[clase]
            assert "support" in m[clase]

    def test_support_correcto(self):
        y_t = ["spam", "spam", "ham"]
        y_p = ["spam", "spam", "ham"]
        m = _metricas_por_clase(y_t, y_p)
        assert m["spam"]["support"] == 2
        assert m["ham"]["support"] == 1


# ---------------------------------------------------------------------------
# Capas del clasificador (requieren modelo)
# ---------------------------------------------------------------------------

class TestCapa0CorreccionesUsuario:
    def test_ham_user_override(self, modelo):
        """Correcciones HAM tienen prioridad absoluta."""
        texto = "urgente verifique su cuenta gratis fraude estafa"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=["urgente"])
        assert resultado["clasificacion"] == "HAM"
        assert resultado["ajustado"] is True

    def test_spam_user_override(self, modelo):
        """Correcciones SPAM tienen prioridad sobre dominio confiable."""
        texto = "hola cómo estás nos vemos mañana en el trabajo todo bien"
        resultado = clasificar(texto, modelo, spam_usr=["hola"], ham_usr=[])
        assert resultado["clasificacion"] == "SPAM"
        assert resultado["ajustado"] is True

    def test_ham_gana_sobre_spam_cuando_mas_hits(self, modelo):
        texto = "transferencia cuenta banco pago"
        resultado = clasificar(texto, modelo,
                               spam_usr=["transferencia"],
                               ham_usr=["transferencia", "cuenta", "banco"])
        assert resultado["clasificacion"] == "HAM"


class TestCapa1PatronesEstafa:
    def test_estafa_clasifica_spam_alto(self, modelo):
        texto = "Envía los datos de tu tarjeta de crédito o enfrentarás consecuencias legales"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[])
        assert resultado["clasificacion"] == "SPAM"
        assert resultado["confianza"] >= 90.0

    def test_razon_incluye_patron(self, modelo):
        texto = "CVV de tu tarjeta o te denunciaré ante la justicia"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[])
        assert "estafa" in resultado["razon"].lower() or "coerción" in resultado["razon"].lower()


class TestCapa2DominiConfianza:
    def test_banco_transaccional_es_ham(self, modelo):
        texto = "Su tarjeta fue utilizada por RD 2350 comprobante de transferencia"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[],
                               remitente="Banco Popular <noreply@bancopopular.com.do>")
        assert resultado["clasificacion"] == "HAM"
        assert resultado["confianza"] == 98.0

    def test_servicio_promocional_es_sospechoso(self, modelo):
        texto = "Oferta exclusiva descuento especial aprovecha ya no te pierdas"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[],
                               remitente="Google <noreply@google.com>")
        assert resultado["clasificacion"] == "SOSPECHOSO"

    def test_banco_neutro_es_ham(self, modelo):
        texto = "Estimado cliente, le enviamos este mensaje de parte del banco."
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[],
                               remitente="BHD León <info@bhdleon.com.do>")
        assert resultado["clasificacion"] == "HAM"
        assert resultado["confianza"] == 85.0


class TestCapa3ModeloML:
    def test_spam_extorsion(self, modelo):
        texto = "tengo acceso a tu cámara págame bitcoin o publico todo gratis urgente fraude"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[])
        assert resultado["clasificacion"] == "SPAM"

    def test_ham_personal(self, modelo):
        texto = "hola primo te mando los archivos que pediste para la presentación del lunes"
        resultado = clasificar(texto, modelo, spam_usr=[], ham_usr=[])
        assert resultado["clasificacion"] == "HAM"

    def test_texto_vacio(self, modelo):
        resultado = clasificar("", modelo, spam_usr=[], ham_usr=[])
        assert resultado["clasificacion"] == "INDETERMINADO"
        assert resultado["confianza"] == 0

    def test_resultado_tiene_campos_requeridos(self, modelo):
        resultado = clasificar("test de clasificación", modelo, spam_usr=[], ham_usr=[])
        for campo in ("clasificacion", "confianza", "prob_spam", "prob_ham", "ajustado", "razon"):
            assert campo in resultado

    def test_confianza_en_rango(self, modelo):
        resultado = clasificar("urgente dinero gratis fraude estafa click", modelo, spam_usr=[], ham_usr=[])
        assert 0 <= resultado["confianza"] <= 100
        assert 0 <= resultado["prob_spam"] <= 100
        assert 0 <= resultado["prob_ham"] <= 100
