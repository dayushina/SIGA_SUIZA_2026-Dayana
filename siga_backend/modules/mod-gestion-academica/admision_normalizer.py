"""Normaliza los datos que llegan de Admision (encabezados crudos del Excel).

La ingesta guarda la fila original en `metadata_admision` y, ademas, agrega las
claves normalizadas que usa el resto del sistema (las mismas que escribe el
registro manual del frontend). Tambien devuelve los valores para las columnas
propias de `estudiantes` que antes quedaban vacias.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Optional


def _key(k: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r":\s*$", "", str(k))).strip().upper()


def _clean(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" or s.lower() in {"none", "null", "nan"} else s


def parse_fecha(v: Any) -> Optional[date]:
    """'2008-05-06T00:00:00' | '2008-05-06' -> date(2008, 5, 6)."""
    s = _clean(v)
    m = s and re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalizar_admitido(raw_meta: Optional[Dict[str, Any]], fecha_nacimiento: Any = None) -> Dict[str, Any]:
    """Devuelve {"metadata": dict, "fecha_nacimiento": date|None, "direccion": str|None}."""
    meta = dict(raw_meta or {})
    r = {_key(k): v for k, v in meta.items()}
    get = lambda *keys: next((c for c in (_clean(r.get(k)) for k in keys) if c), None)  # noqa: E731

    sexo = get("SEXO")
    genero = {"M": "Masculino", "F": "Femenino"}.get((sexo or "").upper(), sexo)
    nac = get("NACIONALIDAD")
    if nac and nac.upper() in {"PERUANA", "PERUANO"}:
        nac = "Peruana"
    disc = (get("DISCAPACIDAD SI/NO") or "").upper()
    tipo_disc = get("TIPO DE DISCAPACIDAD")
    anio = get("AÑO QUE TERMINO SECUNDARIA")
    direccion = get("DOMICILIO")

    normalizado = {
        "genero": genero,
        "sexo": genero,
        "nacionalidad": nac,
        "lengua_materna": get("LENGUA MATERNA"),
        "departamento": get("DEPARTAMENTO"),
        "provincia": get("PROVINCIA"),
        "distrito": get("DISTRITO"),
        "direccion_residencia": direccion,
        "discapacidad": "SI" if disc == "SI" else ("NO" if disc == "NO" else None),
        "tipo_discapacidad": tipo_disc if tipo_disc and tipo_disc.upper() not in {"NINGUNA", "NINGUNO", "NO"} else None,
        "tipo_colegio": get("TIPO (ESTATAL O PARTICULAR)"),
        "anio_termino_secundaria": anio,
        "institucion_educativa": {
            "nombre": get("INSTITUCION EDUCATIVA"),
            "anio_egreso": int(anio) if anio and anio.isdigit() else None,
            "departamento": get("DEPARTAMENTO IE"),
            "provincia": get("PROVINCIA IE"),
            "distrito": get("DISTRITO IE"),
        },
        "apoderado": {
            "nombre": get("NOMBRE DEL APODERADO"),
            "celular": get("CELULAR (APODERADO)"),
            "dni": get("DNI (APODERADO)", "NRO. DNI (APODERADO)"),
        },
        "emergencia": {
            "contacto_nombre": get("NOMBRE DEL APODERADO"),
            "contacto_telefono": get("CELULAR (APODERADO)"),
        },
    }
    normalizado["ubigeo"] = {k: normalizado[k] for k in ("departamento", "provincia", "distrito")}
    # Las claves crudas del Excel se conservan; las normalizadas no pisan datos ya editados en SIGA.
    for k, v in normalizado.items():
        if isinstance(v, dict):
            v = {sk: sv for sk, sv in v.items() if sv is not None} or None
        if v is not None and meta.get(k) in (None, "", {}):
            meta[k] = v
    meta.setdefault("origen", "admision")

    return {
        "metadata": meta,
        "fecha_nacimiento": parse_fecha(fecha_nacimiento) or parse_fecha(r.get("FECHA NACIMIENTO")),
        "direccion": direccion,
    }
