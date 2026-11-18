import psycopg2
from db_env import connect_db  # credenciales desde .env

conn = connect_db()
cur = conn.cursor()

print('=== VERIFICACION FINAL ===')
cur.execute("""
    SELECT 'core_users' AS tabla, COUNT(*) AS registros FROM core_users
    UNION ALL
    SELECT 'estudiantes', COUNT(*) FROM estudiantes
    UNION ALL
    SELECT 'programas_estudio', COUNT(*) FROM programas_estudio
    UNION ALL
    SELECT 'core_roles', COUNT(*) FROM core_roles
    UNION ALL
    SELECT 'core_permissions', COUNT(*) FROM core_permissions;
""")
for tabla, registros in cur.fetchall():
    print(f'  {tabla}: {registros}')

cur.close()
conn.close()