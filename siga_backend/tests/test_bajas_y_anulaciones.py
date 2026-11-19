"""Anulacion de boletas, desmatricula y eliminacion de estudiantes.

Reglas verificadas:
- Cada operacion exige su rol (el permiso generico de escritura no basta).
- Motivo obligatorio; nada se borra en silencio (quedan estado, motivo y usuario).
- Orden seguro: desmatricular -> anular boleta -> eliminar estudiante.
- No se anula una matricula con notas ni se elimina un estudiante con historia.
"""
import importlib

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from tests.helpers.jwt_helper import auth_headers
from tests.helpers.module_loader import load_module_component

SUPERADMIN, CAJA, SECRETARIA, DOCENTE = 1, 6, 5, 3
EST_A, EST_B, EST_LIMPIO = 501, 502, 503
MAT_A, MAT_B = 601, 602
DET_A, DET_B = 701, 702
PAGO_MAT = 801


@pytest_asyncio.fixture
async def app_bajas():
    routes = load_module_component("mod-gestion-academica", "routes")
    tesoreria = importlib.reload(importlib.import_module("tesoreria_routes"))
    database = importlib.import_module("database")
    # Mismo arranque que el modulo: tablas + columnas nuevas (anulacion)
    await database.init_db()
    await database.ensure_schema()
    await tesoreria.asegurar_esquema_tesoreria()
    app = FastAPI()
    app.include_router(routes.router)
    app.include_router(tesoreria.router)
    return app


@pytest_asyncio.fixture
async def client(app_bajas):
    async with AsyncClient(transport=ASGITransport(app=app_bajas), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def datos(db_session):
    await db_session.execute(text("""
        INSERT INTO core_roles (id, name, description, created_at) VALUES
            (1, 'superadmin', 'Super', NOW()), (3, 'docente', 'Docente', NOW()),
            (5, 'secretaria_academica', 'Secretaria', NOW()), (6, 'caja_tesoreria', 'Caja', NOW())
        ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
    """))
    await db_session.execute(text("""
        INSERT INTO core_users (id, email, full_name, hashed_password, is_active, is_superuser, created_at) VALUES
            (1, 'super@siga.test', 'Super', 'x', true, true, NOW()),
            (3, 'docente@siga.test', 'Docente', 'x', true, false, NOW()),
            (5, 'secretaria@siga.test', 'Secretaria', 'x', true, false, NOW()),
            (6, 'caja@siga.test', 'Caja', 'x', true, false, NOW())
        ON CONFLICT (id) DO NOTHING
    """))
    await db_session.execute(text(
        "INSERT INTO core_user_roles (user_id, role_id) VALUES (1,1),(3,3),(5,5),(6,6) ON CONFLICT DO NOTHING"))
    await db_session.execute(text("""
        INSERT INTO estudiantes (id, codigo_estudiante, dni, nombres, apellidos, programa_id, estado_academico,
                                 pago_matricula, documentos_completos, ciclo_actual, created_at, updated_at) VALUES
            (:a, 'T-501', '50100000', 'Ana', 'Error', 1, 'matriculado', true, true, 1, NOW(), NOW()),
            (:b, 'T-502', '50200000', 'Beto', 'ConNotas', 1, 'matriculado', true, true, 1, NOW(), NOW()),
            (:c, 'T-503', '50300000', 'Carla', 'Limpia', 1, 'ingresante', false, true, 1, NOW(), NOW())
    """), {"a": EST_A, "b": EST_B, "c": EST_LIMPIO})
    await db_session.execute(text("""
        INSERT INTO matriculas (id, estudiante_id, programa_id, periodo_id, estado_matricula, tipo_ingreso,
                                consolidada, created_at, updated_at) VALUES
            (:ma, :a, 1, 1, 'confirmada', 'Ordinario', false, NOW(), NOW()),
            (:mb, :b, 1, 1, 'confirmada', 'Ordinario', false, NOW(), NOW())
    """), {"ma": MAT_A, "mb": MAT_B, "a": EST_A, "b": EST_B})
    await db_session.execute(text("""
        INSERT INTO matricula_detalles (id, matricula_id, unidad_didactica_id, creditos, estado_curso, created_at, updated_at)
        VALUES (:da, :ma, 1, 4, 'cursando', NOW(), NOW()), (:db, :mb, 1, 4, 'cursando', NOW(), NOW())
    """), {"da": DET_A, "db": DET_B, "ma": MAT_A, "mb": MAT_B})
    await db_session.execute(text("""
        INSERT INTO evaluacion_registros (id, matricula_detalle_id, estudiante_id, unidad_didactica_id, periodo_id,
                                          nota_c1, estado, created_at, updated_at)
        VALUES (901, :db, :b, 1, 1, 14, 'cursando', NOW(), NOW())
    """), {"db": DET_B, "b": EST_B})
    await db_session.execute(text("""
        INSERT INTO tesoreria_pagos (id, codigo_recibo, estudiante_id, dni, estudiante_nombre, concepto_codigo,
               concepto_nombre, monto, metodo_pago_id, metodo_pago_nombre, libera_matricula, estado, fecha_pago,
               created_at, updated_at)
        VALUES (:p, 'REC-TEST-0001', :a, '50100000', 'Ana Error', 'MAT-01', 'Derecho de Matricula', 150,
                'efectivo', 'Efectivo', true, 'PAGADO', NOW(), NOW(), NOW())
    """), {"p": PAGO_MAT, "a": EST_A})
    await db_session.commit()
    return db_session


def h(user_id):
    return {**auth_headers(user_id), "X-User-Email": f"user{user_id}@siga.test"}


@pytest.mark.asyncio
async def test_cada_operacion_exige_su_rol(client, datos):
    r = await client.post(f"/tesoreria/pagos/{PAGO_MAT}/anular", json={"motivo": "registro por error"}, headers=h(DOCENTE))
    assert r.status_code == 403
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "registro por error"}, headers=h(CAJA))
    assert r.status_code == 403
    r = await client.delete(f"/estudiantes/{EST_LIMPIO}", params={"motivo": "registro por error"}, headers=h(SECRETARIA))
    assert r.status_code == 403
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "registro por error"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_flujo_completo_desmatricular_anular_y_eliminar(client, datos):
    db = datos
    # La boleta que habilito la matricula no se anula mientras la matricula este vigente.
    r = await client.post(f"/tesoreria/pagos/{PAGO_MAT}/anular", json={"motivo": "cobro duplicado"}, headers=h(CAJA))
    assert r.status_code == 409 and "desmatric" in r.json()["detail"].lower()

    # Tampoco se elimina el estudiante con historia vigente: el mensaje dice que falta.
    r = await client.delete(f"/estudiantes/{EST_A}", params={"motivo": "registro por error"}, headers=h(SUPERADMIN))
    assert r.status_code == 409
    assert "matrícula" in r.json()["detail"] and "recibo" in r.json()["detail"]

    # 1. Desmatricular (motivo obligatorio)
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "x"}, headers=h(SECRETARIA))
    assert r.status_code == 400
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "matriculado por error"}, headers=h(SECRETARIA))
    assert r.status_code == 200, r.text
    fila = (await db.execute(text(
        "SELECT estado_matricula, anulada_motivo, anulada_por FROM matriculas WHERE id = :id"), {"id": MAT_A})).one()
    assert fila == ("anulada", "matriculado por error", "user5@siga.test")
    assert (await db.execute(text("SELECT estado_academico FROM estudiantes WHERE id = :id"),
                             {"id": EST_A})).scalar() == "ingresante"
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "otra vez"}, headers=h(SECRETARIA))
    assert r.status_code == 409

    # 2. Anular la boleta: el alumno vuelve a tener deuda de matricula
    r = await client.post(f"/tesoreria/pagos/{PAGO_MAT}/anular", json={"motivo": "cobro duplicado"}, headers=h(CAJA))
    assert r.status_code == 200, r.text
    assert r.json()["pagoMatricula"] is False
    fila = (await db.execute(text(
        "SELECT estado, anulado_motivo, anulado_por FROM tesoreria_pagos WHERE id = :id"), {"id": PAGO_MAT})).one()
    assert fila == ("ANULADO", "cobro duplicado", "user6@siga.test")
    assert (await db.execute(text("SELECT pago_matricula FROM estudiantes WHERE id = :id"),
                             {"id": EST_A})).scalar() is False
    libro = (await client.get("/tesoreria/pagos", headers=h(CAJA))).json()
    assert all(p["id"] != PAGO_MAT for p in libro)
    r = await client.post(f"/tesoreria/pagos/{PAGO_MAT}/anular", json={"motivo": "cobro duplicado"}, headers=h(CAJA))
    assert r.status_code == 409

    # 3. Eliminar: ya sin historia vigente; arrastra lo anulado y deja auditoria
    r = await client.delete(f"/estudiantes/{EST_A}", params={"motivo": "registro duplicado"}, headers=h(SUPERADMIN))
    assert r.status_code == 200, r.text
    for tabla, col in (("estudiantes", "id"), ("matriculas", "estudiante_id"), ("tesoreria_pagos", "estudiante_id")):
        assert (await db.execute(text(f"SELECT COUNT(*) FROM {tabla} WHERE {col} = :id"), {"id": EST_A})).scalar() == 0
    evento = (await db.execute(text(
        "SELECT payload FROM outbox_events WHERE event_type = 'gestion_academica.estudiante.eliminado'"))).scalar()
    assert evento["data"]["dni"] == "50100000"
    assert evento["data"]["recibos_anulados"] == ["REC-TEST-0001"]
    assert evento["data"]["eliminado_por"] == "user1@siga.test"


@pytest.mark.asyncio
async def test_no_desmatricula_con_notas_y_elimina_estudiante_sin_historia(client, datos):
    r = await client.post(f"/matriculas/{MAT_B}/anular", json={"motivo": "matriculado por error"}, headers=h(SUPERADMIN))
    assert r.status_code == 409 and "notas" in r.json()["detail"]

    r = await client.delete(f"/estudiantes/{EST_LIMPIO}", params={"motivo": "registro por error"}, headers=h(SUPERADMIN))
    assert r.status_code == 200
    r = await client.delete(f"/estudiantes/{EST_LIMPIO}", params={"motivo": "registro por error"}, headers=h(SUPERADMIN))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_matricula_anulada_permite_volver_a_matricular(client, datos):
    """Antes se comparaba con 'anulado' (estado inexistente) y bloqueaba la rematricula."""
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "periodo equivocado"}, headers=h(SECRETARIA))
    assert r.status_code == 200
    vigentes = (await datos.execute(text("""
        SELECT COUNT(*) FROM matriculas WHERE estudiante_id = :id AND periodo_id = 1
          AND estado_matricula NOT IN ('anulada', 'anulada_por_reserva')"""), {"id": EST_A})).scalar()
    assert vigentes == 0
