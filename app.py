import glob
import html
import json

import streamlit as st

from brand_kit import cargar_brand_kit
from generador import (
    cargar_historial,
    construir_prompt,
    elegir_angulo,
    generar_ideas,
    guardar_en_historial,
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
</style>
""", unsafe_allow_html=True)

# ── Traducción etiqueta visible → valor interno de generar_ideas() ────────────
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
    Jerarquía visual: concepto (negrita) > texto en pantalla (destacado con acento)
    > campos medios > por qué funciona (discreto).

    Usamos html.escape() en todos los campos dinámicos para evitar que caracteres
    como < > & rompan el HTML si la IA los incluye en el texto.
    """
    numero    = html.escape(str(idea.get("idea", "?")))
    concepto  = html.escape(idea.get("concepto", "—"))
    que_grabar = html.escape(idea.get("tipo_contenido_sugerido", "—"))
    texto     = html.escape(idea.get("texto_en_pantalla", "—"))
    elemento  = html.escape(idea.get("elemento_interactivo", "—"))
    por_que   = html.escape(idea.get("por_que_funciona", "—"))

    st.markdown(f"""
    <div class="idea-card">
        <div class="idea-numero">{prefijo} {numero}</div>
        <div class="campo-label">Concepto</div>
        <div class="idea-concepto">{concepto}</div>
        <div class="campo-label">Texto en pantalla</div>
        <div class="campo-texto-pantalla">{texto}</div>
        <div class="campo-label">Qué grabar</div>
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
    concepto   = html.escape(idea.get("concepto", "—"))
    que_grabar = html.escape(idea.get("tipo_contenido_sugerido", "—"))
    texto      = html.escape(idea.get("texto_en_pantalla", "—"))
    elemento   = html.escape(idea.get("elemento_interactivo", "—"))
    por_que    = html.escape(idea.get("por_que_funciona", "—"))

    st.markdown(f"""
    <div class="refinada-card">
        <div class="refinada-label">Versión refinada</div>
        <div class="campo-label">Concepto</div>
        <div class="idea-concepto">{concepto}</div>
        <div class="campo-label">Texto en pantalla</div>
        <div class="campo-texto-pantalla">{texto}</div>
        <div class="campo-label">Qué grabar</div>
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
    # Permite encadenar refinamientos: cada refine parte del resultado anterior,
    # no de la idea original.
    st.session_state.ideas_refinadas = {}


# ── Sidebar: controles de configuración y botón principal ────────────────────
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

    st.divider()

    # Botón principal: type="primary" aplica el color de acento de Streamlit
    generar_pulsado = st.button(
        "Generar ideas",
        use_container_width=True,
        type="primary",
    )


# ── Cabecera del área principal ───────────────────────────────────────────────
st.markdown(f"""
<div class="app-header">
    <p class="app-titulo">{APP_NOMBRE}</p>
    <p class="app-subtitulo">{APP_SUBTITULO}</p>
</div>
""", unsafe_allow_html=True)


# ── Lógica de generación ──────────────────────────────────────────────────────
if generar_pulsado:
    try:
        with st.spinner(f"Generando ideas para {nombre_seleccionado}..."):
            ficha     = cargar_brand_kit(ruta_seleccionada)
            historial = cargar_historial(nombre_seleccionado)
            angulo    = elegir_angulo(historial)
            prompt    = construir_prompt(ficha, historial_conceptos=historial, angulo=angulo)
            ideas     = generar_ideas(prompt, nivel_creatividad=nivel_interno)
            guardar_en_historial(nombre_seleccionado, ideas)

        # Guardamos en session_state para que sobrevivan a los reruns de Streamlit.
        # Al generar ideas nuevas, reseteamos las versiones refinadas: las de la
        # tanda anterior no tienen sentido sobre ideas completamente distintas.
        st.session_state.ideas           = ideas
        st.session_state.angulo_usado    = angulo
        st.session_state.ideas_refinadas = {}

    except (ValueError, ConnectionError) as e:
        st.error(f"No se han podido generar las ideas. Detalle del error:\n\n{e}")


# ── Área principal: bienvenida o ideas ───────────────────────────────────────
if not st.session_state.ideas:
    # Estado inicial: se muestra hasta que el usuario genera ideas por primera vez.
    st.markdown("""
    <div class="bienvenida">
        <div class="bienvenida-titulo">Listo para empezar</div>
        <div class="bienvenida-desc">
            Elige un cliente y un nivel de creatividad en el panel izquierdo
            y pulsa <strong>Generar ideas</strong> para comenzar.
        </div>
    </div>
    """, unsafe_allow_html=True)

else:
    # Ángulo temático, como contexto discreto encima de las tarjetas
    st.caption(f"Angulo tematico: {st.session_state.angulo_usado}")

    # Las 3 ideas en columnas de igual ancho (aprovecha el layout wide)
    columnas = st.columns(3, gap="medium")
    for col, idea in zip(columnas, st.session_state.ideas):
        with col:
            renderizar_idea(idea)

    # ── Sección de refinamiento ───────────────────────────────────────────────
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
