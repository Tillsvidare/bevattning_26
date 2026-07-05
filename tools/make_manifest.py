#!/usr/bin/env python3
"""Generera esp32/manifest.json för OTA-uppdatering.

Hashar koden i esp32/ (inklusive lib/) och skriver manifestet som
enheten jämför mot vid knapp-uppdatering (esp32/ota_update.py).
Kör efter varje ändring, före push till GitHub.
"""

import hashlib
import json
import time
from pathlib import Path

ESP32 = Path(__file__).resolve().parent.parent / "esp32"

# Enhetslokala filer som OTA aldrig får skriva över, plus sådant som
# inte hör hemma på enheten.
EXCLUDE = {"manifest.json", "config.json", "config.json.example",
           "schedule.json", "settings.json"}


def main():
    files = {}
    for path in sorted(ESP32.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ESP32).as_posix()
        if rel in EXCLUDE or "__pycache__" in rel or rel.endswith(".tmp"):
            continue
        files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"version": time.strftime("%Y-%m-%d %H:%M"), "files": files}
    out = ESP32 / "manifest.json"
    out.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print("Skrev %s (%d filer, version %s)"
          % (out, len(files), manifest["version"]))


if __name__ == "__main__":
    main()
