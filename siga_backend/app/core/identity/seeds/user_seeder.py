from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Dict, Any
import os
import secrets
import bcrypt

from .base_seeder import BaseSeeder, SeederFatalError
from ..models import CoreUser, CoreRole
from ...config import settings

PRODUCTION_ENVIRONMENTS = {"production", "prod"}
MIN_ADMIN_PASSWORD_LENGTH = 12
DEFAULT_ADMIN_EMAIL = "admin@siga.edu"


def is_production() -> bool:
    return (settings.environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS


# Cuentas de prueba que versiones anteriores creaban en desarrollo, con sus
# contrasenas PUBLICAS (estan en el repositorio). Ya NO se crean en ningun
# entorno; la lista se conserva solo para que bootstrap/restore_seed.py pueda
# detectarlas y desactivarlas en bases de datos antiguas o snapshots.
LEGACY_PUBLIC_ACCOUNTS: List[Dict[str, Any]] = [
    {"email": "admin@siga.edu", "password": "admin123"},
    {"email": "tesoreria@siga.edu", "password": "tesoreria123"},
    {"email": "secretaria@siga.edu", "password": "secretaria123"},
    {"email": "secretaria_prog@siga.edu", "password": "secretariaprog123"},
    {"email": "coordinador@siga.edu", "password": "coordinador123"},
    {"email": "admision@siga.edu", "password": "admision123"},
    {"email": "docente@siga.edu", "password": "docente123"},
    {"email": "estudiante@siga.edu", "password": "estudiante123"},
]
PUBLIC_PASSWORDS = {u["password"] for u in LEGACY_PUBLIC_ACCOUNTS}


class UserSeeder(BaseSeeder):
    """Crea el UNICO usuario inicial del sistema: el superadministrador.

    Politica (igual en desarrollo, Docker y produccion/Railway):
    - Solo actua si core_users esta vacia (BD recien construida).
    - Email: SIGA_ADMIN_EMAIL (por defecto admin@siga.edu).
    - Contrasena: SIGA_ADMIN_PASSWORD (>= 12 caracteres, no publica).
        * Produccion: obligatoria; sin ella el Core no arranca.
        * Desarrollo: si falta, se genera una aleatoria y se muestra UNA vez en el log.
    Los demas usuarios se crean desde el sistema (Administracion).
    """

    def get_dependencies(self) -> List[str]:
        return ["RoleSeeder"]

    async def should_run(self) -> bool:
        result = await self.db.execute(select(CoreUser.id).limit(1))
        return result.first() is None

    def _admin_password(self) -> str:
        password = os.environ.get("SIGA_ADMIN_PASSWORD", "")
        if not password:
            if is_production():
                raise SeederFatalError(
                    "Base de datos sin usuarios en produccion: define SIGA_ADMIN_PASSWORD "
                    f"(minimo {MIN_ADMIN_PASSWORD_LENGTH} caracteres) para crear el superadmin inicial."
                )
            password = secrets.token_urlsafe(18)
            self.generated_password = password
            return password
        if len(password) < MIN_ADMIN_PASSWORD_LENGTH or password in PUBLIC_PASSWORDS:
            raise SeederFatalError(
                f"SIGA_ADMIN_PASSWORD es debil o publica: usa al menos {MIN_ADMIN_PASSWORD_LENGTH} "
                "caracteres y no una contrasena publicada en el repositorio."
            )
        return password

    def get_users_data(self) -> List[Dict[str, Any]]:
        self.generated_password = None
        email = os.environ.get("SIGA_ADMIN_EMAIL", "").strip() or DEFAULT_ADMIN_EMAIL
        return [{
            "email": email,
            "password": self._admin_password(),
            "full_name": "Super Admin",
            "roles": ["superadmin"],
            "is_superuser": True,
        }]

    async def run(self) -> List[CoreUser]:
        """Crea usuarios iniciales del sistema"""
        if not await self.should_run():
            self.log_warning("Usuarios ya existen, saltando...")
            return []
        
        # Obtener roles CON la sesión activa
        result = await self.db.execute(select(CoreRole))
        roles = {role.name: role for role in result.scalars().all()}
        
        users_data = self.get_users_data()
        
        created_users = []
        user_roles_info = []  # Para guardar info de roles SIN acceder a relaciones lazy
        
        for user_data in users_data:
            # Hash password
            hashed_password = bcrypt.hashpw(
                user_data["password"].encode('utf-8'), 
                bcrypt.gensalt()
            ).decode('utf-8')
            
            # Crear usuario
            user = CoreUser(
                email=user_data["email"],
                full_name=user_data["full_name"],
                hashed_password=hashed_password,
                is_active=True,
                is_superuser=user_data["is_superuser"]
            )
            
            # Asignar roles y guardar info para logging
            assigned_roles = []
            for role_name in user_data["roles"]:
                if role_name in roles:
                    user.roles.append(roles[role_name])
                    assigned_roles.append(role_name)
            
            self.db.add(user)
            created_users.append(user)
            user_roles_info.append((user_data["email"], assigned_roles))
        
        await self.db.commit()
        
        # 🆕 EVITAR ACCEDER A RELACIONES LAZY - Usar la info que ya tenemos
        self.log_success(f"Creados {len(created_users)} usuarios")
        for email, roles_list in user_roles_info:
            self.log_success(f"  👤 {email} - Roles: {roles_list}")
        if self.generated_password:
            self.log_warning(
                "SIGA_ADMIN_PASSWORD no esta definida: contrasena GENERADA para el superadmin "
                f"(se muestra solo esta vez, guardala y cambiala al entrar): {self.generated_password}"
            )
        
        return created_users