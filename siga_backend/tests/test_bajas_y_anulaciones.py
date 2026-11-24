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
                                consolidada, registro_academico, boleta_codigo, created_at, updated_at) VALUES
            -- Igual que al terminar el asistente: consolidada (registro academico + boleta QR)
            (:ma, :a, 1, 1, 'confirmada', 'Ordinario', true, 'RA-1-00601', 'BOL-TEST-601', NOW(), NOW()),
            (:mb, :b, 1, 1, 'confirmada', 'Ordinario', false, NULL, NULL, NOW(), NOW())
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

    # La lista de estudiantes expone la matricula vigente (habilita "Desmatricular" en pantalla)
    lista = {e["id"]: e for e in (await client.get("/estudiantes/", headers=h(SECRETARIA))).json()}
    assert lista[EST_A]["matricula_vigente"]["id"] == MAT_A
    assert lista[EST_A]["matricula_vigente"]["consolidada"] is True  # consolidada NO impide desmatricular
    assert lista[EST_LIMPIO]["matricula_vigente"] is None

    # 1. Desmatricular (motivo obligatorio)
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "x"}, headers=h(SECRETARIA))
    assert r.status_code == 400
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "matriculado por error"}, headers=h(SECRETARIA))
    assert r.status_code == 200, r.text
    lista = {e["id"]: e for e in (await client.get("/estudiantes/", headers=h(SECRETARIA))).json()}
    assert lista[EST_A]["matricula_vigente"] is None
    hist = (await client.get(f"/matriculas/estudiante/{EST_A}", headers=h(SECRETARIA))).json()
    assert hist[0]["estado_matricula"] == "anulada" and hist[0]["anulada_motivo"] == "matriculado por error"
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
async def test_eliminacion_por_lote_omite_historia_y_revalida_la_seleccion(client, datos):
    ids = [EST_A, EST_LIMPIO, EST_LIMPIO]
    lote = "/estudiantes/lote"
    vista = await client.post(f"{lote}/vista-previa", json={"ids": ids}, headers=h(SECRETARIA))
    assert vista.status_code == 403
    vista = await client.post(f"{lote}/vista-previa", json={"ids": ids}, headers=h(SUPERADMIN))
    assert vista.status_code == 200, vista.text
    assert vista.json()["elegibles"] == [EST_LIMPIO]
    assert vista.json()["omitidos"][0]["id"] == EST_A
    assert any("matrícula" in motivo for motivo in vista.json()["omitidos"][0]["motivos"])
    assert (await datos.execute(text("SELECT COUNT(*) FROM estudiantes"))).scalar() == 3

    body = {"ids": ids, "motivo": "registro por error", "confirmacion": "ELIMINAR"}
    sin_permiso = await client.post(f"{lote}/eliminar", json=body, headers=h(SECRETARIA))
    assert sin_permiso.status_code == 403
    sin_confirmacion = await client.post(
        f"{lote}/eliminar", json={**body, "confirmacion": ""}, headers=h(SUPERADMIN))
    assert sin_confirmacion.status_code == 422

    # Se agregó una matrícula vigente después de la vista previa.
    await datos.execute(text("""
        INSERT INTO matriculas (id, estudiante_id, programa_id, periodo_id, estado_matricula, tipo_ingreso,
                                consolidada, created_at, updated_at)
        VALUES (603, :estudiante, 1, 1, 'confirmada', 'Ordinario', false, NOW(), NOW())
    """), {"estudiante": EST_LIMPIO})
    await datos.commit()
    resultado = await client.post(f"{lote}/eliminar", json=body, headers=h(SUPERADMIN))
    assert resultado.status_code == 200, resultado.text
    assert resultado.json()["eliminados"] == []
    assert {item["id"] for item in resultado.json()["omitidos"]} == {EST_A, EST_LIMPIO}

    await datos.execute(text("UPDATE matriculas SET estado_matricula = 'anulada' WHERE id = 603"))
    await datos.commit()
    resultado = await client.post(f"{lote}/eliminar", json=body, headers=h(SUPERADMIN))
    assert resultado.status_code == 200, resultado.text
    assert resultado.json()["eliminados"] == [EST_LIMPIO]
    assert [item["id"] for item in resultado.json()["omitidos"]] == [EST_A]
    assert (await datos.execute(text("SELECT id FROM estudiantes ORDER BY id"))).scalars().all() == [EST_A, EST_B]
    eventos = (await datos.execute(text("""
        SELECT payload FROM outbox_events WHERE event_type = 'gestion_academica.estudiante.eliminado'
    """))).scalars().all()
    assert [evento["data"]["id"] for evento in eventos] == [EST_LIMPIO]
    assert eventos[0]["data"]["motivo"] == body["motivo"]

    resultado = await client.post(f"{lote}/eliminar", json=body, headers=h(SUPERADMIN))
    assert resultado.json()["eliminados"] == []
    assert {item["id"] for item in resultado.json()["omitidos"]} == {EST_A, EST_LIMPIO}


@pytest.mark.asyncio
async def test_desmatricula_lote_superadmin_revalida_y_conserva_historial(client, datos):
    otra, reciente = 603, 604
    await datos.execute(text("""
        INSERT INTO matriculas (id, estudiante_id, programa_id, periodo_id, estado_matricula,
                                tipo_ingreso, consolidada, created_at, updated_at)
        VALUES (:otra, :a, 1, 2, 'confirmada', 'Ordinario', false, NOW(), NOW()),
               (:reciente, :c, 1, 1, 'confirmada', 'Ordinario', false, NOW(), NOW())
    """), {"otra": otra, "reciente": reciente, "a": EST_A, "c": EST_LIMPIO})
    await datos.execute(text("""
        INSERT INTO matricula_detalles (id, matricula_id, unidad_didactica_id, creditos, estado_curso, created_at, updated_at)
        VALUES (703, :id, 1, 4, 'cursando', NOW(), NOW())
    """), {"id": reciente})
    await datos.commit()

    lote = "/matriculas/lote"
    filtros = {"periodo_id": 1, "programa_id": 1}
    cuerpo = {"periodo_id": 1, "motivo": "Corrección de registro",
              "confirmacion": "DESMATRICULAR", "ids": [MAT_A, MAT_B, reciente, otra, MAT_A]}
    assert (await client.post(f"{lote}/vista-previa", json=filtros, headers=h(SECRETARIA))).status_code == 403
    assert (await client.post(f"{lote}/anular", json=cuerpo, headers=h(SECRETARIA))).status_code == 403
    r = await client.post(f"{lote}/vista-previa", json=filtros, headers=h(SUPERADMIN))
    assert r.status_code == 200, r.text
    vista = {m["id"]: m for m in r.json()["matriculas"]}
    assert set(vista) == {MAT_A, MAT_B, reciente}
    assert not vista[MAT_A]["bloqueo"] and not vista[reciente]["bloqueo"]
    assert "notas" in vista[MAT_B]["bloqueo"]
    r = await client.post(f"{lote}/anular", json={**cuerpo, "confirmacion": "no"}, headers=h(SUPERADMIN))
    assert r.status_code == 422

    # Se guardó una nota entre la vista previa y la confirmación.
    await datos.execute(text("""
        INSERT INTO evaluacion_registros (id, matricula_detalle_id, estudiante_id, unidad_didactica_id,
                                          periodo_id, nota_c1, estado, created_at, updated_at)
        VALUES (902, 703, :id, 1, 1, 0, 'cursando', NOW(), NOW())
    """), {"id": EST_LIMPIO})
    await datos.commit()
    r = await client.post(f"{lote}/anular", json=cuerpo, headers=h(SUPERADMIN))
    assert r.status_code == 200, r.text
    assert r.json()["anuladas"] == [MAT_A]
    assert {m["id"] for m in r.json()["omitidas"]} == {MAT_B, reciente, otra}
    assert await datos.scalar(text("SELECT estado FROM tesoreria_pagos WHERE id = :id"), {"id": PAGO_MAT}) == "PAGADO"
    assert await datos.scalar(text("SELECT estado_academico FROM estudiantes WHERE id = :id"), {"id": EST_A}) == "matriculado"
    assert await datos.scalar(text("SELECT count(*) FROM matricula_detalles WHERE matricula_id = :id"), {"id": MAT_A}) == 1
    evento = await datos.scalar(text("SELECT payload FROM outbox_events WHERE event_type = 'gestion_academica.matricula.anulada'"))
    assert evento["data"]["anulada_por"] == "user1@siga.test"
    assert evento["data"]["motivo"] == "Corrección de registro"

    r = await client.post(f"{lote}/anular", json=cuerpo, headers=h(SUPERADMIN))
    assert r.json()["anuladas"] == []
    assert await datos.scalar(text("SELECT count(*) FROM outbox_events WHERE event_type = 'gestion_academica.matricula.anulada'")) == 1


@pytest.mark.asyncio
async def test_matricula_anulada_permite_volver_a_matricular(client, datos):
    """Antes se comparaba con 'anulado' (estado inexistente) y bloqueaba la rematricula."""
    r = await client.post(f"/matriculas/{MAT_A}/anular", json={"motivo": "periodo equivocado"}, headers=h(SECRETARIA))
    assert r.status_code == 200
    vigentes = (await datos.execute(text("""
        SELECT COUNT(*) FROM matriculas WHERE estudiante_id = :id AND periodo_id = 1
          AND estado_matricula NOT IN ('anulada', 'anulada_por_reserva')"""), {"id": EST_A})).scalar()
    assert vigentes == 0
