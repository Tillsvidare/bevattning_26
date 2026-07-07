"""Engångsimport av ventilhistorik från tiden före multi-tenant.

Två lägen (ren sqlite3, inga beroenden — kan köras direkt på servern):

  Adoptera rader utan enhet i samma databas (efter migreringen):
    python tools/import_legacy_events.py --db irrigation.db --device-id bv-xxxxxxxx

  Kopiera från en gammal databasfil (t.ex. lokal dev-databas):
    python tools/import_legacy_events.py --db irrigation.db \\
        --source gamla-irrigation.db --device-id bv-xxxxxxxx
"""

import argparse
import sqlite3

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--db", required=True, help="måldatabasen (irrigation.db)")
parser.add_argument("--device-id", required=True, help="enheten som får historiken")
parser.add_argument("--source", help="gammal databasfil att kopiera från")
args = parser.parse_args()

con = sqlite3.connect(args.db)
try:
    owner = con.execute(
        "SELECT id FROM devices WHERE id = ?", (args.device_id,)
    ).fetchone()
    if owner is None:
        raise SystemExit(f"Enheten {args.device_id} finns inte i {args.db}")

    if args.source:
        src = sqlite3.connect(args.source)
        rows = src.execute(
            "SELECT valve_id, state, ts, received_at FROM valve_events ORDER BY ts"
        ).fetchall()
        src.close()
        con.executemany(
            "INSERT INTO valve_events (device_id, valve_id, state, ts, received_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(args.device_id, *row) for row in rows],
        )
        print(f"{len(rows)} händelser kopierade från {args.source}")
    else:
        n = con.execute(
            "UPDATE valve_events SET device_id = ? WHERE device_id IS NULL",
            (args.device_id,),
        ).rowcount
        print(f"{n} enhetslösa händelser adopterade")
    con.commit()
finally:
    con.close()
