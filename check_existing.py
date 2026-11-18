import psycopg2
from db_env import connect_db  # credenciales desde .env
conn = connect_db()
cur = conn.cursor()
cur.execute("SELECT email FROM core_users WHERE email LIKE '%@siga.edu'")
emails = [r[0] for r in cur.fetchall()]
for e in emails:
    print(e)
cur.close()
conn.close()