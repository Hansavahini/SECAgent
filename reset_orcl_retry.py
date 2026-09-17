import json
import shutil
from datetime import datetime
from pathlib import Path

from django.db import transaction

from watcher.knowledge_base.models import Filing
from watcher.services.download_registry import DownloadRegistry


ACCESSION = "0001193125-26-389753"


filings = list(
    Filing.objects
    .filter(
        accession_number=ACCESSION,
        form="8-K",
    )
    .select_related("company")
    .prefetch_related("documents")
)

print("TARGET FILINGS:", len(filings))

if len(filings) != 1:
    raise RuntimeError(
        f"SAFETY STOP: expected exactly 1 ORCL filing, found {len(filings)}"
    )

filing = filings[0]

print("Ticker    :", filing.company.ticker)
print("CIK       :", filing.company.cik)
print("Accession :", filing.accession_number)
print("Sequence  :", filing.sequence)
print("Path      :", filing.local_path)


# ------------------------------------------------------------
# Backup
# ------------------------------------------------------------

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

backup_root = (
    Path(r"D:\SECFILE")
    / "_replay_backups"
    / f"orcl_retry_{stamp}"
)

backup_root.mkdir(
    parents=True,
    exist_ok=False,
)

print("BACKUP:", backup_root)

registry = DownloadRegistry()

shutil.copy2(
    registry.path,
    backup_root / "downloaded_files.json",
)


# ------------------------------------------------------------
# Collect physical files
# ------------------------------------------------------------

active_paths = set()

if filing.local_path:
    active_paths.add(
        Path(filing.local_path)
    )

for document in filing.documents.all():
    if document.local_path:
        active_paths.add(
            Path(document.local_path)
        )


files_backup = backup_root / "files"

files_backup.mkdir(
    parents=True,
    exist_ok=True,
)

for index, path in enumerate(
    sorted(active_paths),
    start=1,
):
    if path.exists() and path.is_file():
        shutil.copy2(
            path,
            files_backup / f"{index:03d}_{path.name}",
        )


# ------------------------------------------------------------
# Remove registry entry
# ------------------------------------------------------------

registry_key = registry.make_key(
    filing.company.cik,
    filing.accession_number,
    filing.sequence,
)

records = registry._load()

new_records = [
    record
    for record in records
    if record != registry_key
]

registry.path.write_text(
    json.dumps(
        new_records,
        indent=4,
    ),
    encoding="utf-8",
)

print(
    "REGISTRY ENTRIES REMOVED:",
    len(records) - len(new_records),
)


# ------------------------------------------------------------
# Delete filing + cascading DB records
# ------------------------------------------------------------

filing_id = filing.id

with transaction.atomic():
    deleted_count, breakdown = (
        Filing.objects
        .filter(id=filing_id)
        .delete()
    )

print(
    "DATABASE OBJECTS DELETED:",
    deleted_count,
)

print(
    "DELETE BREAKDOWN:",
    breakdown,
)


# ------------------------------------------------------------
# Remove active physical filing
# ------------------------------------------------------------

removed_files = 0

for path in active_paths:
    if path.exists() and path.is_file():
        path.unlink()
        removed_files += 1

print(
    "PHYSICAL FILES REMOVED:",
    removed_files,
)

print()
print("ORCL RESET COMPLETE")
print("Recovery backup:")
print(backup_root)