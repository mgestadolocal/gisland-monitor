"""
GisLand.cl — Monitor del Diario Oficial de Chile
Requiere: pip install requests beautifulsoup4 pypdf
"""

import json, smtplib, logging, hashlib, re, io, os
from datetime import date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

import requests
from bs4 import BeautifulSoup

CONFIG = {
    "gmail_usuario":  os.environ.get("GMAIL_USUARIO", ""),
    "gmail_password": os.environ.get("GMAIL_PASSWORD", ""),
    "email_destino":  os.environ.get("EMAIL_DESTINO", ""),
    "palabras_clave": [
        "humedal", "area silvestre protegida", "área silvestre protegida",
        "parque nacional", "reserva nacional", "monumento natural",
        "santuario de la naturaleza", "area marina protegida", "área marina protegida",
        "sitio ramsar", "poligono", "polígono", "cartografia", "cartografía",
        "delimitacion", "delimitación", "snaspe", "sbap", "plan de manejo",
        "corredor biologico", "corredor biológico", "zona de prohibicion",
        "zona de prohibición", "aguas subterraneas", "aguas subterráneas",
        "sector hidrogeologico", "sector hidrogeológico", "recursos hidricos",
        "recursos hídricos",
    ],
    "archivo_vistos":        "publicaciones_vistas.json",
    "archivo_publicaciones": "docs/publicaciones.json",
    "archivo_log":           "monitor.log",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["archivo_log"], encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)
BASE    = "https://www.diariooficial.interior.gob.cl"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; GisLandMonitor/1.0)"}


def cargar_vistos() -> set:
    p = Path(CONFIG["archivo_vistos"])
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def guardar_vistos(vistos: set):
    with open(CONFIG["archivo_vistos"], "w", encoding="utf-8") as f:
        json.dump(list(vistos), f, ensure_ascii=False, indent=2)


def cargar_publicaciones() -> list:
    p = Path(CONFIG["archivo_publicaciones"])
    if p.exists():
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []


def guardar_publicaciones(publicaciones: list):
    Path("docs").mkdir(exist_ok=True)
    with open(CONFIG["archivo_publicaciones"], "w", encoding="utf-8") as f:
        json.dump(publicaciones, f, ensure_ascii=False, indent=2)


def id_pub(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()


def obtener_urls_del_dia(fecha: date) -> list:
    url = f"{BASE}/edicionelectronica/index.php?date={fecha.strftime('%d-%m-%Y')}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Error descargando índice {fecha}: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    urls, vistos_set = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"/publicaciones/\d{4}/\d{2}/\d{2}/\d+/\d+/\d+", href):
            continue
        url_pub = href if href.startswith("http") else BASE + href
        if url_pub not in vistos_set:
            vistos_set.add(url_pub)
            urls.append({"url": url_pub, "fecha": fecha.isoformat()})

    log.info(f"  {fecha}: {len(urls)} PDFs encontrados")
    return urls


def extraer_titulo_y_texto(url: str) -> tuple:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ""
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        reader = PdfReader(io.BytesIO(r.content))
        p1 = reader.pages[0].extract_text() or ""
        titulo = extraer_titulo_del_texto(p1)
        texto_completo = p1
        for i in range(1, min(3, len(reader.pages))):
            texto_completo += " " + (reader.pages[i].extract_text() or "")
        return titulo, texto_completo
    except Exception as e:
        log.warning(f"  No se pudo leer PDF {url}: {e}")
        return "", ""


def extraer_titulo_del_texto(texto: str) -> str:
    patron = r"CVE \d+.*?(?:ORDEN GENERAL|ORDEN PARTICULAR|AVISOS)\s*"
    texto_limpio = re.sub(patron, "", texto, flags=re.DOTALL | re.IGNORECASE)
    m = re.search(r"((?:[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ\s\d°\.,\(\)\-\/Nº]+){3,})", texto_limpio)
    if m:
        return " ".join(m.group(1).split())[:250]
    lineas = [l.strip() for l in texto_limpio.splitlines() if len(l.strip()) > 20]
    return lineas[0][:250] if lineas else ""


def es_relevante(texto: str) -> list:
    t = texto.lower()
    return [kw for kw in CONFIG["palabras_clave"] if kw in t]


def enviar_email(publicaciones: list):
    asunto = f"GisLand.cl | {len(publicaciones)} nueva(s) publicacion(es) en el Diario Oficial - {date.today().strftime('%d/%m/%Y')}"
    filas = ""
    for pub in publicaciones:
        kws   = ", ".join(pub.get("keywords", []))
        titulo = pub.get("titulo") or pub["url"]
        filas += f"""
        <tr>
          <td style="padding:10px 8px;border-bottom:1px solid #eee;">
            <a href="{pub['url']}" style="color:#1a6b3a;font-weight:bold;text-decoration:none;">{titulo[:200]}</a><br>
            <small style="color:#888;">Fecha: {pub['fecha']} | Palabras clave: <em>{kws}</em></small>
          </td>
        </tr>"""

    html = f"""<html><body style="font-family:Arial,sans-serif;background:#f9f9f9;">
      <div style="max-width:680px;margin:30px auto;background:#fff;padding:30px;border-radius:8px;">
        <h2 style="color:#1a6b3a;">GisLand.cl — Alerta Diario Oficial</h2>
        <p>{len(publicaciones)} publicacion(es) relevante(s) el {date.today().strftime('%d/%m/%Y')}:</p>
        <table width="100%" cellspacing="0" style="border-collapse:collapse;">{filas}</table>
        <p style="font-size:12px;color:#aaa;margin-top:24px;">Generado por <a href="https://gisland.cl" style="color:#1a6b3a;">GisLand.cl</a></p>
      </div></body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = CONFIG["gmail_usuario"]
    msg["To"]      = CONFIG["email_destino"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(CONFIG["gmail_usuario"], CONFIG["gmail_password"])
            s.sendmail(CONFIG["gmail_usuario"], CONFIG["email_destino"], msg.as_string())
        log.info(f"✅ Email enviado a {CONFIG['email_destino']}")
    except Exception as e:
        log.error(f"❌ Error enviando email: {e}")


def ejecutar():
    log.info("=" * 50)
    log.info("  GisLand Monitor - iniciando revision")
    log.info("=" * 50)

    vistos        = cargar_vistos()
    historial     = cargar_publicaciones()
    hoy           = date.today()
    todas         = []

    for delta in [0, 1, 2]:
        todas.extend(obtener_urls_del_dia(hoy - timedelta(days=delta)))

    por_url = {p["url"]: p for p in todas}
    todas   = list(por_url.values())
    log.info(f"Total PDFs unicos a revisar: {len(todas)}")

    nuevas = []
    for i, pub in enumerate(todas, 1):
        pid = id_pub(pub["url"])
        if pid in vistos:
            continue
        log.info(f"  [{i}/{len(todas)}] Leyendo PDF...")
        titulo, texto = extraer_titulo_y_texto(pub["url"])
        kws = es_relevante(texto)
        vistos.add(pid)
        if kws:
            pub["titulo"]   = titulo
            pub["keywords"] = kws
            nuevas.append(pub)
            log.info(f"    ✅ RELEVANTE: {titulo[:80]}")
        else:
            log.info(f"    - No relevante: {titulo[:60]}")

    log.info(f"Nuevas relevantes: {len(nuevas)}")

    if nuevas:
        enviar_email(nuevas)
        # Agregar al historial (más recientes primero)
        historial = nuevas + historial
        # Mantener solo los últimos 500 registros
        historial = historial[:500]
        guardar_publicaciones(historial)

    guardar_vistos(vistos)
    log.info("Revision finalizada.\n")


if __name__ == "__main__":
    ejecutar()
