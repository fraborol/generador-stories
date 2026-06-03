import glob
import html
import json
import os
import tempfile

import streamlit as st

from brand_kit import cargar_brand_kit
from generador import (
    generar_ideas_flexible,
    refinar_idea,
)

# ── Identidad de la app: cambiar aquí si el nombre cambia ─────────────────────
APP_NOMBRE    = "Copiloto"
APP_SUBTITULO = "Generador de ideas para Stories"

# ── Configuración de la página ────────────────────────────────────────────────
# Debe ser la primera llamada a Streamlit del script.
st.set_page_config(
    page_title=APP_NOMBRE,
    page_icon="📋",
    layout="wide",
)

# ── CSS personalizado ─────────────────────────────────────────────────────────
# st.markdown con unsafe_allow_html=True es el método estándar en Streamlit para
# inyectar estilos globales. Definimos aquí la paleta, las tarjetas y la tipografía.
st.markdown("""
<style>
    /* ── Paleta de colores ─────────────────────────────────────────────── */
    :root {
        --c-primario:  #1e293b;   /* azul pizarra oscuro: texto principal y títulos */
        --c-acento:    #2563eb;   /* azul corporativo: acento, botones y detalles */
        --c-fondo:     #f8f9fb;   /* gris muy claro: fondo de tarjetas */
        --c-borde:     #e2e6ea;   /* borde suave */
        --c-sec:       #6b7280;   /* gris medio: texto secundario */
        --c-ok:        #15803d;   /* verde semántico: tarjeta de versión refinada */
        --c-ok-fondo:  #f0fdf4;
        --c-ok-borde:  #86efac;
    }

    /* ── Cabecera de la app ────────────────────────────────────────────── */
    .app-header {
        padding: 0.5rem 0 1.25rem 0;
        border-bottom: 2px solid var(--c-acento);
        margin-bottom: 1.5rem;
    }
    .app-titulo {
        font-size: 2.6rem;
        font-weight: 700;
        color: var(--c-primario);
        margin: 0;
        letter-spacing: -0.5px;
    }
    .app-subtitulo {
        font-size: 0.9rem;
        color: var(--c-sec);
        margin: 0.2rem 0 0 0;
    }

    /* ── Tarjeta de idea ───────────────────────────────────────────────── */
    .idea-card {
        background: var(--c-fondo);
        border: 1px solid var(--c-borde);
        border-radius: 10px;
        padding: 1.25rem 1.4rem 1.5rem 1.4rem;
        height: 100%;
    }
    .idea-numero {
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 1.8px;
        text-transform: uppercase;
        color: var(--c-acento);
        margin-bottom: 0.35rem;
    }
    /* El concepto es el valor del campo, no una etiqueta: peso normal */
    .idea-concepto {
        font-size: 1rem;
        font-weight: 400;
        color: var(--c-primario);
        line-height: 1.35;
        margin-bottom: 1rem;
    }
    /* Etiquetas de campo en versalitas — solo las etiquetas van en negrita */
    .campo-label {
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: var(--c-sec);
        margin: 0.9rem 0 0.2rem 0;
    }
    /* Valores de campo: texto oscuro en peso normal */
    .campo-valor {
        font-size: 0.86rem;
        color: #1f2937;
        line-height: 1.5;
    }
    /* Texto en pantalla: borde izquierdo de acento con fondo muy tenue */
    .campo-texto-pantalla {
        font-size: 0.93rem;
        color: var(--c-primario);
        background: #eef2ff;
        border-left: 3px solid var(--c-acento);
        padding: 0.5rem 0.8rem;
        border-radius: 0 6px 6px 0;
        line-height: 1.5;
        font-style: italic;
    }
    /* Por qué funciona: texto secundario discreto */
    .campo-por-que {
        font-size: 0.78rem;
        color: var(--c-sec);
        line-height: 1.45;
    }

    /* ── Tarjeta de versión refinada (verde semántico) ─────────────────── */
    .refinada-card {
        background: var(--c-ok-fondo);
        border: 1px solid var(--c-ok-borde);
        border-radius: 10px;
        padding: 1.25rem 1.4rem 1.5rem 1.4rem;
        margin-top: 0.5rem;
    }
    .refinada-label {
        font-size: 0.68rem;
        font-weight: 700;
        letter-spacing: 1.8px;
        text-transform: uppercase;
        color: var(--c-ok);
        margin-bottom: 0.35rem;
    }

    /* ── Pantalla de bienvenida (estado inicial) ────────────────────────── */
    .bienvenida {
        text-align: center;
        padding: 5rem 2rem;
        color: var(--c-sec);
    }
    .bienvenida-titulo {
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--c-primario);
        margin-bottom: 0.5rem;
    }
    .bienvenida-desc {
        font-size: 0.88rem;
        max-width: 380px;
        margin: 0 auto;
        line-height: 1.6;
    }

    /* ── Botón principal: color de acento ──────────────────────────────── */
    div[data-testid="stButton"] > button[kind="primary"] {
        background-color: var(--c-acento);
        border: none;
    }

    /* ── Ocultar el icono de GitHub y el botón Fork en Streamlit Cloud ─── */
    /* Streamlit Community Cloud añade en la toolbar dos elementos que       */
    /* exponen el repositorio: un enlace al código fuente (icono de GitHub)  */
    /* y un botón de "Fork". Los ocultamos con sus data-testid estables,     */
    /* sin tocar el menú principal (tres puntos) ni el resto de la toolbar.  */

    /* Contenedor del enlace "Ver código fuente" / icono de GitHub */
    [data-testid="stToolbarActionsViewer"] {
        display: none !important;
    }

    /* Botón de Fork */
    [data-testid="stForkButton"] {
        display: none !important;
    }

    /* Fallback por href: por si Streamlit cambia los testids entre versiones,
       oculta directamente cualquier enlace a github.com dentro de la toolbar */
    [data-testid="stToolbarActions"] a[href*="github.com"] {
        display: none !important;
    }
</style>
""", unsafe_allow_html=True)

# ── Traducción etiqueta visible → valor interno de generar_ideas_flexible() ───
MAPA_CREATIVIDAD = {
    "Seguras y clásicas":     "seguras",
    "Equilibradas":           "equilibradas",
    "Atrevidas y originales": "atrevidas",
}


# ── Funciones auxiliares ──────────────────────────────────────────────────────

def descubrir_clientes():
    """
    Busca en la carpeta del proyecto todos los archivos cliente_*.json
    y devuelve un diccionario {nombre_legible: ruta_archivo}.
    Los archivos ilegibles se ignoran sin romper la app.
    """
    clientes = {}
    for ruta in sorted(glob.glob("cliente_*.json")):
        try:
            with open(ruta, encoding="utf-8") as f:
                datos = json.load(f)
            nombre = datos.get("cliente", ruta)
            clientes[nombre] = ruta
        except (json.JSONDecodeError, OSError):
            pass
    return clientes


def renderizar_idea(idea, prefijo="IDEA"):
    """
    Renderiza una idea como tarjeta HTML.
    Jerarquía visual: concepto > texto en pantalla (destacado) > campos medios > por qué funciona.

    Usamos html.escape() en todos los campos dinámicos para evitar que caracteres
    como < > & rompan el HTML si la IA los incluye en el texto.
    """
    numero   = html.escape(str(idea.get("idea", "?")))
    concepto = html.escape(idea.get("concepto", "—"))
    texto    = html.escape(idea.get("texto_en_pantalla", "—"))
    elemento = html.escape(idea.get("elemento_interactivo", "—"))
    por_que  = html.escape(idea.get("por_que_funciona", "—"))

    # Modos con imagen usan "tratamiento_imagen"; modos de texto usan "tipo_contenido_sugerido".
    # Comprobamos qué campo trae la idea para mostrar la etiqueta correcta.
    if "tratamiento_imagen" in idea:
        etiqueta_formato = "Tratamiento de la imagen"
        que_grabar = html.escape(idea.get("tratamiento_imagen", "—"))
    else:
        etiqueta_formato = "Qué grabar"
        que_grabar = html.escape(idea.get("tipo_contenido_sugerido", "—"))

    st.markdown(f"""
    <div class="idea-card">
        <div class="idea-numero">{prefijo} {numero}</div>
        <div class="campo-label">Concepto</div>
        <div class="idea-concepto">{concepto}</div>
        <div class="campo-label">Texto en pantalla</div>
        <div class="campo-texto-pantalla">{texto}</div>
        <div class="campo-label">{etiqueta_formato}</div>
        <div class="campo-valor">{que_grabar}</div>
        <div class="campo-label">Elemento interactivo</div>
        <div class="campo-valor">{elemento}</div>
        <div class="campo-label">Por qué funciona</div>
        <div class="campo-por-que">{por_que}</div>
    </div>
    """, unsafe_allow_html=True)


def renderizar_idea_refinada(idea):
    """
    Renderiza la versión refinada de una idea con una tarjeta de fondo verde
    para que el usuario distinga de un vistazo que es la versión revisada.
    Misma estructura de jerarquía que renderizar_idea().
    """
    concepto = html.escape(idea.get("concepto", "—"))
    texto    = html.escape(idea.get("texto_en_pantalla", "—"))
    elemento = html.escape(idea.get("elemento_interactivo", "—"))
    por_que  = html.escape(idea.get("por_que_funciona", "—"))

    # Mismo criterio que en renderizar_idea: el campo presente determina la etiqueta.
    if "tratamiento_imagen" in idea:
        etiqueta_formato = "Tratamiento de la imagen"
        que_grabar = html.escape(idea.get("tratamiento_imagen", "—"))
    else:
        etiqueta_formato = "Qué grabar"
        que_grabar = html.escape(idea.get("tipo_contenido_sugerido", "—"))

    st.markdown(f"""
    <div class="refinada-card">
        <div class="refinada-label">Versión refinada</div>
        <div class="campo-label">Concepto</div>
        <div class="idea-concepto">{concepto}</div>
        <div class="campo-label">Texto en pantalla</div>
        <div class="campo-texto-pantalla">{texto}</div>
        <div class="campo-label">{etiqueta_formato}</div>
        <div class="campo-valor">{que_grabar}</div>
        <div class="campo-label">Elemento interactivo</div>
        <div class="campo-valor">{elemento}</div>
        <div class="campo-label">Por qué funciona</div>
        <div class="campo-por-que">{por_que}</div>
    </div>
    """, unsafe_allow_html=True)


# ── Inicialización del session_state ──────────────────────────────────────────
# Streamlit reejecuta el script completo en cada interacción del usuario.
# session_state es un diccionario que persiste entre esas reejucuciones.
# Inicializamos cada clave solo si no existe aún (primera carga de la app).
if "ideas" not in st.session_state:
    st.session_state.ideas = None
if "angulo_usado" not in st.session_state:
    st.session_state.angulo_usado = None
if "ideas_refinadas" not in st.session_state:
    # Diccionario {índice_idea (0/1/2): dict con la última versión refinada}.
    # Permite encadenar refinamientos: cada petición parte del resultado anterior.
    st.session_state.ideas_refinadas = {}

# Clave de contexto y lista de ideas previas para la anti-repetición (Modos A, C, A+).
# contexto_id = tupla (modo, foto_part, desc_part) que identifica el contexto actual.
# Ahora incluye el modo (pestaña activa) para que cambiar de pestaña reinicie el contexto.
# En Modo B (contexto_id = None) no se usa: el historial lo gestiona generar_ideas_flexible.
if "contexto_id" not in st.session_state:
    st.session_state.contexto_id = None
if "ideas_previas_contexto" not in st.session_state:
    st.session_state.ideas_previas_contexto = []


# ── Sidebar: solo controles que aplican a todos los modos ────────────────────
# El selector de cliente y el nivel de creatividad son independientes del modo,
# por eso permanecen aquí. La foto, la descripción y el botón de generar se han
# movido a cada pestaña para que el modo sea una elección explícita del usuario.
with st.sidebar:
    st.markdown(f"### {APP_NOMBRE}")
    st.caption(APP_SUBTITULO)
    st.divider()

    clientes = descubrir_clientes()

    if not clientes:
        st.error(
            "No se encontró ningún archivo cliente_*.json "
            "en la carpeta del proyecto."
        )
        st.stop()

    nombre_seleccionado = st.selectbox(
        "Cliente",
        options=list(clientes.keys()),
    )
    ruta_seleccionada = clientes[nombre_seleccionado]

    st.markdown("**Nivel de creatividad**")
    # label_visibility="collapsed" oculta la etiqueta del radio porque ya
    # la ponemos nosotros manualmente con st.markdown justo encima.
    nivel_etiqueta = st.radio(
        "Nivel de creatividad",
        options=list(MAPA_CREATIVIDAD.keys()),
        index=1,
        label_visibility="collapsed",
    )
    nivel_interno = MAPA_CREATIVIDAD[nivel_etiqueta]


# ── Cabecera del área principal ───────────────────────────────────────────────
st.markdown(f"""
<div class="app-header">
    <p class="app-titulo">{APP_NOMBRE}</p>
    <p class="app-subtitulo">{APP_SUBTITULO}</p>
</div>
""", unsafe_allow_html=True)


# ── Pestañas de selección de modo ────────────────────────────────────────────
# Cada pestaña corresponde a un modo de generación. El usuario elige explícitamente
# cómo quiere trabajar antes de aportar inputs, en lugar de que la app lo deduzca.
tab_lluvia, tab_foto, tab_tema, tab_foto_tema = st.tabs([
    "Lluvia de ideas",   # Modo B: sin foto, sin descripción
    "Desde una foto",    # Modo A: con foto, sin descripción
    "Desde un tema",     # Modo C: sin foto, con descripción
    "Foto + tema",       # Modo A+: con foto y descripción
])

# Variables de control que se rellenan dentro de la pestaña cuyo botón se pulsa.
# Solo una pestaña puede tener generar_pulsado=True en cada ejecución del script.
generar_pulsado = False
modo_activo     = None   # "B" | "A" | "C" | "A+"
foto_activa     = None   # UploadedFile o None
desc_activa     = ""     # str (ya sin espacios) o ""


# ── Pestaña 1: Lluvia de ideas (Modo B) ──────────────────────────────────────
with tab_lluvia:
    st.markdown(
        "Te damos 3 ideas variadas a partir de la identidad del cliente, "
        "rotando temas para que no se repitan entre generaciones."
    )
    # En Modo B la anti-repetición la gestiona generar_ideas_flexible internamente,
    # por eso el botón siempre muestra la misma etiqueta (no hay ideas_previas_contexto).
    if st.button(
        "Generar ideas",
        key="btn_lluvia",
        type="primary",
        use_container_width=True,
    ):
        generar_pulsado = True
        modo_activo     = "B"


# ── Pestaña 2: Desde una foto (Modo A) ───────────────────────────────────────
with tab_foto:
    foto_a = st.file_uploader(
        "Sube una foto del local, un plato o cualquier imagen del cliente",
        type=["jpg", "jpeg", "png"],
        key="foto_modo_a",
        help=(
            "La IA analizará la imagen y propondrá ideas de Story "
            "basadas en lo que aparece en ella."
        ),
    )
    # Calcular si ya existen ideas previas para este contexto concreto.
    # El contexto_id incluye el modo y la identidad de la foto (nombre, tamaño).
    ctx_a     = ("A", (foto_a.name, foto_a.size) if foto_a else None, None)
    previas_a = (
        ctx_a == st.session_state.contexto_id
        and bool(st.session_state.ideas_previas_contexto)
    )
    label_a = "Generar otras 3 ideas" if previas_a else "Generar ideas para esta foto"
    if st.button(
        label_a,
        key="btn_modo_a",
        type="primary",
        use_container_width=True,
    ):
        generar_pulsado = True
        modo_activo     = "A"
        foto_activa     = foto_a


# ── Pestaña 3: Desde un tema (Modo C) ────────────────────────────────────────
with tab_tema:
    desc_c = st.text_area(
        "Describe el tema o la ocasión",
        placeholder=(
            "Ej: anuncio del menú de Fallas del viernes, "
            "promoción del aperitivo del sábado, presentación del nuevo chef..."
        ),
        height=100,
        key="desc_modo_c",
    )
    desc_c_strip = desc_c.strip() if desc_c else ""
    # El contexto_id incluye el modo y el texto de la descripción.
    ctx_c     = ("C", None, desc_c_strip or None)
    previas_c = (
        ctx_c == st.session_state.contexto_id
        and bool(st.session_state.ideas_previas_contexto)
    )
    label_c = "Generar otras 3 ideas" if previas_c else "Generar ideas sobre este tema"
    if st.button(
        label_c,
        key="btn_modo_c",
        type="primary",
        use_container_width=True,
    ):
        generar_pulsado = True
        modo_activo     = "C"
        desc_activa     = desc_c_strip


# ── Pestaña 4: Foto + tema (Modo A+) ─────────────────────────────────────────
with tab_foto_tema:
    foto_ap = st.file_uploader(
        "Sube una foto del local, un plato o cualquier imagen del cliente",
        type=["jpg", "jpeg", "png"],
        key="foto_modo_ap",
        help=(
            "La IA analizará la imagen y aplicará el tema que describas "
            "para proponer ideas de Story más ajustadas."
        ),
    )
    desc_ap = st.text_area(
        "Describe el tema o la ocasión",
        placeholder=(
            "Ej: anuncio del menú de Fallas del viernes, "
            "promoción del aperitivo del sábado, presentación del nuevo chef..."
        ),
        height=100,
        key="desc_modo_ap",
    )
    desc_ap_strip = desc_ap.strip() if desc_ap else ""
    # El contexto_id incluye el modo, la identidad de la foto y el texto del tema.
    ctx_ap    = (
        "A+",
        (foto_ap.name, foto_ap.size) if foto_ap else None,
        desc_ap_strip or None,
    )
    previas_ap = (
        ctx_ap == st.session_state.contexto_id
        and bool(st.session_state.ideas_previas_contexto)
    )
    label_ap = "Generar otras 3 ideas" if previas_ap else "Generar ideas para esta foto y tema"
    if st.button(
        label_ap,
        key="btn_modo_ap",
        type="primary",
        use_container_width=True,
    ):
        generar_pulsado = True
        modo_activo     = "A+"
        foto_activa     = foto_ap
        desc_activa     = desc_ap_strip


# ── Lógica de generación ──────────────────────────────────────────────────────
if generar_pulsado:
    # Calcular el nuevo contexto_id en función de la pestaña activa y sus inputs.
    # El modo forma parte de la clave para que cambiar de pestaña reinicie el contexto
    # aunque la foto o la descripción coincidan con una sesión anterior.
    if modo_activo == "B":
        nuevo_ctx = None
    elif modo_activo == "A":
        nuevo_ctx = (
            "A",
            (foto_activa.name, foto_activa.size) if foto_activa else None,
            None,
        )
    elif modo_activo == "C":
        nuevo_ctx = ("C", None, desc_activa or None)
    else:  # A+
        nuevo_ctx = (
            "A+",
            (foto_activa.name, foto_activa.size) if foto_activa else None,
            desc_activa or None,
        )

    # Resetear las ideas previas si el contexto ha cambiado respecto a la última generación.
    # Esto ocurre al cambiar de pestaña, subir una foto distinta o escribir un tema diferente.
    if nuevo_ctx != st.session_state.contexto_id:
        st.session_state.contexto_id            = nuevo_ctx
        st.session_state.ideas_previas_contexto = []

    hay_foto = foto_activa is not None
    hay_desc = bool(desc_activa)

    try:
        ficha = cargar_brand_kit(ruta_seleccionada)

        # Mensaje del spinner adaptado al modo activo
        if modo_activo == "A+":
            msg_spinner = f"Analizando la foto y el tema para {nombre_seleccionado}..."
        elif modo_activo == "A":
            msg_spinner = f"Analizando la foto y generando ideas para {nombre_seleccionado}..."
        elif modo_activo == "C":
            msg_spinner = f"Generando ideas sobre el tema para {nombre_seleccionado}..."
        else:
            msg_spinner = f"Generando ideas para {nombre_seleccionado}..."

        with st.spinner(msg_spinner):
            # generar_ideas_flexible espera una ruta de archivo, no bytes en memoria.
            # Si hay foto, la escribimos en un temporal, lo usamos y lo borramos.
            ruta_tmp = None
            if hay_foto:
                ext = os.path.splitext(foto_activa.name)[1].lower()
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    tmp.write(foto_activa.getvalue())
                    ruta_tmp = tmp.name

            try:
                ideas, angulo = generar_ideas_flexible(
                    ficha,
                    nivel_interno,
                    ruta_imagen=ruta_tmp,
                    descripcion=desc_activa or None,
                    # En Modo B el historial es interno; en el resto usamos las previas
                    ideas_previas=st.session_state.ideas_previas_contexto or None,
                )
            finally:
                # Borrar el temporal en cualquier caso (éxito o error)
                if ruta_tmp is not None:
                    try:
                        os.unlink(ruta_tmp)
                    except OSError:
                        pass

        # Acumular los nuevos conceptos para la anti-repetición (Modos A, C y A+).
        # En Modo B no acumulamos aquí: generar_ideas_flexible ya guardó en historial.
        if hay_foto or hay_desc:
            nuevos = [i.get("concepto", "") for i in ideas if i.get("concepto")]
            st.session_state.ideas_previas_contexto = (
                st.session_state.ideas_previas_contexto + nuevos
            )

        # Guardar resultado y ángulo; limpiar refinamientos de la tanda anterior
        st.session_state.ideas           = ideas
        st.session_state.angulo_usado    = angulo   # None en todos los modos salvo B
        st.session_state.ideas_refinadas = {}

        # Forzar rerun para que el botón se redibuje con el texto correcto al instante
        st.rerun()

    except (ValueError, ConnectionError, FileNotFoundError) as e:
        st.error(f"No se han podido generar las ideas. Detalle del error:\n\n{e}")


# ── Área de resultados: bienvenida o ideas ───────────────────────────────────
if not st.session_state.ideas:
    # Pantalla de bienvenida: visible hasta que el usuario genera ideas por primera vez.
    # Invita a elegir una pestaña en lugar de describir controles de la sidebar.
    st.markdown("""
    <div class="bienvenida">
        <div class="bienvenida-titulo">Elige cómo quieres empezar</div>
        <div class="bienvenida-desc">
            Selecciona una pestaña según lo que tengas: una foto, un tema,
            ambas cosas, o deja que el generador sorprenda con ideas variadas.
        </div>
    </div>
    """, unsafe_allow_html=True)

else:
    # Ángulo temático: solo se muestra en Modo B (generar_ideas_flexible devuelve
    # el ángulo únicamente cuando no hay imagen ni descripción).
    if st.session_state.angulo_usado:
        st.caption(f"Ángulo temático: {st.session_state.angulo_usado}")

    # Las 3 ideas en columnas de igual ancho (aprovecha el layout wide)
    columnas = st.columns(3, gap="medium")
    for col, idea in zip(columnas, st.session_state.ideas):
        with col:
            renderizar_idea(idea)

    # ── Sección de refinamiento ───────────────────────────────────────────
    st.divider()
    st.markdown("#### Refinar una idea")

    # Dos columnas: selector + botón a la izquierda, campo de texto a la derecha
    col_izq, col_der = st.columns([1, 2], gap="medium")

    with col_izq:
        opcion_elegida   = st.radio("Idea a refinar", ["Idea 1", "Idea 2", "Idea 3"])
        # Convertimos "Idea 2" → índice 1 leyendo el último carácter y restando 1
        idx_seleccionado = int(opcion_elegida[-1]) - 1

        refinar_pulsado = st.button(
            "Aplicar cambio",
            use_container_width=True,
            type="primary",
        )

    with col_der:
        peticion = st.text_area(
            "¿Qué quieres cambiar?",
            placeholder=(
                "Ej: hazla más breve, menciona que es un plan de sábado, "
                "quítale los emojis, cambia el tono a más directo..."
            ),
            height=120,
        )

    # Lógica de refinamiento: se ejecuta cuando el usuario pulsa "Aplicar cambio"
    if refinar_pulsado:
        if not peticion.strip():
            st.warning("Escribe qué quieres cambiar antes de aplicar el refinamiento.")
        else:
            try:
                with st.spinner("Aplicando cambios..."):
                    # Si ya existe una versión refinada de esta idea en session_state,
                    # partimos de ella (no de la original). Así los refinamientos se
                    # encadenan: cada petición se aplica sobre el resultado del anterior.
                    base = st.session_state.ideas_refinadas.get(
                        idx_seleccionado,
                        st.session_state.ideas[idx_seleccionado],
                    )
                    idea_revisada = refinar_idea(base, peticion, nivel_creatividad=nivel_interno)

                # Guardamos la versión revisada; el siguiente refinamiento partirá de aquí
                st.session_state.ideas_refinadas[idx_seleccionado] = idea_revisada

            except (ValueError, ConnectionError) as e:
                st.error(f"No se ha podido refinar la idea. Detalle del error:\n\n{e}")

    # Mostramos la versión refinada si existe para la idea actualmente seleccionada.
    # Se renderiza en cada rerun de Streamlit gracias a que está en session_state.
    if idx_seleccionado in st.session_state.ideas_refinadas:
        renderizar_idea_refinada(st.session_state.ideas_refinadas[idx_seleccionado])
