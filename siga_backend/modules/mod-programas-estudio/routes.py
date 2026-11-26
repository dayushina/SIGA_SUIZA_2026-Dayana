from fastapi import APIRouter, HTTPException, Depends, status, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func, delete, text, table, column
from typing import List, Optional
from datetime import datetime, date
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from database import get_db, OutboxEvent
from models import ProgramaEstudio, ProgramaConfiguracion, ProgramaModulo, ProgramaUnidad, ProgramaRequisito, PeriodoAcademico
from shared.identity import (
    Identity, get_identity, es_superadmin, es_coordinador_de, es_coordinador_de_docente
)
from shared.programas import programa_implementado
from shared.periodos import DEPENDENCIAS_PERIODO, referencias_periodo, restringe_borrado
from schemas import (
    SilaboUpdate, PlanTrabajoDocenteUpdate, CargaLectivaUpdate, HorarioPeriodoUpdate
)

router = APIRouter()

# Schemas Pydantic
class ProgramaBase(BaseModel):
    nombre: str
    codigo: str
    descripcion: Optional[str] = None
    duracion_periodos: int = 6
    creditos_totales: int
    plan_estudio_id: Optional[int] = None
    modalidad: str = "presencial"
    estado: str = "activo"

class ProgramaCreate(ProgramaBase):
    pass

class ProgramaResponse(ProgramaBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Schemas Periodo Academico
ESTADOS_PERIODO_EDITABLES = {"planificacion", "matricula_abierta", "en_curso"}


class PeriodoBase(BaseModel):
    codigo: str
    fecha_inicio: datetime
    fecha_fin: datetime
    estado: str = "planificacion"
    fecha_fin_matricula_regular: Optional[datetime] = None
    fecha_fin_matricula_extemporanea: Optional[datetime] = None

class PeriodoCreate(PeriodoBase):
    pass


class PeriodoUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    codigo: str = Field(min_length=1, max_length=20)
    fecha_inicio: datetime
    fecha_fin: datetime
    fecha_fin_matricula_regular: Optional[datetime]
    fecha_fin_matricula_extemporanea: Optional[datetime]
    updated_at: Optional[datetime]

    @field_validator("fecha_inicio", "fecha_fin", "fecha_fin_matricula_regular", "fecha_fin_matricula_extemporanea")
    @classmethod
    def fecha_local(cls, value):
        return value.replace(tzinfo=None) if value is not None else None


class PeriodoResponse(PeriodoBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    nombre: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def populate_nombre(self):
        if not self.nombre:
            self.nombre = self.codigo
        return self

# Health Check
@router.get("/health")
async def health_check():
    return {"status": "healthy", "module": "mod-programas-estudio"}

# Endpoints CRUD
@router.post("/programas", response_model=ProgramaResponse, status_code=status.HTTP_201_CREATED)
async def crear_programa(programa: ProgramaCreate, request: Request, db: AsyncSession = Depends(get_db)):
    import unicodedata
    def normalize_str(s: str) -> str:
        if not s:
            return ""
        return "".join(c for c in unicodedata.normalize("NFD", s.strip().lower()) if unicodedata.category(c) != "Mn")

    codigo_norm = programa.codigo.strip().upper()
    nombre_norm = normalize_str(programa.nombre)

    all_existentes = (await db.execute(select(ProgramaEstudio))).scalars().all()
    for p in all_existentes:
        if p.codigo and p.codigo.strip().upper() == codigo_norm:
            raise HTTPException(status_code=400, detail=f"Ya existe un programa de estudio con el código '{programa.codigo}'")
        if normalize_str(p.nombre) == nombre_norm:
            raise HTTPException(status_code=400, detail=f"El programa de estudio '{programa.nombre}' ya existe en el catálogo institucional")

    db_programa = ProgramaEstudio(**programa.model_dump())
    db.add(db_programa)
    await db.flush() # Para obtener ID antes de crear el outbox

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="carrera.creada",
        payload={
            "id": db_programa.id,
            "nombre": db_programa.nombre,
            "codigo": db_programa.codigo,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_programa)
    return db_programa

# Endpoints Periodos Academicos
@router.post("/periodos", response_model=PeriodoResponse, status_code=status.HTTP_201_CREATED)
async def crear_periodo(periodo: PeriodoCreate, request: Request, db: AsyncSession = Depends(get_db)):
    data = periodo.model_dump()
    for key in ['fecha_inicio', 'fecha_fin', 'fecha_fin_matricula_regular', 'fecha_fin_matricula_extemporanea']:
        if data.get(key) and getattr(data[key], 'tzinfo', None):
            data[key] = data[key].replace(tzinfo=None)
            
    db_periodo = PeriodoAcademico(**data)
    db.add(db_periodo)
    await db.flush()

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="periodo.creado",
        payload={
            "id": db_periodo.id,
            "codigo": db_periodo.codigo,
            "estado": db_periodo.estado,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_periodo)
    return db_periodo

@router.get("/periodos", response_model=List[PeriodoResponse])
async def obtener_periodos(response: Response, db: AsyncSession = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    result = await db.execute(select(PeriodoAcademico).order_by(PeriodoAcademico.fecha_inicio.desc()))
    return result.scalars().all()


@router.put("/periodos/{periodo_id}", response_model=PeriodoResponse)
async def actualizar_periodo(periodo_id: int, datos: PeriodoUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_identity(request, db)
    if not es_superadmin(actor) and "secretaria_academica" not in actor.roles:
        raise HTTPException(403, detail="Solo el superadministrador o Secretaría Académica pueden editar periodos.")

    periodo = await db.get(PeriodoAcademico, periodo_id, with_for_update=True)
    if periodo is None:
        raise HTTPException(404, detail="El periodo académico ya no existe.")
    if (periodo.estado or "").strip().lower() not in ESTADOS_PERIODO_EDITABLES:
        raise HTTPException(409, detail="Solo se pueden editar periodos en planificación, matrícula abierta o en curso.")
    if periodo.updated_at != datos.updated_at:
        raise HTTPException(409, detail="El periodo cambió desde que lo abriste. Cancela y vuelve a abrir la edición para ver sus datos actuales.")
    if datos.fecha_fin < datos.fecha_inicio:
        raise HTTPException(422, detail="La fecha de fin debe ser igual o posterior a la fecha de inicio.")
    if (datos.fecha_fin_matricula_regular is not None and datos.fecha_fin_matricula_extemporanea is not None
            and datos.fecha_fin_matricula_extemporanea < datos.fecha_fin_matricula_regular):
        raise HTTPException(422, detail="El cierre de matrícula extemporánea debe ser igual o posterior al cierre de matrícula regular.")

    cambios = {
        campo: valor for campo, valor in datos.model_dump(exclude={"updated_at"}).items()
        if valor != getattr(periodo, campo)
    }
    if not cambios:
        return periodo

    for campo, valor in cambios.items():
        setattr(periodo, campo, valor)
    db.add(OutboxEvent(
        event_type="periodo.actualizado",
        payload={
            "id": periodo_id,
            "codigo": periodo.codigo,
            "estado": periodo.estado,
            "campos": list(cambios),
            "actualizado_por": actor.user_id,
            "metadata": {"request_id": request.headers.get("X-Request-ID", "unknown")},
        },
    ))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) != "23505":
            raise
        raise HTTPException(409, detail=f"Ya existe un periodo con el código {datos.codigo}.") from exc
    await db.refresh(periodo)
    return periodo


@router.put("/periodos/{periodo_id}/estado", response_model=PeriodoResponse)
async def actualizar_estado_periodo(periodo_id: int, estado: str, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PeriodoAcademico).where(PeriodoAcademico.id == periodo_id).with_for_update())
    db_periodo = result.scalar_one_or_none()
    if not db_periodo:
        raise HTTPException(status_code=404, detail="Periodo no encontrado")
        
    db_periodo.estado = estado
    
    event = OutboxEvent(
        event_type="periodo.estado_actualizado",
        payload={
            "id": db_periodo.id,
            "codigo": db_periodo.codigo,
            "nuevo_estado": estado,
            "metadata": {"request_id": request.headers.get("X-Request-ID", "unknown")}
        }
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_periodo)
    return db_periodo


@router.delete("/periodos/{periodo_id}")
async def eliminar_periodo(periodo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_identity(request, db)
    if not es_superadmin(actor):
        raise HTTPException(403, detail="Solo el superadministrador puede eliminar periodos académicos.")

    periodo = await db.get(PeriodoAcademico, periodo_id, with_for_update=True)
    if periodo is None:
        raise HTTPException(404, detail="El periodo académico ya no existe.")

    referencias = await referencias_periodo(db)
    if any(not restringe_borrado(claves) for claves in referencias.values()):
        raise HTTPException(503, detail="La protección de referencias del calendario todavía no está disponible.")

    bloqueos = []
    for tabla, (etiqueta, columnas) in DEPENDENCIAS_PERIODO.items():
        condiciones = " OR ".join(f"{columna} = :id" for columna in columnas if (tabla, columna) in referencias)
        if condiciones:
            cantidad = await db.scalar(
                text(f"SELECT count(*) FROM public.{tabla} WHERE {condiciones}"), {"id": periodo_id}
            )
            if cantidad:
                bloqueos.append(f"{etiqueta} ({cantidad})")
    if bloqueos:
        raise HTTPException(
            409, detail=f"El periodo {periodo.codigo} tiene registros vinculados: {', '.join(bloqueos)}."
        )

    codigo = periodo.codigo
    await db.delete(periodo)
    db.add(OutboxEvent(
        event_type="periodo.eliminado",
        payload={
            "id": periodo_id,
            "codigo": codigo,
            "estado": periodo.estado,
            "eliminado_por": actor.user_id,
            "metadata": {"request_id": request.headers.get("X-Request-ID", "unknown")},
        },
    ))
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if getattr(exc.orig, "sqlstate", None) != "23503":
            raise
        raise HTTPException(409, detail=f"El periodo {codigo} tiene otros registros vinculados.") from exc
    return {"id": periodo_id, "message": f"Periodo {codigo} eliminado."}


@router.get("/programas", response_model=List[ProgramaResponse])
async def obtener_programas(implementados: bool = False, db: AsyncSession = Depends(get_db)):
    consulta = select(ProgramaEstudio).order_by(ProgramaEstudio.nombre)
    if implementados:
        tiene_malla = (select(1).select_from(ProgramaModulo)
                       .join(ProgramaUnidad, ProgramaUnidad.programa_modulo_id == ProgramaModulo.id)
                       .where(ProgramaModulo.programa_id == ProgramaEstudio.id).exists())
        consulta = consulta.where(ProgramaEstudio.estado == "activo", ProgramaEstudio.plan_estudio_id.is_not(None), tiene_malla)
    result = await db.execute(consulta)
    return result.scalars().all()

@router.get("/programas/{programa_id}", response_model=ProgramaResponse)
async def obtener_programa(programa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id))
    programa = result.scalar_one_or_none()
    if not programa:
        raise HTTPException(status_code=404, detail="Programa no encontrado")
    return programa

@router.get("/programas/{programa_id}/estructura-completa")
async def obtener_estructura_completa(programa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id))
    programa = result.scalar_one_or_none()
    if not programa:
        raise HTTPException(status_code=404, detail="Programa no encontrado")

    result_config = await db.execute(
        select(ProgramaConfiguracion)
        .where(ProgramaConfiguracion.programa_id == programa_id)
        .order_by(ProgramaConfiguracion.periodo_academico)
    )
    configuraciones = result_config.scalars().all()

    result_modulos = await db.execute(
        select(ProgramaModulo)
        .where(ProgramaModulo.programa_id == programa_id)
        .order_by(ProgramaModulo.periodo, ProgramaModulo.orden)
    )
    modulos = result_modulos.scalars().all()

    modulos_con_unidades = []
    for modulo in modulos:
        result_unidades = await db.execute(
            select(ProgramaUnidad)
            .where(ProgramaUnidad.programa_modulo_id == modulo.id)
            .order_by(ProgramaUnidad.orden)
        )
        unidades = result_unidades.scalars().all()
        modulos_con_unidades.append({
            **{k: v for k, v in modulo.__dict__.items() if not k.startswith('_')},
            "unidades": [{k: v for k, v in u.__dict__.items() if not k.startswith('_')} for u in unidades]
        })

    result_requisitos = await db.execute(
        select(ProgramaRequisito)
        .where(ProgramaRequisito.programa_id == programa_id)
    )
    requisitos = result_requisitos.scalars().all()

    return {
        **{k: v for k, v in programa.__dict__.items() if not k.startswith('_')},
        "configuraciones": configuraciones,
        "modulos": modulos_con_unidades,
        "requisitos": requisitos
    }

@router.put("/programas/{programa_id}", response_model=ProgramaResponse)
async def actualizar_programa(programa_id: int, programa_data: ProgramaCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id))
    programa = result.scalar_one_or_none()
    if not programa:
        raise HTTPException(status_code=404, detail="Programa no encontrado")

    # Solo los campos enviados: lo omitido (p.ej. plan_estudio_id) no se borra.
    for field, value in programa_data.model_dump(exclude_unset=True).items():
        setattr(programa, field, value)

    await db.commit()
    await db.refresh(programa)
    return programa

@router.delete("/programas/{programa_id}")
async def eliminar_programa(programa_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id))
    db_programa = result.scalar_one_or_none()
    if not db_programa:
        raise HTTPException(status_code=404, detail="Programa no encontrado")

    # Verificar si hay estudiantes vinculados
    res_estudiantes = await db.execute(
        select(func.count()).select_from(text("estudiantes")).where(text("programa_id = :pid")),
        {"pid": programa_id}
    )
    total_estudiantes = res_estudiantes.scalar() or 0
    if total_estudiantes > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"No se puede eliminar el programa porque tiene {total_estudiantes} estudiantes asociados. Si ya no se oferta, se recomienda cambiar su estado a 'Inactivo' o 'En liquidación'."
        )

    # Eliminar configuraciones asociadas
    await db.execute(
        delete(ProgramaConfiguracion).where(ProgramaConfiguracion.programa_id == programa_id)
    )

    # Eliminar requisitos asociados
    await db.execute(
        delete(ProgramaRequisito).where(ProgramaRequisito.programa_id == programa_id)
    )

    # Eliminar unidades de los módulos del programa
    res_modulos = await db.execute(
        select(ProgramaModulo.id).where(ProgramaModulo.programa_id == programa_id)
    )
    modulo_ids = res_modulos.scalars().all()
    if modulo_ids:
        await db.execute(
            delete(ProgramaUnidad).where(ProgramaUnidad.programa_modulo_id.in_(modulo_ids))
        )
        await db.execute(
            delete(ProgramaModulo).where(ProgramaModulo.id.in_(modulo_ids))
        )

    # Eliminar tutorías, horarios, planes docentes y cargas si existieran
    await db.execute(text("DELETE FROM tutorias WHERE programa_id = :pid"), {"pid": programa_id})
    await db.execute(text("DELETE FROM horarios_periodo WHERE programa_id = :pid"), {"pid": programa_id})
    await db.execute(text("DELETE FROM planes_trabajo_docente WHERE programa_id = :pid"), {"pid": programa_id})
    await db.execute(text("DELETE FROM carga_lectiva WHERE programa_id = :pid"), {"pid": programa_id})

    # Eliminar el programa
    await db.delete(db_programa)

    # Registrar evento en outbox
    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="carrera.eliminada",
        payload={
            "id": programa_id,
            "nombre": db_programa.nombre,
            "codigo": db_programa.codigo,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()

    return {"message": "Programa de estudio eliminado exitosamente", "id": programa_id}

class ProgramaConfiguracionCreate(BaseModel):
    periodo_academico: Optional[int] = 1
    turnos: Optional[List[str]] = ["mañana", "noche"]
    coordinador_id: Optional[int] = None
    max_estudiantes: Optional[int] = 35

@router.get("/programas/{programa_id}/configuracion")
async def obtener_configuracion_programa(programa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ProgramaConfiguracion)
        .where(ProgramaConfiguracion.programa_id == programa_id)
        .order_by(ProgramaConfiguracion.id.desc())
    )
    config = result.scalars().first()
    if not config:
        return {
            "programa_id": programa_id,
            "periodo_academico": 1,
            "turnos": ["mañana", "noche"],
            "coordinador_id": None,
            "max_estudiantes": 35
        }
    turnos_list = config.turnos if isinstance(config.turnos, list) else (list(config.turnos.keys()) if isinstance(config.turnos, dict) else ["mañana"])
    return {
        "id": config.id,
        "programa_id": config.programa_id,
        "periodo_academico": config.periodo_academico,
        "turnos": turnos_list,
        "coordinador_id": config.coordinador_id,
        "max_estudiantes": config.max_estudiantes
    }

@router.post("/programas/{programa_id}/configuracion")
async def guardar_configuracion_programa(programa_id: int, config_data: ProgramaConfiguracionCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ProgramaConfiguracion)
        .where(ProgramaConfiguracion.programa_id == programa_id)
        .order_by(ProgramaConfiguracion.id.desc())
    )
    config = result.scalars().first()
    if config:
        config.turnos = config_data.turnos
        config.max_estudiantes = config_data.max_estudiantes
        config.coordinador_id = config_data.coordinador_id
        config.periodo_academico = config_data.periodo_academico or 1
    else:
        config = ProgramaConfiguracion(
            programa_id=programa_id,
            periodo_academico=config_data.periodo_academico or 1,
            turnos=config_data.turnos,
            coordinador_id=config_data.coordinador_id,
            max_estudiantes=config_data.max_estudiantes or 35
        )
        db.add(config)
    await db.commit()
    await db.refresh(config)
    return {
        "id": config.id,
        "programa_id": config.programa_id,
        "periodo_academico": config.periodo_academico,
        "turnos": config.turnos,
        "coordinador_id": config.coordinador_id,
        "max_estudiantes": config.max_estudiantes
    }

@router.post("/programas/{programa_id}/modulos", status_code=status.HTTP_201_CREATED)
async def agregar_modulo(programa_id: int, modulo_data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    db_modulo = ProgramaModulo(programa_id=programa_id, **modulo_data)
    db.add(db_modulo)
    await db.flush()

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="malla.modulo_agregado",
        payload={
            "programa_id": programa_id,
            "modulo_id": db_modulo.id,
            "nombre": db_modulo.nombre,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_modulo)
    return db_modulo

@router.post("/modulos/{modulo_id}/unidades", status_code=status.HTTP_201_CREATED)
async def agregar_unidad(modulo_id: int, unidad_data: dict, request: Request, db: AsyncSession = Depends(get_db)):
    db_unidad = ProgramaUnidad(programa_modulo_id=modulo_id, **unidad_data)
    db.add(db_unidad)
    await db.flush()

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="malla.unidad_agregada",
        payload={
            "modulo_id": modulo_id,
            "unidad_id": db_unidad.id,
            "nombre": db_unidad.nombre,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_unidad)
    return db_unidad

@router.delete("/modulos/{modulo_id}")
async def eliminar_modulo(modulo_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaModulo).where(ProgramaModulo.id == modulo_id))
    modulo = result.scalar_one_or_none()
    if not modulo:
        raise HTTPException(status_code=404, detail="Módulo no encontrado")
    await db.delete(modulo)
    await db.commit()
    return {"message": "Módulo eliminado exitosamente"}

@router.delete("/unidades/{unidad_id}")
async def eliminar_unidad(unidad_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaUnidad).where(ProgramaUnidad.id == unidad_id))
    unidad = result.scalar_one_or_none()
    if not unidad:
        raise HTTPException(status_code=404, detail="Unidad didáctica no encontrada")
    await db.delete(unidad)
    await db.commit()
    return {"message": "Unidad didáctica eliminada exitosamente"}

@router.get("/programas/{programa_id}/malla")
async def obtener_malla(programa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ProgramaModulo)
        .where(ProgramaModulo.programa_id == programa_id)
        .order_by(ProgramaModulo.periodo)
    )
    modulos = result.scalars().all()

    malla = []
    for m in modulos:
        res_u = await db.execute(select(ProgramaUnidad).where(ProgramaUnidad.programa_modulo_id == m.id))
        unidades = res_u.scalars().all()
        
        unidades_dict = []
        for u in unidades:
            unidades_dict.append({
                "id": u.id,
                "nombre": u.nombre,
                "creditos": u.creditos,
                "horas": (u.horas_teoria or 0) + (u.horas_practica or 0),
                "orden": u.orden
            })
            
        malla.append({
            "id": m.id,
            "nombre": m.nombre,
            "periodo": m.periodo,
            "unidades": unidades_dict
        })

    return malla

@router.delete("/programas/{programa_id}")
async def eliminar_programa(programa_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id))
    programa = result.scalar_one_or_none()
    if not programa:
        raise HTTPException(status_code=404, detail="Programa no encontrado")

    programa.estado = "inactivo"
    await db.commit()
    return {"message": "Programa eliminado correctamente"}

# --- Schemas Coordinación (Carga Lectiva y Horarios) ---
class CargaLectivaBase(BaseModel):
    periodo_id: int
    docente_id: int
    unidad_didactica_id: int
    turno: Optional[str] = None
    seccion: Optional[str] = None
    estado: str = "borrador"

class CargaLectivaCreate(CargaLectivaBase):
    pass

class CargaLectivaResponse(CargaLectivaBase):
    id: int
    programa_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    fecha_limite_notas: Optional[date] = None
    acta_cerrada: bool = False
    acta_cerrada_at: Optional[datetime] = None
    acta_cerrada_por: Optional[int] = None
    unidad_nombre: Optional[str] = None
    docente_nombre: Optional[str] = None
    programa_nombre: Optional[str] = None
    modulo_nombre: Optional[str] = None
    ciclo: Optional[int] = None
    creditos: Optional[int] = None
    horas: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)

class HorarioPeriodoBase(BaseModel):
    periodo_id: int
    estado: str = "borrador"
    archivo_excel_url: str
    observaciones: Optional[str] = None

class HorarioPeriodoCreate(HorarioPeriodoBase):
    pass

class HorarioPeriodoResponse(HorarioPeriodoBase):
    id: int
    programa_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

# --- Endpoints Coordinación ---
from models import CargaLectiva, HorarioPeriodo

class DocenteDisponible(BaseModel):
    id: int
    full_name: str


async def _docentes_del_periodo(db, programa_id, periodo_id, ids=None):
    consulta = """
        SELECT u.id, COALESCE(NULLIF(trim(u.full_name), ''), 'Docente #' || u.id) AS full_name
        FROM perfiles_personal p JOIN core_users u ON u.id = p.user_id
        WHERE p.programa_estudio_id = :programa AND u.is_active
          AND EXISTS (
              SELECT 1 FROM core_user_roles ur JOIN core_roles r ON r.id = ur.role_id
              WHERE ur.user_id = u.id AND r.name = 'docente'
          )
          AND (
              EXISTS (SELECT 1 FROM docentes_periodo d
                      WHERE d.programa_id = :programa AND d.periodo_id = :periodo AND d.docente_id = u.id)
              OR EXISTS (SELECT 1 FROM carga_lectiva c
                         WHERE c.programa_id = :programa AND c.periodo_id = :periodo AND c.docente_id = u.id)
          )
    """
    params = {"programa": programa_id, "periodo": periodo_id}
    if ids is not None:
        consulta += " AND u.id = ANY(:ids)"
        params["ids"] = ids
    consulta += " ORDER BY lower(u.full_name), u.id"
    return (await db.execute(text(consulta), params)).mappings().all()


async def _validar_docentes_del_periodo(db, programa_id, periodo_id, ids):
    # La edición de cuentas toma un bloqueo exclusivo sobre estas mismas filas.
    # Consultar la elegibilidad después del bloqueo permite leer los cambios ya confirmados.
    ids_ordenados = sorted(ids)
    await db.execute(text("""
        SELECT id FROM core_users WHERE id = ANY(:ids) ORDER BY id FOR SHARE
    """), {"ids": ids_ordenados})
    disponibles = await _docentes_del_periodo(db, programa_id, periodo_id, ids_ordenados)
    if ids != {d["id"] for d in disponibles}:
        raise HTTPException(422, detail="Todos los docentes deben estar activos y habilitados para el programa y periodo lectivo.")


async def _periodo_para_carga(db, periodo_id):
    periodo = await db.scalar(
        select(PeriodoAcademico).where(PeriodoAcademico.id == periodo_id).with_for_update(read=True)
    )
    if periodo is None or (periodo.estado or "").lower() in {"cerrado", "finalizado"}:
        raise HTTPException(409, detail="La carga lectiva requiere un periodo existente que permita modificaciones.")


@router.get("/programas/{programa_id}/docentes-disponibles", response_model=list[DocenteDisponible])
async def listar_docentes_disponibles(
    programa_id: int, periodo_id: int, request: Request, response: Response,
    db: AsyncSession = Depends(get_db),
):
    ident = await get_identity(request, db)
    if not es_coordinador_de(ident, programa_id):
        raise HTTPException(403, detail="No tiene permisos para consultar los docentes de este programa.")
    await programa_implementado(db, programa_id)
    if await db.get(PeriodoAcademico, periodo_id) is None:
        raise HTTPException(404, detail="Periodo lectivo no encontrado.")
    response.headers["Cache-Control"] = "no-store"
    return await _docentes_del_periodo(db, programa_id, periodo_id)


async def _validar_acceso_docente(ident: Identity, docente_id: int, db: AsyncSession):
    """
    Regla de acceso a datos de un docente:
    Permitido si user_id == docente_id, o si es superadmin,
    o si es coordinador y su programa coincide con al menos una carga_lectiva de ese docente.
    """
    if es_superadmin(ident):
        return
    if ident.user_id == docente_id:
        return
    if await es_coordinador_de_docente(ident, docente_id, db):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No tiene permisos para acceder a la información de este docente"
    )

@router.post("/programas/{programa_id}/carga-lectiva", response_model=CargaLectivaResponse, status_code=status.HTTP_201_CREATED)
async def crear_carga_lectiva(programa_id: int, carga: CargaLectivaCreate, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para gestionar la carga lectiva de este programa"
        )

    await programa_implementado(db, programa_id)
    await _periodo_para_carga(db, carga.periodo_id)
    await _validar_docentes_del_periodo(db, programa_id, carga.periodo_id, {carga.docente_id})

    # 3. Validar duplicidad
    duplicada = await db.execute(
        select(CargaLectiva).where(
            CargaLectiva.programa_id == programa_id,
            CargaLectiva.periodo_id == carga.periodo_id,
            CargaLectiva.unidad_didactica_id == carga.unidad_didactica_id,
            CargaLectiva.seccion == carga.seccion,
        )
    )
    if duplicada.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esa unidad didáctica ya tiene un docente asignado en este periodo y sección",
        )
    db_carga = CargaLectiva(**carga.model_dump(), programa_id=programa_id)
    db.add(db_carga)
    await db.commit()
    await db.refresh(db_carga)
    return db_carga

@router.get("/programas/{programa_id}/carga-lectiva", response_model=List[CargaLectivaResponse])
async def listar_carga_lectiva(programa_id: int, periodo_id: int, db: AsyncSession = Depends(get_db)):
    usuarios = table("core_users", column("id"), column("full_name"))
    result = await db.execute(
        select(CargaLectiva, usuarios.c.full_name)
        .outerjoin(usuarios, usuarios.c.id == CargaLectiva.docente_id)
        .where(CargaLectiva.programa_id == programa_id, CargaLectiva.periodo_id == periodo_id)
    )
    return [
        CargaLectivaResponse.model_validate(carga).model_copy(update={"docente_nombre": nombre})
        for carga, nombre in result.all()
    ]

class ClonarCargaRequest(BaseModel):
    from_periodo_id: int
    to_periodo_id: int

@router.post("/programas/{programa_id}/carga-lectiva/clonar")
async def clonar_carga_lectiva(programa_id: int, req: ClonarCargaRequest, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para clonar la carga lectiva de este programa"
        )

    await _periodo_para_carga(db, req.to_periodo_id)

    result = await db.execute(
        select(CargaLectiva)
        .where(CargaLectiva.programa_id == programa_id, CargaLectiva.periodo_id == req.from_periodo_id)
    )
    cargas_anteriores = result.scalars().all()
    
    if not cargas_anteriores:
        raise HTTPException(status_code=404, detail="No hay carga lectiva en el periodo origen")
        
    nuevas_cargas = []
    for carga in cargas_anteriores:
        res_exist = await db.execute(
            select(CargaLectiva)
            .where(
                CargaLectiva.programa_id == programa_id,
                CargaLectiva.periodo_id == req.to_periodo_id,
                CargaLectiva.unidad_didactica_id == carga.unidad_didactica_id,
                CargaLectiva.seccion == carga.seccion
            )
        )
        if not res_exist.scalar_one_or_none():
            nueva_carga = CargaLectiva(
                programa_id=programa_id,
                periodo_id=req.to_periodo_id,
                docente_id=carga.docente_id,
                unidad_didactica_id=carga.unidad_didactica_id,
                turno=carga.turno,
                seccion=carga.seccion,
                estado="borrador",
                acta_cerrada=False,
                acta_cerrada_at=None,
                acta_cerrada_por=None,
                fecha_limite_notas=None
            )
            nuevas_cargas.append(nueva_carga)

    if nuevas_cargas:
        await _validar_docentes_del_periodo(db, programa_id, req.to_periodo_id, {c.docente_id for c in nuevas_cargas})
        db.add_all(nuevas_cargas)
    await db.commit()
    return {"message": f"Se clonaron {len(nuevas_cargas)} registros de carga lectiva."}

@router.get("/docentes/{docente_id}/carga-lectiva", response_model=List[CargaLectivaResponse])
@router.get("/docente/{docente_id}/carga-lectiva", response_model=List[CargaLectivaResponse])
async def listar_carga_docente(
    docente_id: int, request: Request, response: Response, periodo_id: Optional[int] = None,
    programa_id: Optional[int] = None, db: AsyncSession = Depends(get_db),
):
    ident = await get_identity(request, db)
    await _validar_acceso_docente(ident, docente_id, db)
    response.headers["Cache-Control"] = "no-store"

    query = (
        select(CargaLectiva, ProgramaUnidad, ProgramaModulo.nombre, ProgramaModulo.periodo, ProgramaEstudio.nombre)
        .outerjoin(ProgramaUnidad, ProgramaUnidad.id == CargaLectiva.unidad_didactica_id)
        .outerjoin(ProgramaModulo, ProgramaModulo.id == ProgramaUnidad.programa_modulo_id)
        .outerjoin(ProgramaEstudio, ProgramaEstudio.id == CargaLectiva.programa_id)
        .where(CargaLectiva.docente_id == docente_id)
        .order_by(CargaLectiva.id)
    )
    if periodo_id is not None:
        query = query.where(CargaLectiva.periodo_id == periodo_id)
    if programa_id is not None:
        query = query.where(CargaLectiva.programa_id == programa_id)
    filas = await db.execute(query)

    return [
        CargaLectivaResponse(
            id=c.id,
            programa_id=c.programa_id,
            periodo_id=c.periodo_id,
            docente_id=c.docente_id,
            unidad_didactica_id=c.unidad_didactica_id,
            turno=c.turno,
            seccion=c.seccion,
            estado=c.estado,
            created_at=c.created_at,
            unidad_nombre=u.nombre if u else f"Unidad #{c.unidad_didactica_id}",
            programa_nombre=programa_nombre,
            modulo_nombre=modulo_nombre,
            ciclo=ciclo,
            creditos=u.creditos if u else None,
            horas=((u.horas_teoria or 0) + (u.horas_practica or 0)) if u else None,
        )
        for c, u, modulo_nombre, ciclo, programa_nombre in filas
    ]

@router.post("/programas/{programa_id}/horarios", response_model=HorarioPeriodoResponse, status_code=status.HTTP_201_CREATED)
async def crear_horario(programa_id: int, horario: HorarioPeriodoCreate, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para registrar horarios en este programa"
        )
    db_horario = HorarioPeriodo(**horario.model_dump(), programa_id=programa_id)
    db.add(db_horario)
    await db.commit()
    await db.refresh(db_horario)
    return db_horario

@router.get("/programas/{programa_id}/horarios", response_model=List[HorarioPeriodoResponse])
async def listar_horarios(programa_id: int, periodo_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(HorarioPeriodo)
        .where(HorarioPeriodo.programa_id == programa_id, HorarioPeriodo.periodo_id == periodo_id)
    )
    return result.scalars().all()

@router.delete("/programas/{programa_id}/carga-lectiva/{carga_id}")
async def eliminar_carga_lectiva(programa_id: int, carga_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para eliminar la carga lectiva de este programa"
        )

    result = await db.execute(select(CargaLectiva).where(CargaLectiva.id == carga_id, CargaLectiva.programa_id == programa_id))
    db_carga = result.scalar_one_or_none()
    if not db_carga:
        raise HTTPException(status_code=404, detail="Carga lectiva no encontrada")

    # Reglas de negocio para evitar saltarse el cierre de actas:
    # 1. No se puede eliminar si el acta ya está cerrada
    if getattr(db_carga, "acta_cerrada", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede eliminar la carga lectiva porque su acta se encuentra formalmente cerrada."
        )

    # 2. No se puede eliminar si ya existen registros de evaluación para esa unidad y periodo
    ev_res = await db.execute(
        text("SELECT 1 FROM evaluacion_registros WHERE unidad_didactica_id = :uid AND periodo_id = :pid LIMIT 1"),
        {"uid": db_carga.unidad_didactica_id, "pid": db_carga.periodo_id}
    )
    if ev_res.fetchone():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede eliminar la carga lectiva porque ya existen calificaciones registradas en esta unidad y periodo."
        )

    await db.delete(db_carga)
    await db.commit()
    return {"message": "Carga lectiva eliminada"}

@router.delete("/programas/{programa_id}/horarios/{horario_id}")
async def eliminar_horario(programa_id: int, horario_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(HorarioPeriodo).where(HorarioPeriodo.id == horario_id, HorarioPeriodo.programa_id == programa_id))
    db_horario = result.scalar_one_or_none()
    if not db_horario:
        raise HTTPException(status_code=404, detail="Horario no encontrado")
    await db.delete(db_horario)
    await db.commit()
    return {"message": "Horario eliminado"}

@router.put(
    "/programas/{programa_id}/carga-lectiva/{carga_id}",
    response_model=CargaLectivaResponse,
    status_code=status.HTTP_200_OK,
    summary="Actualizar asignación de carga lectiva",
    tags=["Coordinación - Carga Lectiva"]
)
async def actualizar_carga_lectiva(
    programa_id: int,
    carga_id: int,
    carga_in: CargaLectivaUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Actualiza los datos de una asignación de carga lectiva (docente, turno, sección, estado, fecha límite).
    """
    # 1. Autenticación y Autorización RBAC
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para gestionar la carga lectiva de este programa"
        )

    # 2. Validar existencia del programa de estudio
    prog_res = await db.execute(
        select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id)
    )
    if not prog_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Programa no encontrado"
        )

    # 3. Validar existencia de la carga lectiva en dicho programa
    result = await db.execute(
        select(CargaLectiva).where(
            CargaLectiva.id == carga_id,
            CargaLectiva.programa_id == programa_id
        )
    )
    db_carga = result.scalar_one_or_none()
    if not db_carga:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Carga lectiva no encontrada"
        )

    # 4. Candado de Cierre de Actas (Seguro Crítico Anti-Bypass)
    if getattr(db_carga, "acta_cerrada", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede modificar la carga lectiva porque su acta se encuentra formalmente cerrada."
        )

    # 5. Validar que el periodo académico permita modificaciones
    per_res = await db.execute(
        text("SELECT estado FROM periodos_academicos WHERE id = :pid"),
        {"pid": db_carga.periodo_id}
    )
    per_row = per_res.fetchone()
    if not per_row or per_row[0] in ("cerrado", "finalizado"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede modificar carga lectiva en un periodo académico cerrado o finalizado"
        )

    update_data = carga_in.model_dump(exclude_unset=True)

    if "docente_id" in update_data and update_data["docente_id"] != db_carga.docente_id:
        nuevo_docente = update_data["docente_id"]
        if nuevo_docente is None:
            raise HTTPException(422, detail="Indique el docente responsable de la carga lectiva.")
        await _validar_docentes_del_periodo(db, programa_id, db_carga.periodo_id, {nuevo_docente})

    # 7. Validar duplicidad de sección si fue modificada
    if "seccion" in update_data and update_data["seccion"] is not None:
        nueva_seccion = update_data["seccion"]
        if nueva_seccion != db_carga.seccion:
            duplicada = await db.execute(
                select(CargaLectiva).where(
                    CargaLectiva.programa_id == programa_id,
                    CargaLectiva.periodo_id == db_carga.periodo_id,
                    CargaLectiva.unidad_didactica_id == db_carga.unidad_didactica_id,
                    CargaLectiva.seccion == nueva_seccion,
                    CargaLectiva.id != carga_id
                )
            )
            if duplicada.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Esa unidad didáctica ya tiene un docente asignado en este periodo y sección"
                )

    # 8. Validar fecha límite no retroactiva
    if "fecha_limite_notas" in update_data and update_data["fecha_limite_notas"] is not None:
        if update_data["fecha_limite_notas"] < date.today():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="La fecha límite no puede ser anterior a la fecha actual"
            )

    # 9. Validar estado permitido
    if "estado" in update_data and update_data["estado"] is not None:
        if update_data["estado"] not in ("borrador", "revision", "publicado"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Estado inválido para la carga lectiva. Valores permitidos: borrador, revision, publicado"
            )

    # 10. Aplicar cambios a la entidad
    for key, value in update_data.items():
        setattr(db_carga, key, value)
    db_carga.updated_at = datetime.utcnow()

    # 11. Registrar evento Outbox para sincronización / auditoría
    event = OutboxEvent(
        event_type="programas.carga_lectiva.actualizada",
        payload={
            "carga_id": db_carga.id,
            "programa_id": db_carga.programa_id,
            "periodo_id": db_carga.periodo_id,
            "docente_id": db_carga.docente_id,
            "unidad_didactica_id": db_carga.unidad_didactica_id,
            "seccion": db_carga.seccion,
            "turno": db_carga.turno,
            "estado": db_carga.estado,
            "fecha_limite_notas": str(db_carga.fecha_limite_notas) if db_carga.fecha_limite_notas else None
        }
    )
    db.add(event)

    # 12. Commit asíncrono (sin db.refresh)
    await db.commit()

    # 13. Enriquecer metadatos para CargaLectivaResponse
    res_u = await db.execute(
        select(ProgramaUnidad).where(ProgramaUnidad.id == db_carga.unidad_didactica_id)
    )
    u = res_u.scalar_one_or_none()
    modulo_nombre = None
    ciclo = None
    if u and u.programa_modulo_id:
        res_m = await db.execute(
            select(ProgramaModulo).where(ProgramaModulo.id == u.programa_modulo_id)
        )
        m = res_m.scalar_one_or_none()
        if m:
            modulo_nombre = m.nombre
            ciclo = m.periodo

    res_p = await db.execute(
        select(ProgramaEstudio).where(ProgramaEstudio.id == db_carga.programa_id)
    )
    p = res_p.scalar_one_or_none()

    return CargaLectivaResponse(
        id=db_carga.id,
        programa_id=db_carga.programa_id,
        periodo_id=db_carga.periodo_id,
        docente_id=db_carga.docente_id,
        unidad_didactica_id=db_carga.unidad_didactica_id,
        turno=db_carga.turno,
        seccion=db_carga.seccion,
        estado=db_carga.estado,
        created_at=db_carga.created_at,
        updated_at=db_carga.updated_at,
        fecha_limite_notas=db_carga.fecha_limite_notas,
        acta_cerrada=db_carga.acta_cerrada,
        acta_cerrada_at=db_carga.acta_cerrada_at,
        acta_cerrada_por=db_carga.acta_cerrada_por,
        unidad_nombre=u.nombre if u else f"Unidad #{db_carga.unidad_didactica_id}",
        programa_nombre=p.nombre if p else None,
        modulo_nombre=modulo_nombre,
        ciclo=ciclo,
        creditos=u.creditos if u else None,
        horas=((u.horas_teoria or 0) + (u.horas_practica or 0)) if u else None
    )

@router.put(
    "/programas/{programa_id}/horarios/{horario_id}",
    response_model=HorarioPeriodoResponse,
    status_code=status.HTTP_200_OK,
    summary="Actualizar archivo, estado u observaciones de un horario",
    tags=["Coordinación - Horarios"]
)
async def actualizar_horario(
    programa_id: int,
    horario_id: int,
    horario_in: HorarioPeriodoUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Actualiza la URL del archivo, el estado o las observaciones de un horario de periodo.
    """
    # 1. Autenticación y Autorización RBAC
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para modificar horarios en este programa"
        )

    # 2. Validar existencia del programa
    prog_res = await db.execute(
        select(ProgramaEstudio).where(ProgramaEstudio.id == programa_id)
    )
    if not prog_res.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Programa no encontrado"
        )

    # 3. Validar existencia del horario en dicho programa
    result = await db.execute(
        select(HorarioPeriodo).where(
            HorarioPeriodo.id == horario_id,
            HorarioPeriodo.programa_id == programa_id
        )
    )
    db_horario = result.scalar_one_or_none()
    if not db_horario:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Horario no encontrado"
        )

    # 4. Validar que el periodo académico no esté cerrado o finalizado
    per_res = await db.execute(
        text("SELECT estado FROM periodos_academicos WHERE id = :pid"),
        {"pid": db_horario.periodo_id}
    )
    per_row = per_res.fetchone()
    if per_row and per_row[0] in ("cerrado", "finalizado"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede modificar el horario de un periodo académico cerrado o finalizado"
        )

    update_data = horario_in.model_dump(exclude_unset=True)

    # 5. Validar estado permitido
    if "estado" in update_data and update_data["estado"] is not None:
        if update_data["estado"] not in ("borrador", "revision", "publicado"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Estado inválido para el horario. Valores permitidos: borrador, revision, publicado"
            )

    # 6. Actualizar campos en el modelo
    for key, value in update_data.items():
        setattr(db_horario, key, value)
    db_horario.updated_at = datetime.utcnow()

    # 7. Registrar evento Outbox
    event = OutboxEvent(
        event_type="programas.horario.actualizado",
        payload={
            "horario_id": db_horario.id,
            "programa_id": db_horario.programa_id,
            "periodo_id": db_horario.periodo_id,
            "estado": db_horario.estado,
            "archivo_excel_url": db_horario.archivo_excel_url
        }
    )
    db.add(event)

    # 8. Commit asíncrono y respuesta directa
    await db.commit()
    return HorarioPeriodoResponse.model_validate(db_horario)

# --- Schemas Docente (Sílabo y Plan de Trabajo) ---
class SilaboBase(BaseModel):
    carga_lectiva_id: int
    estado: Optional[str] = "borrador"
    archivo_url: Optional[str] = None
    observaciones: Optional[str] = None

class SilaboCreate(SilaboBase):
    pass

class SilaboResponse(SilaboBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class PlanTrabajoDocenteBase(BaseModel):
    periodo_id: int
    programa_id: int
    docente_id: int
    estado: Optional[str] = "borrador"
    archivo_url: Optional[str] = None
    observaciones: Optional[str] = None

class PlanTrabajoDocenteCreate(PlanTrabajoDocenteBase):
    pass

class PlanTrabajoDocenteResponse(PlanTrabajoDocenteBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

# --- Endpoints Docente (Sílabos y Planes) ---
from models import Silabo, PlanTrabajoDocente

@router.post("/silabos", response_model=SilaboResponse, status_code=status.HTTP_201_CREATED)
async def crear_silabo(silabo: SilaboCreate, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)

    # 1. Obtener la carga lectiva para validar que exista y a qué docente pertenece
    res_cl = await db.execute(
        select(CargaLectiva).where(CargaLectiva.id == silabo.carga_lectiva_id)
    )
    db_cl = res_cl.scalar_one_or_none()
    if not db_cl:
        raise HTTPException(status_code=404, detail="Carga lectiva no encontrada")

    # 2. El usuario debe ser el docente de la carga, coordinador del programa o superadmin
    if not (es_superadmin(ident) or ident.user_id == db_cl.docente_id or es_coordinador_de(ident, db_cl.programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para registrar el sílabo de una carga lectiva que no le pertenece"
        )

    # 3. Upsert: Si ya existe sílabo para esta carga lectiva, actualizar datos en lugar de fallar
    res_silabo = await db.execute(
        select(Silabo).where(Silabo.carga_lectiva_id == silabo.carga_lectiva_id)
    )
    db_silabo = res_silabo.scalars().first()
    if db_silabo:
        db_silabo.archivo_url = silabo.archivo_url
        db_silabo.estado = silabo.estado or "presentado"
        if silabo.observaciones is not None:
            db_silabo.observaciones = silabo.observaciones
    else:
        db_silabo = Silabo(**silabo.model_dump())
        db.add(db_silabo)

    await db.commit()
    await db.refresh(db_silabo)
    return db_silabo

@router.get("/docente/{docente_id}/silabos", response_model=List[SilaboResponse])
async def listar_silabos_docente(docente_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    await _validar_acceso_docente(ident, docente_id, db)

    # Get silabos that belong to the teacher's carga lectiva
    result = await db.execute(
        select(Silabo)
        .join(CargaLectiva)
        .where(CargaLectiva.docente_id == docente_id)
    )
    return result.scalars().all()

@router.post("/planes-trabajo", response_model=PlanTrabajoDocenteResponse, status_code=status.HTTP_201_CREATED)
async def crear_plan_trabajo(plan: PlanTrabajoDocenteCreate, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)

    # El usuario autenticado debe ser el docente_id indicado en el plan, coordinador del programa o superadmin
    if not (es_superadmin(ident) or ident.user_id == plan.docente_id or es_coordinador_de(ident, plan.programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para crear un plan de trabajo asignado a otro docente"
        )

    # Verificar que el docente posea al menos una carga lectiva en ese programa y periodo
    res_cl = await db.execute(
        text("SELECT 1 FROM carga_lectiva WHERE docente_id = :did AND programa_id = :pid AND periodo_id = :per LIMIT 1"),
        {"did": plan.docente_id, "pid": plan.programa_id, "per": plan.periodo_id}
    )
    if not es_superadmin(ident) and not res_cl.fetchone():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El docente no tiene carga lectiva asignada en el programa y periodo seleccionados"
        )

    # Upsert: Si ya existe plan de trabajo para el docente, programa y periodo, actualizarlo
    res_plan = await db.execute(
        select(PlanTrabajoDocente).where(
            PlanTrabajoDocente.docente_id == plan.docente_id,
            PlanTrabajoDocente.periodo_id == plan.periodo_id,
            PlanTrabajoDocente.programa_id == plan.programa_id
        )
    )
    db_plan = res_plan.scalars().first()
    if db_plan:
        db_plan.archivo_url = plan.archivo_url
        db_plan.estado = plan.estado or "presentado"
        if plan.observaciones is not None:
            db_plan.observaciones = plan.observaciones
    else:
        db_plan = PlanTrabajoDocente(**plan.model_dump())
        db.add(db_plan)

    await db.commit()
    await db.refresh(db_plan)
    return db_plan

@router.get("/docente/{docente_id}/planes-trabajo", response_model=List[PlanTrabajoDocenteResponse])
async def listar_planes_trabajo(docente_id: int, periodo_id: int, request: Request, programa_id: Optional[int] = None, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    await _validar_acceso_docente(ident, docente_id, db)

    query = select(PlanTrabajoDocente).where(
        PlanTrabajoDocente.docente_id == docente_id,
        PlanTrabajoDocente.periodo_id == periodo_id
    )
    if programa_id:
        query = query.where(PlanTrabajoDocente.programa_id == programa_id)
    result = await db.execute(query)
    return result.scalars().all()

# --- Endpoints Coordinador (Revisión) ---
@router.get("/programas/{programa_id}/silabos", response_model=List[SilaboResponse])
async def listar_silabos_programa(programa_id: int, periodo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para revisar los sílabos de este programa"
        )

    result = await db.execute(
        select(Silabo)
        .join(CargaLectiva)
        .where(CargaLectiva.programa_id == programa_id, CargaLectiva.periodo_id == periodo_id)
    )
    return result.scalars().all()

@router.get("/programas/{programa_id}/planes-trabajo", response_model=List[PlanTrabajoDocenteResponse])
async def listar_planes_programa(programa_id: int, periodo_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para revisar los planes de trabajo de este programa"
        )

    result = await db.execute(
        select(PlanTrabajoDocente)
        .where(PlanTrabajoDocente.programa_id == programa_id, PlanTrabajoDocente.periodo_id == periodo_id)
    )
    return result.scalars().all()

@router.put("/silabos/{silabo_id}", response_model=SilaboResponse)
async def actualizar_silabo(
    silabo_id: int,
    silabo: SilaboUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Actualiza los datos de un sílabo existente (archivo_url, estado, observaciones).
    Permitido para:
    - Docente asignado a la carga lectiva asociada (no puede autoaprobar).
    - Coordinador del programa de estudio de la carga.
    - Superadministrador.
    """
    ident = await get_identity(request, db)

    # 1. Obtener sílabo y carga_lectiva asociada para resolver permisos
    result = await db.execute(
        select(Silabo, CargaLectiva.programa_id, CargaLectiva.docente_id)
        .outerjoin(CargaLectiva, Silabo.carga_lectiva_id == CargaLectiva.id)
        .where(Silabo.id == silabo_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Silabo no encontrado"
        )

    db_silabo, programa_id, docente_id = row

    # 2. Comprobación de Titularidad y Autorización RBAC / BOLA
    es_docente_propietario = (docente_id is not None and ident.user_id == docente_id)
    es_coordinador_programa = (programa_id is not None and es_coordinador_de(ident, programa_id))

    if not (es_superadmin(ident) or es_coordinador_programa or es_docente_propietario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para modificar este sílabo"
        )

    # 3. Blindaje de Regla de Negocio: Un docente no puede autoaprobar su propio sílabo
    if es_docente_propietario and not (es_superadmin(ident) or es_coordinador_programa):
        if silabo.estado == "aprobado":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el coordinador de programa puede aprobar el sílabo"
            )

    # 4. Actualización selectiva de atributos presentes en el body
    update_data = silabo.model_dump(exclude_unset=True)
    if "archivo_url" in update_data:
        db_silabo.archivo_url = update_data["archivo_url"]
    if "estado" in update_data and update_data["estado"] is not None:
        db_silabo.estado = update_data["estado"]
    if "observaciones" in update_data:
        db_silabo.observaciones = update_data["observaciones"]

    db_silabo.updated_at = datetime.utcnow()

    # 5. Persistencia y respuesta directa (sin db.refresh para evitar InvalidRequestError en NullPool/joins)
    await db.commit()
    return SilaboResponse.model_validate(db_silabo)

@router.put("/silabos/{silabo_id}/estado", response_model=SilaboResponse)
async def actualizar_estado_silabo(silabo_id: int, estado: str, request: Request, observaciones: str = None, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)

    # 1. Obtener el sílabo y la carga_lectiva asociada para deducir el programa_id
    result = await db.execute(
        select(Silabo, CargaLectiva.programa_id)
        .join(CargaLectiva, Silabo.carga_lectiva_id == CargaLectiva.id)
        .where(Silabo.id == silabo_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Silabo no encontrado")
    
    db_silabo, programa_id = row

    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para modificar el estado del sílabo de este programa"
        )
    
    db_silabo.estado = estado
    if observaciones is not None:
        db_silabo.observaciones = observaciones
    db_silabo.updated_at = datetime.utcnow()
        
    await db.commit()
    return SilaboResponse.model_validate(db_silabo)

@router.put("/planes-trabajo/{plan_id}", response_model=PlanTrabajoDocenteResponse)
async def actualizar_plan_trabajo(
    plan_id: int,
    plan: PlanTrabajoDocenteUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Actualiza los datos de un plan de trabajo docente existente (archivo_url, estado, observaciones).
    Permitido para:
    - Docente titular del plan (no puede autoaprobar).
    - Coordinador del programa de estudio al que pertenece el plan.
    - Superadministrador.
    """
    ident = await get_identity(request, db)

    # 1. Obtener el plan de trabajo por ID
    result = await db.execute(
        select(PlanTrabajoDocente).where(PlanTrabajoDocente.id == plan_id)
    )
    db_plan = result.scalar_one_or_none()
    if not db_plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan de trabajo no encontrado"
        )

    # 2. Comprobación de Titularidad y Autorización RBAC / BOLA
    es_docente_propietario = (ident.user_id == db_plan.docente_id)
    es_coordinador_programa = (db_plan.programa_id is not None and es_coordinador_de(ident, db_plan.programa_id))

    if not (es_superadmin(ident) or es_coordinador_programa or es_docente_propietario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para modificar este plan de trabajo"
        )

    # 3. Blindaje de Regla de Negocio: Un docente no puede autoaprobar su propio plan
    if es_docente_propietario and not (es_superadmin(ident) or es_coordinador_programa):
        if plan.estado == "aprobado":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el coordinador de programa puede aprobar el plan de trabajo"
            )

    # 4. Actualización selectiva de campos
    update_data = plan.model_dump(exclude_unset=True)
    if "archivo_url" in update_data:
        db_plan.archivo_url = update_data["archivo_url"]
    if "estado" in update_data and update_data["estado"] is not None:
        db_plan.estado = update_data["estado"]
    if "observaciones" in update_data:
        db_plan.observaciones = update_data["observaciones"]

    db_plan.updated_at = datetime.utcnow()

    # 5. Persistencia y respuesta directa (sin db.refresh para evitar InvalidRequestError)
    await db.commit()
    return PlanTrabajoDocenteResponse.model_validate(db_plan)

@router.put("/planes-trabajo/{plan_id}/estado", response_model=PlanTrabajoDocenteResponse)
async def actualizar_estado_plan(plan_id: int, estado: str, request: Request, observaciones: str = None, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)

    result = await db.execute(select(PlanTrabajoDocente).where(PlanTrabajoDocente.id == plan_id))
    db_plan = result.scalar_one_or_none()
    if not db_plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")
        
    if not (es_superadmin(ident) or es_coordinador_de(ident, db_plan.programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para modificar el estado del plan de trabajo de este programa"
        )

    db_plan.estado = estado
    if observaciones is not None:
        db_plan.observaciones = observaciones
    db_plan.updated_at = datetime.utcnow()
        
    await db.commit()
    return PlanTrabajoDocenteResponse.model_validate(db_plan)

# --- Schemas y Endpoints (Tutorías) ---
class TutoriaBase(BaseModel):
    periodo_id: int
    docente_id: int
    ciclo: int
    observaciones: Optional[str] = None

class TutoriaCreate(TutoriaBase):
    pass

class TutoriaResponse(TutoriaBase):
    id: int
    programa_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

from models import Tutoria

@router.post("/programas/{programa_id}/tutorias", response_model=TutoriaResponse, status_code=status.HTTP_201_CREATED)
async def crear_tutoria(programa_id: int, tutoria: TutoriaCreate, request: Request, db: AsyncSession = Depends(get_db)):
    ident = await get_identity(request, db)
    if not (es_superadmin(ident) or es_coordinador_de(ident, programa_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tiene permisos para gestionar tutorías en este programa"
        )

    result = await db.execute(
        select(Tutoria)
        .where(Tutoria.programa_id == programa_id, Tutoria.periodo_id == tutoria.periodo_id, Tutoria.ciclo == tutoria.ciclo)
    )
    db_tutoria = result.scalar_one_or_none()
    
    if db_tutoria:
        db_tutoria.docente_id = tutoria.docente_id
        db_tutoria.observaciones = tutoria.observaciones
        await db.commit()
        await db.refresh(db_tutoria)
        return db_tutoria
        
    db_tutoria = Tutoria(**tutoria.model_dump(), programa_id=programa_id)
    db.add(db_tutoria)
    await db.commit()
    await db.refresh(db_tutoria)
    return db_tutoria

@router.get("/programas/{programa_id}/tutorias", response_model=List[TutoriaResponse])
async def listar_tutorias(programa_id: int, periodo_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Tutoria)
        .where(Tutoria.programa_id == programa_id, Tutoria.periodo_id == periodo_id)
    )
    return result.scalars().all()

@router.delete("/programas/{programa_id}/tutorias/{tutoria_id}")
async def eliminar_tutoria(programa_id: int, tutoria_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tutoria).where(Tutoria.id == tutoria_id, Tutoria.programa_id == programa_id))
    db_tutoria = result.scalar_one_or_none()
    if not db_tutoria:
        raise HTTPException(status_code=404, detail="Tutoría no encontrada")
    await db.delete(db_tutoria)
    await db.commit()
    return {"message": "Tutoría eliminada"}

