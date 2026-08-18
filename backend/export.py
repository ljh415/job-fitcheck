"""데이터 백업/내보내기 공용 유틸. routers/settings.py(전체 ZIP export)와
routers/companies.py(삭제 전 자동 백업)에서 함께 사용한다."""
import io
import zipfile
from datetime import datetime

from config import settings


def build_export_zip(buf: io.BytesIO, include_pdf: bool = False, include_log: bool = False) -> None:
    """지정된 옵션에 따라 데이터 파일을 buf에 ZIP으로 압축한다."""
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(settings.companies_dir.glob("*.md")):
            zf.write(path, f"companies/{path.name}")
        for path in sorted(settings.companies_dir.glob("*.raw.txt")):
            zf.write(path, f"companies/{path.name}")
        profile = settings.candidate_profile_path
        if profile.exists():
            zf.write(profile, profile.name)
        criteria = settings.data_dir / "eval_criteria.md"
        if criteria.exists():
            zf.write(criteria, criteria.name)
        candidate_note = settings.data_dir / "candidate_note.md"
        if candidate_note.exists():
            zf.write(candidate_note, candidate_note.name)
        app_db = settings.data_dir / "app.db"
        if app_db.exists():
            zf.write(app_db, app_db.name)
        if include_pdf:
            for path in sorted(settings.uploads_dir.glob("*.pdf")):
                zf.write(path, f"uploads/{path.name}")
        if include_log:
            log_path = settings.data_dir / "usage_log.jsonl"
            if log_path.exists():
                zf.write(log_path, log_path.name)


def save_backup_zip() -> None:
    """삭제 직전 자동 백업 — 타임스탬프 파일로 저장, 최근 5개만 유지."""
    backup_dir = settings.data_dir / "backup"
    backup_dir.mkdir(exist_ok=True)
    buf = io.BytesIO()
    build_export_zip(buf)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    (backup_dir / f"backup_{ts}.zip").write_bytes(buf.getvalue())
    existing = sorted(backup_dir.glob("backup_*.zip"), key=lambda p: p.name)
    for old in existing[:-5]:
        old.unlink()
