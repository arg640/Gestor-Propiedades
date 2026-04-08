import streamlit as st
import streamlit.components.v1 as components
import os, re, uuid, json, tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo
from supabase import create_client, Client

from extractor import extraer_datos_contrato
from generador import generar_recibo

# ── CONSTANTS ─────────────────────────────────────────────────────────────────
# Streamlit Cloud: usa st.secrets. Local/Replit: usa variables de entorno.
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except Exception:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PLANTILLA       = "attached_assets/Plantilla_recibos_1775446398445.docx"
REGLAMENTOS_DIR = "reglamentos"
CONTRATOS_DIR   = "contratos_pdf"
META_FILE       = "contratos_meta.json"
MEXICO_TZ       = ZoneInfo("America/Mexico_City")
METODOS_COBRO   = ["Entregar a dueño", "Cuota de vecindario", "Depósito"]

for d in (REGLAMENTOS_DIR, CONTRATOS_DIR):
    os.makedirs(d, exist_ok=True)

# ── HELPERS: Fecha/Hora México ────────────────────────────────────────────────
_MESES_NUM = {"ENERO":1,"FEBRERO":2,"MARZO":3,"ABRIL":4,"MAYO":5,"JUNIO":6,
              "JULIO":7,"AGOSTO":8,"SEPTIEMBRE":9,"OCTUBRE":10,"NOVIEMBRE":11,"DICIEMBRE":12}

def _hoy() -> date:
    return datetime.now(tz=MEXICO_TZ).date()

def _ahora() -> datetime:
    return datetime.now(tz=MEXICO_TZ)

def parsear_fecha(s: str) -> str:
    m = re.search(r'(\d{1,2}).*?(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO'
                  r'|SEPTIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE).*?(\d{4})', s, re.IGNORECASE)
    if m:
        return f"{int(m.group(3)):04d}-{_MESES_NUM[m.group(2).upper()]:02d}-{int(m.group(1)):02d}"
    m2 = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m2:
        return f"{m2.group(3)}-{int(m2.group(2)):02d}-{int(m2.group(1)):02d}"
    return s

def parsear_monto(s: str) -> float:
    return float(s.replace("$","").replace(" MXN","").replace(",","").strip())

def prioridad_pago(dia_pago: int) -> int:
    return (dia_pago - _hoy().day + 31) % 31

# ── LOCAL META JSON ───────────────────────────────────────────────────────────
def _load_meta() -> dict:
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_meta(cid: str, data: dict):
    meta = _load_meta()
    meta[cid] = {**meta.get(cid, {}), **data}
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def _add_pago_meta(cid: str, mes: int, anio: int):
    meta = _load_meta()
    if cid not in meta:
        meta[cid] = {}
    pagos = meta[cid].get("pagos", [])
    entry = {"mes": mes, "anio": anio}
    if entry not in pagos:
        pagos.append(entry)
    meta[cid]["pagos"] = pagos
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

# ── PAGOS SUPABASE ────────────────────────────────────────────────────────────
def _cargar_pagos_mes(meta: dict) -> set:
    """Return set of contrato_ids with payment for current month."""
    hoy = _hoy()
    mes, anio = hoy.month, hoy.year
    pagados = set()

    # From Supabase
    try:
        r = supabase.table("pagos").select("contrato_id,mes,anio,fecha_pago").execute()
        for p in r.data:
            ok = False
            if p.get("mes") == mes and p.get("anio") == anio:
                ok = True
            elif p.get("fecha_pago"):
                try:
                    fp = date.fromisoformat(str(p["fecha_pago"])[:10])
                    ok = fp.month == mes and fp.year == anio
                except Exception:
                    pass
            if ok and p.get("contrato_id"):
                pagados.add(p["contrato_id"])
    except Exception:
        pass

    # From local meta (backup)
    for cid, cdata in meta.items():
        for pago in cdata.get("pagos", []):
            if pago.get("mes") == mes and pago.get("anio") == anio:
                pagados.add(cid)

    return pagados

def _registrar_pago(cid: str, monto: float):
    hoy = _hoy()
    mes, anio = hoy.month, hoy.year
    today_iso = hoy.isoformat()

    for payload in [
        {"contrato_id": cid, "mes": mes, "anio": anio, "monto": monto, "fecha_pago": today_iso},
        {"contrato_id": cid, "mes": mes, "anio": anio, "monto": monto},
        {"contrato_id": cid, "mes": mes, "anio": anio},
        {"contrato_id": cid, "monto": monto, "fecha_pago": today_iso},
        {"contrato_id": cid, "fecha_pago": today_iso},
    ]:
        try:
            supabase.table("pagos").insert(payload).execute()
            break
        except Exception:
            continue

    _add_pago_meta(cid, mes, anio)

# ── SUPABASE SAVE ─────────────────────────────────────────────────────────────
def guardar_en_supabase(datos: dict, reglamento_bytes=None, reglamento_nombre=None) -> str:
    nombre_id = (f"Finca {datos['num_finca']}" if datos.get("num_finca")
                 else datos.get("direccion_coto", "Sin identificador"))
    monto     = parsear_monto(datos["monto_renta"])
    fecha_fin = parsear_fecha(datos["fecha_vencimiento"])
    dia_pago  = int(datos.get("dia_pago_mensual", 1))
    arrendador = datos.get("nombre_arrendador", "")

    url_reglamento = None
    if reglamento_bytes and reglamento_nombre:
        ext  = os.path.splitext(reglamento_nombre)[1] or ".pdf"
        ruta = os.path.join(REGLAMENTOS_DIR, f"{uuid.uuid4().hex}{ext}")
        with open(ruta, "wb") as f:
            f.write(reglamento_bytes)
        url_reglamento = ruta

    # Propiedad
    prop_r = supabase.table("propiedades").select("id").eq("nombre_identificador", nombre_id).execute()
    if prop_r.data:
        propiedad_id = prop_r.data[0]["id"]
        upd = {}
        if url_reglamento: upd["url_reglamento_pdf"] = url_reglamento
        if arrendador:     upd["nombre_arrendador"]  = arrendador
        if upd:
            try:
                supabase.table("propiedades").update(upd).eq("id", propiedad_id).execute()
            except Exception:
                safe = {k: v for k, v in upd.items() if k != "nombre_arrendador"}
                if safe:
                    supabase.table("propiedades").update(safe).eq("id", propiedad_id).execute()
    else:
        ins = {"nombre_identificador": nombre_id, "direccion": datos.get("direccion_coto", "")}
        if url_reglamento: ins["url_reglamento_pdf"] = url_reglamento
        if arrendador:     ins["nombre_arrendador"]  = arrendador
        try:
            propiedad_id = supabase.table("propiedades").insert(ins).execute().data[0]["id"]
        except Exception:
            ins.pop("nombre_arrendador", None)
            propiedad_id = supabase.table("propiedades").insert(ins).execute().data[0]["id"]

    # Inquilino
    inq_r = supabase.table("inquilinos").select("id").eq("nombre_completo", datos["nombre_inquilino"]).execute()
    if inq_r.data:
        inquilino_id = inq_r.data[0]["id"]
    else:
        inquilino_id = supabase.table("inquilinos").insert(
            {"nombre_completo": datos["nombre_inquilino"]}
        ).execute().data[0]["id"]

    # Contrato
    contrato_id = supabase.table("contratos").insert({
        "propiedad_id": propiedad_id, "inquilino_id": inquilino_id,
        "monto_renta": monto, "fecha_fin": fecha_fin,
        "dia_pago_mensual": dia_pago, "activo": True,
    }).execute().data[0]["id"]
    return contrato_id

def eliminar_contrato(cid: str):
    supabase.table("contratos").delete().eq("id", cid).execute()

def _pdf_path(cid: str) -> str:
    return os.path.join(CONTRATOS_DIR, f"{cid}.pdf")

# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Gestor de Propiedades", page_icon="🏠",
                   layout="wide", initial_sidebar_state="expanded")

# ── PWA: manifest + service worker ───────────────────────────────────────────
components.html("""
<script>
(function(){
  try {
    var lnk = window.parent.document.createElement('link');
    lnk.rel = 'manifest'; lnk.href = '/app/static/manifest.json';
    window.parent.document.head.appendChild(lnk);
  } catch(e){}
  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/app/static/sw.js')
      .then(function(r){console.log('SW ok',r.scope);})
      .catch(function(e){console.log('SW:',e.message);});
  }
})();
</script>
""", height=0, scrolling=False)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
html,body,[class*="css"]{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}
#MainMenu,footer{visibility:hidden;}

/* Big tap-friendly buttons */
div.stButton>button,div.stDownloadButton>button{
    width:100%;min-height:54px;padding:.85rem 1.2rem;font-size:1.05rem;
    border-radius:14px;font-weight:700;border:none;cursor:pointer;
    transition:opacity .15s,transform .1s;letter-spacing:.01em;
}
div.stButton>button:active,div.stDownloadButton>button:active{transform:scale(.97);}
div.stButton>button:hover,div.stDownloadButton>button:hover{opacity:.87;}

/* Property cards */
.prop-card{border-radius:14px;padding:16px 20px;margin-bottom:10px;
    box-shadow:0 2px 10px rgba(0,0,0,.10);border-left:7px solid #ccc;color:#111 !important;}
.card-red   {background:#fff1f1;border-left-color:#e53935;}
.card-orange{background:#fff8f0;border-left-color:#fb8c00;}
.card-yellow{background:#fffde7;border-left-color:#f9a825;}
.card-green {background:#f1f8e9;border-left-color:#43a047;}
.card-blue  {background:#e8f4fd;border-left-color:#1976d2;}
.prop-card *{color:#111 !important;}

/* Badges */
.card-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.7rem;
    font-weight:800;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;margin-right:4px;}
.badge-expired{background:#b71c1c;color:#fff !important;}
.badge-red    {background:#e53935;color:#fff !important;}
.badge-orange {background:#fb8c00;color:#fff !important;}
.badge-yellow {background:#f9a825;color:#333 !important;}
.badge-green  {background:#43a047;color:#fff !important;}
.badge-paid   {background:#1976d2;color:#fff !important;}
.badge-unpaid {background:#e53935;color:#fff !important;}
.card-badge *{color:inherit !important;}

.card-title{font-size:1.1rem;font-weight:800;margin:0 0 3px;color:#0d0d0d !important;}
.card-sub  {font-size:.88rem;color:#333 !important;margin:0;}
.card-meta {font-size:.82rem;color:#444 !important;margin-top:5px;}
.card-meta strong{color:#111 !important;}
.card-entrega{font-size:.82rem;color:#333 !important;margin-top:5px;
    padding:5px 10px;border-radius:8px;background:rgba(0,0,0,.04);display:inline-block;}
.card-entrega strong{color:#111 !important;}

[data-testid="stFileUploadDropzone"]{border-radius:12px !important;border:2px dashed #aaa !important;padding:20px !important;}

@media(max-width:640px){
    .prop-card{padding:12px 14px;}
    .card-title{font-size:1rem;}
    div.stButton>button,div.stDownloadButton>button{min-height:58px;font-size:1.1rem;}
}
</style>
""", unsafe_allow_html=True)

# ── SESSION STATE ─────────────────────────────────────────────────────────────
def _init(k, v):
    if k not in st.session_state: st.session_state[k] = v

_init("datos_pdf",     None)
_init("pdf_bytes",     None)
_init("confirm_del",   {})
_init("expanded",      {})
_init("recibo_estado", {})   # cid → None | "dup_warn" | {"bytes":..,"filename":..}

# ── SIDEBAR: Nuevo Contrato ───────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📄 Nuevo Contrato")
    st.caption("Sube el PDF — revisa antes de guardar.")

    archivo_pdf = st.file_uploader("Contrato PDF", type=["pdf"],
                                   key="upload_contrato", label_visibility="collapsed")

    if archivo_pdf and st.session_state.datos_pdf is None:
        raw = archivo_pdf.read()
        st.session_state.pdf_bytes = raw
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(raw); ruta_tmp = tmp.name
        with st.spinner("Leyendo contrato..."):
            try:
                st.session_state.datos_pdf = extraer_datos_contrato(ruta_tmp)
            except Exception as e:
                st.error(f"Error: {e}")
            finally:
                os.unlink(ruta_tmp)

    if st.session_state.datos_pdf:
        d = st.session_state.datos_pdf
        st.success("✅ Revisa y corrige si necesario:")

        with st.form("form_validacion"):
            d["nombre_inquilino"]  = st.text_input("👤 Inquilino",     value=d.get("nombre_inquilino") or "")
            d["nombre_arrendador"] = st.text_input("🏠 Arrendador",    value=d.get("nombre_arrendador") or "")
            d["monto_renta"]       = st.text_input("💰 Renta mensual", value=d.get("monto_renta") or "")
            d["num_finca"]         = st.text_input("🔢 Núm. Finca",    value=d.get("num_finca") or "")
            d["direccion_coto"]    = st.text_input("📍 Dirección",     value=d.get("direccion_coto") or "")
            d["dia_pago_mensual"]  = st.number_input("📅 Día de pago",
                value=int(d.get("dia_pago_mensual") or 1), min_value=1, max_value=31)
            d["fecha_vencimiento"] = st.text_input("🗓 Fecha fin",     value=d.get("fecha_vencimiento") or "")

            st.markdown("---")
            d["metodo_cobro"] = st.selectbox("💳 Método de cobro", METODOS_COBRO)
            if d["metodo_cobro"] != "Depósito":
                d["domicilio_dueno"] = st.text_input(
                    "🏡 Domicilio del dueño",
                    value=d.get("domicilio_dueno") or "",
                    placeholder="Ej. Calle Agricultores 5255-2, Jardines de Guadalupe, Zapopan",
                )
            else:
                d["domicilio_dueno"] = ""

            st.markdown("**Reglamento del vecindario** *(opcional)*")
            reglamento = st.file_uploader("PDF reglamento", type=["pdf"],
                                          key="upload_reglamento", label_visibility="collapsed")

            c1, c2 = st.columns(2)
            confirmar = c1.form_submit_button("💾 Guardar",   use_container_width=True)
            cancelar  = c2.form_submit_button("✖ Cancelar", use_container_width=True)

        if confirmar:
            reg_bytes  = reglamento.read() if reglamento else None
            reg_nombre = reglamento.name   if reglamento else None
            with st.spinner("Guardando..."):
                try:
                    cid = guardar_en_supabase(d, reg_bytes, reg_nombre)
                    if st.session_state.pdf_bytes:
                        with open(_pdf_path(cid), "wb") as f:
                            f.write(st.session_state.pdf_bytes)
                    _save_meta(cid, {
                        "nombre_arrendador": (d.get("nombre_arrendador") or "").strip(),
                        "metodo_cobro":      d.get("metodo_cobro", ""),
                        "domicilio_dueno":   (d.get("domicilio_dueno") or "").strip(),
                    })
                    st.success("✅ ¡Guardado!")
                    st.session_state.datos_pdf = None
                    st.session_state.pdf_bytes = None
                    st.rerun()
                except Exception as e:
                    st.error(f"Error: {e}")

        if cancelar:
            st.session_state.datos_pdf = None
            st.session_state.pdf_bytes = None
            st.rerun()

# ── DASHBOARD: Cargar datos ───────────────────────────────────────────────────
st.markdown("# 🏠 Gestor de Propiedades")
st.divider()

try:
    resp = supabase.table("contratos").select(
        "id,monto_renta,fecha_fin,dia_pago_mensual,"
        "propiedades(id,nombre_identificador,direccion,url_reglamento_pdf,nombre_arrendador),"
        "inquilinos(nombre_completo)"
    ).eq("activo", True).execute()
    contratos = resp.data or []
except Exception:
    try:
        resp = supabase.table("contratos").select(
            "id,monto_renta,fecha_fin,dia_pago_mensual,"
            "propiedades(id,nombre_identificador,direccion,url_reglamento_pdf),"
            "inquilinos(nombre_completo)"
        ).eq("activo", True).execute()
        contratos = resp.data or []
    except Exception as e2:
        st.error(f"Error Supabase: {e2}")
        contratos = []

if not contratos:
    st.info("No hay contratos activos. Sube un PDF en la barra lateral para comenzar.")
    st.stop()

hoy  = _hoy()
meta = _load_meta()
pagados_mes = _cargar_pagos_mes(meta)
mes_str = _ahora().strftime("%Y%m")

# ── Compute per-contract data for sorting and alerts ─────────────────────────
def _enrich(c):
    try:    ff = date.fromisoformat(c["fecha_fin"])
    except: ff = hoy
    vencido = ff < hoy
    pagado  = c["id"] in pagados_mes
    prio    = prioridad_pago(c["dia_pago_mensual"])
    dias_ff = (ff - hoy).days
    return vencido, pagado, prio, ff, dias_ff

# ── Sort: expired(0) → unpaid by proximity(1) → paid(2) ─────────────────────
def sort_key(c):
    vencido, pagado, prio, ff, _ = _enrich(c)
    if vencido: return (0, prio)
    if not pagado: return (1, prio)
    return (2, prio)

contratos.sort(key=sort_key)

# ── Alert banner + browser notifications ─────────────────────────────────────
alertas_banner = []
alertas_notif  = []

for c in contratos:
    vencido, pagado, prio, ff, dias_ff = _enrich(c)
    inq = c["inquilinos"]["nombre_completo"]
    if vencido:
        alertas_banner.append(("error", f"⚠️ **Contrato VENCIDO** — {inq}"))
    elif 0 < dias_ff <= 30:
        alertas_banner.append(("warning", f"⏰ Contrato de **{inq}** vence en **{dias_ff} días**"))
        alertas_notif.append(f"Contrato próximo a vencer: {inq} ({dias_ff} días)")
    if prio <= 5 and not pagado and not vencido:
        alertas_banner.append(("warning", f"🔴 **{inq}**: cobro en {prio} día{'s' if prio!=1 else ''}"))
        alertas_notif.append(f"Cobro urgente: {inq} en {prio} días")

for tipo, msg in alertas_banner:
    if tipo == "error":   st.error(msg)
    else:                 st.warning(msg)

# ── Browser notifications (works when tab is open) ───────────────────────────
if alertas_notif:
    notif_payload = json.dumps(alertas_notif)
    components.html(f"""
    <script>
    (function(){{
        var msgs = {notif_payload};
        function disparar(){{
            msgs.forEach(function(m){{
                try{{ new Notification('\U0001f3e0 Gestor de Propiedades',{{body:m,icon:''}}) }}catch(e){{}}
            }});
        }}
        if(!('Notification' in window))return;
        if(Notification.permission==='granted'){{disparar();}}
        else if(Notification.permission==='default'){{
            Notification.requestPermission().then(function(p){{if(p==='granted')disparar();}});
        }}
    }})();
    </script>
    """, height=0, scrolling=False)

# ── Contract cards ────────────────────────────────────────────────────────────
n = len(contratos)
st.markdown(f"**{n} contrato{'s' if n>1 else ''} activo{'s' if n>1 else ''}**")

for c in contratos:
    cid       = c["id"]
    inq       = c["inquilinos"]["nombre_completo"]
    prop_data = c["propiedades"]
    propiedad = prop_data["nombre_identificador"]
    direccion = prop_data.get("direccion") or "—"
    regl_path = prop_data.get("url_reglamento_pdf")
    arrendador_db = prop_data.get("nombre_arrendador") or ""
    dia_pago  = c["dia_pago_mensual"]
    monto     = c["monto_renta"]

    vencido, pagado, prio, fecha_fin, dias_ff = _enrich(c)

    # ── Card color based on status ───────────────────────────────────────
    if vencido:
        card_cls, prox_badge, prox_txt = "card-red",    "badge-expired", "⚠ Contrato Vencido"
    elif pagado:
        card_cls, prox_badge, prox_txt = "card-blue",   "badge-green",   f"🟢 En {prio} días"
    elif prio == 0:
        card_cls, prox_badge, prox_txt = "card-red",    "badge-red",     "🔴 Cobrar Hoy"
    elif prio <= 3:
        card_cls, prox_badge, prox_txt = "card-orange", "badge-orange",  f"🟠 En {prio} días"
    elif prio <= 7:
        card_cls, prox_badge, prox_txt = "card-yellow", "badge-yellow",  f"🟡 En {prio} días"
    else:
        card_cls, prox_badge, prox_txt = "card-green",  "badge-green",   f"🟢 {prio} días"

    pago_badge = ('<span class="card-badge badge-paid">🟢 PAGADO</span>'
                  if pagado else
                  '<span class="card-badge badge-unpaid">🔴 SIN PAGO</span>')

    # ── Resolve arrendador & extra meta ──────────────────────────────────
    c_meta        = meta.get(cid, {})
    arrendador    = arrendador_db or c_meta.get("nombre_arrendador", "")
    metodo_cobro  = c_meta.get("metodo_cobro", "")
    domicilio     = c_meta.get("domicilio_dueno", "")

    dueno_html = (f"&nbsp;·&nbsp; Dueño: <strong>{arrendador}</strong>" if arrendador else "")

    # ── Delivery icon line ────────────────────────────────────────────────
    _COBRO_ICON = {
        "Entregar a dueño":     "🏠",
        "Cuota de vecindario":  "🏘️",
        "Depósito":             "🏦",
    }
    if metodo_cobro:
        icono = _COBRO_ICON.get(metodo_cobro, "💳")
        if domicilio and metodo_cobro != "Depósito":
            entrega_html = (
                f'<p class="card-entrega">{icono} <strong>{metodo_cobro}</strong>'
                f'&nbsp;→&nbsp; {domicilio}</p>'
            )
        else:
            entrega_html = f'<p class="card-entrega">{icono} <strong>{metodo_cobro}</strong></p>'
    else:
        entrega_html = '<p class="card-entrega" style="opacity:.55">📍 Método de entrega no configurado</p>'

    card_html = (
        f'<div class="prop-card {card_cls}">'
        f'<span class="card-badge {prox_badge}">{prox_txt}</span>{pago_badge}'
        f'<p class="card-title">{inq}</p>'
        f'<p class="card-sub">{propiedad} &nbsp;·&nbsp; {direccion}</p>'
        f'<p class="card-meta">Renta: <strong>${monto:,.2f}</strong>'
        f'&nbsp;·&nbsp; Pago: día <strong>{dia_pago}</strong>'
        f'&nbsp;·&nbsp; Vence: <strong>{fecha_fin.strftime("%d/%m/%Y")}</strong>'
        f'{dueno_html}</p>'
        f'{entrega_html}</div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)

    # ── Toggle button row ────────────────────────────────────────────────
    num_finca = re.sub(r"(?i)finca\s*", "", propiedad).strip()
    expanded  = st.session_state.expanded.get(cid, False)

    if vencido:
        t_col, n_col = st.columns([3, 2])
        if n_col.button("📄 Nuevo contrato", key=f"nuevo_{cid}", use_container_width=True):
            st.session_state.datos_pdf = None
            st.rerun()
    else:
        t_col = st.columns([1])[0]

    if t_col.button("▲ Cerrar" if expanded else "▼ Ver acciones",
                    key=f"toggle_{cid}", use_container_width=True):
        st.session_state.expanded[cid] = not expanded
        st.rerun()

    # ── Expanded panel ───────────────────────────────────────────────────
    if st.session_state.expanded.get(cid, False):

        # Resolve full receipt data
        arrendador_recibo = arrendador or ""
        datos_recibo = {
            "nombre_inquilino":    inq,
            "nombre_arrendador":   arrendador_recibo,
            "nombre_administrador": arrendador_recibo,
            "domicilio_dueno":     domicilio,
            "monto_renta":         f"${monto:,.2f} MXN",
            "num_finca":           num_finca,
            "direccion_coto":      direccion,
        }

        # ── Quick-edit arrendador if missing ─────────────────────────────
        if not arrendador_recibo:
            with st.expander("✏️ Nombre del dueño falta — toca aquí"):
                new_arr = st.text_input("Nombre del dueño", key=f"edit_arr_{cid}",
                                        placeholder="Ej. ALMA ROSA ZAIZAR FLORES")
                if st.button("💾 Guardar", key=f"save_arr_{cid}", use_container_width=True):
                    if new_arr.strip():
                        _save_meta(cid, {"nombre_arrendador": new_arr.strip()})
                        st.rerun()

        # ── Edit: Método de entrega ───────────────────────────────────────
        lbl_cobro = (f"✏️ Cambiar entrega · actual: {metodo_cobro}"
                     if metodo_cobro else "📍 Configurar dónde entregar el dinero")
        with st.expander(lbl_cobro):
            nuevo_metodo = st.selectbox("💳 Método de cobro", METODOS_COBRO,
                index=METODOS_COBRO.index(metodo_cobro) if metodo_cobro in METODOS_COBRO else 0,
                key=f"sel_cobro_{cid}")
            nuevo_dom = ""
            if nuevo_metodo != "Depósito":
                nuevo_dom = st.text_input(
                    "🏡 Domicilio del dueño",
                    value=domicilio,
                    key=f"inp_dom_{cid}",
                    placeholder="Ej. Calle Agricultores 5255-2, Jardines de Guadalupe, Zapopan",
                )
            if st.button("💾 Guardar entrega", key=f"save_cobro_{cid}", use_container_width=True):
                _save_meta(cid, {"metodo_cobro": nuevo_metodo, "domicilio_dueno": nuevo_dom.strip()})
                st.rerun()

        # ── RECIBO: State-based flow ──────────────────────────────────────
        st.markdown("**📄 Recibo del mes**")
        estado = st.session_state.recibo_estado.get(cid)

        if estado is None:
            if st.button("📄 Preparar Recibo", key=f"prep_{cid}", use_container_width=True):
                if cid in pagados_mes:
                    st.session_state.recibo_estado[cid] = "dup_warn"
                else:
                    try:
                        docx = generar_recibo(datos_recibo, PLANTILLA)
                        _registrar_pago(cid, monto)
                        pagados_mes.add(cid)
                        st.session_state.recibo_estado[cid] = {
                            "bytes": docx,
                            "filename": f"recibo_{num_finca}_{mes_str}.docx",
                        }
                    except Exception as e:
                        st.error(f"Error al generar recibo: {e}")
                st.rerun()

        elif estado == "dup_warn":
            st.warning("⚠️ Ya se registró un recibo este mes. ¿Deseas generar otro?")
            w1, w2 = st.columns(2)
            if w1.button("✅ Sí, generar otro", key=f"dup_ok_{cid}", use_container_width=True):
                try:
                    docx = generar_recibo(datos_recibo, PLANTILLA)
                    _registrar_pago(cid, monto)
                    st.session_state.recibo_estado[cid] = {
                        "bytes": docx,
                        "filename": f"recibo_{num_finca}_{mes_str}.docx",
                    }
                except Exception as e:
                    st.error(f"Error: {e}")
                st.rerun()
            if w2.button("✖ Cancelar", key=f"dup_no_{cid}", use_container_width=True):
                st.session_state.recibo_estado[cid] = None
                st.rerun()

        elif isinstance(estado, dict):
            dl_c, rst_c = st.columns([3, 1])
            dl_c.download_button(
                "📥 Descargar Recibo",
                data=estado["bytes"],
                file_name=estado["filename"],
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key=f"dl_{cid}",
                use_container_width=True,
            )
            if rst_c.button("🔄", key=f"rst_{cid}", use_container_width=True,
                            help="Preparar otro recibo"):
                st.session_state.recibo_estado[cid] = None
                st.rerun()

        st.markdown("---")

        # ── Other actions: 3 columns ──────────────────────────────────────
        b1, b2, b3 = st.columns(3)

        # PDF Contrato
        pdf_p = _pdf_path(cid)
        if os.path.exists(pdf_p):
            with open(pdf_p, "rb") as f:
                b1.download_button("📑 Contrato PDF", data=f.read(),
                    file_name=f"contrato_{num_finca}.pdf", mime="application/pdf",
                    key=f"pdf_{cid}", use_container_width=True)
        else:
            b1.button("📑 Sin PDF", disabled=True, key=f"pdf_d_{cid}", use_container_width=True)

        # Reglamento
        if regl_path and os.path.exists(regl_path):
            with open(regl_path, "rb") as f:
                b2.download_button("📋 Reglamento", data=f.read(),
                    file_name=os.path.basename(regl_path), mime="application/pdf",
                    key=f"regl_{cid}", use_container_width=True)
        else:
            b2.button("📋 Sin Reglamento", disabled=True, key=f"regl_d_{cid}", use_container_width=True)

        # Eliminar
        if not st.session_state.confirm_del.get(cid):
            if b3.button("🗑 Eliminar", key=f"del_{cid}", use_container_width=True):
                st.session_state.confirm_del[cid] = True
                st.rerun()
        else:
            st.warning(f"¿Eliminar el contrato de **{inq}**? No se puede deshacer.")
            e1, e2 = st.columns(2)
            if e1.button("✅ Sí, eliminar", key=f"del_ok_{cid}", use_container_width=True):
                eliminar_contrato(cid)
                if os.path.exists(pdf_p): os.remove(pdf_p)
                st.session_state.confirm_del.pop(cid, None)
                st.session_state.expanded.pop(cid, None)
                st.rerun()
            if e2.button("✖ Cancelar", key=f"del_no_{cid}", use_container_width=True):
                st.session_state.confirm_del.pop(cid, None)
                st.rerun()

    st.markdown("<hr style='margin:8px 0;opacity:.15'>", unsafe_allow_html=True)
