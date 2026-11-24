from collections import Counter
from pathlib import Path

import pytest

from tests.helpers.module_loader import load_module_component


@pytest.fixture
def nomina_original():
    ruta = Path(__file__).resolve().parents[2] / "MATRICULADOS I SEM 2026 _rev(2).xlsx"
    if not ruta.exists():
        pytest.skip("La nómina de matrícula institucional no está disponible.")
    return ruta.name, ruta.read_bytes()


def test_nomina_leer_todos_los_ciclos_del_programa(nomina_original):
    parser = load_module_component("mod-gestion-academica", "nomina_matriculados")
    nombre, contenido = nomina_original
    nomina = parser.leer_nomina_matriculados(contenido, nombre)

    assert nomina["codigo"] == "2026-1"
    assert Counter(r["ciclo"] for r in nomina["registros"]) == {1: 49, 3: 56, 5: 20}
    assert len({r["dni"] for r in nomina["registros"]}) == 125
    assert {r["programa_nombre"] for r in nomina["registros"]} == {"DESARROLLO DE SISTEMAS DE INFORMACION"}
    assert all(r["nombres"] and r["apellidos"] and len(r["dni"]) == 8 for r in nomina["registros"])


def test_nomina_rechaza_anio_que_no_coincide_con_el_encabezado(nomina_original):
    parser = load_module_component("mod-gestion-academica", "nomina_matriculados")
    nombre, contenido = nomina_original
    with pytest.raises(ValueError, match="programa, ciclo y año"):
        parser.leer_nomina_matriculados(contenido, nombre.replace("2026", "2027"))
