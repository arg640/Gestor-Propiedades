import pymupdf as fitz
import json
import os
import streamlit as st
from groq import Groq

def extraer_datos_contrato(ruta_pdf: str) -> dict:
    """
    Extrae datos de un contrato usando la API de Groq (Llama 3).
    Si la API falla o no está configurada, devuelve un diccionario vacío
    para que el usuario lo llene manualmente.
    """
    # 1. Extraer el texto del PDF (las primeras 3 páginas suelen ser suficientes)
    texto_completo = ""
    try:
        doc = fitz.open(ruta_pdf)
        for i in range(min(3, len(doc))):  # Leer máximo 3 páginas para ahorrar tokens
            texto_completo += doc[i].get_text()
        doc.close()
    except Exception as e:
        print(f"Error al leer PDF: {e}")
        return {}

    # 2. Configurar el cliente de Groq
    # Busca la llave primero en secrets de Streamlit, luego en variables de entorno
    try:
        groq_api_key = st.secrets["GROQ_API_KEY"]
    except Exception:
        groq_api_key = os.environ.get("GROQ_API_KEY", "")

    if not groq_api_key:
        print("⚠️ No se encontró GROQ_API_KEY. Configúrala en los Secrets.")
        return {} # Devuelve vacío si no hay llave

    cliente = Groq(api_key=groq_api_key)

    # 3. El Prompt "Mágico" para el LLM
    prompt_sistema = """
    Eres un asistente experto en análisis de contratos de arrendamiento en México.
    Tu tarea es leer el texto de un contrato y extraer EXCLUSIVAMENTE los siguientes datos en formato JSON.
    No agregues texto adicional, saludos ni explicaciones. Solo el JSON puro.

    Estructura esperada:
    {
      "inquilino": "Nombre completo de quien renta (el Arrendatario).",
      "dueno": "Nombre completo del dueño (el Arrendador).",
      "finca": "Dirección completa de la propiedad que se está rentando.",
      "monto_renta": "Solo el número (ej. 4500). Sin signos de pesos ni comas.",
      "dia_pago_mensual": "Día del mes en que se debe pagar (ej. 5).",
      "fecha_fin": "Fecha en que termina o vence el contrato en formato YYYY-MM-DD. (ej. 2026-08-31)"
    }

    Reglas:
    - Si no encuentras un dato, pon la cadena "NO ENCONTRADO" (o 0 en los números).
    - Para la fecha_fin, si dice '31 de Agosto de 2026', conviértelo a '2026-08-31'.
    """

    # 4. Llamada a la API
    try:
        respuesta = cliente.chat.completions.create(
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": f"Extrae los datos de este contrato:\n\n{texto_completo}"}
            ],
            model="llama-3.3-70b-versatile", # Modelo gratuito y súper rápido
            temperature=0.1, # Muy baja temperatura para que sea preciso y no invente
            response_format={"type": "json_object"} # Obliga a devolver JSON
        )
        
        # 5. Procesar la respuesta
        contenido = respuesta.choices[0].message.content
        datos_json = json.loads(contenido)
        
        # Pequeña limpieza de seguridad
        return {
            "inquilino": datos_json.get("inquilino", "").strip().title(),
            "dueno": datos_json.get("dueno", "").strip().title(),
            "finca": datos_json.get("finca", "").strip(),
            "monto_renta": float(datos_json.get("monto_renta", 0)),
            "dia_pago_mensual": int(datos_json.get("dia_pago_mensual", 1)),
            "fecha_fin": datos_json.get("fecha_fin", "2025-01-01")
        }

    except Exception as e:
        print(f"Error en la API de Groq: {e}")
        return {} # Fallback: diccionario vacío para que el usuario capture a mano
