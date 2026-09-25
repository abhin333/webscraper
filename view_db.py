import sqlite3

conn = sqlite3.connect("exhibitors.db")
cursor = conn.cursor()

cursor.execute("SELECT id, name, hallNo,boothNo, country, profile_url FROM exhibitors LIMIT 10;")
rows = cursor.fetchall()

for row in rows:
    print(row)

conn.close()