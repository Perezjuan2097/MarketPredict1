"""Run a synthetic pilot against a loopback web process, worker and dedicated PostgreSQL DB."""
from datetime import date, timedelta
from io import StringIO
import json
import os
from secrets import token_hex
import socket
import subprocess
import sys
from time import monotonic, sleep

import requests
from sqlalchemy.engine import make_url

from app.db import database_url, get_engine
from app.repositories.sql import SQLRepository
from scripts.synthetic_pilot import generate


def main():
    url = make_url(database_url())
    if (url.get_backend_name() != "postgresql" or url.host not in ("127.0.0.1", "::1")
            or url.database != "marketpredict_sprint5_staging" or url.query):
        raise RuntimeError("La prueba exige PostgreSQL aislado y explícito en loopback.")
    env = {**os.environ, "APP_ENV": "development", "SECRET_KEY": token_hex(32), "AUTO_CREATE_TABLES": "false",
           "ENTERPRISE_FORECAST_ARIMA": "false", "ENTERPRISE_FORECAST_CONCURRENCY": "1"}
    subprocess.run([sys.executable, "-m", "scripts.staging_bootstrap"], env=env, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
    with socket.socket() as initial:
        initial.bind(("127.0.0.1", 0))
        port = initial.getsockname()[1]
    root = f"http://127.0.0.1:{port}"
    if os.name == "posix":
        command = [sys.executable, "-m", "gunicorn", "app:create_app()", "--bind", f"127.0.0.1:{port}"]
    else:
        command = [sys.executable, "-m", "flask", "--app", "app:create_app", "run",
                   "--host", "127.0.0.1", "--port", str(port)]
    web = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        with requests.Session() as owner, requests.Session() as admin:
            owner.trust_env = admin.trust_env = False

            def call(browser, method, path, code=200, **kwargs):
                response = browser.request(method, root + path, timeout=8, **kwargs)
                if response.status_code != code:
                    raise RuntimeError(f"{method} {path}: HTTP {response.status_code}, se esperaba {code}")
                return response

            end = monotonic() + 30
            while True:
                if web.poll() is not None or monotonic() >= end:
                    raise RuntimeError("La web de ensayo no alcanzó readiness.")
                try:
                    if owner.get(root + "/api/ready", timeout=2).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                sleep(0.25)

            def register(browser, name):
                token = call(browser, "GET", "/api/auth/me").json()["csrf"]
                response = call(browser, "POST", "/api/auth/register", 201,
                                headers={"X-CSRF-Token": token}, json={
                                    "username": name, "email": name + "@example.test", "password": "D9" + token_hex(16),
                                }).json()
                return {"X-CSRF-Token": response["csrf"]}, response["user"]["id"]

            akey, admin_id = register(admin, "demo_ops")
            key, _ = register(owner, "demo_pilot_owner")
            SQLRepository(get_engine()).admin_update_user(admin_id, None, role="admin")
            org = call(owner, "POST", "/api/me/organizations", 201, headers=key,
                       json={"name": "DEMO Piloto sintético", "slug": "demo-pilot"}).json()["organization"]
            call(admin, "PATCH", f"/admin/api/commercial/organizations/{org['id']}", 204,
                 headers=akey, json={"plan": "Pilot"})
            datasets = "/api/me/organizations/demo-pilot/datasets"
            dataset = call(owner, "POST", datasets, 201, headers=key, json={"name": "DEMO Ventas"}).json()["dataset"]
            base = f"{datasets}/{dataset['id']}"
            call(admin, "GET", base, 404)
            output = StringIO()
            generate(output, 90)
            raw = output.getvalue().encode("utf-8")
            mapping = {"date": "fecha", "product": "producto", "quantity": "cantidad", "price": "precio",
                       "category": "categoria", "transaction_id": "tx"}
            preview = call(owner, "POST", base + "/preview", headers=key,
                           files={"file": ("demo.csv", raw)}, data={"mapping": json.dumps(mapping)}).json()
            call(owner, "POST", base + "/imports", 201, headers=key,
                 files={"file": ("demo.csv", raw)}, data={"preview_token": preview["preview_token"]})

            def worker(path, payload):
                job = call(owner, "POST", path, 202, headers=key, json=payload).json()["job"]
                subprocess.run([sys.executable, "-m", "scripts.enterprise_worker", "--once"], env=env, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
                status = call(owner, "GET", base + "/automation/jobs/" + str(job["id"])).json()["job"]
                if status["state"] != "completed":
                    raise RuntimeError("El trabajo sintético no terminó: " + str(status["error_code"]))
                return status

            params = {"version": 1, "target": "revenue", "group_by": "date", "horizon": 3,
                      "start": "2025-01-01", "complete_through": (date(2025, 1, 1) + timedelta(days=89)).isoformat()}
            run_id = worker(base + "/forecasts", params)["result"]["run_id"]
            call(owner, "POST", base + "/automation/schedules", 201, headers=key, json={
                **params, "kind": "forecast", "cadence": "daily", "hour_utc": 7, "version_policy": "latest",
            })
            call(owner, "POST", base + "/automation/rules", 201, headers=key, json={
                "kind": "stale_data", "comparison": "above", "threshold": 1, "severity": "warning",
            })
            worker(base + "/automation/jobs", {"kind": "indicators", "version": 1})
            report = worker(base + "/automation/jobs", {"kind": "report", "version": 1, "run_id": run_id})
            for extension in ("csv", "pdf"):
                path = base + f"/automation/reports/{report['id']}/{extension}"
                call(owner, "GET", path)
                call(admin, "GET", path, 404)
            operations = call(admin, "GET", "/admin/api/commercial").json()
            if not operations["web_ready"] or not operations["workers"]:
                raise RuntimeError("Falta la telemetría comercial.")
            if not call(owner, "GET", base + "/automation").json()["alerts"]:
                raise RuntimeError("Falta la alerta sintética.")
            print("Piloto DEMO validado: web, migraciones, cuota, importación, worker, pronóstico, alerta y reportes.")
    finally:
        web.terminate()
        try:
            web.wait(timeout=8)
        except subprocess.TimeoutExpired:
            web.kill()
            web.wait()
        get_engine().dispose()


if __name__ == "__main__":
    main()
