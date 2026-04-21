import sqlite3
import json

old_db = sqlite3.connect("data/seoul/cafedata.db")
test_db = sqlite3.connect("test_clean.db")

def check_merge(name, old_ids):
    print(f"\nChecking {name}...")
    source_ids = []
    for old_id in old_ids:
        row = old_db.execute("SELECT source_ids FROM clean_cafes WHERE id=?", (old_id,)).fetchone()
        if row:
            source_ids.extend(json.loads(row[0]))
    
    print(f"  Source IDs to look for: {source_ids}")
    
    # Check in test_db
    found_clean_ids = set()
    for sid in source_ids:
        row = test_db.execute("SELECT belongs_to_cafe_id FROM scraped_cafes WHERE id=?", (sid,)).fetchone()
        if row and row[0]:
            found_clean_ids.add(row[0])
            
    if len(found_clean_ids) == 1:
        print("  ✅ SUCCESS: All merged into a single clean_cafe!")
        clean_id = list(found_clean_ids)[0]
        row = test_db.execute("SELECT name, providers FROM clean_cafes WHERE id=?", (clean_id,)).fetchone()
        print(f"  Merged Cafe: {row[0]} - Providers: {row[1]}")
    elif len(found_clean_ids) == 0:
        print("  ❌ ERROR: None found in test DB.")
    else:
        print(f"  ❌ FAILED: Split across multiple clean_cafes: {found_clean_ids}")

check_merge("Starbucks", ["da6525d8-8d64-4548-85ee-1c712a4153df", "239dcdb9-ca80-415d-bd7f-a63017144f69"])
check_merge("Compose Cafe", ["07eb6c5b-4e8e-40de-93cc-3710c3e37361", "4362d5cd-5ca6-4d8d-9f42-a4cc75cccd60"])
check_merge("Looks similar", ["89e89c2e-2e8b-422e-bf89-a66875b1e554", "0e309e64-ed65-460f-a808-bb866af31187"])
check_merge("The Coffee Bean", ["dd5e46c5-cab7-4670-a558-a47f4f769505", "e73d6bcb-c13e-4758-9901-75a07576e4df"])

