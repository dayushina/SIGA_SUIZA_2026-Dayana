"""Ingesta de admitidos (POST /admision/ingesta en mod-gestion-academica).

Reglas verificadas:
- Solo superadmin/admin pueden ejecutarla.
- Se crean solo los DNI nuevos; los existentes no se tocan.
- Un admitido cuya carrera no existe en programas_estudio no se crea y se informa.
"""
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.helpers.jwt_helper import auth_headers
from tests.helpers.module_loader import load_module_component

SUPERADMIN, SECRETARIA = 1, 5


@pytest_asyncio.fixture
async def client():
    routes = load_module_component("mod-gestion-academica", "routes")
    app = FastAPI()
    app.include_router(routes.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def datos(db_session):
    await db_session.execute(text("""
        INSERT INTO core_roles (id, name, description, created_at) VALUES
            (1, 'superadmin', 'Super', NOW()), (5, 'secretaria_academica', 'Secretaria', NOW())
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
    """))
    await db_session.execute(text("""
        INSERT INTO core_users (id, email, full_name, hashed_password, is_active, is_superuser, created_at) VALUES
            (1, 'super@siga.test', 'Super', 'x', true, true, NOW()),
            (5, 'secretaria@siga.test', 'Secretaria', 'x', true, false, NOW())
        ON CONFLICT (id) DO NOTHING
    """))
    await db_session.execute(text(
        "INSERT INTO core_user_roles (user_id, role_id) VALUES (1,1),(5,5) ON CONFLICT DO NOTHING"))
    await db_session.execute(text("""
        INSERT INTO programas_estudio (id, nombre, codigo, duracion_periodos, creditos_totales, modalidad, estado,
                                       created_at, updated_at)
        VALUES (2, 'Enfermería Técnica', 'T-ENF', 6, 0, 'presencial', 'activo', NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """))
    await db_session.execute(text("""
        INSERT INTO estudiantes (id, codigo_estudiante, dni, nombres, apellidos, programa_id, estado_academico,
                                 pago_matricula, documentos_completos, ciclo_actual, created_at, updated_at)
        VALUES (801, 'T-801', '80100000', 'Ya', 'Existe', 2, 'matriculado', true, true, 1, NOW(), NOW())
    """))
    await db_session.commit()
    return db_session


def _admitido(dni, programa_id=2, programa_nombre="ENFERMERÍA TÉCNICA"):
    return {"dni": dni, "nombres": "ANA", "apellidos": "PEREZ LOPEZ", "programa_id": programa_id,
            "programa_nombre": programa_nombre, "celular": "987654321", "email_personal": "ana@mail.com",
            "fecha_nacimiento": "2005-03-02", "modalidad": "ORDINARIO",
            "metadata_completa": {"DOMICILIO:": "Jr. Lima 123", "FECHA NACIMIENTO: ": "2005-03-02T00:00:00"}}


def h(user_id):
    return {**auth_headers(user_id), "X-User-Email": f"user{user_id}@siga.test"}


@pytest.mark.asyncio
async def test_ingesta_exige_rol_admin(client, datos):
    r = await client.post("/admision/ingesta", json={"admitidos": [_admitido("80200000")]}, headers=h(SECRETARIA))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_ingesta_crea_nuevos_omite_existentes_y_carreras_inexistentes(client, datos):
    lote = [
        _admitido("80100000"),                                   # ya existe
        _admitido("80200000"),                                   # nuevo
        _admitido("80300000", 99, "ODONTOLOGIA"),                # carrera inexistente
        _admitido("80400000", None, "SIN CARRERA"),              # Admision no la reconocio
    ]
    r = await client.post("/admision/ingesta", json={"admitidos": lote}, headers=h(SUPERADMIN))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["estudiantes_creados"] == 1
    assert body["ya_existentes"] == 1
    assert sorted(o["dni"] for o in body["sin_programa"]) == ["80300000", "80400000"]

    fila = (await datos.execute(text(
        "SELECT programa_id, fecha_nacimiento, direccion_domicilio, modalidad_admision "
        "FROM estudiantes WHERE dni = '80200000'"))).one()
    assert fila.programa_id == 2
    assert str(fila.fecha_nacimiento) == "2005-03-02"
    assert fila.modalidad_admision == "ORDINARIO"
    assert (await datos.execute(text(
        "SELECT count(*) FROM estudiantes WHERE dni IN ('80300000','80400000')"))).scalar() == 0

    # Repetir la sincronizacion es idempotente
    r = await client.post("/admision/ingesta", json={"admitidos": lote}, headers=h(SUPERADMIN))
    assert r.json()["estudiantes_creados"] == 0
    assert r.json()["ya_existentes"] == 2


@pytest.mark.asyncio
async def test_ingesta_registra_carrera_oficial_sin_plan_y_avisa(client, datos):
    """Una carrera del catalogo oficial sin fila en programas_estudio no bloquea la carga masiva."""
    lote = [_admitido("80500000", 7, "ELECTRICIDAD INDUSTRIAL"), _admitido("80600000", 2)]
    r = await client.post("/admision/ingesta", json={"admitidos": lote}, headers=h(SUPERADMIN))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["estudiantes_creados"] == 2
    assert body["sin_programa"] == []
    assert body["programas_sin_plan"] == [7]
    assert body["creados_por_programa"] == {"2": 1, "7": 1}
    assert (await datos.execute(text(
        "SELECT programa_id FROM estudiantes WHERE dni = '80500000'"))).scalar() == 7


@pytest.mark.asyncio
async def test_ingesta_avisa_catalogo_desalineado(client, datos):
    """Si el id oficial lo ocupa otra carrera (BD sin unificar) se registra igual y se avisa."""
    await datos.execute(text("""
        INSERT INTO programas_estudio (id, nombre, codigo, duracion_periodos, creditos_totales, modalidad, estado,
                                       created_at, updated_at)
        VALUES (6, 'DESARROLLO DE SISTEMAS DE INFORMACION', 'T-DSI', 6, 0, 'presencial', 'activo', NOW(), NOW())
    """))
    await datos.commit()
    r = await client.post("/admision/ingesta", json={"admitidos": [
        _admitido("81000000", 6, "MECATRÓNICA AUTOMOTRIZ"), _admitido("81100000", 2)]}, headers=h(SUPERADMIN))
    body = r.json()
    assert body["estudiantes_creados"] == 2
    assert body["programas_desalineados"] == [
        {"id": 6, "esperado": "MECATRONICA AUTOMOTRIZ", "en_bd": "DESARROLLO DE SISTEMAS DE INFORMACION"}]


@pytest.mark.asyncio
async def test_ingesta_fila_invalida_no_tumba_el_lote(client, datos):
    malo = _admitido("80700000")
    malo["nombres"] = "X" * 150  # excede VARCHAR(100)
    lote = [_admitido("80800000"), malo, _admitido("80900000")]
    r = await client.post("/admision/ingesta", json={"admitidos": lote}, headers=h(SUPERADMIN))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["estudiantes_creados"] == 2
    assert [e["dni"] for e in body["con_error"]] == ["80700000"]
    assert (await datos.execute(text(
        "SELECT count(*) FROM estudiantes WHERE dni IN ('80800000','80900000')"))).scalar() == 2
