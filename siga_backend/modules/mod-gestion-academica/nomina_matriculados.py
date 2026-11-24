"""Lectura de nóminas históricas con encabezado institucional y una hoja por ciclo."""

import re
import unicodedata
from io import BytesIO
from pathlib import Path

import pandas as pd


def normalizar(texto) -> str:
    return " ".join("".join(
        c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c)
    ).upper().split())


_CICLOS = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}
_TITULO = re.compile(
    r"PROGRAMA DE (?:DE )?ESTUDIOS? DE (.+?)\s*[-–]\s*(I{1,3}|IV|V|VI) CICLO\s*[-–]\s*(20\d{2})\b"
)


def leer_nomina_matriculados(contenido: bytes, nombre: str) -> dict | None:
    """Devuelve None para los Excel de ingresantes de formato tabular."""
    with pd.ExcelFile(BytesIO(contenido)) as excel:
        encabezados = {
            hoja: pd.read_excel(excel, sheet_name=hoja, header=None, nrows=12).fillna("").values.tolist()
            for hoja in excel.sheet_names
        }
        if not any("ALUMNOS MATRICULADOS" in normalizar(celda)
                   for filas in encabezados.values() for fila in filas for celda in fila):
            return None
        periodo = re.search(r"\b(I|II|[12])\s+SEM(?:ESTRE)?\s+(20\d{2})\b", normalizar(Path(nombre).stem))
        if not periodo:
            raise ValueError("El nombre de la nómina debe indicar el semestre y año, por ejemplo «I SEM 2026».")
        semestre = {"I": 1, "II": 2, "1": 1, "2": 2}[periodo[1]]
        anio = periodo[2]
        registros, vistos = [], set()
        for hoja, filas in encabezados.items():
            titulo = next((normalizar(c) for fila in filas for c in fila if "ALUMNOS MATRICULADOS" in normalizar(c)), None)
            if titulo is None:
                if re.fullmatch(r"(?:I{1,3}|IV|V|VI) CICLO", normalizar(hoja)):
                    raise ValueError(f"La hoja «{hoja}» no tiene un encabezado de nómina válido.")
                continue
            identificado = _TITULO.search(titulo)
            if not identificado or identificado[3] != anio:
                raise ValueError(f"No se pudo verificar el programa, ciclo y año en la hoja «{hoja}».")
            programa, romano = identificado[1], identificado[2]
            ciclo = _CICLOS[romano]
            if normalizar(hoja) != f"{romano} CICLO":
                raise ValueError(f"El ciclo del encabezado no coincide con la hoja «{hoja}».")
            columnas = next(((i, fila) for i, fila in enumerate(filas)
                             if any(normalizar(c).replace(" ", "") == "DNI/CODMATRICULA" for c in fila)
                             and any(normalizar(c).startswith("APELLIDOS Y NOMBRES") for c in fila)), None)
            if columnas is None:
                raise ValueError(f"Faltan las columnas de DNI y nombres en «{hoja}».")
            inicio, encabezado = columnas
            dni_col = next(i for i, c in enumerate(encabezado) if normalizar(c).replace(" ", "") == "DNI/CODMATRICULA")
            nombre_col = next(i for i, c in enumerate(encabezado) if normalizar(c).startswith("APELLIDOS Y NOMBRES"))
            datos = pd.read_excel(excel, sheet_name=hoja, header=None, skiprows=inicio + 1, dtype=str).fillna("")
            for numero, fila in enumerate(datos.itertuples(index=False, name=None), start=inicio + 2):
                if not re.fullmatch(r"\d+(?:\.0)?", str(fila[0]).strip()):
                    continue
                dni = re.sub(r"\.0$", "", str(fila[dni_col]).strip())
                apellidos, separador, nombres = str(fila[nombre_col]).partition(",")
                if not re.fullmatch(r"[0-9]{1,8}", dni) or not (separador and apellidos.strip() and nombres.strip()):
                    raise ValueError(f"Revisa el DNI o los apellidos y nombres en «{hoja}», fila {numero}.")
                dni = dni.zfill(8)
                if dni in vistos:
                    raise ValueError(f"Hay un DNI repetido en «{hoja}», fila {numero}.")
                vistos.add(dni)
                registros.append({
                    "dni": dni, "nombres": nombres.strip(), "apellidos": apellidos.strip(),
                    "programa_nombre": programa, "ciclo": ciclo, "hoja": hoja, "fila": numero,
                })
        if not registros:
            raise ValueError("La nómina no contiene filas numeradas de estudiantes.")
        return {"anio": int(anio), "semestre": semestre, "codigo": f"{anio}-{semestre}", "registros": registros}
