import psycopg2
from db_env import connect_db  # credenciales desde .env

conn = connect_db()
cur = conn.cursor()

print('=== ESTRUCTURA DE TABLAS ===')

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='programas_estudio' ORDER BY ordinal_position;")
print('programas_estudio:', [r[0] for r in cur.fetchall()])

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_role_permissions' ORDER BY ordinal_position;")
print('core_role_permissions:', [r[0] for r in cur.fetchall()])

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_permissions' ORDER BY ordinal_position;")
print('core_permissions:', [r[0] for r in cur.fetchall()])

cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='core_user_roles' ORDER BY ordinal_position;")
print('core_user_roles:', [r[0] for r in cur.fetchall()])

cur.close()
conn.close()