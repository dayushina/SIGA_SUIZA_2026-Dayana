import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.helpers.jwt_helper import auth_headers
from tests.helpers.module_loader import load_module_component


@pytest.mark.asyncio
async def test_edicion_de_periodos_verifica_estado_version_y_vinculos(db_session, fixture_evaluacion):
    routes = load_module_component("mod-programas-estudio", "routes")
    app = FastAPI()
    app.include_router(routes.router)

    async with db_session.bind.connect() as conn:
        async with conn.begin() as transaccion:
            await conn.execute(text("""
                UPDATE periodos_academicos SET fecha_inicio = '2026-03-20 09:30', fecha_fin = '2026-07-31'
                WHERE id = 1
            """))
            await conn.execute(text("""
                INSERT INTO periodos_academicos (id, codigo, fecha_inicio, fecha_fin, estado)
                VALUES (31, '2026-II', '2026-08-01', '2026-12-31', 'planificacion'),
                       (32, '2027-I', '2027-04-01', '2027-07-31', 'matricula_abierta'),
                       (33, '2025-I', '2025-04-01', '2025-07-31', 'cerrado'),
                       (34, '2025-II', '2025-08-01', '2025-12-31', 'historico')
            """))
            await conn.execute(text("INSERT INTO core_user_roles (user_id, role_id) VALUES (55, 5)"))

            async def get_db():
                async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False) as sesion:
                    yield sesion

            app.dependency_overrides[routes.get_db] = get_db
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
                admin = auth_headers(1)
                datos = (await cliente.get("/periodos")).json()
                payloads = {
                    p["id"]: {clave: p[clave] for clave in routes.PeriodoUpdate.model_fields}
                    for p in datos
                }
                original = payloads[1]
                cambios = {
                    **original,
                    "codigo": "2026-I-A",
                    "fecha_fin": "2026-08-15",
                    "fecha_fin_matricula_regular": "2026-03-10",
                    "fecha_fin_matricula_extemporanea": "2026-03-25",
                }

                assert (await cliente.put("/periodos/1", json=cambios)).status_code == 401
                assert (await cliente.put("/periodos/1", json=cambios, headers=auth_headers(10))).status_code == 403
                assert (await cliente.put("/periodos/999", json=cambios, headers=admin)).status_code == 404
                for pid in (33, 34):
                    bloqueado = await cliente.put(f"/periodos/{pid}", json=payloads[pid], headers=admin)
                    assert bloqueado.status_code == 409
                    assert "Solo se pueden editar" in bloqueado.json()["detail"]

                for invalidos in (
                    {"estado": "planificacion"},
                    {"codigo": "   "},
                    {"fecha_fin": "2026-03-01"},
                    {"fecha_fin_matricula_extemporanea": "2026-03-09"},
                ):
                    response = await cliente.put("/periodos/1", json={**cambios, **invalidos}, headers=admin)
                    assert response.status_code == 422, response.text

                duplicado = await cliente.put("/periodos/1", json={**cambios, "codigo": "2026-II"}, headers=admin)
                assert duplicado.status_code == 409
                assert "Ya existe" in duplicado.json()["detail"]
                assert await conn.scalar(text("SELECT codigo FROM periodos_academicos WHERE id = 1")) == "2026-I"

                actualizado = await cliente.put("/periodos/1", json=cambios, headers=admin)
                assert actualizado.status_code == 200, actualizado.text
                assert actualizado.json()["estado"] == "en_curso"
                assert actualizado.json()["fecha_inicio"] == "2026-03-20T09:30:00"
                assert actualizado.json()["fecha_fin"] == "2026-08-15T00:00:00"
                assert actualizado.json()["updated_at"] != original["updated_at"]
                assert await conn.scalar(text("SELECT periodo_id FROM matriculas WHERE id = 50")) == 1

                vieja = await cliente.put("/periodos/1", json=cambios, headers=admin)
                assert vieja.status_code == 409
                assert "cambió desde que lo abriste" in vieja.json()["detail"]

                for pid in (31, 32):
                    respuesta = await cliente.put(f"/periodos/{pid}", headers=auth_headers(55), json={
                        **payloads[pid], "fecha_fin_matricula_regular": "2026-03-10",
                    })
                    assert respuesta.status_code == 200, respuesta.text
                    assert respuesta.json()["fecha_fin_matricula_regular"] == "2026-03-10T00:00:00"

                vigente = {clave: actualizado.json()[clave] for clave in routes.PeriodoUpdate.model_fields}
                assert (await cliente.put("/periodos/1/estado?estado=historico", headers=admin)).status_code == 200
                assert (await cliente.put("/periodos/1", json=vigente, headers=admin)).status_code == 409

            assert await conn.scalar(text("""
                SELECT count(*) FROM outbox_events WHERE event_type = 'periodo.actualizado'
                    AND payload->>'id' = '1' AND payload->>'actualizado_por' = '1'
            """)) == 1
            await transaccion.rollback()
