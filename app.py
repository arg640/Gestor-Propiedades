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

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("⚠️ Error: Credenciales de Supabase no encontradas.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

PLANTILLA       = "attached_assets/Plantilla_recibos_1775446398445.docx"
REGLAMENTOS_DIR = "reglamentos"
CONTRATOS_DIR   = "contratos_pdf"
MEXICO_TZ       = ZoneInfo("America/Mexico_City")
METODOS_COBRO   = ["Entregar a dueño", "Cuota de vecindario", "Depósito"]

for d in (REGLAMENTOS_DIR, CONTRATOS_DIR):
    os.makedirs(d, exist_ok=True)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_now():
    return datetime.now(MEXICO_TZ)

def cargar_contratos():
    try:
        res = supabase.table("contratos").select("*").execute()
        return res.data if res.data else []
    except Exception as e:
        st.error(f"Error cargando contratos: {e}")
        return []

def eliminar_contrato(cid):
    """Solo borra los pagos relacionados antes de borrar el contrato"""
    try:
        # EL ARREGLO: Borrar pagos asociados para que Supabase permita borrar el contrato
        supabase.table("pagos").delete().eq("contrato_id", cid).execute()
        
        # Borrar contrato
        supabase.table("contratos").delete().eq("id", cid).execute()
        st.success("Propiedad eliminada.")
        st.rerun()
    except Exception as e:
        st.error(f"Error al eliminar: {e}")

def registrar_pago_db(cid, mes, anio):
    try:
        data = {
            "contrato_id": cid,
            "mes_correspondiente": mes,
            "anio_correspondiente": anio,
            "fecha_pago": get_now().isoformat()
        }
        supabase.table("pagos").insert(data).execute()
        return True
    except: return False

def verificar_pago_mes_actual(cid):
    now = get_now()
    try:
        res = supabase.table("pagos").select("*")\
            .eq("contrato_id", cid)\
            .eq("mes_correspondiente", now.month)\
            .eq("anio_correspondiente", now.year)\
            .execute()
        return len(res.data) > 0
    except: return False

def inject_pwa_and_css():
    st.markdown("""
    <link rel="manifest" href="/static/manifest.json">
    <style>
        .stApp { background-color: #f0f2f6; }
        .property-card {
            background: white; padding: 20px; border-radius: 12px;
            margin-bottom: 15px; border-left: 5px solid #007bff;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        .card-paid { border-left-color: #28a745; }
        .card-urgent { border-left-color: #dc3545; }
    </style>
    """, unsafe_allow_html=True)

# ── UI ────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Gestor Rentas", layout="wide")
    inject_pwa_and_css()

    if 'confirm_del' not in st.session_state: st.session_state.confirm_del = {}

    st.title("🏠 Gestor de Propiedades")
    t1, t2 = st.tabs(["📋 Dashboard", "➕ Nuevo"])

    with t1:
        contratos = cargar_contratos()
        now = get_now()
        
        for c in contratos:
            cid = c['id']
            inq = c.get('inquilino', 'N/A')
            pagado = verificar_pago_mes_actual(cid)
            
            # Tarjeta visual
            card_style = "property-card"
            if pagado: card_style += " card-paid"
            
            st.markdown(f"""
            <div class="{card_style}">
                <h4>{inq}</h4>
                <p>Finca: {c.get('finca','N/A')} | Renta: ${c.get('monto_renta',0)}</p>
                <p>Cobro: {c.get('metodo_cobro','N/A')} {f'({c.get("domicilio_dueno","")})' if c.get('domicilio_dueno') else ""}</p>
            </div>
            """, unsafe_allow_html=True)

            col1, col2, col3 = st.columns(3)
            
            if col1.button("📄 Recibo", key=f"rec_{cid}"):
                success, path = generar_recibo(PLANTILLA, c, now.month, now.year)
                if success:
                    registrar_pago_db(cid, now.month, now.year)
                    with open(path, "rb") as f:
                        st.download_button("Descargar", f.read(), f"Recibo_{inq}.docx", key=f"dl_{cid}")
            
            # Botón de eliminar con el arreglo de cascada
            if not st.session_state.confirm_del.get(cid):
                if col3.button("🗑 Eliminar", key=f"del_{cid}"):
                    st.session_state.confirm_del[cid] = True
                    st.rerun()
            else:
                st.warning("¿Borrar propiedad?")
                if st.button("Sí, borrar", key=f"ok_{cid}"):
                    eliminar_contrato(cid)

    with t2:
        # Formulario simplificado
        with st.form("nuevo"):
            f_inq = st.text_input("Inquilino")
            f_mon = st.number_input("Renta", value=0.0)
            f_met = st.selectbox("Método", METODOS_COBRO)
            f_dom = st.text_input("Domicilio dueño (si no es depósito)")
            if st.form_submit_button("Guardar"):
                nuevo = {
                    "inquilino": f_inq, "monto_renta": f_mon, 
                    "metodo_cobro": f_met, "domicilio_dueno": f_dom,
                    "fecha_fin": "2026-01-01" # Default
                }
                supabase.table("contratos").insert(nuevo).execute()
                st.rerun()

if __name__ == "__main__":
    main()
