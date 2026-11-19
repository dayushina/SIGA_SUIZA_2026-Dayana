"""Ingesta de Admision: la fila cruda del Excel se normaliza sin perder datos."""
from datetime import date

from tests.helpers.module_loader import load_module_component

normalizer = load_module_component("mod-gestion-academica", "admision_normalizer")

FILA_EXCEL = {
    "SEXO: ": "F",
    "NACIONALIDAD": "PERUANO",
    "LENGUA MATERNA:": "CASTELLANO",
    "DEPARTAMENTO:": "UCAYALI ",
    "PROVINCIA:": "CORONEL PORTILLO",
    "DISTRITO:": "MANANTAY",
    "DOMICILIO:": "JR. HUASCAR MZ 28 LT 11",
    "FECHA NACIMIENTO: ": "2008-05-06T00:00:00",
    "INSTITUCION EDUCATIVA:": "FAUSTINO MALDONADO",
    "Año que termino Secundaria:": "2025",
    "TIPO (Estatal o Particular):": "ESTATAL",
    "DISCAPACIDAD SI/NO:": "NO",
    "TIPO DE DISCAPACIDAD:": "NINGUNA",
    "NOMBRE DEL APODERADO:": "ANA RIOS",
    "CELULAR (APODERADO):": "987654321",
    "CORREO:": None,
}


def test_normaliza_claves_y_conserva_las_crudas():
    r = normalizer.normalizar_admitido(FILA_EXCEL)
    m = r["metadata"]
    assert m["genero"] == "Femenino"
    assert m["nacionalidad"] == "Peruana"
    assert (m["departamento"], m["provincia"], m["distrito"]) == ("UCAYALI", "CORONEL PORTILLO", "MANANTAY")
    assert m["institucion_educativa"]["nombre"] == "FAUSTINO MALDONADO"
    assert m["institucion_educativa"]["anio_egreso"] == 2025
    assert m["emergencia"] == {"contacto_nombre": "ANA RIOS", "contacto_telefono": "987654321"}
    assert m["discapacidad"] == "NO" and "tipo_discapacidad" not in m
    assert m["origen"] == "admision"
    # La fila original se mantiene intacta para trazabilidad.
    for k, v in FILA_EXCEL.items():
        assert m[k] == v


def test_llena_columnas_de_la_tabla():
    r = normalizer.normalizar_admitido(FILA_EXCEL)
    assert r["fecha_nacimiento"] == date(2008, 5, 6)
    assert r["direccion"] == "JR. HUASCAR MZ 28 LT 11"
    # La fecha explicita de Admision tiene prioridad sobre la del Excel.
    assert normalizer.normalizar_admitido(FILA_EXCEL, "2007-01-02")["fecha_nacimiento"] == date(2007, 1, 2)


def test_no_pisa_datos_ya_normalizados_ni_inventa_valores():
    r = normalizer.normalizar_admitido({**FILA_EXCEL, "genero": "Otro"})
    assert r["metadata"]["genero"] == "Otro"

    vacio = normalizer.normalizar_admitido({"SEXO: ": None, "FECHA NACIMIENTO: ": "None"})
    assert "genero" not in vacio["metadata"]
    assert vacio["fecha_nacimiento"] is None and vacio["direccion"] is None
    assert normalizer.normalizar_admitido(None)["metadata"] == {"origen": "admision"}
