import sqlite3
import json

conn = sqlite3.connect('unipulse_checkpoint.db')
cursor = conn.cursor()

cursor.execute("SELECT count(1) FROM checkpoints")
total = cursor.fetchone()[0]
print(f"\n==========================================")
print(f"  TOTAL CHECKPOINTED RECORDS: {total}")
print(f"==========================================")

if total > 0:
    cursor.execute("SELECT record_json FROM checkpoints")
    sources = {}
    for (raw_json,) in cursor.fetchall():
        data = json.loads(raw_json)
        src = data.get("enrichment_source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    print("\n--- ENRICHMENT SOURCE BREAKDOWN ---")
    for src, count in sources.items():
        print(f"  * {src:<25} : {count} records")
print()