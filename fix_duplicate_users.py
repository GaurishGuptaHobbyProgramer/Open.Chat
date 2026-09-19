import sqlite3

DB_PATH = r"C:\Users\Gaurish Gupta\OneDrive\Desktop\OpenChat\chat.db"

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("SELECT id, username FROM users ORDER BY id")
rows = cur.fetchall()

seen_usernames = set()
remove_ids = set()

for user_id, username in rows:
    normalized_username = (username or "").strip().lower()
    if normalized_username and normalized_username in seen_usernames:
        remove_ids.add(user_id)
    else:
        if normalized_username:
            seen_usernames.add(normalized_username)

if remove_ids:
    placeholders = ", ".join("?" for _ in remove_ids)
    cur.execute(f"DELETE FROM users WHERE id IN ({placeholders})", tuple(sorted(remove_ids)))
    conn.commit()
    print(f"Removed duplicate accounts: {len(remove_ids)}")
else:
    print("No duplicate accounts found.")

cur.execute("SELECT COUNT(*) FROM users")
print("Remaining users:", cur.fetchone()[0])

conn.close()
