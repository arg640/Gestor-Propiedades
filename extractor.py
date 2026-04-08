import pymupdf as fitz
import re

# Spanish-accented chars used in word-class patterns
_SP = r"ÁÉÍÓÚáéíóúÑñÜü"


def _norm(s: str) -> str:
    return " ".join(s.split()) if s else s


def _primera_clausula(texto: str) -> str:
    """Return the text of the PRIMERA clause (up to SEGUNDA) for address search."""
    m1 = re.search(r"\bPRIMERA\b", texto, re.IGNORECASE)
    m2 = re.search(r"\bSEGUNDA\b", texto, re.IGNORECASE)
    if m1:
        start = m1.start()
        end   = m2.start() if (m2 and m2.start() > start) else start + 900
        return texto[start:end]
    return texto[:900]


def extraer_datos_contrato(ruta_pdf: str) -> dict:
    """
    Extrae datos de un contrato de arrendamiento en PDF.
    Compatible con tres formatos de contrato mexicano:
      • Formato A (Marisol)  : 'LA CIUDADANA X, A QUIEN EN LO SUCESIVO'
      • Formato B (Guillermo): 'EL SR. X, A QUIEN EN LO SUCESIVO SE LE DENOMINARA'
      • Formato C (Sara)     : 'LA SRA./SEÑORITA X A QUIEN EN LO SUCESIVO SE LE DENOMINARA'
    """
    doc = fitz.open(ruta_pdf)
    texto = ""
    for pag in doc:
        texto += pag.get_text()
    doc.close()

    print("=" * 60)
    print("TEXTO EXTRAÍDO DEL PDF:")
    print("=" * 60)
    print(texto)
    print("=" * 60)

    resultado = {
        "nombre_inquilino":  None,
        "nombre_arrendador": None,
        "monto_renta":       None,
        "num_finca":         None,
        "direccion_coto":    None,
        "dia_pago_mensual":  1,
        "fecha_vencimiento": None,
    }

    # Reusable sub-patterns
    TITULO = (
        r"(?:EL\s+SR\.|LA\s+SRA\.|EL\s+SE[ÑN]OR|LA\s+SE[ÑN]ORITA"
        r"|LA\s+CIUDADANA|EL\s+CIUDADANO)\s+"
    )
    # A "name" is 2–6 capitalized words (allows Spanish accents)
    NOMBRE = fr"([A-Z{_SP}][A-Za-z{_SP}]+(?:\s+[A-Za-z{_SP}]+){{1,5}})"

    # ── 1. ARRENDADOR (dueño) ──────────────────────────────────────────────
    # Pattern: any title + NAME + optional comma + "A QUIEN EN LO SUCESIVO"
    m = re.search(
        TITULO + NOMBRE + r"\s*,?\s*A\s+QUIEN\s+EN\s+LO\s+SUCESIVO",
        texto, re.IGNORECASE,
    )
    if m:
        resultado["nombre_arrendador"] = _norm(m.group(1))
    else:
        # Fallback: signature block "NAME\nLA ARRENDADORA"
        m = re.search(
            fr"([A-Z{_SP}][A-Za-z{_SP}\s]{{6,50}})\s*\n\s*[\u201c\"]?(?:LA\s+ARRENDADORA|EL\s+ARRENDADOR)[\u201d\"]?",
            texto, re.IGNORECASE,
        )
        if m:
            resultado["nombre_arrendador"] = _norm(m.group(1))

    # ── 2. ARRENDATARIO/A (inquilino) ──────────────────────────────────────
    # Format B/C: "POR OTRA PARTE [TITULO] NAME A QUIEN EN LO SUCESIVO SE LE DENOMINARA EL/LA ARRENDAT"
    m = re.search(
        r"POR\s+(?:LA\s+)?OTRA\s+PARTE\s+" + TITULO + r"?" + NOMBRE
        + r"\s+A\s+QUIEN\s+EN\s+LO\s+SUCESIVO\s+SE\s+LE\s+DENOMINARA"
        + r"\s+[\u201c\"]?(?:EL|LA)\s+ARRENDAT",
        texto, re.IGNORECASE,
    )
    if m:
        resultado["nombre_inquilino"] = _norm(m.group(1))

    if not resultado["nombre_inquilino"]:
        # Format A (Marisol): "POR LA OTRA PARTE LA CIUDADANA NAME [Y NAME] EN LO SUCESIVO «LA ARRENDATARIA»"
        m = re.search(
            r"POR\s+LA\s+OTRA\s+PARTE\s+(?:LA\s+CIUDADANA|EL\s+CIUDADANO|LA\s+SE[ÑN]ORITA)\s+"
            + NOMBRE
            + r"(?:\s+Y\s+[A-Z{_SP}][A-Za-z{_SP}\s]+?)?"
            + r"\s+EN\s+LO\s+SUCESIVO\s+[\u201c\"]?(?:LA|EL)\s+ARRENDAT",
            texto, re.IGNORECASE,
        )
        if m:
            resultado["nombre_inquilino"] = _norm(m.group(1))

    if not resultado["nombre_inquilino"]:
        # Fallback: "Declara [el Sr./la Sra.] NAME ser mexicano/a / que es"
        m = re.search(
            r"Declara\s+(?:el\s+Sr\.|la\s+Sra\.|la\s+Se[ñn]orita)?\s*"
            + NOMBRE
            + r"\s+(?:ser\s+(?:mexicana?|casad[oa])|que\s+es)",
            texto, re.IGNORECASE,
        )
        if m:
            resultado["nombre_inquilino"] = _norm(m.group(1))

    if not resultado["nombre_inquilino"]:
        # Last resort: signature block
        sigs = re.findall(
            fr"([A-Z{_SP}][A-Za-z{_SP}\s]{{6,60}})\s*\n\s*[\u201c\"]?(?:LA|EL)\s+ARRENDAT[\u201d\"]?",
            texto, re.IGNORECASE,
        )
        if sigs:
            resultado["nombre_inquilino"] = " / ".join(_norm(n) for n in sigs)

    # ── 3. DIRECCIÓN Y NÚMERO DE FINCA ────────────────────────────────────
    # Search inside the PRIMERA clause to avoid picking up the arrendador's
    # personal address or other "finca" mentions.
    primera = _primera_clausula(texto)

    # Pattern A (new): "finca marcada/identifica con el número NNN [interior XX] <street>"
    m = re.search(
        r"finca\s+(?:marcada\s+con|que\s+se\s+identifica\s+con|identificada\s+con)\s+el\s+n[uú]mero\s+"
        r"([\d]+(?:\s+(?:interior|int\.?|depto\.?)\s+[\w\d]+)?)"   # num [+ interior/depto]
        r"\s+([\w\s\',\.\-áéíóúÁÉÍÓÚñÑ]+?)"                        # street / name
        r"(?=\s*,?\s*en\s+la\s+[Cc]olonia|,\s+[Cc]ol\."
        r"|\s+en\s+la\s+[Cc]ol\.|\s+en\s+el\s+[Mm]unicipio"
        r"|\.?\s*y\s+[\u201c\"]?(?:EL|LA)\s+ARRENDAT"
        r"|\.?\s*CP\s+\d|Quien\s+lo\s+recibe)",
        primera, re.IGNORECASE,
    )
    if m:
        num_raw  = m.group(1).strip()
        calle    = m.group(2).strip().rstrip(",")
        num_dig  = re.match(r"(\d+)", num_raw)
        resultado["num_finca"] = num_dig.group(1) if num_dig else num_raw

        # Grab colonia and city from the text right after the match
        after    = primera[m.end():]
        col_m    = re.search(
            r"[Cc]olonia\s+([\w\s]+?)(?=,|\.|\s+en\s+(?:el\s+[Mm]unicipio|Zapopan|Guadalajara|San\s+Pedro))",
            after[:350], re.IGNORECASE,
        )
        city_m   = re.search(
            r"(?:en\s+el\s+[Mm]unicipio\s+de|en)\s+(Zapopan|Guadalajara|San\s+Pedro|Tlaquepaque)",
            after[:350], re.IGNORECASE,
        )
        partes   = [calle]
        if col_m:  partes.append(f"Col. {_norm(col_m.group(1))}")
        if city_m: partes.append(f"{_norm(city_m.group(1))}, Jalisco")
        resultado["direccion_coto"] = ", ".join(p for p in partes if p)

    else:
        # Pattern B (Marisol): "ubicado en: CALLE X, NÚMERO EXTERIOR NNN, COLONIA Y"
        m = re.search(
            r"(?:ubicad[oa]\s+en|inmueble\s+identificado\s+y\s+ubicado\s+en)\s*:?\s*"
            r"((?:CALLE|AVENIDA|AV\.?|BOULEVARD|PRIVADA|CALZADA|CERRADA)"
            fr"[\w\s,\.#°{_SP}]+?)"
            r"(?:Quien|\.(?:\s|$)|SEGUNDA)",
            primera, re.IGNORECASE | re.DOTALL,
        )
        if not m:
            # Try full text as last resort
            m = re.search(
                r"(?:ubicad[oa]\s+en|inmueble\s+identificado\s+y\s+ubicado\s+en)\s*:?\s*"
                r"((?:CALLE|AVENIDA|AV\.?|BOULEVARD|PRIVADA|CALZADA|CERRADA)"
                fr"[\w\s,\.#°{_SP}]+?)"
                r"(?:Quien|\.(?:\s|$)|SEGUNDA)",
                texto, re.IGNORECASE | re.DOTALL,
            )
        if m:
            addr_raw = _norm(m.group(1))
            num_ext  = re.search(r"N[ÚU]MERO\s+EXTERIOR\s+([\w\-]+)", addr_raw, re.IGNORECASE)
            if num_ext:
                resultado["num_finca"] = num_ext.group(1)
                addr_clean = re.sub(
                    r",?\s*N[ÚU]MERO\s+EXTERIOR\s+[\w\-]+,?", "",
                    addr_raw, flags=re.IGNORECASE,
                ).strip().strip(",").strip()
                resultado["direccion_coto"] = addr_clean
            else:
                n = re.search(r"\b(\d+)\b", addr_raw)
                if n:
                    resultado["num_finca"] = n.group(1)
                resultado["direccion_coto"] = addr_raw

    # ── 4. DÍA DE PAGO MENSUAL ────────────────────────────────────────────
    resultado["dia_pago_mensual"] = 1  # default (≤5 days = day 1)

    # "primeros cinco días siguientes del 23 de cada mes" → 23
    m = re.search(r"siguientes\s+del\s+(\d{1,2})\s+de\s+cada\s+mes", texto, re.IGNORECASE)
    if m:
        resultado["dia_pago_mensual"] = int(m.group(1))
    else:
        # "pagarse los días 15 de cada mes" or "días 1 primero"
        m = re.search(r"pagarse?\s+(?:los?\s+)?d[ií]as?\s+(\d{1,2})", texto, re.IGNORECASE)
        if m:
            resultado["dia_pago_mensual"] = int(m.group(1))
        else:
            # "dentro de los primeros N días" (no specific day) → day 1 default kept
            pass

    # ── 5. MONTO DE RENTA ─────────────────────────────────────────────────
    MONTO_PATRONES = [
        r"precio\s+de\s+la\s+renta\s+la\s+cantidad\s+de\s+\$\s*([\d,]+\.?\d*)",   # Guillermo/Sara
        r"renta\s+mensual\s+la\s+cantidad\s+de\s+\$\s*([\d,]+\.?\d*)",             # Marisol
        r"fijan.*?(?:como\s+)?precio.*?\$\s*([\d,]+\.?\d*)",
        r"por\s+concepto\s+de\s+renta.*?\$\s*([\d,]+\.?\d*)",
        r"cantidad\s+de\s+\$\s*([\d,]+\.?\d*)\s*\(",
    ]
    for p in MONTO_PATRONES:
        m = re.search(p, texto, re.IGNORECASE | re.DOTALL)
        if m:
            resultado["monto_renta"] = f"${m.group(1).strip()} MXN"
            break

    # Fallback: first $ amount near the word "renta"
    if not resultado["monto_renta"]:
        idx = texto.upper().find("RENTA")
        if idx != -1:
            frag = texto[idx: idx + 400]
            m = re.search(r"\$([\d,]+\.?\d*)", frag)
            if m:
                resultado["monto_renta"] = f"${m.group(1).strip()} MXN"

    # ── 6. FECHA DE VENCIMIENTO ───────────────────────────────────────────
    MESES = (
        r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto"
        r"|septiembre|octubre|noviembre|diciembre)"
    )
    # Compact Spanish date: "22 de Septiembre del 2026" or "30 de Abril del 2026"
    FECHA_ESP  = fr"(\d{{1,2}}°?\s+(?:de\s+)?{MESES}\s+del?\s+\d{{4}})"
    # Long date: "31 TREINTA Y UNO DE AGOSTO DEL AÑO 2026"
    FECHA_LRGA = fr"(\d+\s+[\w\s]+?(?:DEL?\s+A[ÑN]O)\s+\d{{4}})"

    FECHA_PATRONES = [
        rf"concluye(?:ndo)?[^.\n]{{0,60}}el\s+d[ií]a\s+{FECHA_ESP}",    # "concluyendo el día 22 de Sept 2026"
        rf"concluye[^.\n]{{0,60}}el\s+d[ií]a\s+{FECHA_LRGA}",            # Marisol long format
        rf"\bal\s+{FECHA_ESP}",                                            # "al 30 de Abril del 2026"
        rf"hasta\s+el\s+{FECHA_ESP}",
        rf"termina[^.\n]{{0,60}}el\s+d[ií]a\s+{FECHA_ESP}",
        rf"vence[^.\n]{{0,60}}el\s+d[ií]a\s+{FECHA_ESP}",
    ]
    for p in FECHA_PATRONES:
        m = re.search(p, texto, re.IGNORECASE | re.DOTALL)
        if m:
            resultado["fecha_vencimiento"] = _norm(m.group(1))
            break

    if not resultado["fecha_vencimiento"]:
        m = re.search(
            r"(?:concluye|termina|vence)[^.\n]{0,100}?(\d{1,2}/\d{1,2}/\d{4})",
            texto, re.IGNORECASE | re.DOTALL,
        )
        if m:
            resultado["fecha_vencimiento"] = m.group(1)

    # ── LOG ───────────────────────────────────────────────────────────────
    print("DATOS EXTRAÍDOS:")
    for k, v in resultado.items():
        print(f"  {k:<22}: {v}")
    print("=" * 60)

    return resultado
