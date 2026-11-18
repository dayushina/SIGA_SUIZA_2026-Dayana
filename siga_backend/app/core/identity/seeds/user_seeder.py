from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Dict, Any
import os
import bcrypt

from .base_seeder import BaseSeeder, SeederFatalError
from ..models import CoreUser, CoreRole
from ...config import settings

PRODUCTION_ENVIRONMENTS = {"production", "prod"}
MIN_ADMIN_PASSWORD_LENGTH = 12


def is_production() -> bool:
    return (settings.environment or "").strip().lower() in PRODUCTION_ENVIRONMENTS

# Cuentas de desarrollo: sus contrasenas son publicas (estan en el repositorio),
# por eso NUNCA se crean en produccion.
DEV_USERS: List[Dict[str, Any]] = [
    {
        "email": "admin@siga.edu",
        "password": "admin123",
        "full_name": "Super Admin",
        "roles": ["superadmin"],
        "is_superuser": True
    },
    {
        "email": "tesoreria@siga.edu",
        "password": "tesoreria123",
        "full_name": "Caja Principal",
        "roles": ["caja_tesoreria"],
        "is_superuser": False
    },
    {
        "email": "secretaria@siga.edu",
        "password": "secretaria123",
        "full_name": "Secretaría Académica",
        "roles": ["secretaria_academica"],
        "is_superuser": False
    },
    {
        "email": "secretaria_prog@siga.edu",
        "password": "secretariaprog123",
        "full_name": "Secretaría de Programa (Computación)",
        "roles": ["secretaria_programa"],
        "is_superuser": False
    },
    {
        "email": "coordinador@siga.edu",
        "password": "coordinador123",
        "full_name": "Jefatura de Programa (Computación)",
        "roles": ["coordinador_programa"],
        "is_superuser": False
    },
    {
        "email": "admision@siga.edu",
        "password": "admision123",
        "full_name": "Oficina de Admisión",
        "roles": ["admin_admision"],
        "is_superuser": False
    },
    {
        "email": "docente@siga.edu", 
        "password": "docente123",
        "full_name": "Docente Titular",
        "roles": ["docente"],
        "is_superuser": False
    },
    {
        "email": "estudiante@siga.edu",
        "password": "estudiante123", 
        "full_name": "Estudiante Promedio",
        "roles": ["estudiante"],
        "is_superuser": False
    }
]


class UserSeeder(BaseSeeder):
    """Seeder para usuarios del sistema"""
    
    def get_dependencies(self) -> List[str]:
        return ["RoleSeeder"]
    
    async def should_run(self) -> bool:
        result = await self.db.execute(select(CoreUser))
        existing_users = result.scalars().all()
        return len(existing_users) == 0
    
    def get_users_data(self) -> List[Dict[str, Any]]:
        """Desarrollo: cuentas de prueba. Produccion: solo el superadmin definido por entorno."""
        if not is_production():
            return DEV_USERS

        email = os.environ.get("SIGA_ADMIN_EMAIL", "").strip() or "admin@siga.edu"
        password = os.environ.get("SIGA_ADMIN_PASSWORD", "")
        public_passwords = {u["password"] for u in DEV_USERS}
        if len(password) < MIN_ADMIN_PASSWORD_LENGTH or password in public_passwords:
            raise SeederFatalError(
                "Base de datos sin usuarios en produccion: define SIGA_ADMIN_PASSWORD "
                f"(minimo {MIN_ADMIN_PASSWORD_LENGTH} caracteres) para crear el superadmin inicial."
            )
        return [{
            "email": email,
            "password": password,
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
        
        return created_users