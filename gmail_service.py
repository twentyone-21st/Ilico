"""
@file gmail_service.py
@brief Capa de integración con la Gmail API. Gestiona OAuth2, descarga y parseo de correos.
"""
import os
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib    import Path
from datetime   import datetime

logger = logging.getLogger(__name__)

try:
    from google.oauth2.credentials      import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow      import Flow
    from googleapiclient.discovery      import build
    from googleapiclient.errors         import HttpError
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    logger.warning("Librerías de Google no instaladas.")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

CREDENTIALS_PATH = Path(__file__).parent / "credentials.json"

QUERY_POR_CATEGORIA = {
    "archivados": "-label:INBOX -label:DRAFT -label:SENT -label:TRASH -label:SPAM",
}

# labelIds es más fiable que q= para etiquetas del sistema de Gmail
LABEL_IDS_POR_CATEGORIA = {
    "principal":    ["INBOX"],
    "restringidos": ["SPAM"],
}

# Para restringidos solo necesitamos cabeceras, no el cuerpo completo
FORMATO_POR_CATEGORIA = {
    "restringidos": "metadata",
}

_METADATA_HEADERS = [
    "Subject", "From", "Date",
    "Authentication-Results", "ARC-Authentication-Results",
    "Received-SPF", "DKIM-Signature",
]


def obtener_credenciales(token_dict: dict, on_refresh=None):
    """
    @brief Crea un objeto Credentials desde un dict y lo renueva si está vencido.
    @param token_dict  Dict con los datos del token (almacenado en la sesión de Flask).
    @param on_refresh  Callback opcional invocado con el nuevo dict si el token se renovó.
    @return Objeto Credentials válido, o None si el token es inválido.
    """
    if not GOOGLE_AVAILABLE or not token_dict:
        return None
    try:
        creds = Credentials.from_authorized_user_info(token_dict, SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if on_refresh:
                on_refresh(json.loads(creds.to_json()))
        return creds if (creds and creds.valid) else None
    except Exception as e:
        logger.error(f"Error cargando credenciales: {e}")
        return None


def credenciales_a_dict(creds) -> dict:
    """
    @brief Serializa un objeto Credentials a dict para guardarlo en la sesión de Flask.
    @return Dict con los datos del token.
    """
    return json.loads(creds.to_json())


def crear_flujo_oauth(redirect_uri: str):
    """
    @brief Crea el flujo de autenticación OAuth2, usando variable de entorno o archivo de credenciales.
    @param redirect_uri URI de retorno registrada en Google Cloud Console.
    @return Objeto Flow listo para iniciar la autorización.
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        return Flow.from_client_config(
            json.loads(creds_json), scopes=SCOPES, redirect_uri=redirect_uri
        )
    elif CREDENTIALS_PATH.exists():
        return Flow.from_client_secrets_file(
            str(CREDENTIALS_PATH), scopes=SCOPES, redirect_uri=redirect_uri
        )
    raise FileNotFoundError("No se encontraron credenciales de Google.")


def guardar_credenciales_desde_codigo(codigo: str, redirect_uri: str) -> dict:
    """
    @brief Intercambia el código de autorización por tokens y los devuelve como dict.
    @param codigo       Código OAuth recibido en el callback de Google.
    @param redirect_uri Misma URI usada al iniciar el flujo.
    @return Dict con los datos del token para guardar en la sesión de Flask.
    """
    flujo = crear_flujo_oauth(redirect_uri)
    flujo.fetch_token(code=codigo)
    return json.loads(flujo.credentials.to_json())


def obtener_servicio(creds):
    """
    @brief Construye el cliente autenticado de la Gmail API v1.
    @param creds Objeto Credentials válido del usuario autenticado.
    @return Recurso de la Gmail API listo para usarse.
    """
    if not creds:
        raise PermissionError("Usuario no autenticado con Gmail.")
    return build("gmail", "v1", credentials=creds)


def listar_correos(creds, max_resultados: int = 500, etiqueta: str = "INBOX",
                   categoria: str = "principal") -> list:
    """
    @brief Obtiene y parsea correos de Gmail en paralelo, devueltos en orden cronológico inverso.
    @param creds          Objeto Credentials del usuario autenticado.
    @param max_resultados Número máximo de correos a obtener.
    @param etiqueta       Parámetro heredado (no usado directamente; se usa 'categoria').
    @param categoria      'principal', 'archivados' o 'restringidos'.
    @return Lista de dicts de correo ordenada del más reciente al más antiguo.
    """
    try:
        servicio  = obtener_servicio(creds)
        query     = QUERY_POR_CATEGORIA.get(categoria)
        label_ids = LABEL_IDS_POR_CATEGORIA.get(categoria)

        mensajes = []
        vistos   = set()
        page_token = None

        while len(vistos) < max_resultados:
            page_size = min(500, max_resultados - len(vistos))
            kwargs = {
                "userId":     "me",
                "maxResults": page_size,
                "fields":     "messages(id),nextPageToken",
            }
            if label_ids:
                kwargs["labelIds"] = label_ids
            elif query:
                kwargs["q"] = query
            else:
                kwargs["labelIds"] = ["INBOX"]
            if page_token:
                kwargs["pageToken"] = page_token

            resultado = servicio.users().messages().list(**kwargs).execute()
            batch     = resultado.get("messages") or []
            if not batch:
                break
            for ref in batch:
                mid = ref.get("id") if isinstance(ref, dict) else None
                if not mid or mid in vistos:
                    continue
                vistos.add(mid)
                mensajes.append(ref)
                if len(vistos) >= max_resultados:
                    break

            page_token = resultado.get("nextPageToken")
            if not page_token or len(vistos) >= max_resultados:
                break

        correos = []
        fmt = FORMATO_POR_CATEGORIA.get(categoria, "full")

        def _parsear_con_creds_propias(mensaje_id: str):
            svc = build("gmail", "v1", credentials=creds)
            return _parsear_correo(svc, mensaje_id, formato=fmt)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futuros = {
                executor.submit(_parsear_con_creds_propias, msg["id"]): msg["id"]
                for msg in mensajes
            }
            for futuro in as_completed(futuros):
                try:
                    c = futuro.result()
                    if c:
                        correos.append(c)
                except Exception as e:
                    logger.debug(f"Error parseando {futuros[futuro]}: {e}")

        correos.sort(key=lambda c: c.get("fecha_ts", 0), reverse=True)
        return correos

    except HttpError as e:
        logger.error(f"Gmail API HttpError: {e}")
        return []
    except Exception as e:
        logger.error(f"Gmail API error inesperado: {type(e).__name__}: {e}")
        return []


def _parsear_correo(servicio, mensaje_id: str, formato: str = "full"):
    """
    @brief Extrae asunto, remitente, fecha, cuerpo y etiquetas de un mensaje de Gmail.
    @param servicio    Cliente autenticado de la Gmail API.
    @param mensaje_id  ID del mensaje a parsear.
    @param formato     'full' para cuerpo completo, 'metadata' solo cabeceras (más rápido).
    @return Dict con los campos del correo, o None si ocurre un error.
    """
    try:
        kwargs = {"userId": "me", "id": mensaje_id, "format": formato}
        if formato == "metadata":
            kwargs["metadataHeaders"] = _METADATA_HEADERS
        msg     = servicio.users().messages().get(**kwargs).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        asunto  = headers.get("Subject", "(sin asunto)")
        remite  = headers.get("From",    "(desconocido)")
        fecha   = headers.get("Date",    "")

        cuerpo      = _extraer_cuerpo(msg["payload"]) if formato == "full" else ""
        html_cuerpo = _extraer_html(msg["payload"])   if formato == "full" else ""
        labels      = msg.get("labelIds", [])

        _AUTH_HEADERS = (
            "Authentication-Results",
            "ARC-Authentication-Results",
            "Received-SPF",
            "DKIM-Signature",
        )
        headers_auth = {k: headers.get(k, "") for k in _AUTH_HEADERS}

        return {
            "id":               mensaje_id,
            "asunto":           asunto[:200],
            "remite":           remite[:120],
            "fecha":            _formatear_fecha(fecha),
            "fecha_ts":         _fecha_timestamp(fecha),
            "cuerpo":           cuerpo[:5000],
            "html_cuerpo":      html_cuerpo[:50000] if html_cuerpo else "",
            "texto_clasificar": f"{asunto} {cuerpo}".strip(),
            "labels":           labels,
            "headers_auth":     headers_auth,
        }
    except Exception as e:
        logger.debug(f"Error _parsear_correo {mensaje_id}: {e}")
        return None


def _extraer_cuerpo(payload: dict) -> str:
    """
    @brief Extrae el texto plano del payload MIME de forma recursiva, preferiendo text/plain.
    @param payload Dict de payload del mensaje de Gmail.
    @return Texto plano del correo, o cadena vacía si no hay ninguno.
    """
    cuerpo = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    cuerpo = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break
            elif "parts" in part:
                cuerpo = _extraer_cuerpo(part)
                if cuerpo:
                    break
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            cuerpo = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return cuerpo.strip()


def _extraer_html(payload: dict) -> str:
    """
    @brief Extrae el contenido HTML del payload MIME de forma recursiva para mostrarlo en el modal.
    @param payload Dict de payload del mensaje de Gmail.
    @return HTML del correo, o cadena vacía si no existe.
    """
    html = ""
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    break
            elif "parts" in part:
                html = _extraer_html(part)
                if html:
                    break
    else:
        if payload.get("mimeType") == "text/html":
            data = payload.get("body", {}).get("data", "")
            if data:
                html = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
    return html.strip()


def _fecha_timestamp(fecha_str: str) -> float:
    """
    @brief Convierte una fecha de cabecera RFC-2822 a Unix timestamp para ordenar correos.
    @param fecha_str Cadena de fecha del header 'Date' del correo.
    @return Timestamp numérico, o 0.0 si la fecha no es parseable.
    """
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(fecha_str).timestamp()
    except Exception:
        return 0.0


def _formatear_fecha(fecha_str: str) -> str:
    """
    @brief Convierte una fecha RFC-2822 al formato de visualización local: '22 abr 2026, 03:28'.
    @param fecha_str Cadena de fecha del header 'Date'.
    @return Fecha formateada en español, o la cadena original si falla el parseo.
    """
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(fecha_str)
        meses = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
        return f"{dt.day} {meses[dt.month-1]} {dt.year}, {dt.strftime('%H:%M')}"
    except Exception:
        return fecha_str[:20] if fecha_str else "—"


def obtener_correo_por_id(creds, mensaje_id: str):
    """
    @brief Obtiene un correo completo de Gmail dado su ID, incluyendo cuerpo HTML y texto.
    @param creds      Objeto Credentials del usuario autenticado.
    @param mensaje_id ID del mensaje en Gmail.
    @return Dict con los campos del correo, o None si no se encuentra.
    """
    try:
        servicio = obtener_servicio(creds)
        return _parsear_correo(servicio, mensaje_id)
    except Exception as e:
        logger.error(f"Error obteniendo correo {mensaje_id}: {e}")
        return None


def _modificar_etiquetas(creds, mensaje_id: str, agregar: list = None, quitar: list = None):
    """
    @brief Añade y/o quita etiquetas de Gmail a un mensaje.
    @param agregar Lista de IDs de etiquetas a añadir (ej. ['INBOX', 'SPAM']).
    @param quitar  Lista de IDs de etiquetas a quitar.
    """
    servicio = obtener_servicio(creds)
    body = {}
    if agregar:
        body["addLabelIds"]    = agregar
    if quitar:
        body["removeLabelIds"] = quitar
    return servicio.users().messages().modify(userId="me", id=mensaje_id, body=body).execute()


def archivar_correo(creds, mensaje_id: str):
    """@brief Quita el label INBOX (archiva el correo)."""
    return _modificar_etiquetas(creds, mensaje_id, quitar=["INBOX"])


def desarchivar_correo(creds, mensaje_id: str):
    """@brief Añade el label INBOX de vuelta (desarchiva el correo)."""
    return _modificar_etiquetas(creds, mensaje_id, agregar=["INBOX"])


def mover_a_restringidos(creds, mensaje_id: str):
    """@brief Añade label SPAM y quita INBOX — mueve a la carpeta Spam de Gmail."""
    return _modificar_etiquetas(creds, mensaje_id, agregar=["SPAM"], quitar=["INBOX"])


def restaurar_de_restringidos(creds, mensaje_id: str):
    """@brief Quita label SPAM y añade INBOX — restaura el correo a la bandeja principal."""
    return _modificar_etiquetas(creds, mensaje_id, quitar=["SPAM"], agregar=["INBOX"])


def eliminar_correo(creds, mensaje_id: str):
    """@brief Mueve el correo a la Papelera de Gmail (messages.trash)."""
    servicio = obtener_servicio(creds)
    return servicio.users().messages().trash(userId="me", id=mensaje_id).execute()


def activar_gmail_push(creds, topic_name: str) -> dict:
    """
    @brief Registra un watch() en Gmail para recibir notificaciones Pub/Sub cuando llegan correos nuevos.
    El watch expira cada 7 días — debe renovarse periódicamente (Cloud Scheduler lo gestiona).
    @param creds      Credenciales OAuth del usuario.
    @param topic_name Nombre completo del topic Pub/Sub (ej. projects/PROJECT/topics/TOPIC).
    @return Dict con historyId y expiration del watch activo.
    """
    servicio = obtener_servicio(creds)
    body = {
        "labelIds":  ["INBOX"],
        "topicName": topic_name,
    }
    resultado = servicio.users().watch(userId="me", body=body).execute()
    logger.info(f"Gmail watch activo — expira: {resultado.get('expiration')}")
    return resultado


def desactivar_gmail_push(creds) -> None:
    """@brief Cancela el watch() activo en Gmail para el usuario."""
    servicio = obtener_servicio(creds)
    servicio.users().stop(userId="me").execute()
    logger.info("Gmail watch cancelado.")


def obtener_perfil_usuario(creds) -> dict:
    """
    @brief Obtiene el email y el total de mensajes del perfil Gmail del usuario autenticado.
    @param creds Objeto Credentials del usuario autenticado.
    @return Dict con 'email' y 'total_correos'.
    """
    try:
        servicio = obtener_servicio(creds)
        perfil   = servicio.users().getProfile(userId="me").execute()
        return {
            "email":          perfil.get("emailAddress", ""),
            "total_correos":  perfil.get("messagesTotal", 0),
        }
    except Exception as e:
        logger.error(f"Error obteniendo perfil: {e}")
        return {"email": "", "total_correos": 0}
