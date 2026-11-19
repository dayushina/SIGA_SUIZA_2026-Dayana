#!/bin/sh
set -e

echo "=========================================="
echo "🚀 Iniciando SIGA Backend (Railway)"
echo "=========================================="

PORT="${PORT:-8000}"
export PORT
echo "📡 Puerto principal: $PORT"

# PIDs de los modulos lanzados en segundo plano: solo matamos los nuestros
# (kill 0 alcanzaba a todo el grupo de procesos, incluido este script y su padre).
CHILD_PIDS=""
cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [ -n "$CHILD_PIDS" ]; then
        echo "🛑 Deteniendo servicios internos..."
        kill $CHILD_PIDS 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

fail() {
    echo "❌ ERROR DE CONFIGURACION: $1" >&2
    exit 1
}

# Valores por defecto de produccion (sobrescribibles desde las Variables de Railway).
# En Railway no hay NATS: sin esto cada modulo reintenta conectarse y llena los logs.
export ENVIRONMENT="${ENVIRONMENT:-production}"
export DEBUG="${DEBUG:-false}"
export ENABLE_NATS="${ENABLE_NATS:-false}"
echo "🏷️ Entorno: $ENVIRONMENT | DEBUG=$DEBUG | NATS=$ENABLE_NATS"

# Validar configuracion obligatoria ANTES de arrancar nada.
if [ "${#SECRET_KEY}" -lt 32 ]; then
    fail "SECRET_KEY no definida o con menos de 32 caracteres. Agregala en Variables del servicio (genera una con: python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
fi
if [ -z "$DATABASE_URL" ] && [ -z "$DB_URL" ] && [ -z "$DB_PASSWORD" ]; then
    fail "No hay conexion a la base de datos. Agrega la variable DATABASE_URL=\${{Postgres.DATABASE_URL}}."
fi

if [ -n "$DATABASE_URL" ]; then
    echo "📦 DATABASE_URL detectada de Railway."
    export DB_URL="$DATABASE_URL"
fi

# Snapshot de la BD versionado (bootstrap/seed). Por defecto (SIGA_SEED_MODE=skip)
# NO se restaura: una BD reconstruida arranca solo con el superadmin que crea el
# Core. Restaurarlo es opt-in (auto | replace). Ver bootstrap/restore_seed.py.
echo "🗄️ Verificando snapshot de la base de datos (SIGA_SEED_MODE=${SIGA_SEED_MODE:-skip})..."
python -m bootstrap.restore_seed || fail "no se pudo preparar la base de datos (ver el mensaje [seed] ERROR de arriba)."

if [ "$SKIP_MIGRATIONS" != "true" ]; then
    echo "🔄 Ejecutando migraciones de base de datos..."
    alembic upgrade head || fail "alembic upgrade head fallo (revisa el error de arriba: conexion a la BD o migracion invalida)."
    echo "✅ Migraciones aplicadas."
fi

export SIGA_MODULE_USUARIOS_URL="http://127.0.0.1:8001"
export SIGA_MODULE_PLANES_ESTUDIO_URL="http://127.0.0.1:8002"
export SIGA_MODULE_PROGRAMAS_ESTUDIO_URL="http://127.0.0.1:8005"
export SIGA_MODULE_GESTION_ACADEMICA_URL="http://127.0.0.1:8006"
export SIGA_MODULE_AUDITORIA_URL="http://127.0.0.1:8007"
export SIGA_MODULE_EVALUACION_URL="http://127.0.0.1:8008"
export SIGA_MODULE_ADMISION_URL="http://127.0.0.1:8009"

echo "⚙️ Levantando 7 microservicios internos..."
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --app-dir modules/mod-usuarios &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8002 --app-dir modules/mod-planes-estudio &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8005 --app-dir modules/mod-programas-estudio &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8006 --app-dir modules/mod-gestion-academica &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8007 --app-dir modules/mod-auditoria &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8008 --app-dir modules/mod-evaluacion &
CHILD_PIDS="$CHILD_PIDS $!"
python -m uvicorn main:app --host 127.0.0.1 --port 8009 --app-dir modules/mod-admision &
CHILD_PIDS="$CHILD_PIDS $!"

sleep 2

echo "🌐 Iniciando Core Gateway en 0.0.0.0:$PORT..."
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
