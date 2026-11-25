import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.periodos import instalar_integridad_periodos
from tests.helpers.jwt_helper import auth_headers
from tests.helpers.module_loader import load_module_component


@pytest.mark.asyncio
async def test_eliminacion_periodos_protege_historial_y_habilitaciones(db_session, fixture_evaluacion):
    academica = load_module_component("mod-gestion-academica", "models")
    routes = load_module_component("mod-programas-estudio", "routes")
    app = FastAPI()
    app.include_router(routes.router)

    async with db_session.bind.connect() as conn:
        transaccion = await conn.begin()
        try:
            await conn.run_sync(academica.BeneficioEstudiante.__table__.create, checkfirst=True)
            await instalar_integridad_periodos(conn)
            await instalar_integridad_periodos(conn)  # El arranque puede ejecutarse en más de un servicio.
            await conn.execute(text("""
                INSERT INTO periodos_academicos (id, codigo, fecha_inicio, fecha_fin, estado)
                VALUES (31, '2029-I', now(), now() + interval '90 days', 'planificacion'),
                       (32, '2029-II', now(), now() + interval '90 days', 'planificacion'),
                       (33, '2030-I', now(), now() + interval '90 days', 'planificacion')
            """))
            await conn.execute(text("""
                INSERT INTO docentes_periodo (docente_id, programa_id, periodo_id) VALUES (10, 1, 31)
            """))
            await conn.execute(text("UPDATE matriculas SET estado_matricula = 'anulada' WHERE id = 50"))
            await conn.execute(text("""
                INSERT INTO beneficios_estudiante (
                    estudiante_id, tipo_beneficio, porcentaje_descuento, periodo_validez_inicio_id, periodo_validez_fin_id
                ) VALUES (100, 'Beca de prueba', 25, 33, 33)
            """))

            async def get_db():
                async with AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False) as sesion:
                    yield sesion

            app.dependency_overrides[routes.get_db] = get_db
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
                admin = auth_headers(1)
                assert (await cliente.delete("/periodos/32")).status_code == 401
                assert (await cliente.delete("/periodos/32", headers=auth_headers(2))).status_code == 403

                ocupado = await cliente.delete("/periodos/1", headers=admin)
                assert ocupado.status_code == 409
                assert "matrículas (1)" in ocupado.json()["detail"]
                assert "registros de evaluación (1)" in ocupado.json()["detail"]
                habilitado = await cliente.delete("/periodos/31", headers=admin)
                assert habilitado.status_code == 409
                assert "habilitaciones docentes (1)" in habilitado.json()["detail"]
                assert await conn.scalar(text("SELECT count(*) FROM docentes_periodo WHERE periodo_id = 31")) == 1
                becado = await cliente.delete("/periodos/33", headers=admin)
                assert becado.status_code == 409
                assert "becas o beneficios (1)" in becado.json()["detail"]

                eliminado = await cliente.delete("/periodos/32", headers=admin)
                assert eliminado.status_code == 200, eliminado.text
                assert eliminado.json()["id"] == 32
                assert (await cliente.delete("/periodos/32", headers=admin)).status_code == 404
                respuesta = await cliente.get("/periodos")
                assert respuesta.headers["cache-control"] == "no-store"
                assert {p["id"] for p in respuesta.json()} >= {1, 31, 33}
                assert all(p["id"] != 32 for p in respuesta.json())

            assert await conn.scalar(text("""
                SELECT count(*) FROM outbox_events WHERE event_type = 'periodo.eliminado'
                    AND payload->>'codigo' = '2029-II' AND payload->>'eliminado_por' = '1'
            """)) == 1

            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(text("DELETE FROM periodos_academicos WHERE id = 31"))

            with pytest.raises(IntegrityError):
                async with conn.begin_nested():
                    await conn.execute(text("""
                        INSERT INTO matriculas (estudiante_id, programa_id, periodo_id, estado_matricula)
                        VALUES (100, 1, 32, 'confirmada')
                    """))
        finally:
            await transaccion.rollback()
