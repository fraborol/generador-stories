import os
import sys
import json
import re
import unicodedata
import base64

import anthropic
from json_repair import repair_json
from dotenv import load_dotenv

# Forzar UTF-8 en la salida del terminal (necesario en Windows)
sys.stdout.reconfigure(encoding="utf-8")

from brand_kit import cargar_brand_kit

# Cargamos las variables de entorno del archivo .env (donde está la API key)
load_dotenv()

# Diccionario que traduce los niveles de creatividad (en español, para el usuario)
# a valores numéricos de temperatura que entiende la IA.
# Temperatura baja = respuestas más predecibles; alta = más variadas y creativas.
NIVELES_CREATIVIDAD = {
    "seguras":      0.5,
    "equilibradas": 0.8,
    "atrevidas":    1.0,
}

# Número máximo de conceptos del historial que se incluyen en el prompt.
# Fijado alto para que en la práctica se use el historial completo,
# pero fácil de bajar si el prompt empieza a hacerse demasiado largo.
MAX_HISTORIAL_EN_PROMPT = 50

# Ángulos temáticos que se rotan en cada generación para mantener variedad.
# Añadir, quitar o reordenar los elementos de esta lista para ajustar la rotación.
ANGULOS = [
    "gastronomía y producto",
    "ambiente y lifestyle",
    "eventos y El Club",
    "el ritual del tardeo",
    "el equipo y el servicio",
]

# Instrucción de prevención que se añade al final de las INSTRUCCIONES DE FORMATO
# de todos los prompts de generación y refinamiento.
# La IA tiende a incluir comillas dobles sin escapar dentro de los valores del JSON
# (por ejemplo en diálogos), lo que rompe json.loads con "Expecting ',' delimiter".
_INSTRUCCION_JSON_COMILLAS = (
    "\n\nIMPORTANTE: dentro de los valores de texto del JSON, NO uses comillas dobles "
    '(") en ningún caso. Si necesitas representar comillas en el texto en pantalla o '
    "en cualquier otro campo, usa comillas tipográficas españolas («…») o comillas "
    "simples (' '). El JSON debe poder parsearse sin errores."
)


def _nombre_a_slug(nombre_cliente):
    """
    Convierte el nombre de un cliente a un slug seguro para usar como nombre de archivo.
    Elimina tildes, pasa a minúsculas y sustituye caracteres no alfanuméricos por '_'.
    Ejemplo: "Don Tomás" → "historial_don_tomas.json"
    """
    # Descomponer caracteres Unicode para separar la letra base del acento
    sin_tildes = unicodedata.normalize("NFKD", nombre_cliente)
    sin_tildes = sin_tildes.encode("ascii", "ignore").decode("ascii")
    # Minúsculas y reemplazar todo lo que no sea letra o dígito por '_'
    slug = re.sub(r"[^a-z0-9]", "_", sin_tildes.lower())
    # Colapsar guiones bajos consecutivos y quitar los extremos
    slug = re.sub(r"_+", "_", slug).strip("_")
    return f"historial_{slug}.json"


def cargar_historial(nombre_cliente):
    """
    Lee el archivo de historial del cliente y devuelve la lista de conceptos
    ya propuestos en sesiones anteriores.
    Si el archivo no existe (primera vez), devuelve una lista vacía sin error.

    Argumento:
      nombre_cliente (str): Nombre del cliente tal como aparece en la ficha.

    Devuelve:
      list[str]: Lista de strings, uno por concepto ya propuesto.
    """
    ruta = _nombre_a_slug(nombre_cliente)

    if not os.path.exists(ruta):
        return []

    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [aviso] No se pudo leer el historial '{ruta}': {e}. Se continúa sin historial.")
        return []


def guardar_en_historial(nombre_cliente, ideas_nuevas):
    """
    Extrae el campo 'concepto' de cada idea nueva y lo añade al historial
    existente del cliente, guardando el resultado en su archivo JSON.

    Si la escritura falla, avisa pero no interrumpe el programa.

    Argumentos:
      nombre_cliente (str):  Nombre del cliente.
      ideas_nuevas   (list): Lista de diccionarios de ideas recién generadas.
    """
    ruta = _nombre_a_slug(nombre_cliente)

    # Solo guardamos el concepto (texto corto) para no inflar el archivo
    nuevos_conceptos = [
        idea.get("concepto", "")
        for idea in ideas_nuevas
        if idea.get("concepto")
    ]

    historial_actual = cargar_historial(nombre_cliente)
    historial_actualizado = historial_actual + nuevos_conceptos

    try:
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(historial_actualizado, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"  [aviso] No se pudo guardar el historial en '{ruta}': {e}.")


def elegir_angulo(historial_conceptos):
    """
    Elige el ángulo temático que toca en esta generación, rotando por la lista
    ANGULOS según el número de generaciones previas estimado a partir del historial.
    Cada tanda de 3 conceptos en el historial cuenta como una generación.

    Argumento:
      historial_conceptos (list[str]): Lista de conceptos ya propuestos.

    Devuelve:
      str: El ángulo elegido para esta generación.
    """
    generaciones_previas = len(historial_conceptos) // 3
    idx = generaciones_previas % len(ANGULOS)
    return ANGULOS[idx]


def construir_prompt(brand_kit, historial_conceptos=None, angulo=None):
    """
    Recibe la ficha de cliente (Brand Kit) y devuelve un prompt completo.

    Incluye siempre los campos obligatorios y añade los opcionales
    solo si existen y tienen contenido. Si se proporcionan, también incorpora
    el historial de conceptos ya propuestos (para evitar repeticiones) y el
    ángulo temático de esta generación.

    Argumentos:
      brand_kit           (dict):      Ficha del cliente.
      historial_conceptos (list[str]): Conceptos ya propuestos (puede ser None o []).
      angulo              (str):       Enfoque temático para esta generación (puede ser None).
    """

    # ── Campos obligatorios (siempre presentes) ────────────────────────────
    cliente            = brand_kit["cliente"]
    sector             = brand_kit["sector"]
    tono               = brand_kit["tono"]
    publico            = brand_kit["publico"]

    # ── Campos opcionales: recuperados con .get() para no rompernos ────────
    valores            = brand_kit.get("valores", [])
    evitar             = brand_kit.get("evitar", [])
    contenido_habitual = brand_kit.get("contenido_habitual", "")

    # ── Bloques de texto opcionales: solo se generan si tienen contenido ───
    bloque_valores = ""
    if valores:
        items = "\n".join(f"  - {v}" for v in valores)
        bloque_valores = f"\nValores de marca:\n{items}"

    bloque_evitar = ""
    if evitar:
        items = "\n".join(f"  - {e}" for e in evitar)
        bloque_evitar = f"\nCosas a evitar:\n{items}"

    bloque_contenido = ""
    if contenido_habitual:
        bloque_contenido = f"\nTipos de contenido habitual del cliente:\n  {contenido_habitual}"

    # ── Bloque de historial: conceptos a NO repetir ────────────────────────
    bloque_historial = ""
    if historial_conceptos:
        # Usamos solo los últimos MAX_HISTORIAL_EN_PROMPT para no inflar el prompt
        recientes = historial_conceptos[-MAX_HISTORIAL_EN_PROMPT:]
        items = "\n".join(f"  - {c}" for c in recientes)
        bloque_historial = (
            f"\nIDEAS YA PROPUESTAS ANTERIORMENTE (NO repitas ninguna ni propongas "
            f"ideas muy parecidas a estas):\n{items}"
        )

    # ── Bloque de ángulo: enfoque temático de esta generación ─────────────
    bloque_angulo = ""
    if angulo:
        bloque_angulo = (
            f"\nÁNGULO TEMÁTICO PARA ESTA GENERACIÓN:\n"
            f"Enfoca las 3 ideas en torno a: {angulo}"
        )

    # ── Ensamblado del prompt completo ─────────────────────────────────────
    # Las llaves dobles {{ }} son literales en f-strings: se imprimen como { }
    prompt = f"""Eres una experta en crear Stories de Instagram para marcas y negocios.

Trabaja para el siguiente cliente:

Cliente: {cliente}
Sector: {sector}
Tono de comunicación: {tono}
Público objetivo: {publico}{bloque_valores}{bloque_evitar}{bloque_contenido}{bloque_historial}{bloque_angulo}

Tu tarea es generar exactamente 3 ideas originales y creativas de Story de Instagram para este cliente.

INSTRUCCIONES DE FORMATO:
Responde ÚNICAMENTE con un JSON válido, sin ningún texto adicional antes ni después.
El JSON debe ser una lista de 3 objetos con exactamente esta estructura:

[
  {{
    "idea": 1,
    "concepto": "Descripción breve de la idea central de la Story",
    "tipo_contenido_sugerido": "Foto / Vídeo / Reel / Boomerang / etc.",
    "texto_en_pantalla": "El copy exacto que aparecería escrito en la Story",
    "elemento_interactivo": "Encuesta / Pregunta / Cuenta atrás / Ninguno / etc.",
    "por_que_funciona": "Explicación breve de por qué esta idea conecta con el público"
  }},
  {{
    "idea": 2,
    "concepto": "...",
    "tipo_contenido_sugerido": "...",
    "texto_en_pantalla": "...",
    "elemento_interactivo": "...",
    "por_que_funciona": "..."
  }},
  {{
    "idea": 3,
    "concepto": "...",
    "tipo_contenido_sugerido": "...",
    "texto_en_pantalla": "...",
    "elemento_interactivo": "...",
    "por_que_funciona": "..."
  }}
]"""

    return prompt + _INSTRUCCION_JSON_COMILLAS


def _llamar_api(prompt, temperatura):
    """
    Función auxiliar privada: envía un mensaje a la API de Anthropic y devuelve
    el texto crudo de la respuesta. Centraliza la autenticación y el manejo de
    errores para que todas las funciones de generación compartan este código.

    Argumentos:
      prompt      (str | list): Texto del prompt (modo texto) O lista de bloques
                                de contenido [{type, ...}] (modo multimodal con imagen).
                                El SDK de Anthropic acepta ambas formas en el campo
                                "content" del mensaje.
      temperatura (float):      Valor entre 0.0 y 1.0 que controla la creatividad.

    Devuelve:
      str: Texto completo de la respuesta de la IA.
    """

    # ── Leer la API key del entorno ────────────────────────────────────────
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError(
            "No se encontró la clave de API de Anthropic. "
            "Comprueba que el archivo .env existe y contiene la línea: "
            "ANTHROPIC_API_KEY=tu-clave-aquí"
        )

    # ── Llamada a la API con manejo de todos los errores conocidos ─────────
    try:
        cliente_api = anthropic.Anthropic(api_key=api_key)

        respuesta = cliente_api.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            temperature=temperatura,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

    except anthropic.AuthenticationError:
        raise ValueError(
            "La clave de API es incorrecta o ha expirado. "
            "Revisa el valor de ANTHROPIC_API_KEY en tu archivo .env."
        )
    except anthropic.PermissionDeniedError:
        raise ValueError(
            "Sin permisos en la cuenta de Anthropic. "
            "Revisa el estado de tu cuenta en: https://console.anthropic.com"
        )
    except anthropic.BadRequestError as e:
        # El SDK usa BadRequestError (400) también para saldo insuficiente
        mensaje = str(e).lower()
        if "credit" in mensaje or "balance" in mensaje or "billing" in mensaje:
            raise ValueError(
                "Saldo insuficiente en la cuenta de Anthropic. "
                "Recarga créditos en: https://console.anthropic.com/settings/plans"
            )
        raise ValueError(f"Solicitud incorrecta a la API: {e}")
    except anthropic.APIConnectionError:
        raise ConnectionError(
            "No se pudo conectar con la API de Anthropic. "
            "Comprueba tu conexión a internet e inténtalo de nuevo."
        )

    return respuesta.content[0].text


def _parsear_lista_json(texto_respuesta):
    """
    Extrae el primer array JSON de la respuesta de la IA y lo parsea.

    Estrategia de dos intentos:
      1. json.loads normal (rápido, sin coste).
      2. Si falla, repair_json intenta corregir problemas comunes (comillas sin
         escapar, comas sobrantes, llaves mal cerradas) antes de parsear de nuevo.
      3. Si tampoco funciona, lanza ValueError con el fragmento recibido.
    """
    inicio = texto_respuesta.find("[")
    fin    = texto_respuesta.rfind("]")

    if inicio == -1 or fin == -1:
        raise ValueError(
            "La respuesta de la IA no contiene un JSON válido. "
            f"Respuesta recibida:\n{texto_respuesta}"
        )

    fragmento = texto_respuesta[inicio:fin + 1]

    try:
        return json.loads(fragmento)
    except json.JSONDecodeError:
        # Segundo intento: reparación automática con json_repair
        try:
            return json.loads(repair_json(fragmento))
        except Exception:
            raise ValueError(
                "La respuesta de la IA contiene JSON mal formado y no se pudo reparar. "
                f"Fragmento recibido:\n{fragmento}"
            )


def _parsear_objeto_json(texto_respuesta):
    """
    Extrae el primer objeto JSON de la respuesta de la IA y lo parsea.
    Mismo mecanismo de dos intentos que _parsear_lista_json, pero para {…}.
    """
    inicio = texto_respuesta.find("{")
    fin    = texto_respuesta.rfind("}")

    if inicio == -1 or fin == -1:
        raise ValueError(
            "La respuesta de la IA no contiene un JSON válido. "
            f"Respuesta recibida:\n{texto_respuesta}"
        )

    fragmento = texto_respuesta[inicio:fin + 1]

    try:
        return json.loads(fragmento)
    except json.JSONDecodeError:
        try:
            return json.loads(repair_json(fragmento))
        except Exception:
            raise ValueError(
                "La respuesta de la IA contiene JSON mal formado y no se pudo reparar. "
                f"Fragmento recibido:\n{fragmento}"
            )


def generar_ideas(prompt, nivel_creatividad="equilibradas"):
    """
    Envía el prompt a la API de Anthropic y devuelve una lista de 3 ideas
    de Story de Instagram en formato de diccionarios de Python.

    Argumentos:
      prompt            (str): El texto del prompt construido con construir_prompt().
      nivel_creatividad (str): "seguras", "equilibradas" o "atrevidas".

    Devuelve:
      list: Lista de 3 diccionarios, cada uno con los campos de una idea de Story.
    """

    # ── Obtener la temperatura según el nivel elegido ──────────────────────
    if nivel_creatividad not in NIVELES_CREATIVIDAD:
        print(f"  [aviso] Nivel '{nivel_creatividad}' no reconocido. Se usará 'equilibradas'.")
        nivel_creatividad = "equilibradas"

    temperatura = NIVELES_CREATIVIDAD[nivel_creatividad]

    # ── Llamar a la API y parsear la respuesta ────────────────────────────
    # _parsear_lista_json extrae el array JSON y, si json.loads falla, intenta
    # repararlo con json_repair antes de propagar el error.
    texto_respuesta = _llamar_api(prompt, temperatura)
    ideas = _parsear_lista_json(texto_respuesta)
    return ideas


def construir_prompt_refinamiento(idea, peticion_cambio):
    """
    Recibe una idea de Story ya generada (diccionario con sus campos) y una
    petición de cambio en texto libre, y devuelve un prompt listo para pedir
    a la IA que reescriba esa idea aplicando la modificación solicitada.

    Argumentos:
      idea            (dict): Diccionario con los campos de una idea de Story.
      peticion_cambio (str):  Cambio que el usuario quiere aplicar (ej. "hazla más breve").

    Devuelve:
      str: Prompt completo para enviar a la IA.
    """

    # ── Mostrar la idea actual con todos sus campos ────────────────────────
    prompt = f"""Eres una experta en crear Stories de Instagram para marcas y negocios.

A continuación tienes una idea de Story ya generada:

Idea número: {idea.get('idea', '?')}
Concepto: {idea.get('concepto', '')}
Tipo de contenido sugerido: {idea.get('tipo_contenido_sugerido', '')}
Texto en pantalla: {idea.get('texto_en_pantalla', '')}
Elemento interactivo: {idea.get('elemento_interactivo', '')}
Por qué funciona: {idea.get('por_que_funciona', '')}

El usuario quiere aplicar el siguiente cambio sobre esta idea:
"{peticion_cambio}"

Tu tarea es reescribir la idea aplicando ese cambio. Sigue estas reglas de prioridad:

REGLAS DE EDICIÓN:
1. "texto_en_pantalla" es el contenido que el usuario ve en la Story y es el campo más importante.
   Cuando la petición se refiera al contenido, el tono, la longitud o el estilo del mensaje,
   aplica el cambio PRINCIPALMENTE sobre este campo.
2. "por_que_funciona" es una nota interna explicativa. NO la acortes ni la alteres salvo que
   el usuario lo pida explícitamente. Solo actualízala lo justo para que siga siendo coherente
   con la idea revisada.
3. "concepto", "tipo_contenido_sugerido" y "elemento_interactivo" solo se modifican si la
   petición del usuario los afecta directamente.
4. Respeta SIEMPRE la ortografía correcta del español: tildes, signos de puntuación (¿?, ¡!,
   comas, puntos) y mayúsculas deben mantenerse. Acortar el texto nunca justifica eliminar
   tildes ni signos necesarios.

INSTRUCCIONES DE FORMATO:
Responde ÚNICAMENTE con un JSON válido, sin ningún texto adicional antes ni después.
El JSON debe ser un único objeto con exactamente esta estructura:

{{
  "idea": {idea.get('idea', 1)},
  "concepto": "Descripción breve de la idea central de la Story",
  "tipo_contenido_sugerido": "Foto / Vídeo / Reel / Boomerang / etc.",
  "texto_en_pantalla": "El copy exacto que aparecería escrito en la Story",
  "elemento_interactivo": "Encuesta / Pregunta / Cuenta atrás / Ninguno / etc.",
  "por_que_funciona": "Explicación breve de por qué esta idea conecta con el público"
}}"""

    return prompt + _INSTRUCCION_JSON_COMILLAS


def refinar_idea(idea, peticion_cambio, nivel_creatividad="equilibradas"):
    """
    Toma una idea de Story ya generada y una petición de cambio, llama a la IA
    y devuelve la idea revisada como diccionario con los mismos campos.

    Argumentos:
      idea            (dict): Diccionario con los campos de la idea a refinar.
      peticion_cambio (str):  Cambio que el usuario quiere aplicar.
      nivel_creatividad (str): "seguras", "equilibradas" o "atrevidas".

    Devuelve:
      dict: Diccionario con la idea revisada y los mismos campos que la original.
    """

    # ── Obtener la temperatura según el nivel elegido ──────────────────────
    if nivel_creatividad not in NIVELES_CREATIVIDAD:
        print(f"  [aviso] Nivel '{nivel_creatividad}' no reconocido. Se usará 'equilibradas'.")
        nivel_creatividad = "equilibradas"

    temperatura = NIVELES_CREATIVIDAD[nivel_creatividad]

    # ── Construir el prompt y llamar a la API ──────────────────────────────
    prompt = construir_prompt_refinamiento(idea, peticion_cambio)
    texto_respuesta = _llamar_api(prompt, temperatura)

    # ── Extraer el objeto JSON de la respuesta ─────────────────────────────
    # _parsear_objeto_json extrae el {…} y aplica repair_json si es necesario.
    idea_revisada = _parsear_objeto_json(texto_respuesta)
    return idea_revisada


# ── Constantes de prueba ──────────────────────────────────────────────────────
# Cambia a True para activar el bloque de prueba de foto al ejecutar el script.
PROBAR_FOTO = False
# Cambia a True para activar el bloque de los cuatro modos flexibles.
PROBAR_FLEXIBLE = False


# Extensiones de imagen soportadas por la API de visión de Anthropic
_TIPOS_IMAGEN = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}


def construir_prompt_foto(brand_kit, ideas_previas=None):
    """
    Devuelve el texto del prompt para pedir a la IA que analice una imagen adjunta
    y proponga 3 ideas de Story basadas en lo que ve en esa foto concreta.

    Argumentos:
      brand_kit     (dict):      Ficha del cliente.
      ideas_previas (list[str]): Conceptos ya propuestos para esta misma foto
                                 (para pedir enfoques distintos en sucesivas llamadas).

    Devuelve:
      str: Texto del prompt (sin la imagen; la imagen va en el bloque aparte del mensaje).
    """

    # ── Campos de la ficha ─────────────────────────────────────────────────
    cliente = brand_kit["cliente"]
    sector  = brand_kit["sector"]
    tono    = brand_kit["tono"]
    publico = brand_kit["publico"]
    valores = brand_kit.get("valores", [])
    evitar  = brand_kit.get("evitar", [])

    # ── Bloques opcionales de identidad de marca ───────────────────────────
    bloque_valores = ""
    if valores:
        items = "\n".join(f"  - {v}" for v in valores)
        bloque_valores = f"\nValores de marca:\n{items}"

    bloque_evitar = ""
    if evitar:
        items = "\n".join(f"  - {e}" for e in evitar)
        bloque_evitar = f"\nCosas a evitar:\n{items}"

    # ── Bloque anti-repetición: ideas ya propuestas para esta foto ─────────
    bloque_previas = ""
    if ideas_previas:
        items = "\n".join(f"  - {i}" for i in ideas_previas)
        bloque_previas = (
            f"\nIDEAS YA PROPUESTAS PARA ESTA FOTO (NO repitas ninguna ni propongas "
            f"ideas muy parecidas; busca enfoques claramente distintos):\n{items}"
        )

    # ── Ensamblado del prompt ──────────────────────────────────────────────
    prompt = f"""Eres una experta en crear Stories de Instagram para marcas y negocios.

Trabaja para el siguiente cliente:

Cliente: {cliente}
Sector: {sector}
Tono de comunicación: {tono}
Público objetivo: {publico}{bloque_valores}{bloque_evitar}

Se te adjunta una imagen relacionada con este cliente. Obsérvala con atención.

IMPORTANTE — LA FOTO YA ESTÁ HECHA Y ES DEFINITIVA:
No puedes cambiar la composición, reencuadrar la escena ni recolocar elementos.
Trabaja siempre con la foto TAL CUAL existe. No propongas "un primer plano de X",
"situar Y a la izquierda" ni ningún otro cambio de encuadre o composición.

Tu tarea es proponer exactamente 3 ideas de Story de Instagram usando ESA imagen
concreta para {cliente}.

El campo "tratamiento_imagen" debe indicar SOLO cómo usar o presentar la foto tal
como es. Ejemplos válidos: recorte al formato 9:16 vertical o cuadrado, formato
final (foto estática / Boomerang / vídeo con zoom suave), retoque sutil (filtro
cálido, viñeteado, blanco y negro…) y cómo colocar el texto encima. NUNCA
propongas encuadres alternativos ni cambios de composición.{bloque_previas}

INSTRUCCIONES DE FORMATO:
Responde ÚNICAMENTE con un JSON válido, sin ningún texto adicional antes ni después.
El JSON debe ser una lista de 3 objetos con exactamente esta estructura:

[
  {{
    "idea": 1,
    "concepto": "Descripción breve de la idea central de la Story",
    "tratamiento_imagen": "Recorte (ej. 9:16 vertical), formato (foto estática / Boomerang / zoom suave), retoque (filtro cálido / b&n…) y posición del texto",
    "texto_en_pantalla": "El copy exacto que aparecería escrito en la Story",
    "elemento_interactivo": "Encuesta / Pregunta / Cuenta atrás / Ninguno / etc.",
    "por_que_funciona": "Explicación breve de por qué esta idea conecta con el público"
  }},
  {{
    "idea": 2,
    "concepto": "...",
    "tratamiento_imagen": "...",
    "texto_en_pantalla": "...",
    "elemento_interactivo": "...",
    "por_que_funciona": "..."
  }},
  {{
    "idea": 3,
    "concepto": "...",
    "tratamiento_imagen": "...",
    "texto_en_pantalla": "...",
    "elemento_interactivo": "...",
    "por_que_funciona": "..."
  }}
]"""

    return prompt + _INSTRUCCION_JSON_COMILLAS


def generar_ideas_desde_foto(ruta_imagen, brand_kit, nivel_creatividad="equilibradas",
                              ideas_previas=None):
    """
    Lee una imagen, la codifica en base64 y llama a la API de Anthropic con un
    mensaje multimodal (imagen + prompt) para obtener 3 ideas de Story basadas
    en esa foto concreta.

    Argumentos:
      ruta_imagen       (str):       Ruta al archivo de imagen (JPG, PNG, GIF o WEBP).
      brand_kit         (dict):      Ficha del cliente.
      nivel_creatividad (str):       "seguras", "equilibradas" o "atrevidas".
      ideas_previas     (list[str]): Conceptos ya propuestos para esta foto (opcional).

    Devuelve:
      list: Lista de 3 diccionarios con los campos de cada idea de Story.
    """

    # ── Verificar que el archivo existe ────────────────────────────────────
    if not os.path.exists(ruta_imagen):
        raise FileNotFoundError(
            f"No se encontró el archivo de imagen: '{ruta_imagen}'. "
            "Comprueba que la ruta es correcta y que el archivo existe."
        )

    # ── Detectar el tipo de imagen por extensión ───────────────────────────
    extension  = os.path.splitext(ruta_imagen)[1].lower()
    media_type = _TIPOS_IMAGEN.get(extension)
    if not media_type:
        raise ValueError(
            f"Formato de imagen no soportado: '{extension}'. "
            f"Usa uno de: {', '.join(_TIPOS_IMAGEN.keys())}."
        )

    # ── Leer el archivo y codificarlo en base64 ────────────────────────────
    try:
        with open(ruta_imagen, "rb") as f:
            datos_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
    except OSError as e:
        raise ValueError(f"No se pudo leer el archivo de imagen: {e}")

    # ── Obtener la temperatura según el nivel elegido ──────────────────────
    if nivel_creatividad not in NIVELES_CREATIVIDAD:
        print(f"  [aviso] Nivel '{nivel_creatividad}' no reconocido. Se usará 'equilibradas'.")
        nivel_creatividad = "equilibradas"

    temperatura = NIVELES_CREATIVIDAD[nivel_creatividad]

    # ── Construir el mensaje multimodal: bloque imagen + bloque texto ──────
    # El SDK de Anthropic acepta "content" como lista de bloques cuando el
    # mensaje incluye más de un tipo de contenido (imagen y texto).
    contenido_mensaje = [
        {
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": media_type,
                "data":       datos_b64,
            },
        },
        {
            "type": "text",
            "text": construir_prompt_foto(brand_kit, ideas_previas=ideas_previas),
        },
    ]

    # ── Llamar a la API y parsear la respuesta ────────────────────────────
    # _llamar_api acepta tanto un string como una lista de bloques de contenido.
    # _parsear_lista_json aplica repair_json si json.loads falla a la primera.
    texto_respuesta = _llamar_api(contenido_mensaje, temperatura)
    ideas = _parsear_lista_json(texto_respuesta)
    return ideas


def _construir_prompt_flexible(brand_kit, hay_imagen, descripcion=None,
                                historial_conceptos=None, angulo=None,
                                ideas_previas=None):
    """
    Construye el texto del prompt de forma modular para generar_ideas_flexible.

    Ensambla solo los bloques que correspondan según los parámetros:
      hay_imagen          → instrucciones "foto ya hecha" + campo tratamiento_imagen
      descripcion         → bloque TEMA PRIORITARIO (Modo C y A+)
      historial_conceptos → anti-repetición del historial del cliente (Modo B)
      angulo              → ángulo temático (Modo B)
      ideas_previas       → anti-repetición gestionada por el llamador (A, A+, C)

    Devuelve:
      str: Texto del prompt listo para enviar a la API. La imagen, si la hay,
           se adjunta por separado en el bloque multimodal del mensaje.
    """

    # ── Campos de la ficha del cliente ─────────────────────────────────────
    cliente            = brand_kit["cliente"]
    sector             = brand_kit["sector"]
    tono               = brand_kit["tono"]
    publico            = brand_kit["publico"]
    valores            = brand_kit.get("valores", [])
    evitar             = brand_kit.get("evitar", [])
    contenido_habitual = brand_kit.get("contenido_habitual", "")

    # ── Bloques de identidad de marca ──────────────────────────────────────
    bloque_valores = ""
    if valores:
        items = "\n".join(f"  - {v}" for v in valores)
        bloque_valores = f"\nValores de marca:\n{items}"

    bloque_evitar = ""
    if evitar:
        items = "\n".join(f"  - {e}" for e in evitar)
        bloque_evitar = f"\nCosas a evitar:\n{items}"

    # Contenido habitual: solo en modos sin imagen (ayuda al modelo a generar ideas)
    bloque_contenido = ""
    if not hay_imagen and contenido_habitual:
        bloque_contenido = f"\nTipos de contenido habitual del cliente:\n  {contenido_habitual}"

    # ── Bloque de descripción — eje central en Modo C y A+ ─────────────────
    # Se coloca antes de las instrucciones de imagen para establecer prioridad.
    bloque_descripcion = ""
    if descripcion:
        bloque_descripcion = (
            f"\nTEMA O EVENTO A TRATAR (PRIORITARIO):\n"
            f"{descripcion}\n"
            f"Las 3 ideas deben girar alrededor de este tema. "
            f"Es el eje central, no una pista secundaria."
        )

    # ── Bloque de imagen — Modo A y A+ ─────────────────────────────────────
    # Recuerda a la IA que la foto ya está hecha y prohíbe proponer reencuadres.
    bloque_imagen = ""
    if hay_imagen:
        bloque_imagen = (
            "\n\nSe te adjunta una imagen relacionada con este cliente. "
            "Obsérvala con atención.\n"
            "\nIMPORTANTE — LA FOTO YA ESTÁ HECHA Y ES DEFINITIVA:\n"
            "No puedes cambiar la composición, reencuadrar la escena ni recolocar elementos.\n"
            'Trabaja siempre con la foto TAL CUAL existe. No propongas "un primer plano de X",\n'
            '"situar Y a la izquierda" ni ningún otro cambio de encuadre o composición.\n'
            '\nEl campo "tratamiento_imagen" debe indicar SOLO cómo usar o presentar la foto tal\n'
            "como es. Ejemplos válidos: recorte al formato 9:16 vertical o cuadrado, formato\n"
            "final (foto estática / Boomerang / vídeo con zoom suave), retoque sutil (filtro\n"
            "cálido, viñeteado, blanco y negro…) y cómo colocar el texto encima. NUNCA\n"
            "propongas encuadres alternativos ni cambios de composición."
        )

    # ── Bloque de historial — solo Modo B ──────────────────────────────────
    bloque_historial = ""
    if historial_conceptos:
        recientes = historial_conceptos[-MAX_HISTORIAL_EN_PROMPT:]
        items = "\n".join(f"  - {c}" for c in recientes)
        bloque_historial = (
            f"\nIDEAS YA PROPUESTAS ANTERIORMENTE (NO repitas ninguna ni propongas "
            f"ideas muy parecidas a estas):\n{items}"
        )

    # ── Bloque de ángulo — solo Modo B ─────────────────────────────────────
    bloque_angulo = ""
    if angulo:
        bloque_angulo = (
            f"\nÁNGULO TEMÁTICO PARA ESTA GENERACIÓN:\n"
            f"Enfoca las 3 ideas en torno a: {angulo}"
        )

    # ── Bloque de ideas previas — Modo A, A+ y opcionalmente C ─────────────
    # Etiqueta adaptada según si hay imagen (foto) o no (tema/sesión).
    bloque_previas = ""
    if ideas_previas:
        etiq = "IDEAS YA PROPUESTAS PARA ESTA FOTO" if hay_imagen else "IDEAS YA PROPUESTAS PARA ESTE TEMA"
        items = "\n".join(f"  - {i}" for i in ideas_previas)
        bloque_previas = (
            f"\n{etiq} (NO repitas ninguna ni propongas "
            f"ideas muy parecidas; busca enfoques claramente distintos):\n{items}"
        )

    # ── Instrucción de tarea — varía según el modo ─────────────────────────
    if hay_imagen and descripcion:
        # Modo A+: foto + tema → la foto es el vehículo, el tema el eje
        tarea = (
            f"Tu tarea es proponer exactamente 3 ideas de Story de Instagram usando "
            f"ESA imagen concreta para {cliente}, con el tema indicado como eje central."
        )
    elif hay_imagen:
        # Modo A: solo foto
        tarea = (
            f"Tu tarea es proponer exactamente 3 ideas de Story de Instagram usando "
            f"ESA imagen concreta para {cliente}."
        )
    elif descripcion:
        # Modo C: solo tema
        tarea = (
            f"Tu tarea es generar exactamente 3 ideas originales y creativas de Story "
            f"de Instagram para {cliente} centradas en el tema indicado."
        )
    else:
        # Modo B: lluvia libre con rotación de ángulos
        tarea = (
            f"Tu tarea es generar exactamente 3 ideas originales y creativas de Story "
            f"de Instagram para este cliente."
        )

    # ── Plantilla JSON: el campo visual cambia según si hay imagen o no ─────
    # Con imagen → "tratamiento_imagen" (cómo usar la foto tal cual existe)
    # Sin imagen → "tipo_contenido_sugerido" (qué tipo de pieza crear)
    if hay_imagen:
        nombre_campo  = "tratamiento_imagen"
        desc_campo_1  = (
            "Recorte (ej. 9:16 vertical), formato (foto estática / Boomerang / zoom suave), "
            "retoque (filtro cálido / b&n…) y posición del texto"
        )
    else:
        nombre_campo  = "tipo_contenido_sugerido"
        desc_campo_1  = "Foto / Vídeo / Reel / Boomerang / etc."

    # Construimos la plantilla como string normal para no mezclar {{ }} de f-string
    # con las llaves reales del JSON del ejemplo.
    json_template = (
        "[\n"
        "  {\n"
        '    "idea": 1,\n'
        '    "concepto": "Descripción breve de la idea central de la Story",\n'
        f'    "{nombre_campo}": "{desc_campo_1}",\n'
        '    "texto_en_pantalla": "El copy exacto que aparecería escrito en la Story",\n'
        '    "elemento_interactivo": "Encuesta / Pregunta / Cuenta atrás / Ninguno / etc.",\n'
        '    "por_que_funciona": "Explicación breve de por qué esta idea conecta con el público"\n'
        "  },\n"
        "  {\n"
        '    "idea": 2,\n'
        '    "concepto": "...",\n'
        f'    "{nombre_campo}": "...",\n'
        '    "texto_en_pantalla": "...",\n'
        '    "elemento_interactivo": "...",\n'
        '    "por_que_funciona": "..."\n'
        "  },\n"
        "  {\n"
        '    "idea": 3,\n'
        '    "concepto": "...",\n'
        f'    "{nombre_campo}": "...",\n'
        '    "texto_en_pantalla": "...",\n'
        '    "elemento_interactivo": "...",\n'
        '    "por_que_funciona": "..."\n'
        "  }\n"
        "]"
    )

    # ── Ensamblado final ────────────────────────────────────────────────────
    prompt = (
        "Eres una experta en crear Stories de Instagram para marcas y negocios.\n\n"
        "Trabaja para el siguiente cliente:\n\n"
        f"Cliente: {cliente}\n"
        f"Sector: {sector}\n"
        f"Tono de comunicación: {tono}\n"
        f"Público objetivo: {publico}"
        f"{bloque_valores}{bloque_evitar}{bloque_contenido}"
        f"{bloque_descripcion}{bloque_imagen}"
        f"{bloque_historial}{bloque_angulo}{bloque_previas}\n\n"
        f"{tarea}\n\n"
        "INSTRUCCIONES DE FORMATO:\n"
        "Responde ÚNICAMENTE con un JSON válido, sin ningún texto adicional antes ni después.\n"
        "El JSON debe ser una lista de 3 objetos con exactamente esta estructura:\n\n"
        + json_template
    )

    return prompt + _INSTRUCCION_JSON_COMILLAS


def generar_ideas_flexible(brand_kit, nivel_creatividad, ruta_imagen=None,
                            descripcion=None, ideas_previas=None):
    """
    Punto de entrada unificado para todos los modos de generación de ideas.
    Elige el modo automáticamente según los parámetros recibidos:

      Modo B  (Lluvia libre) : sin imagen, sin descripción.
              Rota ángulos temáticos y usa el historial del cliente internamente.
      Modo C  (Tema/evento)  : sin imagen, con descripción.
              Las 3 ideas giran alrededor del tema descrito por el usuario.
      Modo A  (Foto)         : con imagen, sin descripción.
              Ideas basadas en lo que aparece en la foto adjunta.
      Modo A+ (Foto + tema)  : con imagen y descripción.
              Ideas a partir de la foto, con el tema como eje central.

    Argumentos:
      brand_kit         (dict):      Ficha del cliente (Brand Kit).
      nivel_creatividad (str):       "seguras", "equilibradas" o "atrevidas".
      ruta_imagen       (str|None):  Ruta al archivo de imagen (JPG, PNG, GIF, WEBP).
      descripcion       (str|None):  Tema o evento a tratar (texto libre del usuario).
      ideas_previas     (list|None): Conceptos ya propuestos para evitar repeticiones.
                                     El llamador gestiona esta lista para A, A+ y C.
                                     En Modo B se ignora (el historial es interno).

    Devuelve:
      tuple[list, str|None]:
        - list:     Lista de 3 diccionarios con los campos de cada idea.
        - str|None: Ángulo temático usado (solo en Modo B; None en los demás modos).
    """

    # ── Validar nivel de creatividad ───────────────────────────────────────
    if nivel_creatividad not in NIVELES_CREATIVIDAD:
        print(f"  [aviso] Nivel '{nivel_creatividad}' no reconocido. Se usará 'equilibradas'.")
        nivel_creatividad = "equilibradas"
    temperatura = NIVELES_CREATIVIDAD[nivel_creatividad]

    hay_imagen = ruta_imagen is not None

    # ── Preparar imagen si la hay ──────────────────────────────────────────
    # Se lee aquí una vez y se codifica en base64 para el mensaje multimodal.
    datos_imagen = None   # tupla (datos_b64, media_type) o None
    if hay_imagen:
        if not os.path.exists(ruta_imagen):
            raise FileNotFoundError(
                f"No se encontró el archivo de imagen: '{ruta_imagen}'. "
                "Comprueba que la ruta es correcta y que el archivo existe."
            )
        extension  = os.path.splitext(ruta_imagen)[1].lower()
        media_type = _TIPOS_IMAGEN.get(extension)
        if not media_type:
            raise ValueError(
                f"Formato de imagen no soportado: '{extension}'. "
                f"Usa uno de: {', '.join(_TIPOS_IMAGEN.keys())}."
            )
        try:
            with open(ruta_imagen, "rb") as f:
                datos_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
        except OSError as e:
            raise ValueError(f"No se pudo leer el archivo de imagen: {e}")
        datos_imagen = (datos_b64, media_type)

    # ── Modo B: historial y ángulo gestionados internamente ────────────────
    # En los demás modos el llamador controla la anti-repetición vía ideas_previas.
    historial_para_prompt = None
    angulo_elegido        = None
    nombre_cliente        = brand_kit["cliente"]

    if not hay_imagen and not descripcion:
        historial_para_prompt = cargar_historial(nombre_cliente)
        angulo_elegido        = elegir_angulo(historial_para_prompt)

    # ── Construir el texto del prompt ──────────────────────────────────────
    texto_prompt = _construir_prompt_flexible(
        brand_kit,
        hay_imagen=hay_imagen,
        descripcion=descripcion,
        historial_conceptos=historial_para_prompt,
        angulo=angulo_elegido,
        ideas_previas=ideas_previas,
    )

    # ── Ensamblar el contenido del mensaje para la API ─────────────────────
    # Con imagen → lista de bloques [imagen, texto] (multimodal).
    # Sin imagen → string de texto directamente.
    if datos_imagen is not None:
        datos_b64, media_type = datos_imagen
        contenido_api = [
            {
                "type": "image",
                "source": {
                    "type":       "base64",
                    "media_type": media_type,
                    "data":       datos_b64,
                },
            },
            {"type": "text", "text": texto_prompt},
        ]
    else:
        contenido_api = texto_prompt

    # ── Llamar a la API y parsear la respuesta ────────────────────────────
    # _parsear_lista_json aplica repair_json si json.loads falla a la primera.
    texto_respuesta = _llamar_api(contenido_api, temperatura)
    ideas = _parsear_lista_json(texto_respuesta)

    # ── Guardar en historial (solo Modo B) ─────────────────────────────────
    # Los modos A, C y A+ no tocan el historial del cliente: su anti-repetición
    # es efímera (gestionada por el llamador vía ideas_previas).
    if not hay_imagen and not descripcion:
        guardar_en_historial(nombre_cliente, ideas)

    return ideas, angulo_elegido


# ══════════════════════════════════════════════════════════════════════════════
# Bloque de prueba: carga la ficha, construye el prompt, llama a la IA
# y muestra las 3 ideas de forma legible.
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ruta_ficha = "cliente_don_tomas.json"

    # 1. Cargar la ficha del cliente
    ficha = cargar_brand_kit(ruta_ficha)
    nombre_cliente = ficha["cliente"]

    # 2. Cargar historial y elegir ángulo antes de generar
    historial = cargar_historial(nombre_cliente)
    angulo    = elegir_angulo(historial)

    print(f"Generando ideas para '{nombre_cliente}' con nivel 'equilibradas'...")
    print(f"  Ángulo temático: {angulo}")
    print(f"  Conceptos en historial: {len(historial)}\n")

    # 3. Construir el prompt con historial y ángulo integrados
    prompt = construir_prompt(ficha, historial_conceptos=historial, angulo=angulo)

    # 4. Llamar a la IA y mostrar el resultado o el error de forma limpia
    try:
        ideas = generar_ideas(prompt, nivel_creatividad="equilibradas")

        # 5. Guardar las ideas nuevas en el historial del cliente
        guardar_en_historial(nombre_cliente, ideas)

        # 6. Mostrar los resultados de forma legible
        print("=" * 70)
        print(f"  3 IDEAS DE STORY PARA {nombre_cliente.upper()}")
        print(f"  Ángulo: {angulo}")
        print("=" * 70)

        etiquetas = {
            "concepto":                "Concepto",
            "tipo_contenido_sugerido": "Tipo de contenido",
            "texto_en_pantalla":       "Texto en pantalla",
            "elemento_interactivo":    "Elemento interactivo",
            "por_que_funciona":        "Por qué funciona",
        }

        for idea in ideas:
            print(f"\n>> IDEA {idea.get('idea', '?')}")
            print("-" * 50)
            for clave, etiqueta in etiquetas.items():
                valor = idea.get(clave, "—")
                print(f"  {etiqueta}: {valor}")

        print(f"\n  [historial actualizado: {len(historial) + len(ideas)} conceptos guardados]")

        # ── Demo de refinamiento: coger la idea 2 y aplicar un cambio ─────────
        idea_a_refinar = ideas[1]  # índice 1 = idea número 2
        peticion = "reescribe el texto en pantalla para que mencione explícitamente que es un plan de sábado"

        print(f"\nRefinando la idea {idea_a_refinar.get('idea', 2)} con la petición:")
        print(f"  \"{peticion}\"\n")

        idea_refinada = refinar_idea(idea_a_refinar, peticion, nivel_creatividad="equilibradas")

        # ── Mostrar antes y después para poder comparar ────────────────────
        print("=" * 70)
        print(f"  IDEA {idea_a_refinar.get('idea', 2)} — ANTES DEL REFINAMIENTO")
        print("=" * 70)
        for clave, etiqueta in etiquetas.items():
            valor = idea_a_refinar.get(clave, "—")
            print(f"  {etiqueta}: {valor}")

        print("\n" + "=" * 70)
        print(f"  IDEA {idea_refinada.get('idea', 2)} — DESPUÉS DEL REFINAMIENTO")
        print("=" * 70)
        for clave, etiqueta in etiquetas.items():
            valor = idea_refinada.get(clave, "—")
            print(f"  {etiqueta}: {valor}")

        print("\n" + "=" * 70)
        print("Listo.")

    except (ValueError, ConnectionError) as e:
        print(f"\n[ERROR] {e}")

    # ── Prueba del Modo A: establecer PROBAR_FOTO = True para ejecutarlo ──────
    # Coloca en la misma carpeta que este script un archivo llamado foto_prueba.jpg
    # (o el nombre que prefieras) y ajusta la variable ruta_foto si es necesario.
    if PROBAR_FOTO:
        ruta_foto = "foto_prueba.jpg"

        print("\n" + "=" * 70)
        print("  MODO A — GENERACIÓN DESDE FOTO")
        print("=" * 70)
        print(f"  Imagen: {ruta_foto}\n")

        try:
            ideas_foto = generar_ideas_desde_foto(
                ruta_foto, ficha, nivel_creatividad="equilibradas"
            )

            etiquetas_foto = {
                "concepto":                "Concepto",
                "tipo_contenido_sugerido": "Tipo de contenido",
                "texto_en_pantalla":       "Texto en pantalla",
                "elemento_interactivo":    "Elemento interactivo",
                "por_que_funciona":        "Por qué funciona",
            }

            for idea in ideas_foto:
                print(f"\n>> IDEA {idea.get('idea', '?')}")
                print("-" * 50)
                for clave, etiqueta in etiquetas_foto.items():
                    valor = idea.get(clave, "—")
                    print(f"  {etiqueta}: {valor}")

            print("\n" + "=" * 70)
            print("Listo (Modo A).")

        except (ValueError, ConnectionError, FileNotFoundError) as e:
            print(f"\n[ERROR Modo A] {e}")

    # ── Prueba de los cuatro modos flexibles: PROBAR_FLEXIBLE = True ──────────
    # Ejecuta Caso B, C, A y A+ en secuencia usando generar_ideas_flexible.
    # Coloca foto_prueba.jpg en la carpeta del proyecto para los casos con imagen.
    if PROBAR_FLEXIBLE:
        ruta_foto_flex = "foto_prueba.jpg"

        # Helper local para mostrar las ideas de un caso
        def _mostrar_caso(titulo, ideas, hay_imagen_caso):
            campo_visual = "tratamiento_imagen" if hay_imagen_caso else "tipo_contenido_sugerido"
            etiq_visual  = "Tratamiento imagen" if hay_imagen_caso else "Tipo de contenido"
            etiquetas_caso = {
                "concepto":      "Concepto",
                campo_visual:    etiq_visual,
                "texto_en_pantalla":    "Texto en pantalla",
                "elemento_interactivo": "Elemento interactivo",
                "por_que_funciona":     "Por qué funciona",
            }
            print("\n" + "=" * 70)
            print(f"  {titulo}")
            print("=" * 70)
            for idea in ideas:
                print(f"\n>> IDEA {idea.get('idea', '?')}")
                print("-" * 50)
                for clave, etiqueta in etiquetas_caso.items():
                    print(f"  {etiqueta}: {idea.get(clave, '—')}")
            print()

        # ── Caso B: sin foto, sin descripción ─────────────────────────────
        print("\n" + "█" * 70)
        print("  PRUEBA MODO FLEXIBLE — CASO B: lluvia libre")
        print("█" * 70)
        try:
            ideas_b, angulo_b = generar_ideas_flexible(
                ficha, nivel_creatividad="equilibradas"
            )
            _mostrar_caso(
                f"CASO B — Sin foto, sin descripción (ángulo: {angulo_b})",
                ideas_b, hay_imagen_caso=False
            )
        except (ValueError, ConnectionError, FileNotFoundError) as e:
            print(f"\n[ERROR Caso B] {e}")

        # ── Caso C: sin foto, con descripción ─────────────────────────────
        print("█" * 70)
        print("  PRUEBA MODO FLEXIBLE — CASO C: tema/evento")
        print("█" * 70)
        descripcion_c = "Anuncio del nuevo menú de Fallas que arranca el viernes 13 de marzo"
        try:
            ideas_c, _ = generar_ideas_flexible(
                ficha,
                nivel_creatividad="equilibradas",
                descripcion=descripcion_c,
            )
            _mostrar_caso(
                f"CASO C — Sin foto, descripción: «{descripcion_c}»",
                ideas_c, hay_imagen_caso=False
            )
        except (ValueError, ConnectionError, FileNotFoundError) as e:
            print(f"\n[ERROR Caso C] {e}")

        # ── Caso A: con foto, sin descripción ─────────────────────────────
        print("█" * 70)
        print("  PRUEBA MODO FLEXIBLE — CASO A: foto sin descripción")
        print("█" * 70)
        try:
            ideas_a, _ = generar_ideas_flexible(
                ficha,
                nivel_creatividad="equilibradas",
                ruta_imagen=ruta_foto_flex,
            )
            _mostrar_caso(
                f"CASO A — Foto: {ruta_foto_flex}, sin descripción",
                ideas_a, hay_imagen_caso=True
            )
        except (ValueError, ConnectionError, FileNotFoundError) as e:
            print(f"\n[ERROR Caso A] {e}")

        # ── Caso A+: con foto + descripción ───────────────────────────────
        print("█" * 70)
        print("  PRUEBA MODO FLEXIBLE — CASO A+: foto + descripción")
        print("█" * 70)
        descripcion_aplus = "Promoción del aperitivo del sábado"
        try:
            ideas_aplus, _ = generar_ideas_flexible(
                ficha,
                nivel_creatividad="equilibradas",
                ruta_imagen=ruta_foto_flex,
                descripcion=descripcion_aplus,
            )
            _mostrar_caso(
                f"CASO A+ — Foto: {ruta_foto_flex}, descripción: «{descripcion_aplus}»",
                ideas_aplus, hay_imagen_caso=True
            )
        except (ValueError, ConnectionError, FileNotFoundError) as e:
            print(f"\n[ERROR Caso A+] {e}")

        print("█" * 70)
        print("Listo (PROBAR_FLEXIBLE).")
