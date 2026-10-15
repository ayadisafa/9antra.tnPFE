import sqlite3

# Adjust database name if needed
db_path = "app.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 1. List all tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = [t[0] for t in cursor.fetchall()]
print(f"=== Tables in DB: {tables} ===\n")

# 2. Print contents of 'programs' table
print("=== PROGRAMS TABLE ===")
cursor.execute("SELECT id, name, price_online, price_onsite, is_approved, embedding FROM programs;")
columns = [desc[0] for desc in cursor.description]
print(f"{' | '.join(columns)}")
print("-" * 60)

for row in cursor.fetchall():
    # Truncate raw embedding string for display readability
    row_list = list(row)
    if row_list[5]:  # embedding field
        row_list[5] = f"{row_list[5][:20]}..."
    print(" | ".join(str(val) for val in row_list))

conn.close()