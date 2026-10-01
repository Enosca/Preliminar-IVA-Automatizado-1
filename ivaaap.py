import subprocess
import sys

# ---------------------------------------------------------
# Autoinstalación de Chromium para Entornos Cloud (Streamlit Cloud)
# ---------------------------------------------------------
try:
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
except Exception as e:
    print(f"Aviso de instalación de Playwright: {e}")

import streamlit as st
import pandas as pd
import sqlite3
import os
import io
import zipfile
import calendar
import random
from datetime import datetime
from playwright.sync_api import sync_playwright

# Configuración de la página
st.set_page_config(page_title="Sistema de Liquidación de IVA y Clientes", layout="wide")

# ---------------------------------------------------------
# Ocultar Marcas de Agua, Menú de Streamlit e Ícono de GitHub
# ---------------------------------------------------------
ocultar_estilos_streamlit = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stAppDeployButton {display:none !important;}
    .viewerBadge_container__1S12D {display:none !important;}
    a[href*="github.com"] {display:none !important;}
    </style>
"""
st.markdown(ocultar_estilos_streamlit, unsafe_allow_html=True)

# ---------------------------------------------------------
# Conexión a Base de Datos (SQLite)
# ---------------------------------------------------------
CONN = sqlite3.connect("iva_datos.db", check_same_thread=False)

def inicializar_db():
    cursor = CONN.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            periodo TEXT,
            comprobante TEXT,
            nombre TEXT,
            cuit TEXT,
            neto REAL,
            iva REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS compras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            periodo TEXT,
            comprobante TEXT,
            nombre TEXT,
            cuit TEXT,
            neto REAL,
            iva REAL
        )
    """)
    
    columnas_nuevas = ["periodo TEXT", "nombre TEXT", "cuit TEXT"]
    for col in columnas_nuevas:
        try:
            cursor.execute(f"ALTER TABLE ventas ADD COLUMN {col}")
        except: pass
        try:
            cursor.execute(f"ALTER TABLE compras ADD COLUMN {col}")
        except: pass

    CONN.commit()

inicializar_db()

# ---------------------------------------------------------
# Funciones Auxiliares de Limpieza
# ---------------------------------------------------------
def limpiar_numero_arg(val):
    if pd.isna(val): return 0.0
    if isinstance(val, (int, float)): return float(val)
    val_str = str(val).replace('.', '').replace(',', '.').strip()
    try:
        return float(val_str)
    except:
        return 0.0

def extraer_periodo(fecha_str):
    try:
        fecha_dt = pd.to_datetime(fecha_str, errors='coerce')
        if pd.notna(fecha_dt):
            return fecha_dt.strftime('%Y-%m')
    except: pass
    return "Sin Período"

# ---------------------------------------------------------
# PROCESADOR UNIFICADO DE COMPROBANTES (OFFLINE)
# ---------------------------------------------------------
def procesar_dataframe_afip(df):
    if df is None or df.empty:
        return None

    df.columns = [str(col).strip() for col in df.columns]

    col_fecha = next((c for c in df.columns if 'Fecha' in c), df.columns[0])
    col_tipo = next((c for c in df.columns if 'Tipo' in c and 'Doc' not in c and 'Cambio' not in c), None)
    col_pv = next((c for c in df.columns if 'Punto de Venta' in c), None)
    col_num = next((c for c in df.columns if 'Número Desde' in c or 'NÃºmero Desde' in c or 'Numero' in c or 'Nro' in c), None)
    col_cuit = next((c for c in df.columns if 'Nro. Doc' in c or 'CUIT' in c or 'Cuit' in c), None)
    col_nombre = next((c for c in df.columns if 'Denominación' in c or 'DenominaciÃ³n' in c or 'Denominacion' in c or 'Nombre' in c or 'Razon Social' in c), None)

    col_neto_total = next((c for c in df.columns if c == 'Imp. Neto Gravado Total'), None)
    col_iva_total = next((c for c in df.columns if c == 'Total IVA'), None)
    col_imp_total = next((c for c in df.columns if c == 'Imp. Total'), None)

    if not col_neto_total:
        col_neto_total = next((c for c in df.columns if 'Neto Gravado Total' in c), None)
    if not col_iva_total:
        col_iva_total = next((c for c in df.columns if 'Total IVA' in c or c == 'IVA'), None)
    if not col_imp_total:
        col_imp_total = next((c for c in df.columns if 'Imp. Total' in c or c == 'Total'), None)

    CODIGOS_NC = [3, 8, 13, 53]
    def es_nota_de_credito(val_tipo):
        if pd.isna(val_tipo): return False
        try:
            if int(val_tipo) in CODIGOS_NC: return True
        except: pass
        val_str = str(val_tipo).lower()
        return ('nota de crédito' in val_str or 'nota de credito' in val_str or 'nc' in val_str)

    imp_totales = df[col_imp_total].apply(limpiar_numero_arg) if col_imp_total else pd.Series([0.0]*len(df))
    iva_vals = df[col_iva_total].apply(limpiar_numero_arg) if col_iva_total else pd.Series([0.0]*len(df))
    neto_vals = df[col_neto_total].apply(limpiar_numero_arg) if col_neto_total else pd.Series([0.0]*len(df))

    neto_final = neto_vals.where(neto_vals > 0, imp_totales - iva_vals)

    m_nc = df[col_tipo].apply(es_nota_de_credito) if col_tipo else pd.Series([False] * len(df))
    signo = m_nc.apply(lambda x: -1.0 if x else 1.0)

    df_resumen = pd.DataFrame()
    df_resumen['neto'] = neto_final * signo
    df_resumen['iva'] = iva_vals * signo
    df_resumen['fecha'] = df[col_fecha].astype(str)
    df_resumen['periodo'] = df_resumen['fecha'].apply(extraer_periodo)

    if col_cuit:
        s_cuit = df[col_cuit].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        df_resumen['cuit'] = s_cuit.replace(['nan', 'None', '', 'NaN', '0'], 'S.D.')
    else:
        df_resumen['cuit'] = "S.D."

    if col_nombre:
        s_nom = df[col_nombre].astype(str).str.strip()
        df_resumen['nombre'] = s_nom.replace(['nan', 'None', '', 'NaN'], 'Consumidor Final')
    else:
        df_resumen['nombre'] = "Consumidor Final"

    if col_pv and col_num:
        comp_num = df[col_pv].astype(str) + "-" + df[col_num].astype(str)
    elif col_num:
        comp_num = df[col_num].astype(str)
    else:
        comp_num = "Sin Comp."

    df_resumen['comprobante'] = comp_num.mask(m_nc, "NC " + comp_num)

    return df_resumen[['fecha', 'periodo', 'comprobante', 'nombre', 'cuit', 'neto', 'iva']]

def extraer_df_de_zip_o_archivo(file_or_path):
    """Apertura y descompresión de archivos descargados"""
    try:
        if isinstance(file_or_path, str):
            if zipfile.is_zipfile(file_or_path):
                with zipfile.ZipFile(file_or_path, 'r') as z:
                    for f in z.namelist():
                        if f.endswith('.csv'):
                            try:
                                return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='utf-8')
                            except:
                                return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='latin1')
                        elif f.endswith(('.xlsx', '.xls')):
                            return pd.read_excel(io.BytesIO(z.read(f)))
            if file_or_path.endswith('.csv'):
                try:
                    return pd.read_csv(file_or_path, sep=';', encoding='utf-8')
                except:
                    return pd.read_csv(file_or_path, sep=';', encoding='latin1')
            return pd.read_excel(file_or_path)
        else:
            if file_or_path.name.endswith('.zip'):
                with zipfile.ZipFile(file_or_path, 'r') as z:
                    for f in z.namelist():
                        if f.endswith('.csv'):
                            try:
                                return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='utf-8')
                            except:
                                return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='latin1')
            elif file_or_path.name.endswith('.csv'):
                try:
                    return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='utf-8')
                except:
                    return pd.read_csv(io.BytesIO(z.read(f)), sep=';', encoding='latin1')
            return pd.read_excel(file_or_path)
    except Exception as e:
        st.error(f"Error leyendo archivo local: {e}")
        return None

# ---------------------------------------------------------
# Interacción Segura con Pausas Humana Sincronizadas
# ---------------------------------------------------------
def pausa_humana(min_ms=400, max_ms=800):
    """Genera una pausa aleatoria que imita el ritmo de navegación de una persona real"""
    import time
    time.sleep(random.uniform(min_ms / 1000.0, max_ms / 1000.0))

def fijar_fecha_en_arca(mc_page, str_desde, str_hasta):
    rango_texto = f"{str_desde} - {str_hasta}"
    try:
        input_elem = mc_page.locator("input#fechaEmision").first
        input_elem.wait_for(state="visible", timeout=5000)
        input_elem.click()
        pausa_humana(300, 500)

        btn_mes_anterior = mc_page.locator("div.daterangepicker li:has-text('Mes Anterior'), .ranges li:has-text('Mes anterior')").first
        if btn_mes_anterior.is_visible(timeout=500):
            btn_mes_anterior.click()
            pausa_humana(400, 600)
            return

        mc_page.keyboard.press("Control+A")
        mc_page.keyboard.press("Backspace")
        pausa_humana(200, 400)

        input_elem.type(rango_texto, delay=25)
        pausa_humana(300, 500)

        btn_aplicar = mc_page.locator("button.applyBtn, button:has-text('Aplicar'), .daterangepicker .applyBtn").first
        if btn_aplicar.is_visible(timeout=1000):
            btn_aplicar.click()
        else:
            mc_page.keyboard.press("Enter")

        pausa_humana(400, 600)
    except Exception:
        pass

def ejecutar_descarga_directa(mc_page):
    """Inicia la descarga de manera segura"""
    mc_page.wait_for_selector("span:has-text('CSV'), .buttons-csv", timeout=25000)
    pausa_humana(500, 800)

    with mc_page.expect_download(timeout=30000) as download_info:
        span_csv = mc_page.locator("span:has-text('CSV')").first
        if span_csv.is_visible(timeout=1000):
            span_csv.click(force=True)
        else:
            mc_page.evaluate("""
                () => {
                    const spans = Array.from(document.querySelectorAll('span'));
                    const targetSpan = spans.find(s => s.textContent.trim() === 'CSV');
                    if (targetSpan) {
                        targetSpan.click();
                        if (targetSpan.parentElement) targetSpan.parentElement.click();
                    }
                }
            """)
    return download_info.value

# ---------------------------------------------------------
# SCRAPER SEGURO AFIP / ARCA
# ---------------------------------------------------------
def ejecutar_scraper_afip(cuit, clave_fiscal, tipo_periodo, fecha_desde, fecha_hasta, ver_navegador=False):
    hoy = datetime.now()

    if tipo_periodo == "mes_anterior":
        primer_dia_este_mes = hoy.replace(day=1)
        ultimo_dia_mes_ant = primer_dia_este_mes - pd.Timedelta(days=1)
        primer_dia_mes_ant = ultimo_dia_mes_ant.replace(day=1)
        str_desde = primer_dia_mes_ant.strftime("%d/%m/%Y")
        str_hasta = ultimo_dia_mes_ant.strftime("%d/%m/%Y")

    elif tipo_periodo == "mes_actual":
        str_desde = hoy.replace(day=1).strftime("%d/%m/%Y")
        ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
        str_hasta = hoy.replace(day=ultimo_dia).strftime("%d/%m/%Y")

    elif tipo_periodo == "personalizado" and fecha_desde and fecha_hasta:
        try:
            str_desde = pd.to_datetime(fecha_desde).strftime("%d/%m/%Y")
            str_hasta = pd.to_datetime(fecha_hasta).strftime("%d/%m/%Y")
        except:
            str_desde = hoy.replace(day=1).strftime("%d/%m/%Y")
            str_hasta = hoy.strftime("%d/%m/%Y")
    else:
        str_desde = hoy.replace(month=1, day=1).strftime("%d/%m/%Y")
        str_hasta = hoy.strftime("%d/%m/%Y")

    res = {"ventas_df": None, "compras_df": None, "error": None}
    path_c = f"temp_compras_{cuit}.csv"
    path_v = f"temp_ventas_{cuit}.csv"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not ver_navegador)
            context = browser.new_context(accept_downloads=True, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            page = context.new_page()

            page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml", timeout=60000)
            pausa_humana(400, 700)
            page.fill("input#F1\\:username", cuit)
            page.click("input#F1\\:btnSiguiente")

            page.wait_for_selector("input#F1\\:password", timeout=15000)
            pausa_humana(300, 600)
            page.fill("input#F1\\:password", clave_fiscal)
            page.click("input#F1\\:btnIngresar")

            try:
                with context.expect_page() as new_page_info:
                    page.locator("h3:has-text('Mis Comprobantes'), h4:has-text('Mis Comprobantes')").first.click(timeout=10000, force=True)
                mc_page = new_page_info.value
            except:
                page.fill("input#buscadorInput", "Mis Comprobantes")
                pausa_humana(500, 800)
                with context.expect_page() as new_page_info:
                    page.locator("ul#resBusqueda li").first.click(timeout=10000, force=True)
                mc_page = new_page_info.value

            mc_page.wait_for_selector("text='Comprobantes Recibidos'", timeout=20000)
            pausa_humana(400, 800)

            try:
                rep = mc_page.locator(f"text='{cuit}'").first
                if rep.count() > 0:
                    rep.click()
                    pausa_humana(500, 800)
            except: pass

            # --- 1. COMPRAS ---
            mc_page.click("text='Comprobantes Recibidos'")
            pausa_humana(500, 800)
            fijar_fecha_en_arca(mc_page, str_desde, str_hasta)
            mc_page.locator("button#buscarComprobantes, input[value='Buscar'], button:has-text('Buscar')").first.click()

            try:
                download_c = ejecutar_descarga_directa(mc_page)
                if download_c:
                    download_c.save_as(path_c)
            except Exception as e_c:
                print(f"Error descargando compras: {e_c}")

            pausa_humana(800, 1500)

            # --- 2. VENTAS ---
            try:
                mc_page.click("text='Volver'")
            except:
                mc_page.go_back()

            mc_page.wait_for_selector("text='Comprobantes Emitidos'", timeout=15000)
            pausa_humana(500, 800)
            mc_page.click("text='Comprobantes Emitidos'")
            pausa_humana(500, 800)
            fijar_fecha_en_arca(mc_page, str_desde, str_hasta)
            mc_page.locator("button#buscarComprobantes, input[value='Buscar'], button:has-text('Buscar')").first.click()

            try:
                download_v = ejecutar_descarga_directa(mc_page)
                if download_v:
                    download_v.save_as(path_v)
            except Exception as e_v:
                print(f"Error descargando ventas: {e_v}")

            pausa_humana(500, 800)
            browser.close()

    except Exception as e:
        res["error"] = str(e)
        return res

    # --- PROCESAMIENTO OFFLINE ---
    if os.path.exists(path_c):
        raw_c = extraer_df_de_zip_o_archivo(path_c)
        res["compras_df"] = procesar_dataframe_afip(raw_c)
        os.remove(path_c)

    if os.path.exists(path_v):
        raw_v = extraer_df_de_zip_o_archivo(path_v)
        res["ventas_df"] = procesar_dataframe_afip(raw_v)
        os.remove(path_v)

    return res

# ---------------------------------------------------------
# Operaciones DB
# ---------------------------------------------------------
def obtener_datos(tabla):
    return pd.read_sql_query(f"SELECT * FROM {tabla}", CONN)

def guardar_dataframe(tabla, df_para_guardar):
    df_para_guardar.to_sql(tabla, CONN, if_exists='append', index=False)

def limpiar_tabla(tabla):
    cursor = CONN.cursor()
    cursor.execute(f"DELETE FROM {tabla}")
    CONN.commit()

# ---------------------------------------------------------
# Control de Acceso
# ---------------------------------------------------------
st.sidebar.title("🔐 Acceso")
rol = st.sidebar.radio("Selecciona tu rol:", ["Cliente / Usuario (Solo Lectura)", "Administrador"])

clave_admin = "1234"
es_admin = False

if rol == "Administrador":
    password = st.sidebar.text_input("Contraseña de Administrador", type="password")
    if password == clave_admin:
        es_admin = True
        st.sidebar.success("Modo Administrador Activo")
    elif password != "":
        st.sidebar.error("Contraseña incorrecta")

# ---------------------------------------------------------
# VISTA PRINCIPAL
# ---------------------------------------------------------
st.title("🧮 Sistema de Liquidación IVA (ARCA / AFIP Automático)")

df_ventas = obtener_datos("ventas")
df_compras = obtener_datos("compras")

periodos_v = df_ventas['periodo'].dropna().unique().tolist() if not df_ventas.empty and 'periodo' in df_ventas.columns else []
periodos_c = df_compras['periodo'].dropna().unique().tolist() if not df_compras.empty and 'periodo' in df_compras.columns else []

periodos_disponibles = sorted(list(set(periodos_v + periodos_c)), reverse=True)

st.sidebar.markdown("---")
st.sidebar.header("📅 Filtro de Período")

if periodos_disponibles:
    opcion_periodo = st.sidebar.selectbox("Selecciona el Mes:", ["Todos los Períodos"] + periodos_disponibles)
else:
    opcion_periodo = "Todos los Períodos"
    st.sidebar.info("No hay datos cargados aún.")

if opcion_periodo != "Todos los Períodos":
    if not df_ventas.empty and 'periodo' in df_ventas.columns:
        df_ventas = df_ventas[df_ventas['periodo'] == opcion_periodo]
    if not df_compras.empty and 'periodo' in df_compras.columns:
        df_compras = df_compras[df_compras['periodo'] == opcion_periodo]

tab_resumen, tab_analisis = st.tabs(["📋 Preliminar de IVA", "📊 Análisis de Clientes y Proveedores"])

with tab_resumen:
    debito_fiscal = df_ventas["iva"].sum() if not df_ventas.empty else 0.0
    credito_fiscal = df_compras["iva"].sum() if not df_compras.empty else 0.0
    neto_ventas = df_ventas["neto"].sum() if not df_ventas.empty else 0.0
    neto_compras = df_compras["neto"].sum() if not df_compras.empty else 0.0
    saldo_iva = debito_fiscal - credito_fiscal

    st.subheader(f"📌 Posición de IVA: **{opcion_periodo}**")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Ventas (Neto)", f"${neto_ventas:,.2f}")
    m2.metric("Total Compras (Neto)", f"${neto_compras:,.2f}")
    m3.metric("Débito Fiscal (Ventas)", f"${debito_fiscal:,.2f}")
    m4.metric("Crédito Fiscal (Compras)", f"${credito_fiscal:,.2f}")

    st.markdown("---")

    if saldo_iva > 0:
        st.error(f"⚠️ **Posición Final:** IVA a Pagar: **${saldo_iva:,.2f}**")
    elif saldo_iva < 0:
        st.success(f"✅ **Posición Final:** Saldo a Favor del Contribuyente: **${abs(saldo_iva):,.2f}**")
    else:
        st.info("ℹ️️ **Posición Final:** Saldo Neutro ($0.00)")

    col_v, col_c = st.columns(2)

    with col_v:
        st.subheader("📋 Detalle de Ventas")
        if not df_ventas.empty:
            cols_mostrar = [c for c in ['fecha', 'comprobante', 'nombre', 'cuit', 'neto', 'iva'] if c in df_ventas.columns]
            st.dataframe(df_ventas[cols_mostrar], use_container_width=True)
        else:
            st.info("No hay ventas para este período.")

    with col_c:
        st.subheader("📋 Detalle de Compras")
        if not df_compras.empty:
            cols_mostrar = [c for c in ['fecha', 'comprobante', 'nombre', 'cuit', 'neto', 'iva'] if c in df_compras.columns]
            st.dataframe(df_compras[cols_mostrar], use_container_width=True)
        else:
            st.info("No hay compras para este período.")

with tab_analisis:
    st.subheader(f"📈 Análisis Comercial y Concentración ({opcion_periodo})")

    col_a1, col_a2 = st.columns(2)

    with col_a1:
        st.markdown("### 🏆 Top Clientes (Mayor Facturación Neto)")
        if not df_ventas.empty and 'nombre' in df_ventas.columns:
            top_clientes = df_ventas.groupby('nombre')[['neto', 'iva']].sum().sort_values(by='neto', ascending=False).reset_index()
            st.bar_chart(top_clientes.head(10).set_index('nombre')['neto'])
            st.dataframe(top_clientes, use_container_width=True)
        else:
            st.info("No hay datos de ventas para analizar.")

    with col_a2:
        st.markdown("### 🏬 Top Proveedores (Mayor Compra Neto)")
        if not df_compras.empty and 'nombre' in df_compras.columns:
            top_prov = df_compras.groupby('nombre')[['neto', 'iva']].sum().sort_values(by='neto', ascending=False).reset_index()
            st.bar_chart(top_prov.head(10).set_index('nombre')['neto'])
            st.dataframe(top_prov, use_container_width=True)
        else:
            st.info("No hay datos de compras para analizar.")

# ---------------------------------------------------------
# PANEL DE ADMINISTRACIÓN
# ---------------------------------------------------------
if es_admin:
    st.markdown("---")
    st.header("⚙️ Panel de Administración")

    tab_auto, tab_masiva, tab_manual, tab_mantenimiento = st.tabs([
        "🤖 Extracción Automática AFIP",
        "📂 Carga Archivos (CSV/ZIP/PDF)", 
        "✍️ Carga Manual", 
        "🧹 Mantenimiento"
    ])

    with tab_auto:
        st.markdown("### 🤖 Extractor Directo desde AFIP / ARCA")
        st.info("Ingresa el CUIT y Clave Fiscal para descargar la información directamente desde la web.")

        col_af1, col_af2 = st.columns(2)
        with col_af1:
            cuit_afip = st.text_input("CUIT del Contribuyente (sin guiones)")
            clave_afip = st.text_input("Clave Fiscal AFIP", type="password")
        with col_af2:
            tipo_per = st.selectbox("Período a consultar:", ["mes_anterior", "mes_actual", "anio_actual", "personalizado"])
            f_desde = None
            f_hasta = None
            if tipo_per == "personalizado":
                f_desde = st.date_input("Fecha Desde")
                f_hasta = st.date_input("Fecha Hasta")

        ver_nav = st.checkbox("Mostrar ventana del navegador (Modo depuración)", value=False)

        if st.button("🚀 Iniciar Extracción Automática", type="primary"):
            if cuit_afip and clave_afip:
                with st.spinner("Conectando con ARCA/AFIP y descargando archivos..."):
                    res_scraper = ejecutar_scraper_afip(cuit_afip, clave_afip, tipo_per, f_desde, f_hasta, ver_navegador=ver_nav)

                if res_scraper["error"]:
                    st.error(f"❌ Error durante la extracción: {res_scraper['error']}")
                else:
                    c_cargas = 0
                    if res_scraper["ventas_df"] is not None and not res_scraper["ventas_df"].empty:
                        guardar_dataframe("ventas", res_scraper["ventas_df"])
                        st.success(f"✅ ¡Se importaron {len(res_scraper['ventas_df'])} ventas!")
                        c_cargas += 1

                    if res_scraper["compras_df"] is not None and not res_scraper["compras_df"].empty:
                        guardar_dataframe("compras", res_scraper["compras_df"])
                        st.success(f"✅ ¡Se importaron {len(res_scraper['compras_df'])} compras!")
                        c_cargas += 1

                    if c_cargas == 0:
                        st.warning("No se encontraron registros nuevos en las fechas indicadas.")
                    else:
                        st.rerun()
            else:
                st.warning("Ingresa el CUIT y la Clave Fiscal para continuar.")

    with tab_masiva:
        st.markdown("### Subir Archivo Manual (CSV / ZIP / Excel / PDF)")
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            tipo_destino = st.selectbox("Destino de los datos:", ["ventas", "compras"])

        archivo_subido = st.file_uploader("Selecciona tu archivo", type=["csv", "zip", "xlsx", "xls", "pdf"])

        if archivo_subido is not None:
            raw_df = extraer_df_de_zip_o_archivo(archivo_subido)
            df_procesado = procesar_dataframe_afip(raw_df)
            if df_procesado is not None and not df_procesado.empty:
                st.dataframe(df_procesado, use_container_width=True)
                if st.button("Confirmar e Importar"):
                    guardar_dataframe(tipo_destino, df_procesado)
                    st.success(f"¡Importado exitosamente en {tipo_destino.upper()}!")
                    st.rerun()

    with tab_manual:
        st.markdown("### Cargar Registro Individual")
        col_f1, col_f2 = st.columns(2)
        with col_f1:
            t_mov = st.selectbox("Tipo de Movimiento", ["ventas", "compras"], key="man_tipo")
            f_in = st.date_input("Fecha", key="man_fecha")
            c_in = st.text_input("Número de Comprobante", key="man_comp")
            nom_in = st.text_input("Nombre / Razón Social", key="man_nom")
            cuit_in = st.text_input("CUIT", key="man_cuit")
        with col_f2:
            n_in = st.number_input("Monto Neto ($)", min_value=0.0, step=100.0, key="man_neto")
            ali = st.selectbox("Alícuota IVA", [21.0, 10.5, 27.0, 0.0], key="man_ali")
            iva_calc = n_in * (ali / 100)
            st.write(f"IVA: **${iva_calc:,.2f}**")

        if st.button("Guardar Comprobante"):
            if n_in > 0:
                per_calc = extraer_periodo(str(f_in))
                df_ind = pd.DataFrame([{
                    "fecha": str(f_in),
                    "periodo": per_calc,
                    "comprobante": c_in if c_in else "Manual",
                    "nombre": nom_in if nom_in else "Consumidor Final",
                    "cuit": cuit_in if cuit_in else "S.D.",
                    "neto": n_in,
                    "iva": iva_calc
                }])
                guardar_dataframe(t_mov, df_ind)
                st.success("Guardado.")
                st.rerun()

    with tab_mantenimiento:
        st.markdown("### Limpieza de Base de Datos")
        col_del1, col_del2 = st.columns(2)
        with col_del1:
            if st.button("Vaciar tabla de Ventas", type="secondary"):
                limpiar_tabla("ventas")
                st.warning("Ventas vaciadas.")
                st.rerun()
        with col_del2:
            if st.button("Vaciar tabla de Compras", type="secondary"):
                limpiar_tabla("compras")
                st.warning("Compras vaciadas.")
                st.rerun()
