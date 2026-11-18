import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
cur = conn.cursor()
cur.execute("SELECT email, full_name FROM core_users WHERE email LIKE '%@siga.edu' AND (email LIKE 'jefe.%' OR email LIKE 'secretaria.%' OR email LIKE 'docente.%') ORDER BY email;")
for email, name in cur.fetchall():
    print(f'{email} -> {name}')
cur.close()
conn.close()