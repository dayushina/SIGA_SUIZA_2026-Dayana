import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT email, full_name, programa_ids FROM core_users WHERE email LIKE 'secretaria.%' OR email LIKE 'jefe.%' OR email LIKE 'docente.%' LIMIT 10;")
rows = cur.fetchall()
for row in rows:
    email, name, pid = row
    print(f'{email}: programa_ids = {pid} (tipo: {type(pid).__name__})')
cur.close()
conn.close()