import sqlite3

conn = sqlite3.connect('chat.db')
cur = conn.cursor()
with open('db_dump.txt', 'w', encoding='utf-8') as f:
    f.write('TABLES\n')
    for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        f.write(str(row) + '\n')
    f.write('\nUSERS\n')
    for row in cur.execute('SELECT id, username, password_hash, created_at FROM users ORDER BY id'):
        f.write(str(row) + '\n')
    f.write('\nCOUNT\n')
    f.write(str(cur.execute('SELECT COUNT(*) FROM users').fetchone()[0]))
conn.close()
