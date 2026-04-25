"""
@file app.py
@brief Servidor Flask de Ilico. Gestiona el cache de correos, autenticación OAuth y la API REST.
"""
import os
import json
import logging
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for, session

from classifier import entrenar_modelo, clasificar, MODEL_CACHE
from gmail_service import (
    crear_flujo_oauth, guardar_credenciales_desde_codigo,
    obtener_credenciales, listar_correos, obtener_perfil_usuario,
    obtener_correo_por_id,
    archivar_correo, desarchivar_correo, mover_a_cuarentena,
    restaurar_de_cuarentena, eliminar_correo,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ilico-dev-2025")

# Estado global del modelo de clasificación
_MODELO   = None
_ACCURACY = None
_MODELO_LISTO = threading.Event()

# Cache en memoria por usuario (clave: email del usuario); evita llamar a Gmail en cada request
_CACHE      = {}
_CACHE_LOCK = threading.RLock()
_LIMITE       = 1000
_TTL_SEGUNDOS = 5 * 60

# Palabras enseñadas por el usuario para ajustar el clasificador en tiempo real
_SPAM_USR = []
_HAM_USR  = []

_CORRECCIONES_FILE    = Path(__file__).parent / "correcciones_usuario.json"
_FEEDBACK_FILE        = Path(__file__).parent / "feedback_correos.json"
_FEEDBACK_LOCK        = threading.Lock()


def _get_creds():
    """
    Obtiene credenciales válidas de la sesión del usuario actual.
    Si el token está vencido lo renueva y guarda el nuevo en la sesión.
    """
    token_dict = session.get("token_data")
    if not token_dict:
        return None

    def _save_refreshed(new_dict):
        session["token_data"] = new_dict

    return obtener_credenciales(token_dict, on_refresh=_save_refreshed)


def _get_user_id() -> str:
    """Devuelve el email del usuario de la sesión, usado como clave de cache."""
    return session.get("user_email", "")


def _get_user_cache(user_id: str) -> dict:
    """Devuelve el bucket de cache del usuario, creándolo si no existe."""
    with _CACHE_LOCK:
        if user_id not in _CACHE:
            _CACHE[user_id] = {
                "principal":  {"correos": [], "stats": {}, "ts": 0.0, "cargando": False},
                "archivados": {"correos": [], "stats": {}, "ts": 0.0, "cargando": False},
                "cuarentena": {"correos": [], "stats": {}, "ts": 0.0, "cargando": False},
            }
        return _CACHE[user_id]


def _cargar_correcciones():
    """
    @brief Carga las palabras de corrección del usuario desde disco al arrancar el servidor.
    """
    global _SPAM_USR, _HAM_USR
    if _CORRECCIONES_FILE.exists():
        try:
            d = json.loads(_CORRECCIONES_FILE.read_text(encoding="utf-8"))
            _SPAM_USR = d.get("spam", [])
            _HAM_USR  = d.get("ham",  [])
        except Exception:
            pass


def _guardar_correcciones():
    """
    @brief Persiste las listas de corrección en JSON para que sobrevivan reinicios del servidor.
    """
    try:
        _CORRECCIONES_FILE.write_text(
            json.dumps({"spam": _SPAM_USR, "ham": _HAM_USR}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"No se guardaron correcciones: {e}")


def _guardar_feedback(texto: str, etiqueta: str, correo_id: str = None):
    """
    @brief Almacena un correo real etiquetado para mejorar futuros reentrenamientos del modelo.
    @param texto    Contenido del correo a registrar.
    @param etiqueta Clasificación correcta: 'spam' o 'ham'.
    @param correo_id ID opcional del mensaje en Gmail.
    """
    texto = (texto or "").strip()
    if not texto or etiqueta not in ("spam", "ham"):
        return
    entrada = {"texto_clasificar": texto, "etiqueta": etiqueta}
    if correo_id:
        entrada["correo_id"] = str(correo_id)
    with _FEEDBACK_LOCK:
        items = []
        if _FEEDBACK_FILE.exists():
            try:
                raw = json.loads(_FEEDBACK_FILE.read_text(encoding="utf-8"))
                items = raw if isinstance(raw, list) else []
            except Exception:
                items = []
        items.append(entrada)
        _FEEDBACK_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _stats(correos):
    """
    @brief Calcula totales de SPAM, HAM y SOSPECHOSO sobre una lista de correos clasificados.
    @param correos Lista de dicts con campo 'clasificacion'.
    @return Dict con claves total, spam, ham, sospechoso.
    """
    return {
        "total":      len(correos),
        "spam":       sum(1 for c in correos if c.get("clasificacion") == "SPAM"),
        "ham":        sum(1 for c in correos if c.get("clasificacion") == "HAM"),
        "sospechoso": sum(1 for c in correos if c.get("clasificacion") == "SOSPECHOSO"),
    }


def _dedup(correos):
    """
    @brief Elimina correos duplicados por ID conservando el primero encontrado.
    @param correos Lista de dicts de correos.
    @return Lista sin duplicados.
    """
    vistos, resultado = set(), []
    for c in correos:
        mid = str(c.get("id") or "").strip()
        if mid and mid not in vistos:
            vistos.add(mid)
            resultado.append(c)
    return resultado


def _clasificar_lote(correos_raw):
    """
    @brief Aplica el clasificador a cada correo y devuelve la lista enriquecida con la predicción.
    @param correos_raw Lista de dicts con id, asunto, remite y texto_clasificar.
    @return Lista de dicts con clasificacion, confianza, prob_spam, prob_ham y razon añadidos.
    """
    resultado = []
    for c in correos_raw:
        try:
            clas = clasificar(
                c["texto_clasificar"], _MODELO, _SPAM_USR, _HAM_USR,
                remitente=c.get("remite", "")
            )
            resultado.append({
                "id":               c["id"],
                "asunto":           c["asunto"],
                "remite":           c["remite"],
                "fecha":            c["fecha"],
                "texto_clasificar": c.get("texto_clasificar", ""),
                "clasificacion":    clas["clasificacion"],
                "confianza":        clas["confianza"],
                "prob_spam":        clas["prob_spam"],
                "prob_ham":         clas["prob_ham"],
                "ajustado":         clas["ajustado"],
                "razon":            clas.get("razon", ""),
            })
        except Exception as e:
            logger.debug(f"Error clasificando {c.get('id')}: {e}")
    return resultado


def _cargar_categoria(creds, user_id: str, categoria: str, cantidad: int, reemplazar: bool):
    """
    @brief Descarga correos de Gmail, los clasifica y actualiza el cache del usuario indicado.
    @param creds      Credenciales OAuth del usuario (capturadas antes de lanzar el hilo).
    @param user_id    Email del usuario, clave del cache.
    @param categoria  'principal' o 'archivados'.
    @param cantidad   Número máximo de correos a obtener.
    @param reemplazar Si True reemplaza el cache completo; si False fusiona con los existentes.
    """
    user_cache = _get_user_cache(user_id)
    try:
        correos_raw  = listar_correos(creds, max_resultados=cantidad, categoria=categoria)
        clasificados = _clasificar_lote(correos_raw)
        with _CACHE_LOCK:
            bucket = user_cache[categoria]
            if reemplazar:
                nuevo = _dedup(clasificados)
            else:
                ids_previos = {str(c.get("id")) for c in bucket["correos"]}
                nuevos = [c for c in clasificados if str(c.get("id")) not in ids_previos]
                nuevo  = _dedup(nuevos + bucket["correos"])
            if len(nuevo) > _LIMITE:
                nuevo = nuevo[:_LIMITE]
            bucket["correos"]  = nuevo
            bucket["stats"]    = _stats(nuevo)
            bucket["ts"]       = time.time()
    except Exception as e:
        logger.error(f"Error cargando {categoria}: {e}")
    finally:
        with _CACHE_LOCK:
            user_cache[categoria]["cargando"] = False


def _cache_vencido(user_cache, categoria):
    """
    @brief Indica si el cache de una categoría superó el TTL de 5 minutos.
    @param user_cache Bucket de cache del usuario.
    @param categoria  Clave del bucket de cache.
    @return True si el cache está vencido y debe recargarse.
    """
    return (time.time() - user_cache[categoria]["ts"]) > _TTL_SEGUNDOS


def _arrancar_modelo():
    """
    @brief Entrena o carga el modelo en un hilo daemon para no bloquear el arranque del servidor.
    """
    global _MODELO, _ACCURACY
    logger.info("  [modelo] Cargando en background...")
    try:
        m, a = entrenar_modelo(forzar=False)
        _MODELO   = m
        _ACCURACY = a
        _MODELO_LISTO.set()
        _cargar_correcciones()
        logger.info(f"  [modelo] Listo — {round(a*100,1)}%")
    except Exception as e:
        logger.error(f"  [modelo] Error: {e}")
        _MODELO_LISTO.set()


threading.Thread(target=_arrancar_modelo, daemon=True).start()


def _esperar_modelo(timeout=90):
    """
    @brief Bloquea hasta que el modelo esté disponible o expire el timeout.
    @param timeout Segundos máximos de espera.
    @return El modelo entrenado, o None si falló la carga.
    """
    _MODELO_LISTO.wait(timeout=timeout)
    return _MODELO


@app.route("/")
def index():
    """
    @brief Renderiza la interfaz principal e informa al template si el usuario está autenticado.
    """
    autenticado = bool(session.get("token_data"))
    return render_template("index.html", autenticado=autenticado)


@app.route("/auth/gmail")
def auth_gmail():
    """
    @brief Inicia el flujo OAuth2 con Google y redirige al usuario a la pantalla de autorización.
    """
    try:
        railway = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        uri = f"https://{railway}/auth/callback" if railway else url_for("auth_callback", _external=True)
        flujo = crear_flujo_oauth(uri)
        url, _ = flujo.authorization_url(prompt="consent", access_type="offline")
        return redirect(url)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500


@app.route("/auth/callback")
def auth_callback():
    """
    @brief Recibe el código OAuth de Google, intercambia por token y lo guarda en la sesión del usuario.
    """
    codigo = request.args.get("code")
    if not codigo:
        return redirect(url_for("index"))
    try:
        railway = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        uri = f"https://{railway}/auth/callback" if railway else url_for("auth_callback", _external=True)
        token_dict = guardar_credenciales_desde_codigo(codigo, uri)
        session["token_data"] = token_dict
        # Guardar email en sesión para usarlo como clave de cache
        creds = obtener_credenciales(token_dict)
        if creds:
            perfil = obtener_perfil_usuario(creds)
            session["user_email"] = perfil.get("email", "")
    except Exception as e:
        logger.error(f"Error callback OAuth: {e}")
    return redirect(url_for("index"))


@app.route("/auth/logout")
def logout():
    """
    @brief Limpia la sesión del usuario y elimina su cache en memoria.
    """
    user_id = session.get("user_email")
    session.clear()
    if user_id:
        with _CACHE_LOCK:
            _CACHE.pop(user_id, None)
    return redirect(url_for("index"))


@app.route("/api/perfil")
def api_perfil():
    """
    @brief Devuelve el email y total de mensajes del usuario autenticado en Gmail.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"autenticado": False})
    perfil = obtener_perfil_usuario(creds)
    perfil["autenticado"] = True
    return jsonify(perfil)


@app.route("/api/correos")
def api_correos():
    """
    @brief Devuelve los correos clasificados del cache; lanza carga en fondo si el cache está vacío o vencido.
    @return JSON con correos, stats, loading y desde_cache.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"error": "No autenticado"}), 401

    user_id = _get_user_id()
    if not user_id:
        return jsonify({"error": "No autenticado"}), 401

    modelo = _esperar_modelo(timeout=90)
    if modelo is None:
        return jsonify({"error": "Modelo no disponible", "correos": [], "stats": {}, "loading": True, "nuevos": 0}), 503

    cat    = request.args.get("categoria", "principal")
    if cat not in ("principal", "archivados", "cuarentena"):
        cat = "principal"
    forzar = request.args.get("refresh", "0") == "1"

    user_cache = _get_user_cache(user_id)

    with _CACHE_LOCK:
        tiene_cache = bool(user_cache[cat]["correos"])
        vencido     = _cache_vencido(user_cache, cat)
        cargando    = user_cache[cat]["cargando"]

    # Carga inicial: 30 correos rápidos en primer plano, luego ampliación a _LIMITE en background
    if not tiene_cache or forzar:
        if not cargando:
            with _CACHE_LOCK:
                user_cache[cat]["cargando"] = True
            _cargar_categoria(creds, user_id, cat, cantidad=30, reemplazar=False)
            with _CACHE_LOCK:
                hay_resultados = bool(user_cache[cat]["correos"])
            if hay_resultados:
                threading.Thread(
                    target=_cargar_categoria,
                    args=(creds, user_id, cat, _LIMITE, True),
                    daemon=True
                ).start()
                with _CACHE_LOCK:
                    user_cache[cat]["cargando"] = True
            else:
                with _CACHE_LOCK:
                    user_cache[cat]["cargando"] = False

    elif vencido and not cargando:
        with _CACHE_LOCK:
            user_cache[cat]["cargando"] = True
        threading.Thread(
            target=_cargar_categoria,
            args=(creds, user_id, cat, _LIMITE, True),
            daemon=True
        ).start()

    with _CACHE_LOCK:
        correos  = list(user_cache[cat]["correos"])
        stats    = dict(user_cache[cat]["stats"])
        cargando = user_cache[cat]["cargando"]

    return jsonify({
        "correos":     correos,
        "stats":       stats,
        "desde_cache": tiene_cache and not forzar,
        "nuevos":      0,
        "loading":     cargando,
    })


@app.route("/api/correos/cache")
def api_correos_cache():
    """
    @brief Devuelve el estado actual del cache sin disparar cargas; usado por el polling del frontend cada 5 s.
    """
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"correos": [], "stats": {}, "desde_cache": True, "nuevos": 0,
                        "stale": False, "loading": False, "vacio": True})

    cat = request.args.get("categoria", "principal")
    if cat not in ("principal", "archivados", "cuarentena"):
        cat = "principal"

    user_cache = _get_user_cache(user_id)
    with _CACHE_LOCK:
        correos  = list(user_cache[cat]["correos"])
        stats    = dict(user_cache[cat]["stats"])
        vencido  = _cache_vencido(user_cache, cat)
        cargando = user_cache[cat]["cargando"]
    return jsonify({
        "correos":     correos,
        "stats":       stats,
        "desde_cache": True,
        "nuevos":      0,
        "stale":       vencido,
        "loading":     cargando,
        "vacio":       len(correos) == 0 and not cargando,
    })


@app.route("/api/correo/<mensaje_id>")
def api_correo_detalle(mensaje_id):
    """
    @brief Devuelve el contenido completo de un correo, fusionando la clasificación del cache si existe.
    @param mensaje_id ID del mensaje en Gmail.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"error": "No autenticado"}), 401

    user_id   = _get_user_id()
    cache_row = None
    if user_id:
        user_cache = _get_user_cache(user_id)
        with _CACHE_LOCK:
            for cat in user_cache:
                for c in user_cache[cat]["correos"]:
                    if c.get("id") == mensaje_id:
                        cache_row = c
                        break
                if cache_row:
                    break

    try:
        completo = obtener_correo_por_id(creds, mensaje_id)
        if not completo:
            return jsonify({"error": "No encontrado"}), 404
        if cache_row:
            completo.update({
                "clasificacion": cache_row.get("clasificacion", ""),
                "confianza":     cache_row.get("confianza", 0),
                "prob_spam":     cache_row.get("prob_spam", 0),
                "prob_ham":      cache_row.get("prob_ham", 0),
                "razon":         cache_row.get("razon", ""),
            })
        return jsonify(completo)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/clasificar", methods=["POST"])
def api_clasificar():
    """
    @brief Clasifica un texto enviado manualmente por el usuario y devuelve la predicción del modelo.
    """
    data  = request.get_json(silent=True) or {}
    texto = data.get("texto", "").strip()
    if not texto:
        return jsonify({"error": "Texto vacío"}), 400
    modelo = _esperar_modelo(timeout=90)
    if modelo is None:
        return jsonify({"error": "Modelo no disponible"}), 503
    resultado = clasificar(texto, modelo, _SPAM_USR, _HAM_USR,
                           remitente=data.get("remitente", ""))
    return jsonify(resultado)


@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    """
    @brief Añade palabras clave a la lista de corrección del usuario y guarda el correo como ejemplo de entrenamiento.
    """
    global _SPAM_USR, _HAM_USR
    data = request.get_json(silent=True) or {}
    tipo = data.get("tipo", "").lower()
    if tipo not in ("spam", "ham"):
        return jsonify({"error": "Tipo inválido"}), 400

    raw_palabras = data.get("palabras")
    if isinstance(raw_palabras, list) and raw_palabras:
        palabras = [str(p).lower().strip() for p in raw_palabras if len(str(p).strip()) > 2]
    else:
        palabra_unica = str(data.get("palabra", "")).lower().strip()
        palabras = [w.strip() for w in palabra_unica.split(",") if len(w.strip()) > 2]

    if not palabras:
        return jsonify({"error": "Sin palabras válidas"}), 400

    lista    = _SPAM_USR if tipo == "spam" else _HAM_USR
    añadidas = 0
    for p in palabras:
        if p not in lista:
            lista.append(p)
            añadidas += 1

    _guardar_correcciones()

    texto_fc   = (data.get("texto_clasificar") or "").strip()
    correo_id  = data.get("correo_id")
    if texto_fc:
        threading.Thread(
            target=_guardar_feedback,
            args=(texto_fc, tipo, correo_id),
            daemon=True
        ).start()

    return jsonify({
        "ok":         True,
        "mensaje":    f"{añadidas} palabra(s) añadida(s) como {tipo.upper()}.",
        "total_spam": len(_SPAM_USR),
        "total_ham":  len(_HAM_USR),
    })


@app.route("/api/correcciones")
def api_correcciones():
    """
    @brief Devuelve las listas actuales de palabras de corrección registradas por el usuario.
    """
    return jsonify({"spam": _SPAM_USR, "ham": _HAM_USR})


@app.route("/api/correcciones/sincronizar", methods=["POST"])
def api_sincronizar_correcciones():
    """
    @brief Fusiona las correcciones guardadas en localStorage del cliente con las del servidor.
    """
    global _SPAM_USR, _HAM_USR
    data = request.get_json(silent=True) or {}
    spam_new = [str(p).lower().strip() for p in data.get("spam", []) if len(str(p).strip()) > 2]
    ham_new  = [str(p).lower().strip() for p in data.get("ham",  []) if len(str(p).strip()) > 2]
    for p in spam_new:
        if p not in _SPAM_USR:
            _SPAM_USR.append(p)
    for p in ham_new:
        if p not in _HAM_USR:
            _HAM_USR.append(p)
    _guardar_correcciones()
    return jsonify({"ok": True, "total_spam": len(_SPAM_USR), "total_ham": len(_HAM_USR)})


@app.route("/api/correcciones/eliminar", methods=["POST"])
def api_eliminar_correccion():
    """
    @brief Elimina una palabra específica de la lista de correcciones spam o ham del usuario.
    """
    global _SPAM_USR, _HAM_USR
    data    = request.get_json(silent=True) or {}
    palabra = data.get("palabra", "").lower().strip()
    tipo    = data.get("tipo", "").lower()
    if not palabra or tipo not in ("spam", "ham"):
        return jsonify({"error": "Datos inválidos"}), 400
    lista = _SPAM_USR if tipo == "spam" else _HAM_USR
    if palabra in lista:
        lista.remove(palabra)
        _guardar_correcciones()
        return jsonify({"ok": True, "mensaje": f"'{palabra}' eliminada.", "total_spam": len(_SPAM_USR), "total_ham": len(_HAM_USR)})
    return jsonify({"ok": False, "mensaje": f"'{palabra}' no encontrada."}), 404


@app.route("/api/correcciones/editar", methods=["POST"])
def api_editar_correccion():
    """
    @brief Reemplaza una palabra de corrección existente por una nueva dentro de la misma categoría.
    """
    global _SPAM_USR, _HAM_USR
    data   = request.get_json(silent=True) or {}
    ant    = data.get("palabra_anterior", "").lower().strip()
    nueva  = data.get("palabra_nueva", "").lower().strip()
    tipo   = data.get("tipo", "").lower()
    if not ant or not nueva or tipo not in ("spam", "ham"):
        return jsonify({"error": "Datos inválidos"}), 400
    lista = _SPAM_USR if tipo == "spam" else _HAM_USR
    if ant not in lista:
        return jsonify({"error": f"'{ant}' no encontrada."}), 404
    if nueva in lista:
        return jsonify({"ok": False, "mensaje": f"'{nueva}' ya existe."}), 400
    lista[lista.index(ant)] = nueva
    _guardar_correcciones()
    return jsonify({"ok": True, "mensaje": f"'{ant}' → '{nueva}' actualizada.", "total_spam": len(_SPAM_USR), "total_ham": len(_HAM_USR)})


def _ejecutar_accion(accion_fn, mensaje_id: str, invalidar_cats: list):
    """
    @brief Ejecuta una acción sobre un mensaje de Gmail y actualiza el cache en memoria.
    @param accion_fn      Función de gmail_service a invocar (recibe creds, mensaje_id).
    @param mensaje_id     ID del mensaje a procesar.
    @param invalidar_cats Si tiene exactamente un elemento, el correo se mueve a ese bucket
                          directamente (aparición inmediata sin reload de Gmail).
    @return Respuesta JSON con ok:True o error.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"error": "No autenticado"}), 401
    user_id = _get_user_id()
    try:
        accion_fn(creds, mensaje_id)
        with _CACHE_LOCK:
            user_cache = _get_user_cache(user_id)
            # Capturar el objeto del correo antes de borrarlo de los buckets
            email_obj = None
            for cat_key in user_cache:
                for c in user_cache[cat_key]["correos"]:
                    if c.get("id") == mensaje_id:
                        email_obj = c
                        break
                if email_obj:
                    break
            # Eliminar de todos los buckets
            for cat_key in user_cache:
                user_cache[cat_key]["correos"] = [
                    c for c in user_cache[cat_key]["correos"] if c.get("id") != mensaje_id
                ]
                user_cache[cat_key]["stats"] = _stats(user_cache[cat_key]["correos"])
            # Insertar directamente en el bucket destino (si hay uno único)
            if email_obj and len(invalidar_cats) == 1:
                dest = invalidar_cats[0]
                if dest in user_cache:
                    ya_existe = any(c.get("id") == mensaje_id for c in user_cache[dest]["correos"])
                    if not ya_existe:
                        user_cache[dest]["correos"].insert(0, email_obj)
                        user_cache[dest]["stats"] = _stats(user_cache[dest]["correos"])
            elif invalidar_cats:
                for cat_key in invalidar_cats:
                    if cat_key in user_cache:
                        user_cache[cat_key]["ts"] = 0.0
        return jsonify({"ok": True})
    except Exception as e:
        logger.error(f"Error en acción sobre {mensaje_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/correo/<mensaje_id>/archivar", methods=["POST"])
def api_archivar(mensaje_id):
    """@brief Archiva un correo (quita INBOX). Aparece en Archivados de Ilico."""
    return _ejecutar_accion(archivar_correo, mensaje_id, invalidar_cats=["archivados"])


@app.route("/api/correo/<mensaje_id>/desarchivar", methods=["POST"])
def api_desarchivar(mensaje_id):
    """@brief Desarchiva un correo (añade INBOX de vuelta). Aparece en Principal."""
    return _ejecutar_accion(desarchivar_correo, mensaje_id, invalidar_cats=["principal"])


@app.route("/api/correo/<mensaje_id>/cuarentena", methods=["POST"])
def api_mover_cuarentena(mensaje_id):
    """@brief Mueve un correo a Cuarentena (carpeta Spam de Gmail)."""
    return _ejecutar_accion(mover_a_cuarentena, mensaje_id, invalidar_cats=["cuarentena"])


@app.route("/api/correo/<mensaje_id>/restaurar", methods=["POST"])
def api_restaurar(mensaje_id):
    """@brief Restaura un correo de Cuarentena a la bandeja Principal."""
    return _ejecutar_accion(restaurar_de_cuarentena, mensaje_id, invalidar_cats=["principal"])


@app.route("/api/correo/<mensaje_id>/eliminar", methods=["POST"])
def api_eliminar_correo(mensaje_id):
    """@brief Mueve un correo a la Papelera de Gmail."""
    return _ejecutar_accion(eliminar_correo, mensaje_id, invalidar_cats=[])


@app.route("/api/correos/limpiar", methods=["POST"])
def api_limpiar_bandeja():
    """
    @brief Mueve en lote a Cuarentena todos los IDs de correos indicados.
    @return JSON con movidos (exitosos) y errores.
    """
    creds = _get_creds()
    if not creds:
        return jsonify({"error": "No autenticado"}), 401
    user_id = _get_user_id()
    data = request.get_json(silent=True) or {}
    ids  = [str(i) for i in data.get("ids", []) if i]
    if not ids:
        return jsonify({"error": "Sin correos para limpiar"}), 400

    movidos, errores = 0, 0
    ids_ok = []
    for msg_id in ids:
        try:
            mover_a_cuarentena(creds, msg_id)
            ids_ok.append(msg_id)
            movidos += 1
        except Exception as e:
            logger.error(f"Error moviendo {msg_id} a cuarentena: {e}")
            errores += 1

    ids_set = set(ids_ok)
    with _CACHE_LOCK:
        user_cache = _get_user_cache(user_id)
        # Capturar objetos de correo antes de eliminarlos de los buckets origen
        emails_a_cuarentena, vistos = [], set()
        for cat in ("principal", "archivados"):
            for c in user_cache[cat]["correos"]:
                cid = c.get("id")
                if cid in ids_set and cid not in vistos:
                    emails_a_cuarentena.append(c)
                    vistos.add(cid)
        # Eliminar de principal y archivados
        for cat in ("principal", "archivados"):
            user_cache[cat]["correos"] = [
                c for c in user_cache[cat]["correos"] if c.get("id") not in ids_set
            ]
            user_cache[cat]["stats"] = _stats(user_cache[cat]["correos"])
        # Insertar directamente en cuarentena (aparición inmediata sin reload)
        if emails_a_cuarentena:
            ya_en_cuarentena = {c.get("id") for c in user_cache["cuarentena"]["correos"]}
            nuevos = [c for c in emails_a_cuarentena if c.get("id") not in ya_en_cuarentena]
            user_cache["cuarentena"]["correos"] = nuevos + user_cache["cuarentena"]["correos"]
            user_cache["cuarentena"]["stats"] = _stats(user_cache["cuarentena"]["correos"])

    return jsonify({"ok": True, "movidos": movidos, "errores": errores})


@app.route("/api/stats")
def api_stats():
    """
    @brief Devuelve la precisión del modelo y el recuento de palabras de corrección del usuario.
    """
    return jsonify({
        "accuracy":          round((_ACCURACY or 0) * 100, 1),
        "correcciones_spam": len(_SPAM_USR),
        "correcciones_ham":  len(_HAM_USR),
    })


@app.route("/api/reentrenar", methods=["POST"])
def api_reentrenar():
    """
    @brief Borra el modelo en cache y lanza un reentrenamiento completo en background.
    """
    global _MODELO, _ACCURACY
    MODEL_CACHE.unlink(missing_ok=True)
    _MODELO   = None
    _ACCURACY = None
    _MODELO_LISTO.clear()
    threading.Thread(target=_arrancar_modelo, daemon=True).start()
    return jsonify({"ok": True, "mensaje": "Reentrenamiento iniciado."})


@app.route("/api/webhook/gmail", methods=["POST"])
def webhook_gmail():
    """
    @brief Recibe notificaciones push de Gmail. Sin contexto de usuario en webhooks,
           el cache se actualizará en el próximo TTL de cada usuario.
    """
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
