import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT u.email, pp.programa_estudio_id FROM core_users u JOIN perfiles_personal pp ON u.id = pp.user_id WHERE u.email LIKE 'secretaria.%' LIMIT 10;")
rows = cur.fetchall()
for row in rows:
    print(f'{row[0]} -> programa_id: {row[1]}')
cur.close()
conn.close()