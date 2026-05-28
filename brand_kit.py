import json
import os

# Campos que deben existir sí o sí en la ficha del cliente
CAMPOS_OBLIGATORIOS = ["cliente", "sector", "tono", "publico"]

# Campos que se usarán si están, pero cuya ausencia no bloquea el proceso
CAMPOS_OPCIONALES = ["valores", "evitar", "hashtags", "contenido_habitual"]


def cargar_brand_kit(ruta_archivo):
    """
    Carga la ficha de cliente (Brand Kit) desde un archivo JSON.

    Pasos que realiza:
      1. Comprueba que el archivo existe en la ruta indicada.
      2. Lee y parsea el contenido JSON del archivo.
      3. Verifica que están presentes todos los campos obligatorios.
      4. Avisa (sin error) si falta algún campo opcional o está vacío.
      5. Devuelve la ficha como diccionario de Python.

    Argumentos:
      ruta_archivo (str): Ruta al archivo .json con la ficha del cliente.

    Devuelve:
      dict: Diccionario con toda la información del Brand Kit.
    """

    # ── 1. Verificar que el archivo existe ─────────────────────────────────
    if not os.path.exists(ruta_archivo):
        raise FileNotFoundError(
            f"No se encontró el archivo de ficha de cliente: '{ruta_archivo}'. "
            "Comprueba que la ruta es correcta y que el archivo existe."
        )

    # ── 2. Leer el archivo y parsear el JSON ───────────────────────────────
    with open(ruta_archivo, "r", encoding="utf-8") as archivo:
        try:
            ficha = json.load(archivo)
        except json.JSONDecodeError as error_json:
            raise ValueError(
                f"La ficha '{ruta_archivo}' tiene un formato JSON incorrecto y no se pudo leer. "
                f"Detalle del error: {error_json}"
            )

    # ── 3. Comprobar campos obligatorios ───────────────────────────────────
    campos_faltantes = [
        campo for campo in CAMPOS_OBLIGATORIOS if campo not in ficha
    ]

    if campos_faltantes:
        lista_faltantes = ", ".join(f'"{c}"' for c in campos_faltantes)
        raise KeyError(
            f"La ficha '{ruta_archivo}' está incompleta. "
            f"Faltan los siguientes campos obligatorios: {lista_faltantes}."
        )

    # ── 4. Avisar de campos opcionales ausentes o vacíos ──────────────────
    for campo in CAMPOS_OPCIONALES:
        valor = ficha.get(campo)  # None si no existe la clave

        # Consideramos "vacío" tanto None como listas vacías y strings vacíos
        campo_vacio = valor is None or valor == [] or valor == ""

        if campo_vacio:
            print(f"  [aviso] El campo opcional '{campo}' no está definido o está vacío. "
                  "Se cargará la ficha igualmente.")

    # ── 5. Devolver la ficha completa ──────────────────────────────────────
    return ficha


# ══════════════════════════════════════════════════════════════════════════════
# Bloque de prueba: se ejecuta solo al lanzar este archivo directamente
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    ruta_prueba = "cliente_don_tomas.json"

    print(f"Cargando ficha de cliente desde: {ruta_prueba}\n")

    ficha_cargada = cargar_brand_kit(ruta_prueba)

    print("Ficha cargada correctamente. Contenido:\n")
    for clave, valor in ficha_cargada.items():
        print(f"  {clave}:")
        if isinstance(valor, list):
            for elemento in valor:
                print(f"    - {elemento}")
        else:
            # Truncar líneas largas para que la salida sea legible en terminal
            texto = str(valor)
            if len(texto) > 120:
                texto = texto[:117] + "..."
            print(f"    {texto}")
    print("\nBrand Kit listo para usar.")
