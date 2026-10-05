"""SERBITO-348: после деплоя проверяем заголовки безопасности главной страницы.

scripts/check_security_headers.sh запускается как есть с поддельным curl, который печатает заданные
заголовки. Шаг workflow — тоже как есть (bash -e -o pipefail, как в GitHub), с поддельными gcloud и curl."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_security_headers.sh"
STEPS = yaml.safe_load((ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]

FULL = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Content-Security-Policy-Report-Only": "default-src 'self'",
}


def _fake_bin(tmp_path, headers, fail=False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    lines = "HTTP/2 200\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
    (tmp_path / "headers.txt").write_text(lines)
    curl = "#!/bin/sh\necho \"$*\" >> \"$FAKE_LOG\"\n" + (
        "exit 6\n" if fail else f"cat '{tmp_path / 'headers.txt'}'\n")
    gcloud = "#!/bin/sh\necho https://gtd-x.a.run.app\n"
    for name, body in {"curl": curl, "gcloud": gcloud}.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PATH=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]), FAKE_LOG=str(tmp_path / "curl.log"),
               SERVICE="gtd", REGION="europe-west1")
    return env


def _check(tmp_path, headers, fail=False):
    env = _fake_bin(tmp_path, headers, fail)
    return subprocess.run(["bash", str(SCRIPT), "https://gtd.example/"], capture_output=True, text=True,
                          timeout=30, env=env)


def test_all_headers_pass_with_one_request_without_redirects(tmp_path):
    r = _check(tmp_path, FULL)
    assert r.returncode == 0, r.stdout + r.stderr
    args = (tmp_path / "curl.log").read_text()
    assert args.count("\n") == 1 and "https://gtd.example/" in args
    assert "-L" not in args.split() and "--max-time 30" in args


def test_header_names_and_values_are_case_insensitive(tmp_path):
    r = _check(tmp_path, {k.upper(): v.upper() for k, v in FULL.items()})
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("drop, name", [
    (["Strict-Transport-Security"], "strict-transport-security"),
    (["X-Content-Type-Options"], "x-content-type-options"),
    (["Referrer-Policy"], "referrer-policy"),
    (["X-Frame-Options", "Content-Security-Policy"], "frame-ancestors"),
    (["Content-Security-Policy", "Content-Security-Policy-Report-Only", "X-Frame-Options"], "report-only"),
])
def test_a_missing_header_is_named_and_fails(tmp_path, drop, name):
    r = _check(tmp_path, {k: v for k, v in FULL.items() if k not in drop})
    assert r.returncode == 1 and name in r.stdout, r.stdout + r.stderr


def test_frame_protection_from_either_header(tmp_path):
    only_csp = {k: v for k, v in FULL.items() if k != "X-Frame-Options"}
    assert _check(tmp_path, only_csp).returncode == 0
    only_xfo = {k: v for k, v in FULL.items() if k != "Content-Security-Policy"}
    assert _check(tmp_path, only_xfo).returncode == 0  # report-only CSP остаётся CSP
    report_only_frame = {**only_xfo}
    report_only_frame.pop("X-Frame-Options")
    report_only_frame["Content-Security-Policy-Report-Only"] = "frame-ancestors 'none'"
    r = _check(tmp_path, report_only_frame)  # report-only frame-ancestors ничего не запрещает
    assert r.returncode == 1 and "frame-ancestors" in r.stdout


def test_wrong_nosniff_value_fails(tmp_path):
    r = _check(tmp_path, {**FULL, "X-Content-Type-Options": "sniff"})
    assert r.returncode == 1 and "nosniff" in r.stdout


def test_curl_failure_fails(tmp_path):
    assert _check(tmp_path, FULL, fail=True).returncode == 1


# ── Шаг workflow ──

STEP = next(i for i, s in enumerate(STEPS) if s.get("name") == "Security headers check")


def _run_step(tmp_path, headers, cwd=ROOT):
    env = _fake_bin(tmp_path, headers)
    script = re.sub(r"\$\{\{\s*env\.(\w+)\s*\}\}", r"${\1}", STEPS[STEP]["run"])
    return subprocess.run(["bash", "-eo", "pipefail", "-c", script], cwd=cwd, capture_output=True, text=True,
                          timeout=30, env=env)


def test_step_runs_after_the_smoke_on_every_deploy_and_checks_the_candidate_url(tmp_path):
    # SERBITO-430: на обычном run.app-адресе главная — 301 на gtd.serbito.rs, поэтому проверяем адрес тега
    smoke = next(i for i, s in enumerate(STEPS) if "Smoke check" in s.get("name", ""))
    assert STEP == len(STEPS) - 2 and STEP > smoke  # после него — только снятие тега candidate
    assert "if" not in STEPS[STEP] and not STEPS[STEP].get("continue-on-error")
    r = _run_step(tmp_path, FULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "https://candidate---gtd-x.a.run.app/" in (tmp_path / "curl.log").read_text()


def test_step_fails_without_rollback_when_a_header_is_missing(tmp_path):
    r = _run_step(tmp_path, {k: v for k, v in FULL.items() if k != "Strict-Transport-Security"})
    assert r.returncode == 1 and "::error::" in r.stdout, r.stdout + r.stderr
    assert "update-traffic" not in STEPS[STEP]["run"]


def test_step_skips_a_tag_without_the_script(tmp_path):
    r = _run_step(tmp_path, {}, cwd=tmp_path)  # пересборка старого тега: скрипта нет
    assert r.returncode == 0 and "::warning::" in r.stdout, r.stdout + r.stderr
