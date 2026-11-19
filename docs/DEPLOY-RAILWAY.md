# Despliegue en Railway (base de datos + backend)

Un `git push` a `main` despliega el backend. Por defecto (`SIGA_SEED_MODE=skip`)
un Postgres vacio o reconstruido arranca **solo con el superadmin**
(`SIGA_ADMIN_EMAIL` / `SIGA_ADMIN_PASSWORD`): no se crean cuentas de prueba ni
datos. Restaurar el snapshot cifrado del repositorio es **opt-in**
(`SIGA_SEED_MODE=auto` o `replace`, ver abajo).

## Como funciona

```
git push ──> Railway construye Dockerfile (raiz) ──> start_railway.sh
   1. valida SECRET_KEY y la conexion a la BD
   2. python -m bootstrap.restore_seed      <- snapshot de la BD (solo si SIGA_SEED_MODE lo pide)
   3. alembic upgrade head                   <- migraciones nuevas, si las hay
   4. 7 modulos internos + Core Gateway en $PORT (healthcheck /health)
```

- **Snapshot**: `siga_backend/bootstrap/seed/siga_seed.enc` = la BD completa
  (esquema + datos + secuencias), comprimida y cifrada (Fernet/AES) con
  `SIGA_SEED_KEY`. El repositorio es publico: sin la clave el archivo es ilegible.
  `siga_seed.manifest` (texto) identifica el contenido sin exponer datos.
- **Restauracion atomica**: una sola transaccion; verifica filas por tabla antes
  de confirmar. Si algo falla no queda nada a medias y el despliegue se detiene
  con un mensaje `[seed] ERROR: ...`.
- **Produccion es la fuente de verdad**: tras la primera carga, los siguientes
  despliegues NO sobrescriben la BD aunque el snapshot del repo cambie.
- **Cuentas de prueba**: ya no se crean en ningun entorno. Si una BD antigua o un
  snapshot las trae, en produccion las que conservan su contrasena publica se
  desactivan y el superadmin recibe `SIGA_ADMIN_PASSWORD`.
- Probado contra PostgreSQL 13, 17 y 18 (contenido identico tabla por tabla).

## Configuracion inicial (una sola vez)

Un repositorio no puede crear servicios de Railway: la base de datos se crea en
el panel. En el proyecto de Railway:

1. **+ Create -> Database -> PostgreSQL** (nombre: `Postgres`).
2. **+ Create -> GitHub Repo** -> este repositorio, rama `main`, *Root Directory* vacio.
3. Servicio del backend -> **Variables**:

   | Variable | Valor |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `SECRET_KEY` | >= 32 caracteres, distinta de la local |
   | `SIGA_SEED_KEY` | la misma de tu `.env` local |
   | `SIGA_ADMIN_PASSWORD` | >= 12 caracteres (nueva clave de `admin@siga.edu`) |
   | `SIGA_ADMIN_EMAIL` | opcional (por defecto `admin@siga.edu`) |

4. **Settings -> Networking -> Generate Domain**.

Desde ahi, cada `git push` a `main` se despliega solo.

## Actualizar el snapshot con la BD local

```powershell
siga_backend\venv\Scripts\python siga_backend\tools\export_seed.py
git add siga_backend/bootstrap/seed
git commit -m "chore(seed): actualizar snapshot de la BD"
git push
```

Si la BD local no cambio, el exportador no reescribe nada.

## Politica de restauracion (`SIGA_SEED_MODE`)

| Valor | Comportamiento |
|---|---|
| `skip` (defecto) | No restaura nada. Una BD vacia o reconstruida arranca **solo con el superadmin** (`SIGA_ADMIN_EMAIL` / `SIGA_ADMIN_PASSWORD`). |
| `auto` | Opt-in. Restaura si la BD esta vacia o solo tiene lo que genero un arranque previo sin datos. Nunca toca una BD con datos reales. |
| `replace` | Opt-in. Reemplaza la BD cuando el snapshot del repo es distinto del ultimo aplicado. Util **antes** de salir a produccion; **quitala despues** o cada snapshot nuevo pisara produccion. |

El historial de cargas queda en la tabla `siga_meta.seed_history`.

## Problemas frecuentes

| Log de Railway | Solucion |
|---|---|
| `falta SIGA_SEED_KEY` | Copiala de `.env` a Variables. |
| `SIGA_SEED_KEY no corresponde a este archivo` | La clave no es la que cifro el snapshot: usa la de tu `.env` o vuelve a exportar. |
| `El superadmin ... tiene la contrasena publica` | Define `SIGA_ADMIN_PASSWORD` (>= 12 caracteres). |
| `la BD ya tiene datos que no provienen del snapshot` | Esperado si la BD tiene datos reales. Para pisarlos: `SIGA_SEED_MODE=replace` una vez. |
| `SECRET_KEY no definida o con menos de 32 caracteres` | Defínela en Variables. |

## Limitaciones

- Las fotos subidas (`modules/mod-gestion-academica/uploads`) viven en el disco
  del contenedor, que Railway borra en cada despliegue. Para conservarlas,
  agrega un **Volume** al servicio montado en
  `/app/modules/mod-gestion-academica/uploads`.
- Si `SIGA_SEED_KEY` se filtra, cualquier version del snapshot que siga en el
  historial de git se puede descifrar. Rotar la clave protege solo los
  snapshots nuevos.
