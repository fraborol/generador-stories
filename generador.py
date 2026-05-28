import os
import sys
import json
import re
import unicodedata

import anthropic
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

    return prompt


def _llamar_api(prompt, temperatura):
    """
    Función auxiliar privada: envía un prompt a la API de Anthropic y devuelve
    el texto crudo de la respuesta. Centraliza la autenticación y el manejo de
    errores para que generar_ideas y refinar_idea no dupliquen ese código.

    Argumentos:
      prompt      (str):   El texto del prompt a enviar.
      temperatura (float): Valor entre 0.0 y 1.0 que controla la creatividad.

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

    # ── Llamar a la API y extraer el JSON de la respuesta ─────────────────
    # La IA a veces añade texto introductorio antes del JSON.
    # Buscamos el primer '[' y el último ']' para extraer solo la lista.
    texto_respuesta = _llamar_api(prompt, temperatura)

    inicio = texto_respuesta.find("[")
    fin    = texto_respuesta.rfind("]")

    if inicio == -1 or fin == -1:
        raise ValueError(
            "La respuesta de la IA no contiene un JSON válido. "
            f"Respuesta recibida:\n{texto_respuesta}"
        )

    fragmento_json = texto_respuesta[inicio: fin + 1]

    try:
        ideas = json.loads(fragmento_json)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"La respuesta de la IA contiene JSON mal formado y no se pudo parsear. "
            f"Detalle: {e}\nFragmento recibido:\n{fragmento_json}"
        )

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

    return prompt


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
    # A diferencia de generar_ideas, aquí esperamos un objeto { }, no una lista [ ]
    inicio = texto_respuesta.find("{")
    fin    = texto_respuesta.rfind("}")

    if inicio == -1 or fin == -1:
        raise ValueError(
            "La respuesta de la IA no contiene un JSON válido. "
            f"Respuesta recibida:\n{texto_respuesta}"
        )

    fragmento_json = texto_respuesta[inicio: fin + 1]

    try:
        idea_revisada = json.loads(fragmento_json)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"La respuesta de la IA contiene JSON mal formado y no se pudo parsear. "
            f"Detalle: {e}\nFragmento recibido:\n{fragmento_json}"
        )

    return idea_revisada


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
