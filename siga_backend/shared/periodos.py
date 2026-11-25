"""Referencias al calendario compartido por los módulos académicos."""

from sqlalchemy import text


DEPENDENCIAS_PERIODO = {
    "matriculas": ("matrículas", ("periodo_id",)),
    "evaluacion_registros": ("registros de evaluación", ("periodo_id",)),
    "historial_academico": ("registros de historial académico", ("periodo_id",)),
    "carga_lectiva": ("asignaciones de carga lectiva", ("periodo_id",)),
    "docentes_periodo": ("habilitaciones docentes", ("periodo_id",)),
    "planes_trabajo_docente": ("planes de trabajo docente", ("periodo_id",)),
    "horarios_periodo": ("horarios", ("periodo_id",)),
    "tutorias": ("tutorías", ("periodo_id",)),
    "beneficios_estudiante": ("becas o beneficios", ("periodo_validez_inicio_id", "periodo_validez_fin_id")),
}


async def referencias_periodo(db):
    """Devuelve las tablas presentes y sus claves foráneas exactas al periodo."""
    filas = await db.execute(text("""
        SELECT t.relname, a.attname, f.conname, f.confdeltype::text
        FROM pg_class t
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum > 0 AND NOT a.attisdropped
        LEFT JOIN pg_constraint f
            ON f.conrelid = t.oid AND f.contype = 'f' AND f.conkey = ARRAY[a.attnum]
            AND f.confrelid = to_regclass('public.periodos_academicos')
            AND f.confkey = ARRAY[(
                SELECT attnum FROM pg_attribute
                WHERE attrelid = f.confrelid AND attname = 'id' AND NOT attisdropped
            )]
        WHERE n.nspname = 'public' AND t.relname = ANY(CAST(:tablas AS name[]))
            AND a.attname IN ('periodo_id', 'periodo_validez_inicio_id', 'periodo_validez_fin_id')
        ORDER BY t.relname, a.attname, f.conname
    """), {"tablas": list(DEPENDENCIAS_PERIODO)})
    referencias = {}
    for tabla, columna, nombre, accion in filas:
        if columna not in DEPENDENCIAS_PERIODO[tabla][1]:
            continue
        claves = referencias.setdefault((tabla, columna), [])
        if nombre is not None:
            claves.append((nombre, accion))
    return referencias


def restringe_borrado(claves):
    return bool(claves) and all(accion in {"a", "r"} for _, accion in claves)


async def instalar_integridad_periodos(conn):
    """Impide referencias nuevas sin reescribir ni validar retrospectivamente el padrón."""
    await conn.execute(text("SELECT pg_advisory_xact_lock(202601, 0)"))
    if await conn.scalar(text("SELECT to_regclass('public.periodos_academicos')")) is None:
        return

    for (tabla, columna), claves in (await referencias_periodo(conn)).items():
        if restringe_borrado(claves):
            continue
        # Los nombres de tablas y columnas se limitan a DEPENDENCIAS_PERIODO.
        quote = conn.dialect.identifier_preparer.quote
        for nombre, accion in claves:
            if accion not in {"a", "r"}:
                await conn.execute(text(f"ALTER TABLE public.{tabla} DROP CONSTRAINT {quote(nombre)}"))
        if not any(accion in {"a", "r"} for _, accion in claves):
            sufijo = "periodo_academico" if columna == "periodo_id" else columna
            await conn.execute(text(f"""
                ALTER TABLE public.{tabla}
                ADD CONSTRAINT fk_{tabla}_{sufijo}
                FOREIGN KEY ({columna}) REFERENCES public.periodos_academicos (id)
                ON DELETE RESTRICT NOT VALID
            """))


async def asegurar_integridad_periodos(engine):
    async with engine.begin() as conn:
        await instalar_integridad_periodos(conn)
