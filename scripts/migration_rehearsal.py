"""Rehearse the production migration on a disposable PostgreSQL server (CI only).

1. Rebuild the production state: public code ``PROD_PATH`` (5da6fe0) creates its tables and migrates to
   20261117, one user is created, then the Sprint 4 code ``S4_PATH`` (a0d691b) creates its Enterprise tables
   with AUTO_CREATE_TABLES, as was done against production during testing.
2. Compare that replica with the production fingerprint (MD5 of ``scripts/schema_fingerprint.sql``, read-only
   query against Supabase on 2026-11-17).
3. Run ``alembic upgrade head`` with this branch, then verify the revision, RLS, the preserved user, the schema
   against a clean install, readiness, and that the public code still works on the upgraded schema (rollback).

Refuses to run against anything but a local server. Writes a report and fingerprints to ``REHEARSAL_OUT``.
"""
import difflib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = (ROOT / "scripts" / "schema_fingerprint.sql").read_text(encoding="utf-8")
ENTERPRISE = ("enterprise", "organization", "forecast_emission_saves")
HEAD = "20261123_enterprise_commercial"


def run(cwd, url, *args, code=None):
    env = {**os.environ, "DATABASE_URL": url, "APP_ENV": "development", "SECRET_KEY": "rehearsal-only",
           "AUTO_CREATE_TABLES": "false", "PYTHONPATH": str(cwd)}
    command = [sys.executable, "-c", code] if code else [sys.executable, "-m", *args]
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode:
        raise RuntimeError(f"{' '.join(command[:3])} en {cwd} falló:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def fingerprint(url):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return [row[0] for row in conn.execute(text(FINGERPRINT))]
    finally:
        engine.dispose()


def digest(lines, part=None):
    def enterprise(line):
        table = line.split("|")[1]
        return table.startswith(ENTERPRISE[:2]) or table == ENTERPRISE[2]
    chosen = [line for line in lines if part is None or enterprise(line) == (part == "enterprise")]
    return hashlib.md5("\n".join(sorted(chosen, key=lambda s: s.encode())).encode()).hexdigest()


def scalar(url, sql):
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql)).scalar()
    finally:
        engine.dispose()


CREATE_TABLES = """
from app.db import get_engine
from app.models.orm import Base
from app.repositories.factory import enable_rls
engine = get_engine()
Base.metadata.create_all(engine)
enable_rls(engine)
"""

CREATE_USER = """
from app.db import get_engine
from app.repositories.sql import SQLRepository
print(SQLRepository(get_engine()).create_user("rehearsal_user", "rehearsal@example.test", "unused")["id"])
"""

ROLLBACK_SMOKE = """
from app.db import get_engine
from app.repositories.sql import SQLRepository
repo = SQLRepository(get_engine())
uid = repo.create_user("rollback_user", "rollback@example.test", "unused")["id"]
assert repo.get_user(uid)["username"] == "rollback_user"
print("ok")
"""

READY = """
from app import create_app
from app.controllers.commercial_controller import readiness
app = create_app()
with app.app_context():
    print(readiness())
"""


def main():
    admin_url = make_url(os.environ["REHEARSAL_ADMIN_URL"])
    if admin_url.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("El ensayo solo corre contra un servidor PostgreSQL local y desechable.")
    prod_path, s4_path = Path(os.environ["PROD_PATH"]), Path(os.environ["S4_PATH"])
    expected = json.loads(os.environ["EXPECTED_PRODUCTION"])
    out = Path(os.environ.get("REHEARSAL_OUT", "rehearsal"))
    out.mkdir(parents=True, exist_ok=True)

    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        for name in ("rehearsal_replica", "rehearsal_clean"):
            conn.exec_driver_sql(f"DROP DATABASE IF EXISTS {name}")
            conn.exec_driver_sql(f"CREATE DATABASE {name}")
    admin.dispose()
    replica = admin_url.set(database="rehearsal_replica").render_as_string(hide_password=False)
    clean = admin_url.set(database="rehearsal_clean").render_as_string(hide_password=False)
    report = {"checks": {}}

    # 1. Production state.
    run(prod_path, replica, code=CREATE_TABLES)
    run(prod_path, replica, "alembic", "upgrade", "20261117_auth_sessions")
    user_id = int(run(prod_path, replica, code=CREATE_USER).strip())
    run(s4_path, replica, code=CREATE_TABLES)
    before = fingerprint(replica)
    (out / "replica_before.txt").write_text("\n".join(before) + "\n", encoding="utf-8")
    observed = {"n": len(before), "total": digest(before), "enterprise": digest(before, "enterprise"),
                "base": digest(before, "base")}
    report["replica_vs_production"] = {"expected": expected, "observed": observed}
    report["checks"]["replica_matches_production"] = observed == expected
    report["checks"]["replica_revision_20261117"] = (
        scalar(replica, "SELECT version_num FROM alembic_version") == "20261117_auth_sessions")

    # 2. The migration under test.
    migration = run(ROOT, replica, "alembic", "upgrade", "head")
    report["alembic_output"] = migration[-2000:]
    after = fingerprint(replica)
    (out / "replica_after.txt").write_text("\n".join(after) + "\n", encoding="utf-8")
    report["checks"]["revision_is_head"] = scalar(replica, "SELECT version_num FROM alembic_version") == HEAD
    report["checks"]["all_tables_rls"] = scalar(replica, """
        SELECT count(*) = 0 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity""")
    report["checks"]["user_preserved"] = scalar(
        replica, f"SELECT count(*) = 1 FROM users WHERE id = {user_id} AND username = 'rehearsal_user'")
    report["checks"]["plans_seeded"] = scalar(replica, "SELECT count(*) = 4 FROM enterprise_plans")
    report["checks"]["readiness"] = run(ROOT, replica, code=READY).strip().endswith("True")

    # 3. Clean install reference, as scripts/staging_bootstrap.py does it.
    run(ROOT, clean, code=CREATE_TABLES)
    run(ROOT, clean, "alembic", "upgrade", "head")
    reference = fingerprint(clean)
    (out / "clean_install.txt").write_text("\n".join(reference) + "\n", encoding="utf-8")
    diff = list(difflib.unified_diff(reference, after, "instalacion_limpia", "produccion_migrada", lineterm="", n=0))
    (out / "schema_diff.txt").write_text("\n".join(diff) + "\n", encoding="utf-8")
    report["schema_diff_lines"] = [line for line in diff if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    report["checks"]["schema_equals_clean_install"] = not report["schema_diff_lines"]

    # 4. Rollback: the public code (5da6fe0) keeps working on the upgraded schema.
    report["checks"]["rollback_code_works"] = run(prod_path, replica, code=ROLLBACK_SMOKE).strip() == "ok"

    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    failed = [name for name, ok in report["checks"].items() if not ok]
    if failed:
        raise SystemExit("Comprobaciones fallidas: " + ", ".join(failed))


if __name__ == "__main__":
    main()
