from fastapi import APIRouter, HTTPException, Depends, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
import bcrypt
from database import get_db, OutboxEvent
from models import Usuario, Rol, Permiso, PerfilPersonal
from pathlib import Path
import sys

backend_dir = str(Path(__file__).resolve().parents[2])
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from shared.programas import programa_implementado


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

router = APIRouter()

ROLES_PROGRAMA = {
    "secretaria_programa": "SECRETARIA_PROGRAMA",
    "coordinador_programa": "JEFE_AREA",
    "docente": "DOCENTE_AULA",
    "estudiante": "ESTUDIANTE",
}

# Schemas Pydantic
class RolBase(BaseModel):
    name: str
    description: Optional[str] = None

class RolCreate(RolBase):
    pass

class RolResponse(RolBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class UsuarioBase(BaseModel):
    email: EmailStr
    full_name: str
    is_active: bool = True
    is_superuser: bool = False

class UsuarioCreate(UsuarioBase):
    password: str
    role_ids: List[int] = []
    programa_estudio_id: Optional[int] = None
    periodos_docencia_ids: list[int] = Field(default_factory=list)

class UsuarioUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None
    role_ids: Optional[List[int]] = None
    programa_estudio_id: Optional[int] = None
    periodos_docencia_ids: Optional[list[int]] = None

class UsuarioResponse(UsuarioBase):
    id: int
    created_at: datetime
    roles: List[RolResponse] = []
    programa_estudio_id: Optional[int] = None
    periodos_docencia_ids: list[int] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class PerfilPersonalBase(BaseModel):
    condicion_laboral: str
    numero_resolucion: Optional[str] = None
    fecha_fin_contrato: Optional[datetime] = None
    cargo_funcional: str
    profesion_titulo: Optional[str] = None
    programa_estudio_id: Optional[int] = None

class PerfilPersonalResponse(PerfilPersonalBase):
    id: int
    user_id: int
    model_config = ConfigDict(from_attributes=True)

class PersonalCreate(BaseModel):
    # User fields
    email: EmailStr
    full_name: str
    password: str
    # Profile fields
    condicion_laboral: str
    numero_resolucion: Optional[str] = None
    fecha_fin_contrato: Optional[datetime] = None
    cargo_funcional: str
    profesion_titulo: Optional[str] = None
    programa_estudio_id: Optional[int] = None

class PersonalUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    condicion_laboral: Optional[str] = None
    numero_resolucion: Optional[str] = None
    fecha_fin_contrato: Optional[datetime] = None
    cargo_funcional: Optional[str] = None
    profesion_titulo: Optional[str] = None
    programa_estudio_id: Optional[int] = None

class PersonalResponse(BaseModel):
    usuario: UsuarioResponse
    perfil: PerfilPersonalResponse
    model_config = ConfigDict(from_attributes=True)

# Health Check
@router.get("/health")
async def health_check():
    return {"status": "healthy", "module": "mod-usuarios"}


# --- CRUD Roles ---
@router.post("/roles", response_model=RolResponse, status_code=status.HTTP_201_CREATED)
async def crear_rol(rol: RolCreate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rol).where(Rol.name == rol.name))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El rol ya existe")

    db_rol = Rol(**rol.model_dump())
    db.add(db_rol)
    await db.flush()

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="rol.creado",
        payload={"id": db_rol.id, "name": db_rol.name, "metadata": {"request_id": request_id}}
    )
    db.add(event)
    await db.commit()
    await db.refresh(db_rol)
    return db_rol

@router.get("/roles", response_model=List[RolResponse])
async def obtener_roles(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rol).order_by(Rol.name))
    return result.scalars().all()


# --- CRUD Usuarios ---
def _usuarios_con_perfil():
    return select(Usuario).options(selectinload(Usuario.roles), selectinload(Usuario.perfil_personal))


async def _asignar_programa(db, usuario, programa_id, roles):
    cargo = next((cargo for rol, cargo in ROLES_PROGRAMA.items() if rol in roles), None)
    if cargo is None:
        return
    await programa_implementado(db, programa_id)
    perfil = await db.scalar(select(PerfilPersonal).where(PerfilPersonal.user_id == usuario.id))
    if perfil is None:
        perfil = PerfilPersonal(
            user_id=usuario.id, cargo_funcional=cargo, condicion_laboral="NO_INFORMADO",
        )
        db.add(perfil)
    elif perfil.condicion_laboral == "NO_INFORMADO":
        perfil.cargo_funcional = cargo
    perfil.programa_estudio_id = programa_id


async def _guardar_periodos_docencia(db, usuario_id, programa_id, roles, periodos):
    periodos = sorted(set(periodos))
    if periodos:
        if "docente" not in roles:
            raise HTTPException(422, detail="Solo las cuentas con rol docente pueden habilitarse para un periodo.")
        await programa_implementado(db, programa_id)
        existentes = (await db.scalars(text(
            "SELECT id FROM periodos_academicos WHERE id = ANY(:periodos)"
        ), {"periodos": periodos})).all()
        if set(existentes) != set(periodos):
            raise HTTPException(422, detail="Uno o más periodos lectivos seleccionados no existen.")
    await db.execute(text("DELETE FROM docentes_periodo WHERE docente_id = :id"), {"id": usuario_id})
    if periodos:
        await db.execute(text("""
            INSERT INTO docentes_periodo (docente_id, programa_id, periodo_id)
            VALUES (:docente, :programa, :periodo)
        """), [{"docente": usuario_id, "programa": programa_id, "periodo": p} for p in periodos])


async def _cargar_periodos_docencia(db, usuarios):
    if not usuarios:
        return usuarios
    filas = await db.execute(text("""
        SELECT d.docente_id, array_agg(d.periodo_id ORDER BY d.periodo_id)
        FROM docentes_periodo d
        JOIN perfiles_personal p ON p.user_id = d.docente_id AND p.programa_estudio_id = d.programa_id
        WHERE d.docente_id = ANY(:ids)
        GROUP BY d.docente_id
    """), {"ids": [u.id for u in usuarios]})
    periodos_por_docente = dict(filas.tuples().all())
    for usuario in usuarios:
        usuario.periodos_docencia_ids = periodos_por_docente.get(usuario.id, [])
    return usuarios


@router.post("/usuarios", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
async def crear_usuario(usuario: UsuarioCreate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Usuario).where(Usuario.email == usuario.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    user_data = usuario.model_dump(exclude={"password", "role_ids", "programa_estudio_id", "periodos_docencia_ids"})
    hashed_password = _hash_password(usuario.password)
    
    db_user = Usuario(**user_data, hashed_password=hashed_password, roles=[])
    
    # Asignar roles
    if usuario.role_ids:
        roles_result = await db.execute(select(Rol).where(Rol.id.in_(usuario.role_ids)))
        db_user.roles = roles_result.scalars().all()

    db.add(db_user)
    await db.flush()
    roles = {rol.name for rol in db_user.roles}
    await _asignar_programa(db, db_user, usuario.programa_estudio_id, roles)
    if usuario.periodos_docencia_ids:
        await _guardar_periodos_docencia(db, db_user.id, usuario.programa_estudio_id, roles, usuario.periodos_docencia_ids)

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="usuario.creado",
        payload={
            "id": db_user.id,
            "email": db_user.email,
            "full_name": db_user.full_name,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    
    # Recargar con relaciones
    result = await db.execute(
        _usuarios_con_perfil().where(Usuario.id == db_user.id)
    )
    guardado = result.scalar_one()
    await _cargar_periodos_docencia(db, [guardado])
    return guardado

@router.get("/usuarios", response_model=List[UsuarioResponse])
async def obtener_usuarios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        _usuarios_con_perfil().order_by(Usuario.created_at.desc())
    )
    return await _cargar_periodos_docencia(db, result.scalars().all())

@router.get("/usuarios/{usuario_id}", response_model=UsuarioResponse)
async def obtener_usuario(usuario_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        _usuarios_con_perfil().where(Usuario.id == usuario_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    await _cargar_periodos_docencia(db, [user])
    return user

@router.put("/usuarios/{usuario_id}", response_model=UsuarioResponse)
async def actualizar_usuario(usuario_id: int, usuario: UsuarioUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        _usuarios_con_perfil().where(Usuario.id == usuario_id).with_for_update()
    )
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    update_data = usuario.model_dump(exclude_unset=True)
    programa_anterior = db_user.programa_estudio_id
    programa_id = update_data.pop("programa_estudio_id", programa_anterior)
    periodos = update_data.pop("periodos_docencia_ids", None)
    era_docente = any(rol.name == "docente" for rol in db_user.roles)
    
    if "email" in update_data and update_data["email"] != db_user.email:
        email_check = await db.execute(select(Usuario).where(Usuario.email == update_data["email"]))
        if email_check.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="El email ya está en uso por otro usuario")

    # Actualizar roles
    if "role_ids" in update_data:
        role_ids = update_data.pop("role_ids")
        roles_result = await db.execute(select(Rol).where(Rol.id.in_(role_ids or [])))
        db_user.roles = roles_result.scalars().all()
    roles = {rol.name for rol in db_user.roles}
    if {"role_ids", "programa_estudio_id", "periodos_docencia_ids"} & usuario.model_fields_set:
        await _asignar_programa(db, db_user, programa_id, roles)
    if periodos is not None or (era_docente and ("docente" not in roles or programa_id != programa_anterior)):
        await _guardar_periodos_docencia(db, db_user.id, programa_id, roles, periodos or [])

    # Actualizar campos
    for key, value in update_data.items():
        setattr(db_user, key, value)

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="usuario.actualizado",
        payload={"id": db_user.id, "metadata": {"request_id": request_id}}
    )
    db.add(event)
    await db.commit()
    actualizado = await db.scalar(_usuarios_con_perfil().where(Usuario.id == usuario_id).execution_options(populate_existing=True))
    await _cargar_periodos_docencia(db, [actualizado])
    return actualizado

@router.delete("/usuarios/{usuario_id}")
async def eliminar_usuario(usuario_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Usuario).where(Usuario.id == usuario_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Eliminación lógica
    db_user.is_active = False
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="usuario.eliminado",
        payload={"id": db_user.id, "metadata": {"request_id": request_id}}
    )
    db.add(event)
    await db.commit()
    return {"message": "Usuario desactivado correctamente"}


# --- CRUD Personal Institucional ---
@router.post("/personal", response_model=PersonalResponse, status_code=status.HTTP_201_CREATED)
async def registrar_personal(personal: PersonalCreate, request: Request, db: AsyncSession = Depends(get_db)):
    await programa_implementado(db, personal.programa_estudio_id)
    # Validar email
    result = await db.execute(select(Usuario).where(Usuario.email == personal.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El email ya está registrado")

    # 1. Crear Usuario
    hashed_password = _hash_password(personal.password)
    db_user = Usuario(
        email=personal.email,
        full_name=personal.full_name,
        hashed_password=hashed_password,
        is_active=True,
        is_superuser=False
    )
    
    # Asignar rol según cargo
    rol_names = ["docente"]
    if personal.cargo_funcional == "SECRETARIA_PROGRAMA":
        rol_names = ["secretaria_programa"]
    elif personal.cargo_funcional == "JEFE_AREA":
        rol_names = ["docente", "coordinador_programa"]
        
    roles_result = await db.execute(select(Rol).where(Rol.name.in_(rol_names)))
    db_user.roles = roles_result.scalars().all()
        
    db.add(db_user)
    await db.flush() # Para obtener db_user.id

    # 2. Crear Perfil Personal
    fecha_fin = personal.fecha_fin_contrato
    if fecha_fin and getattr(fecha_fin, 'tzinfo', None):
        fecha_fin = fecha_fin.replace(tzinfo=None)
        
    db_perfil = PerfilPersonal(
        user_id=db_user.id,
        condicion_laboral=personal.condicion_laboral,
        numero_resolucion=personal.numero_resolucion,
        fecha_fin_contrato=fecha_fin,
        cargo_funcional=personal.cargo_funcional,
        profesion_titulo=personal.profesion_titulo,
        programa_estudio_id=personal.programa_estudio_id
    )
    db.add(db_perfil)
    
    # 3. Emitir evento
    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="personal.creado",
        payload={
            "user_id": db_user.id,
            "cargo": personal.cargo_funcional,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    
    await db.commit()
    
    # Recargar data
    user_result = await db.execute(
        select(Usuario).options(selectinload(Usuario.roles)).where(Usuario.id == db_user.id)
    )
    final_user = user_result.scalar_one()
    
    return {"usuario": final_user, "perfil": db_perfil}


@router.put("/personal/{perfil_id}", response_model=PersonalResponse)
async def actualizar_personal(perfil_id: int, personal: PersonalUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PerfilPersonal).options(selectinload(PerfilPersonal.usuario)).where(PerfilPersonal.id == perfil_id)
    )
    db_perfil = result.scalar_one_or_none()
    if not db_perfil:
        raise HTTPException(status_code=404, detail="Perfil de personal no encontrado")

    db_user = db_perfil.usuario
    await db.execute(select(Usuario.id).where(Usuario.id == db_user.id).with_for_update())
    await db.refresh(db_perfil)
    await db.refresh(db_user, attribute_names=["email", "roles"])
    update_data = personal.model_dump(exclude_unset=True)
    if db_perfil.cargo_funcional == "ESTUDIANTE":
        raise HTTPException(404, detail="Perfil de personal no encontrado")
    programa = update_data.get("programa_estudio_id", db_perfil.programa_estudio_id)
    await programa_implementado(db, programa)
    if programa != db_perfil.programa_estudio_id or update_data.get("cargo_funcional") == "SECRETARIA_PROGRAMA":
        await db.execute(text("DELETE FROM docentes_periodo WHERE docente_id = :id"), {"id": db_user.id})

    # Actualizar campos de Usuario
    if "email" in update_data and update_data["email"] != db_user.email:
        email_check = await db.execute(select(Usuario).where(Usuario.email == update_data["email"]))
        if email_check.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="El email ya está en uso")
        db_user.email = update_data["email"]
    
    if "full_name" in update_data:
        db_user.full_name = update_data["full_name"]
    
    if "password" in update_data and update_data["password"]:
        db_user.hashed_password = _hash_password(update_data["password"])

    # Si cambió el cargo, actualizar los roles
    if "cargo_funcional" in update_data and update_data["cargo_funcional"] != db_perfil.cargo_funcional:
        rol_names = ["docente"]
        if update_data["cargo_funcional"] == "SECRETARIA_PROGRAMA":
            rol_names = ["secretaria_programa"]
        elif update_data["cargo_funcional"] == "JEFE_AREA":
            rol_names = ["docente", "coordinador_programa"]
            
        roles_result = await db.execute(select(Rol).where(Rol.name.in_(rol_names)))
        db_user.roles = roles_result.scalars().all()

    # Actualizar campos de Perfil
    for key in ["condicion_laboral", "numero_resolucion", "fecha_fin_contrato", "cargo_funcional", "profesion_titulo", "programa_estudio_id"]:
        if key in update_data:
            if key == "fecha_fin_contrato" and update_data[key] and getattr(update_data[key], 'tzinfo', None):
                update_data[key] = update_data[key].replace(tzinfo=None)
            setattr(db_perfil, key, update_data[key])

    request_id = request.headers.get("X-Request-ID", "unknown")
    event = OutboxEvent(
        event_type="personal.actualizado",
        payload={
            "user_id": db_user.id,
            "cargo": db_perfil.cargo_funcional,
            "metadata": {"request_id": request_id}
        }
    )
    db.add(event)
    await db.commit()
    
    # Recargar para la respuesta
    final_result = await db.execute(
        select(Usuario).options(selectinload(Usuario.roles)).where(Usuario.id == db_user.id)
    )
    final_user = final_result.scalar_one()
    return {"usuario": final_user, "perfil": db_perfil}


@router.get("/personal", response_model=List[PersonalResponse])
async def obtener_personal(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PerfilPersonal).where(PerfilPersonal.cargo_funcional != "ESTUDIANTE")
        .options(selectinload(PerfilPersonal.usuario).selectinload(Usuario.roles))
    )
    perfiles = result.scalars().all()
    
    response = []
    for p in perfiles:
        response.append({
            "usuario": p.usuario,
            "perfil": p
        })
    return response
