"""Lectura del Excel de Admision (mod-admision/admision_excel.py). Sin BD."""
import json
import sys
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules" / "mod-admision"))
from admision_excel import limpiar_dni, parse_excel, resolver_programa_id  # noqa: E402

COL_PROG = "PROGRAMA DE   ESTUDIOS"  # el Excel real trae 3 espacios


def _excel(ordinarios, exonerados=None, hoja="B_Datos"):
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(ordinarios).to_excel(w, sheet_name=hoja, index=False)
        if exonerados is not None:
            pd.DataFrame(exonerados).to_excel(w, sheet_name="B_Datos_Exonerados", index=False)
    return buf.getvalue()


def _fila(dni, programa="ENFERMERÍA TÉCNICA", **extra):
    return {COL_PROG: programa, "APELLIDO PATERNO": "PEREZ", "APELLIDO MATERNO": "LOPEZ",
            "NOMBRES": "ANA", "TIPO DE DOCUMENTO": "DNI", "NRO DE DOCUMENTO": dni,
            "CELULAR": 987654321.0, "FECHA NACIMIENTO: ": pd.Timestamp("2005-03-02"),
            "CORREO:": "ana@mail.com", **extra}


@pytest.mark.parametrize("nombre,esperado", [
    ("DESARROLLO DE SISTEMAS DE INFORMACIÓN", 1),
    ("Enfermería  Técnica", 2),
    ("GESTIÓN ADMINISTRATIVA", 4),
    ("ASISTENCIA ADMINISTRATIVA", 4),
    ("ADMINISTRACIÓN DE OPERACIONES TURÍSTICAS", 8),
    ("MANEJO FORESTAL", 11),
    ("", None),
    (None, None),
    ("nan", None),
    ("ODONTOLOGIA", None),
])
def test_resolver_programa_id(nombre, esperado):
    assert resolver_programa_id(nombre) == esperado


def test_limpiar_dni_recupera_ceros_solo_en_dni():
    assert limpiar_dni(123739.0, "DNI") == "00123739"
    assert limpiar_dni(32165487.0, "DNI") == "32165487"
    assert limpiar_dni(6293672.0, "CARNET DE EXTRANJERIA") == "6293672"
    assert limpiar_dni(None) == ""


def test_parse_excel_ordinarios_y_exonerados_serializables():
    import numpy as np
    contenido = _excel(
        [_fila(32165487.0), _fila(123739.0, "GESTIÓN ADMINISTRATIVA"), _fila(None)],
        [_fila(np.int64(76296746), "CONTABILIDAD", MODALIDAD="TITULADO")],
    )
    filas, avisos = parse_excel(contenido)

    assert [f["dni"] for f in filas] == ["32165487", "00123739", "76296746"]
    assert filas[0]["modalidad_admision"] == "ORDINARIO"
    assert filas[2]["modalidad_admision"] == "TITULADO"
    assert filas[0]["apellidos"] == "PEREZ LOPEZ"
    assert filas[0]["fecha_nacimiento"] == "2005-03-02"
    assert filas[0]["celular"] == "987654321"
    assert avisos == []
    # La metadata va a una columna JSON: no puede contener tipos numpy ni NaN
    json.dumps([f["metadata_admision"] for f in filas], allow_nan=False)


def test_parse_excel_dni_repetido_y_carrera_desconocida_generan_aviso():
    filas, avisos = parse_excel(_excel([_fila(11111111.0), _fila(11111111.0), _fila(22222222.0, "ODONTOLOGIA")]))
    assert [f["dni"] for f in filas] == ["11111111", "22222222"]
    assert any("repetido" in a for a in avisos)
    assert any("no reconocida" in a for a in avisos)


def test_parse_excel_sin_hoja_b_datos():
    with pytest.raises(ValueError, match="B_Datos"):
        parse_excel(_excel([_fila(1.0)], hoja="Otra"))


def test_parse_excel_archivo_invalido():
    with pytest.raises(ValueError, match="Excel"):
        parse_excel(b"esto no es un excel")


def test_plantilla_real_admision_2026():
    plantilla = Path(__file__).resolve().parents[2] / "ADMISION_2026.xlsx"
    if not plantilla.exists():
        pytest.skip("ADMISION_2026.xlsx no esta en la raiz del proyecto")
    filas, _ = parse_excel(plantilla.read_bytes())
    assert len(filas) > 700
    assert len({f["dni"] for f in filas}) == len(filas)
    assert all(resolver_programa_id(f["programa_nombre"]) for f in filas)
    json.dumps([f["metadata_admision"] for f in filas], allow_nan=False)
