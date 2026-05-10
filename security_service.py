"""
@file security_service.py
@brief Análisis de seguridad de correos: autenticación SPF/DKIM/DMARC y detección de URLs maliciosas.
"""
import re
import os
import logging
import requests
from typing import List, Dict

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^\s<>"\')\]}>]+', re.IGNORECASE)
_URL_CLEAN_RE = re.compile(r'[.,;:!?)>\]]+$')

_SAFE_BROWSING_ENDPOINT = (
    "https://safebrowsing.googleapis.com/v4/threatMatches:find?key={key}"
)
_THREAT_TYPES = [
    "MALWARE",
    "SOCIAL_ENGINEERING",
    "UNWANTED_SOFTWARE",
    "POTENTIALLY_HARMFUL_APPLICATION",
]
_THREAT_LABELS = {
    "MALWARE":                        "Malware",
    "SOCIAL_ENGINEERING":             "Phishing",
    "UNWANTED_SOFTWARE":              "Software no deseado",
    "POTENTIALLY_HARMFUL_APPLICATION":"App dañina",
}
_MAX_URLS_PER_CORREO  = 20
_MAX_URLS_PER_REQUEST = 500


def analizar_autenticacion(headers_auth: dict) -> dict:
    """
    @brief Extrae el resultado de SPF, DKIM y DMARC del header Authentication-Results.
    @param headers_auth Dict con las cabeceras de autenticación del correo.
    @return Dict con claves spf, dkim, dmarc (valores: 'pass', 'fail', 'softfail', 'none', etc.).
    """
    auth  = headers_auth.get("Authentication-Results", "") or ""
    arc   = headers_auth.get("ARC-Authentication-Results", "") or ""
    rspf  = headers_auth.get("Received-SPF", "") or ""
    combined = auth + " " + arc

    def _extraer(patron, texto):
        m = re.search(patron, texto, re.I)
        return m.group(1).lower() if m else "none"

    spf   = _extraer(r'spf=(pass|fail|softfail|neutral|none|temperror|permerror)', combined)
    if spf == "none" and rspf:
        spf = "pass" if "pass" in rspf.lower() else ("fail" if "fail" in rspf.lower() else "none")

    dkim  = _extraer(r'dkim=(pass|fail|none|neutral|policy|temperror|permerror)', combined)
    dmarc = _extraer(r'dmarc=(pass|fail|none|bestguesspass)', combined)

    return {"spf": spf, "dkim": dkim, "dmarc": dmarc}


def extraer_urls(texto: str, html: str = "") -> List[str]:
    """
    @brief Extrae URLs únicas del cuerpo plano y HTML del correo.
    @param texto  Cuerpo en texto plano.
    @param html   Cuerpo en HTML.
    @return Lista de URLs únicas (máximo _MAX_URLS_PER_CORREO).
    """
    raw = _URL_RE.findall((texto or "")[:8000] + " " + (html or "")[:12000])
    cleaned = list(dict.fromkeys(_URL_CLEAN_RE.sub("", u) for u in raw))
    return cleaned[:_MAX_URLS_PER_CORREO]


def verificar_urls_safe_browsing(urls: List[str], api_key: str) -> List[dict]:
    """
    @brief Verifica una lista de URLs contra Google Safe Browsing API v4.
    @param urls    Lista de URLs a verificar.
    @param api_key Clave de API de Google Cloud.
    @return Lista de amenazas encontradas, cada una con url, tipo y threatType.
    """
    if not urls or not api_key:
        return []

    amenazas = []
    for i in range(0, len(urls), _MAX_URLS_PER_REQUEST):
        chunk = urls[i:i + _MAX_URLS_PER_REQUEST]
        body  = {
            "client":     {"clientId": "ilico", "clientVersion": "1.0"},
            "threatInfo": {
                "threatTypes":      _THREAT_TYPES,
                "platformTypes":    ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries":    [{"url": u} for u in chunk],
            },
        }
        try:
            r = requests.post(
                _SAFE_BROWSING_ENDPOINT.format(key=api_key),
                json=body, timeout=6,
            )
            if r.status_code == 200:
                for match in r.json().get("matches", []):
                    amenazas.append({
                        "url":        match.get("threat", {}).get("url", ""),
                        "tipo":       _THREAT_LABELS.get(match.get("threatType", ""),
                                                         match.get("threatType", "")),
                        "threatType": match.get("threatType", ""),
                    })
            else:
                logger.warning(f"Safe Browsing API status {r.status_code}")
        except Exception as e:
            logger.warning(f"Error Safe Browsing: {e}")

    return amenazas


def nivel_seguridad(auth: dict, amenazas: List[dict], clasificacion: str) -> str:
    """
    @brief Calcula el nivel de seguridad consolidado del correo.
    @param auth          Resultado de analizar_autenticacion.
    @param amenazas      Lista de amenazas de URL detectadas.
    @param clasificacion Clasificación del modelo ('SPAM', 'HAM', 'SOSPECHOSO').
    @return 'peligro', 'advertencia' o 'seguro'.
    """
    if amenazas:
        return "peligro"

    fallos = sum([
        auth.get("spf")   not in ("pass",),
        auth.get("dkim")  not in ("pass",),
        auth.get("dmarc") not in ("pass", "bestguesspass"),
    ])

    if fallos >= 2 and clasificacion in ("SPAM", "SOSPECHOSO"):
        return "peligro"
    if fallos >= 2:
        return "advertencia"
    if fallos == 1:
        return "advertencia"
    return "seguro"


def analizar_lote(correos_clasificados: list) -> list:
    """
    @brief Añade análisis de seguridad a una lista de correos ya clasificados.
           Realiza una sola llamada batch a Safe Browsing por lote completo.
    @param correos_clasificados Lista de dicts con clasificacion, headers_auth, cuerpo, html_cuerpo.
    @return La misma lista con el campo 'seguridad' añadido a cada correo.
    """
    api_key = os.environ.get("GOOGLE_SAFE_BROWSING_KEY", "")

    # Paso 1: análisis de autenticación + extracción de URLs (sin llamadas externas)
    url_map: Dict[str, List[int]] = {}  # url → índices de correos que la contienen
    for idx, c in enumerate(correos_clasificados):
        auth    = analizar_autenticacion(c.get("headers_auth", {}))
        urls    = extraer_urls(c.get("cuerpo", ""), c.get("html_cuerpo", ""))
        c["seguridad"] = {
            "auth":            auth,
            "urls_analizadas": len(urls),
            "amenazas":        [],
            "nivel":           nivel_seguridad(auth, [], c.get("clasificacion", "")),
        }
        c["_urls_tmp"] = urls
        for url in urls:
            url_map.setdefault(url, []).append(idx)

    # Paso 2: verificación de URLs (una sola llamada batch)
    if api_key and url_map:
        amenazas_por_url = {
            a["url"]: a
            for a in verificar_urls_safe_browsing(list(url_map.keys()), api_key)
        }
        for idx, c in enumerate(correos_clasificados):
            correo_urls = c.pop("_urls_tmp", [])
            amenazas_correo = [amenazas_por_url[u] for u in correo_urls if u in amenazas_por_url]
            c["seguridad"]["amenazas"] = amenazas_correo
            c["seguridad"]["nivel"]    = nivel_seguridad(
                c["seguridad"]["auth"], amenazas_correo, c.get("clasificacion", "")
            )
    else:
        for c in correos_clasificados:
            c.pop("_urls_tmp", None)

    return correos_clasificados
