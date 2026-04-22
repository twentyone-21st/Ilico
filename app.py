import os
import json
import logging
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, url_for

from classifier import entrenar_modelo, clasificar, MODEL_CACHE
from gmail_service import (
    crear_flujo_oauth, guardar_credenciales_desde_codigo,
    esta_autenticado, listar_correos, obtener_perfil_usuario,
    obtener_correo_por_id,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ilico-dev-2025")

_MODELO   = None
_ACCURACY = None
_MODELO_LISTO = threading.Event()

_CACHE = {
    "principal":  {"correos": [], "stats": {}, "ts": 0.0, "cargando": False},
    "archivados": {"correos": [], "stats": {}, "ts": 0.0, "cargando": False},
}
_CACHE_LOCK   = threading.Lock()
_LIMITE       = 1000
_TTL_SEGUNDOS = 5 * 60

_SPAM_USR = []
_HAM_USR  = []

_CORRECCIONES_FILE    = Path(__file__).parent / "correcciones_usuario.json"
_FEEDBACK_FILE        = Path(__file__).parent / "feedback_correos.json"
_FEEDBACK_LOCK        = threading.Lock()


def _cargar_correcciones():
    global _SPAM_USR, _HAM_USR
    if _CORRECCIONES_FILE.exists():
        try:
            d = json.loads(_CORRECCIONES_FILE.read_text(encoding="utf-8"))
            _SPAM_USR = d.get("spam", [])
            _HAM_USR  = d.get("ham",  [])
        except Exception:
            pass


def _guardar_correcciones():
    try:
        _CORRECCIONES_FILE.write_text(
            json.dumps({"spam": _SPAM_USR, "ham": _HAM_USR}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"No se guardaron correcciones: {e}")


def _guardar_feedback(texto: str, etiqueta: str, correo_id: str = None):
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
    return {
        "total":      len(correos),
        "spam":       sum(1 for c in correos if c.get("clasificacion") == "SPAM"),
        "ham":        sum(1 for c in correos if c.get("clasificacion") == "HAM"),
        "sospechoso": sum(1 for c in correos if c.get("clasificacion") == "SOSPECHOSO"),
    }


def _dedup(correos):
    vistos, resultado = set(), []
    for c in correos:
        mid = str(c.get("id") or "").strip()
        if mid and mid not in vistos:
            vistos.add(mid)
            resultado.append(c)
    return resultado


def _clasificar_lote(correos_raw):
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


def _cargar_categoria(categoria: str, cantidad: int, reemplazar: bool):
    try:
        correos_raw = listar_correos(max_resultados=cantidad, categoria=categoria)
        clasificados = _clasificar_lote(correos_raw)
        with _CACHE_LOCK:
            bucket = _CACHE[categoria]
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
            _CACHE[categoria]["cargando"] = False


def _cache_vencido(categoria):
    return (time.time() - _CACHE[categoria]["ts"]) > _TTL_SEGUNDOS


def _arrancar_modelo():
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
    _MODELO_LISTO.wait(timeout=timeout)
    return _MODELO


@app.route("/")
def index():
    return render_template("index.html", autenticado=esta_autenticado())


@app.route("/auth/gmail")
def auth_gmail():
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
    codigo = request.args.get("code")
    if not codigo:
        return redirect(url_for("index"))
    try:
        railway = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
        uri = f"https://{railway}/auth/callback" if railway else url_for("auth_callback", _external=True)
        guardar_credenciales_desde_codigo(codigo, uri)
    except Exception as e:
        logger.error(f"Error callback OAuth: {e}")
    return redirect(url_for("index"))


@app.route("/auth/logout")
def logout():
    token = Path(__file__).parent / "token.json"
    token.unlink(missing_ok=True)
    with _CACHE_LOCK:
        for cat in _CACHE:
            _CACHE[cat]["correos"] = []
            _CACHE[cat]["stats"]   = {}
            _CACHE[cat]["ts"]      = 0.0
    return redirect(url_for("index"))


@app.route("/api/perfil")
def api_perfil():
    if not esta_autenticado():
        return jsonify({"autenticado": False})
    perfil = obtener_perfil_usuario()
    perfil["autenticado"] = True
    return jsonify(perfil)


@app.route("/api/correos")
def api_correos():
    if not esta_autenticado():
        return jsonify({"error": "No autenticado"}), 401

    modelo = _esperar_modelo(timeout=90)
    if modelo is None:
        return jsonify({"error": "Modelo no disponible", "correos": [], "stats": {}, "loading": True, "nuevos": 0}), 503

    cat    = request.args.get("categoria", "principal")
    if cat not in _CACHE:
        cat = "principal"
    forzar = request.args.get("refresh", "0") == "1"

    with _CACHE_LOCK:
        tiene_cache = bool(_CACHE[cat]["correos"])
        vencido     = _cache_vencido(cat)
        cargando    = _CACHE[cat]["cargando"]

    if not tiene_cache or forzar:
        if not cargando:
            with _CACHE_LOCK:
                _CACHE[cat]["cargando"] = True
            _cargar_categoria(cat, cantidad=30, reemplazar=False)
            with _CACHE_LOCK:
                hay_resultados = bool(_CACHE[cat]["correos"])
            if hay_resultados:
                threading.Thread(
                    target=_cargar_categoria,
                    args=(cat, _LIMITE, True),
                    daemon=True
                ).start()
                with _CACHE_LOCK:
                    _CACHE[cat]["cargando"] = True
            else:
                with _CACHE_LOCK:
                    _CACHE[cat]["cargando"] = False

    elif vencido and not cargando:
        with _CACHE_LOCK:
            _CACHE[cat]["cargando"] = True
        threading.Thread(
            target=_cargar_categoria,
            args=(cat, _LIMITE, True),
            daemon=True
        ).start()

    with _CACHE_LOCK:
        correos  = list(_CACHE[cat]["correos"])
        stats    = dict(_CACHE[cat]["stats"])
        cargando = _CACHE[cat]["cargando"]

    return jsonify({
        "correos":     correos,
        "stats":       stats,
        "desde_cache": tiene_cache and not forzar,
        "nuevos":      0,
        "loading":     cargando,
    })


@app.route("/api/correos/cache")
def api_correos_cache():
    cat = request.args.get("categoria", "principal")
    if cat not in _CACHE:
        cat = "principal"
    with _CACHE_LOCK:
        correos  = list(_CACHE[cat]["correos"])
        stats    = dict(_CACHE[cat]["stats"])
        vencido  = _cache_vencido(cat)
        cargando = _CACHE[cat]["cargando"]
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
    if not esta_autenticado():
        return jsonify({"error": "No autenticado"}), 401

    cache_row = None
    with _CACHE_LOCK:
        for cat in _CACHE:
            for c in _CACHE[cat]["correos"]:
                if c.get("id") == mensaje_id:
                    cache_row = c
                    break
            if cache_row:
                break

    try:
        completo = obtener_correo_por_id(mensaje_id)
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
    return jsonify({"spam": _SPAM_USR, "ham": _HAM_USR})


@app.route("/api/correcciones/sincronizar", methods=["POST"])
def api_sincronizar_correcciones():
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


@app.route("/api/stats")
def api_stats():
    return jsonify({
        "accuracy":          round((_ACCURACY or 0) * 100, 1),
        "correcciones_spam": len(_SPAM_USR),
        "correcciones_ham":  len(_HAM_USR),
    })


@app.route("/api/reentrenar", methods=["POST"])
def api_reentrenar():
    global _MODELO, _ACCURACY
    MODEL_CACHE.unlink(missing_ok=True)
    _MODELO   = None
    _ACCURACY = None
    _MODELO_LISTO.clear()
    threading.Thread(target=_arrancar_modelo, daemon=True).start()
    return jsonify({"ok": True, "mensaje": "Reentrenamiento iniciado."})


@app.route("/api/webhook/gmail", methods=["POST"])
def webhook_gmail():
    envelope = request.get_json(silent=True)
    if not envelope or "message" not in envelope:
        return "OK", 200
    def _refrescar():
        for cat in ("principal", "archivados"):
            if esta_autenticado() and _MODELO is not None:
                with _CACHE_LOCK:
                    if not _CACHE[cat]["cargando"]:
                        _CACHE[cat]["cargando"] = True
                    else:
                        continue
                _cargar_categoria(cat, cantidad=_LIMITE, reemplazar=True)
    threading.Thread(target=_refrescar, daemon=True).start()
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
