"""Lectura del Excel de resultados de Admision (plantilla ADMISION_2026.xlsx).

Funciones puras (sin BD) para poder probarlas aisladamente:
  - parse_excel(contents)      -> filas listas para PostulanteAdmitido + avisos
  - resolver_programa_id(nombre) -> id oficial de la carrera (o None)

Hojas leidas: 'B_Datos' (postulantes ordinarios) y 'B_Datos_Exonerados'.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import unicodedata
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

HOJA_ORDINARIOS = "b_datos"
HOJA_EXONERADOS = "b_datos_exonerados"

# Catalogo oficial: id -> nombre. Debe coincidir con
# siga_frontend/src/core/constants/programas.js y scripts/unificar_programas.py.
PROGRAMAS_OFICIALES: Dict[int, str] = {
    1: "DESARROLLO DE SISTEMAS DE INFORMACION",
    2: "ENFERMERIA TECNICA",
    4: "GESTION ADMINISTRATIVA",
    5: "CONTABILIDAD",
    6: "MECATRONICA AUTOMOTRIZ",
    7: "ELECTRICIDAD INDUSTRIAL",
    8: "ADMINISTRACION DE OPERACIONES TURISTICAS",
    9: "CONSTRUCCION CIVIL",
    10: "PRODUCCION AGROPECUARIA",
    11: "MANEJO FORESTAL",
}
# Nombres alternativos que aparecen en el Excel.
ALIAS_PROGRAMAS: Dict[str, int] = {
    "ASISTENCIA ADMINISTRATIVA": 4,
}


def normalizar_texto(texto: Any) -> str:
    """Mayusculas, sin tildes y con espacios simples ('Enfermería  Técnica' -> 'ENFERMERIA TECNICA')."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.upper().split())


_NOMBRE_A_ID = {**{v: k for k, v in PROGRAMAS_OFICIALES.items()}, **ALIAS_PROGRAMAS}


def resolver_programa_id(nombre: Any) -> Optional[int]:
    """Id oficial de la carrera a partir del texto del Excel. None si no se reconoce."""
    n = normalizar_texto(nombre)
    if not n or n in {"NAN", "NONE", "NULL"}:
        return None
    if n in _NOMBRE_A_ID:
        return _NOMBRE_A_ID[n]
    # Coincidencia parcial ("PE DESARROLLO DE SISTEMAS DE INFORMACION 2026"), la clave mas larga primero.
    for clave in sorted(_NOMBRE_A_ID, key=len, reverse=True):
        if clave in n:
            return _NOMBRE_A_ID[clave]
    return None


def _valor_json(v: Any) -> Any:
    """Convierte una celda de pandas a un valor serializable en JSON."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (pd.Timestamp, dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if hasattr(v, "item"):  # numpy int64/float64/bool_
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _fila_limpia(row: pd.Series) -> Dict[str, Any]:
    return {str(k): _valor_json(v) for k, v in row.items()}


def _numero_como_texto(v: Any) -> str:
    """32165487.0 -> '32165487'; respeta textos ('0123456')."""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def limpiar_dni(v: Any, tipo_documento: Any = None) -> str:
    dni = re.sub(r"\s+", "", _numero_como_texto(v))
    # Excel guarda el DNI como numero y pierde los ceros iniciales (01234567 -> 1234567).
    es_dni = not tipo_documento or "DNI" in normalizar_texto(tipo_documento)
    if es_dni and dni.isdigit() and len(dni) < 8:
        dni = dni.zfill(8)
    return dni[:15]


def _texto(v: Any, largo: int) -> Optional[str]:
    t = _numero_como_texto(v)
    return t[:largo] if t else None


def _fecha(v: Any) -> Optional[str]:
    if not v:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(v))
    return m.group(1) if m else str(v)[:20]


def _buscar_hoja(nombres: List[str], objetivo: str) -> Optional[str]:
    for n in nombres:
        if n.lower().strip() == objetivo:
            return n
    return None


def parse_excel(contents: bytes) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Devuelve (filas, avisos). Cada fila tiene las columnas de PostulanteAdmitido.

    Lanza ValueError si el archivo no es la plantilla de Admision.
    """
    try:
        excel = pd.ExcelFile(BytesIO(contents))
    except Exception as exc:  # archivo corrupto o no-xlsx
        raise ValueError("El archivo no es un Excel valido (.xlsx).") from exc

    hoja_ord = _buscar_hoja(excel.sheet_names, HOJA_ORDINARIOS)
    if hoja_ord is None:
        raise ValueError(
            f"El Excel debe contener la hoja 'B_Datos'. Hojas encontradas: {excel.sheet_names}"
        )
    hojas = [(hoja_ord, "ORDINARIO")]
    hoja_exo = _buscar_hoja(excel.sheet_names, HOJA_EXONERADOS)
    if hoja_exo:
        hojas.append((hoja_exo, None))  # la modalidad viene en la columna MODALIDAD

    filas: List[Dict[str, Any]] = []
    avisos: List[str] = []
    vistos = set()

    for hoja, modalidad_fija in hojas:
        df = pd.read_excel(excel, sheet_name=hoja)
        df.columns = [str(c) for c in df.columns]
        if "NRO DE DOCUMENTO" not in df.columns:
            raise ValueError(f"La hoja '{hoja}' no tiene la columna 'NRO DE DOCUMENTO'.")
        col_programa = next((c for c in df.columns if normalizar_texto(c) == "PROGRAMA DE ESTUDIOS"), None)
        if col_programa is None:
            raise ValueError(f"La hoja '{hoja}' no tiene la columna 'PROGRAMA DE ESTUDIOS'.")

        for idx, row in df.iterrows():
            fila_excel = idx + 2  # +1 encabezado, +1 base 1
            raw = _fila_limpia(row)
            dni = limpiar_dni(raw.get("NRO DE DOCUMENTO"), raw.get("TIPO DE DOCUMENTO"))
            if not dni:
                continue
            if dni in vistos:
                avisos.append(f"{hoja} fila {fila_excel}: DNI {dni} repetido, se omite.")
                continue
            vistos.add(dni)

            nombres = _texto(raw.get("NOMBRES"), 100)
            paterno = _numero_como_texto(raw.get("APELLIDO PATERNO"))
            materno = _numero_como_texto(raw.get("APELLIDO MATERNO"))
            apellidos = " ".join(p for p in (paterno, materno) if p)[:100]
            if not nombres or not apellidos:
                avisos.append(f"{hoja} fila {fila_excel}: DNI {dni} sin nombres/apellidos, se omite.")
                continue

            programa = _numero_como_texto(raw.get(col_programa))
            if resolver_programa_id(programa) is None:
                avisos.append(f"{hoja} fila {fila_excel}: DNI {dni} con carrera no reconocida '{programa}'.")

            modalidad = modalidad_fija or _numero_como_texto(raw.get("MODALIDAD")) or "EXONERADO"
            filas.append({
                "dni": dni,
                "nombres": nombres,
                "apellidos": apellidos,
                "programa_nombre": programa[:150],
                "modalidad_admision": modalidad[:100],
                "email": _texto(raw.get("CORREO:"), 150),
                "celular": _texto(raw.get("CELULAR"), 20),
                "fecha_nacimiento": _fecha(raw.get("FECHA NACIMIENTO:") or raw.get("FECHA NACIMIENTO: ")),
                "metadata_admision": raw,
            })

    return filas, avisos
