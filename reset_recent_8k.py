import json
import shutil
from datetime import datetime
from pathlib import Path

from django.db import transaction

from watcher.knowledge_base.models import Filing
from watcher.services.download_registry import DownloadRegistry


ACCESSIONS = {
    "0001104659-26-107526",  # AMZN
    "0001104659-26-107511",  # FDX
    "0001193125-26-389753",  # ORCL
    "0000004962-26-000353",  # AXP
    "0001193125-26-391921",  # COF
    "0000927628-26-000132",  # COF
    "0000927628-26-000134",  # COF
}


filings = list(
    Filing.objects
    .filter(
        accession_number__in=ACCESSIONS,
        form="8-K",
    )
    .select_related("company")
    .prefetch_related("documents")
)

print("TARGET FILINGS:", len(filings))

if len(filings) != 7:
    raise RuntimeError(
        f"SAFETY STOP: expected exactly 7 filings, found {len(filings)}"
    )


# ------------------------------------------------------------
# Create backup
# ------------------------------------------------------------

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

backup_root = (
    Path(r"D:\SECFILE")
    / "_replay_backups"
    / f"recent_8k_reset_{stamp}"
)

backup_root.mkdir(
    parents=True,
    exist_ok=False,
)

print("BACKUP:", backup_root)


# ------------------------------------------------------------
# Backup registry
# ------------------------------------------------------------

registry = DownloadRegistry()

shutil.copy2(
    registry.path,
    backup_root / "downloaded_files.json",
)


# ------------------------------------------------------------
# Collect registry keys and local files
# ------------------------------------------------------------

manifest = []
active_paths = set()
registry_keys = set()

for filing in filings:

    key = registry.make_key(
        filing.company.cik,
        filing.accession_number,
        filing.sequence,
    )

    registry_keys.add(key)

    if filing.local_path:
        active_paths.add(
            Path(filing.local_path)
        )

    document_paths = []

    for document in filing.documents.all():

        if document.local_path:

            path = Path(
                document.local_path
            )

            active_paths.add(path)

            document_paths.append(
                str(path)
            )

    manifest.append(
        {
            "filing_id": filing.id,
            "ticker": filing.company.ticker,
            "cik": filing.company.cik,
            "accession": filing.accession_number,
            "sequence": filing.sequence,
            "form": filing.form,
            "filing_date": (
                filing.filing_date.isoformat()
                if filing.filing_date
                else None
            ),
            "accepted_at": (
                filing.accepted_at.isoformat()
                if filing.accepted_at
                else None
            ),
            "entry_session": (
                filing.entry_session.isoformat()
                if filing.entry_session
                else None
            ),
            "local_path": filing.local_path,
            "document_paths": document_paths,
            "registry_key": key,
        }
    )


# ------------------------------------------------------------
# Save manifest
# ------------------------------------------------------------

(
    backup_root
    / "filings.json"
).write_text(
    json.dumps(
        manifest,
        indent=2,
    ),
    encoding="utf-8",
)


# ------------------------------------------------------------
# Backup physical files
# ------------------------------------------------------------

files_backup = (
    backup_root
    / "files"
)

files_backup.mkdir(
    parents=True,
    exist_ok=True,
)

for index, path in enumerate(
    sorted(active_paths),
    start=1,
):

    if not path.exists():
        continue

    target = (
        files_backup
        / f"{index:03d}_{path.name}"
    )

    shutil.copy2(
        path,
        target,
    )


# ------------------------------------------------------------
# Remove exact registry entries
# ------------------------------------------------------------

records = registry._load()

new_records = [
    record
    for record in records
    if record not in registry_keys
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
# Delete Filing rows
#
# Related filing data using CASCADE is removed automatically.
# Company rows are NOT deleted.
# ------------------------------------------------------------

filing_ids = [
    filing.id
    for filing in filings
]

with transaction.atomic():

    deleted_count, breakdown = (
        Filing.objects
        .filter(
            id__in=filing_ids
        )
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
# Remove exact active local files
# ------------------------------------------------------------

removed_files = 0

for path in active_paths:

    if (
        path.exists()
        and path.is_file()
    ):
        path.unlink()
        removed_files += 1


print(
    "PHYSICAL FILES REMOVED:",
    removed_files,
)

print()
print("RESET COMPLETE")
print("Recovery backup:")
print(backup_root)