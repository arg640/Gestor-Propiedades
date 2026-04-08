import streamlit as st
import streamlit.components.v1 as components
import os, re, uuid, json, tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo
from supabase import create_client, Client

from extractor import extraer_datos_contrato
from generador import generar_recibo

# ── CONSTANTES Y CONFIGURACIÓN ────────────────────────────────────────────────
# Prioridad: Streamlit Cloud Secrets -> Variables de Entorno
try:
    SUPABASE_URL = st.secrets["SUPABASE_URL"]
    SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
except Exception:
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Inicialización de Cliente Supabase
if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("⚠️ Error: No se encontraron las credenciales de Supabase. Configura los Secrets en Streamlit.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Rutas y Directorios
PLANTILLA       = "attached_assets/Plantilla_recibos_1775446398445.docx"
REGLAMENTOS_DIR = "reglamentos"
CONTRATOS_DIR   = "contratos_pdf"
MEXICO_TZ       = ZoneInfo("America/Mexico_City")
METODOS_COBRO   = ["Entregar a dueño", "Cuota de vecindario", "Depósito"]

for d in (REGLAMENTOS_DIR, CONTRATOS_DIR):
    os.makedirs(d, exist_ok=True)

# ── AYUDANTES: Lógica de Tiempo ───────────────────────────────────────────────
_MESES_NUM = {
    "ENERO":1, "FEBRERO":2, "MARZO":3, "ABRIL":4, "MAYO":5, "JUNIO":6,
    "JULIO":7, "AGOSTO":8, "SEPTIEMBRE":9, "OCTUBRE":10, "NOVIEMBRE":11, "DICIEMBRE":12
}

def get_now():
    return datetime.now(MEXICO_TZ)

def format_currency(val):
    try: return f"${float(val):,.2f}"
    except: return f"${val}"

# ── OPERACIONES DE BASE DE DATOS (SUPABASE) ───────────────────────────────────

def cargar_contratos():
    try:
        res = supabase.table("contratos").select("*").execute()
        return res.data if res.data else []
    except Exception as e:
        st.error(f"Error al cargar contratos: {e}")
        return []

def eliminar_contrato(cid):
    """
    Elimina un contrato y sus datos relacionados (pagos) en cascada.
    """
    try:
        # 1. Eliminar pagos asociados primero (Evita error de llave foránea)
        supabase.table("pagos").delete().eq("contrato_id", cid).execute()
        
        # 2. Eliminar el contrato
        res = supabase.table("contratos").delete().eq("id", cid).execute()
        
        if res.data:
            st.success("✅ Propiedad eliminada correctamente.")
            return True
        return False
    except Exception as e:
        st.error(f"❌ Error al eliminar: Asegúrate de tener permisos de borrado en Supabase. Detalle: {e}")
        return False

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
    except Exception as e:
        st.error(f"Error al registrar pago: {e}")
        return False

def verificar_pago_mes_actual(cid):
    now = get_now()
    try:
        res = supabase.table("pagos").select("*")\
            .eq("contrato_id", cid)\
            .eq("mes_correspondiente", now.month)\
            .eq("anio_correspondiente", now.year)\
            .execute()
        return len(res.data) > 0
    except:
        return False

# ── COMPONENTES DE INTERFAZ (UI) ──────────────────────────────────────────────

def inject_pwa_and_css():
    st.markdown("""
    <link rel="manifest" href="/static/manifest.json">
    <style>
        .stApp { background-color: #f8f9fa; }
        .property-card {
            background: white; padding: 20px; border-radius: 15px;
            margin-bottom: 15px; border-left: 8px solid #007bff;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1); color: #1a1a1a;
        }
        .card-paid { border-left-color: #28a745 !important; background-color: #f0fff4; }
        .card-urgent { border-left-color: #dc3545 !important; }
        .status-badge {
            padding: 4px 12px; border-radius: 20px; font-size: 12px;
            font-weight: bold; text-transform: uppercase;
        }
        .bg-red { background: #ffdee2; color: #d90429; }
        .bg-green { background: #d4edda; color: #155724; }
        .bg-blue { background: #e7f3ff; color: #004085; }
        h3 { color: #1a1a1a !important; margin-bottom: 5px !important; }
        p { margin-bottom: 4px !important; font-size: 14px; color: #444; }
    </style>
    """, unsafe_allow_html=True)
    
    # Service Worker para PWA
    components.html("""
    <script>
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/sw.js');
    }
    </script>
    """, height=0)

# ── APP PRINCIPAL ─────────────────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Gestor de Propiedades", layout="wide", page_icon="🏠")
    inject_pwa_and_css()

    if 'confirm_del' not in st.session_state: st.session_state.confirm_del = {}
    if 'expanded' not in st.session_state: st.session_state.expanded = {}

    st.title("🏠 Gestor de Propiedades")
    
    tabs = st.tabs(["📋 Dashboard", "➕ Nueva Propiedad"])

    # --- TAB: DASHBOARD ---
    with tabs[0]:
        contratos = cargar_contratos()
        if not contratos:
            st.info("No hay propiedades registradas. Ve a la pestaña 'Nueva Propiedad'.")
        else:
            now = get_now()
            # Enriquecer datos para ordenamiento
            for c in contratos:
                try:
                    fecha_fin = datetime.strptime(c['fecha_fin'], "%Y-%m-%d").date()
                    c['_dias_vence'] = (fecha_fin - now.date()).days
                except: c['_dias_vence'] = 999
                
                c['_pagado_hoy'] = verificar_pago_mes_actual(c['id'])
                
                # Prioridad: Vencidos (-1) > Sin Pago (0) > Pagados (1)
                if c['_dias_vence'] < 0: c['_pri'] = -1
                elif not c['_pagado_hoy']: c['_pri'] = 0
                else: c['_pri'] = 1

            # Ordenar: Vencidos primero, luego cercanía de pago, luego pagados
            contratos.sort(key=lambda x: (x['_pri'], x.get('dia_pago_mensual', 31)))

            for c in contratos:
                cid = c['id']
                inq = c.get('inquilino', 'N/A')
                finc = c.get('finca', 'N/A')
                monto = c.get('monto_renta', 0)
                dia_p = c.get('dia_pago_mensual', 1)
                vence_str = c.get('fecha_fin', 'N/A')
                metodo = c.get('metodo_cobro', 'Depósito')
                domicilio = c.get('domicilio_dueno', '')
                pagado = c['_pagado_hoy']
                dias_v = c['_dias_vence']

                # Estilo de tarjeta
                card_class = "property-card"
                if pagado: card_class += " card-paid"
                if dias_v <= 30: card_class += " card-urgent"

                # Badges
                v_badge = f'<span class="status-badge bg-blue">⏳ {dias_v} días para vencer</span>'
                if dias_v < 0: v_badge = f'<span class="status-badge bg-red">⚠️ VENCIDO</span>'
                
                p_badge = '<span class="status-badge bg-red">🔴 SIN PAGO</span>'
                if pagado: p_badge = '<span class="status-badge bg-green">🟢 PAGADO</span>'

                st.markdown(f"""
                <div class="{" card-paid" if pagado else "property-card" if dias_v > 30 else "property-card card-urgent"}">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                        <h3>{inq.upper()}</h3>
                        <div>{v_badge} {p_badge}</div>
                    </div>
                    <p><b>📍 Finca:</b> {finc}</p>
                    <p><b>💰 Renta:</b> {format_currency(monto)} (Día de pago: {dia_p})</p>
                    <p><b>📅 Vence:</b> {vence_str} | <b>Dueño:</b> {c.get('dueno', 'N/A')}</p>
                    <div style="margin-top:10px; padding:8px; background:#f1f3f5; border-radius:8px; font-size:13px;">
                        {'🏦' if metodo == 'Depósito' else '🤝'} <b>Método:</b> {metodo} 
                        {f" ⮕ 📍 {domicilio}" if domicilio else ""}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                # Acciones
                with st.expander(f"⚙️ Acciones para {inq}"):
                    col1, col2, col3 = st.columns(3)
                    
                    # Generar Recibo
                    if col1.button("📄 Preparar Recibo", key=f"rec_{cid}", use_container_width=True):
                        if pagado:
                            st.session_state[f"warn_{cid}"] = True
                        else:
                            # Generar normal
                            success, path = generar_recibo(PLANTILLA, c, now.month, now.year)
                            if success:
                                registrar_pago_db(cid, now.month, now.year)
                                with open(path, "rb") as f:
                                    st.download_button("📥 Descargar Word", f.read(), f"Recibo_{inq}_{now.month}.docx", key=f"dl_{cid}")
                                st.rerun()

                    if st.session_state.get(f"warn_{cid}"):
                        st.warning("⚠️ Ya existe un pago este mes. ¿Generar otro?")
                        if st.button("Confirmar nuevo recibo", key=f"conf_{cid}"):
                            success, path = generar_recibo(PLANTILLA, c, now.month, now.year)
                            if success:
                                with open(path, "rb") as f:
                                    st.download_button("📥 Descargar Word", f.read(), f"Recibo_Extra_{inq}.docx")
                                st.session_state[f"warn_{cid}"] = False

                    # Eliminar con confirmación
                    if not st.session_state.confirm_del.get(cid):
                        if col3.button("🗑 Eliminar", key=f"del_btn_{cid}", use_container_width=True):
                            st.session_state.confirm_del[cid] = True
                            st.rerun()
                    else:
                        st.error("¿Seguro que deseas eliminar esta propiedad?")
                        d1, d2 = st.columns(2)
                        if d1.button("✅ Sí", key=f"y_{cid}"):
                            if eliminar_contrato(cid):
                                st.session_state.confirm_del.pop(cid)
                                st.rerun()
                        if d2.button("❌ No", key=f"n_{cid}"):
                            st.session_state.confirm_del.pop(cid)
                            st.rerun()

    # --- TAB: NUEVA PROPIEDAD ---
    with tabs[1]:
        st.subheader("Cargar Nuevo Contrato (PDF)")
        archivo = st.file_uploader("Sube el PDF del contrato", type=["pdf"])
        
        if archivo:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(archivo.read())
                tmp_path = tmp.name
            
            with st.spinner("Extrayendo datos..."):
                datos = extraer_datos_contrato(tmp_path)
            
            with st.form("valida_datos"):
                st.info("Valida la información extraída:")
                f_inq = st.text_input("Inquilino", datos.get('inquilino',''))
                f_due = st.text_input("Dueño/Arrendador", datos.get('dueno',''))
                f_fin = st.text_input("Finca/Dirección", datos.get('finca',''))
                c1, c2, c3 = st.columns(3)
                f_mon = c1.number_input("Renta Mensual", value=float(datos.get('monto_renta',0)))
                f_dia = c2.number_input("Día de Pago", 1, 31, value=int(datos.get('dia_pago_mensual',1)))
                f_fec = c3.date_input("Vencimiento Contrato", value=datetime.strptime(datos.get('fecha_fin','2025-01-01'), "%Y-%m-%d").date())
                
                # Método de Cobro (Nuevo)
                f_met = st.selectbox("Método de Cobro", METODOS_COBRO)
                f_dom = ""
                if f_met != "Depósito":
                    f_dom = st.text_input("Domicilio del dueño (para entrega)", help="Obligatorio para métodos físicos")

                if st.form_submit_button("💾 Guardar Propiedad"):
                    if f_met != "Depósito" and not f_dom:
                        st.error("Por favor ingresa el domicilio del dueño.")
                    else:
                        nuevo_c = {
                            "inquilino": f_inq, "dueno": f_due, "finca": f_fin,
                            "monto_renta": f_mon, "dia_pago_mensual": f_dia,
                            "fecha_fin": f_fec.isoformat(), "metodo_cobro": f_met,
                            "domicilio_dueno": f_dom
                        }
                        try:
                            supabase.table("contratos").insert(nuevo_c).execute()
                            st.success("¡Propiedad guardada!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar: {e}")

if __name__ == "__main__":
    main()
