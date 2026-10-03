"""SERBITO-405: ops/artifact-cleanup/cleanup.py deletes old images from Artifact Registry.

The selection logic runs on fake registry data: no network. A fake API stands in for Cloud Run and
Artifact Registry in the end-to-end cases."""
import importlib.util
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cleanup", ROOT / "ops" / "artifact-cleanup" / "cleanup.py")
C = sys.modules["cleanup"] = importlib.util.module_from_spec(_spec)  # dataclasses look it up
_spec.loader.exec_module(C)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"


def d(name: str) -> str:
    return "sha256:" + (name.encode().hex() * 64)[:64]


def V(name, days_old, tags=(), children=(), package="app", media=None):
    t = NOW - timedelta(days=days_old)
    return C.Version(package=package, digest=d(name), tags=tuple(tags),
                     media_type=media or (INDEX if children else MANIFEST), uploaded=t, updated=t,
                     children=tuple(d(c) for c in children))


def buildx(n, days_old, tag=None, package="app"):
    """One buildx push: index (tagged), platform manifest, attestation."""
    return [V(f"i{n}", days_old, [tag or f"sha{n}"], [f"p{n}", f"a{n}"], package),
            V(f"p{n}", days_old, package=package), V(f"a{n}", days_old, package=package)]


def plan(versions, in_use=(), keep=10, days=3):
    p = C.plan(versions, {d(x) for x in in_use}, NOW, days, keep)
    C.check_plan(p, versions)
    return p


_NAMES = [f"{k}{i}" for k in "ipa" for i in range(40)] + ["oldcache", "orphan", "young", "x"]


def names(p):
    """Short names of the versions that the plan deletes."""
    doomed = {v.digest for v in p.delete}
    return {n for n in _NAMES if d(n) in doomed}


def test_old_versions_go_and_the_newest_tagged_stay():
    versions = [v for n in range(15) for v in buildx(n, 20 - n)]  # i0 oldest .. i14 newest
    p = plan(versions)
    # 15 tagged images, keep 10: i0..i4 go with their platform manifests and attestations
    assert names(p) == {f"{k}{n}" for k in "ipa" for n in range(5)}
    assert p.reasons[("app", d("i14"))] == "newest"
    assert p.reasons[("app", d("p14"))].startswith("child of kept")


def test_indexes_go_before_their_manifests():
    p = plan([v for n in range(12) for v in buildx(n, 30 - n)])
    kinds = [v.is_index for v in p.delete]
    assert kinds == sorted(kinds, reverse=True) and kinds[0] and not kinds[-1]


def test_young_versions_stay_even_untagged():
    versions = [v for n in range(12) for v in buildx(n, 30 - n)] + [V("young", 1), V("orphan", 5)]
    p = plan(versions)
    assert ("app", d("young")) in p.reasons and "orphan" in names(p)


def test_in_use_index_keeps_its_children():
    versions = [v for n in range(14) for v in buildx(n, 30 - n)]
    p = plan(versions, in_use=["i0"])
    assert not {"i0", "p0", "a0"} & names(p)
    assert p.reasons[("app", d("i0"))] == "in-use"


def test_in_use_platform_manifest_keeps_its_index_and_the_attestation():
    # Cloud Run records the digest of the platform manifest, not the index
    versions = [v for n in range(14) for v in buildx(n, 30 - n)]
    p = plan(versions, in_use=["p1"])
    assert not {"i1", "p1", "a1"} & names(p)
    assert p.reasons[("app", d("i1"))].startswith("parent of in-use")


def test_an_index_that_shares_a_child_with_a_kept_index_stays():
    # Two pushes of the same build share the platform manifest. Deleting the old index can take
    # the shared child with it, so the old index stays too.
    old = [V("i0", 30, ["sha0"], ["p0", "a0"]), V("p0", 30), V("a0", 30)]
    new = [V("i1", 1, ["sha0-r20261003"], ["p0", "a1"]), V("a1", 1)]
    p = plan(old + new, keep=1)
    assert names(p) == set()
    assert p.reasons[("app", d("i0"))] == "shares a child with a kept version"
    assert p.reasons[("app", d("a0"))].startswith("child of kept")


def test_protected_tags_and_their_content_stay_and_do_not_count_as_newest():
    versions = [v for n in range(12) for v in buildx(n, 30 - n)]
    versions += [V("oldcache", 40, ["buildcache"]), V("x", 50, ["latest"], ["p39"]), V("p39", 50)]
    p = plan(versions)
    assert not {"oldcache", "x", "p39"} & names(p)
    # protected tags do not take a place among the 10 newest: 12 images, i0 and i1 go
    assert {"i0", "i1"} <= names(p) and "i2" not in names(p)


def test_keep_counts_per_package():
    a = [v for n in range(11) for v in buildx(n, 30 - n, package="web")]
    b = [v for n in range(20, 23) for v in buildx(n, 30, package="worker")]
    p = plan(a + b)
    assert {v.package for v in p.delete} == {"web"} and len(p.delete) == 3


def test_a_version_without_a_timestamp_stays():
    v = C.Version(package="app", digest=d("orphan"))
    assert ("app", d("orphan")) in plan([v]).reasons


def test_check_plan_rejects_an_unsafe_plan():
    versions = [v for n in range(12) for v in buildx(n, 30 - n)]
    p = C.plan(versions, {d("p5")}, NOW, 3, 10)
    p.delete.append(next(v for v in versions if v.digest == d("p5")))
    with pytest.raises(C.SafetyError, match="in use"):
        C.check_plan(p, versions)
    p2 = C.plan(versions, set(), NOW, 3, 10)
    p2.delete.append(next(v for v in versions if v.digest == d("p11")))  # child of the newest
    with pytest.raises(C.SafetyError, match="child of kept"):
        C.check_plan(p2, versions)
    p3 = C.plan(versions, set(), NOW, 3, 10)
    p3.delete = [v for v in p3.delete if v.digest != d("p0")]  # the index goes, its child stays
    with pytest.raises(C.SafetyError, match="holds kept child"):
        C.check_plan(p3, versions)


@pytest.mark.parametrize("ref, want", [
    ("europe-west1-docker.pkg.dev/serbito/gtd/gtd:abc-r20261003", ("gtd", "gtd", "abc-r20261003", None)),
    (f"europe-west1-docker.pkg.dev/serbito/gtd/gtd@{d('p1')}", ("gtd", "gtd", None, d("p1"))),
    (f"europe-west1-docker.pkg.dev/serbito/r/team/app:v1@{d('p1')}", ("r", "team/app", "v1", d("p1"))),
    ("europe-west1-docker.pkg.dev/serbito/r/app", ("r", "app", None, None)),
])
def test_parse_image_ref(ref, want):
    r = C.parse_image_ref(ref)
    assert (r.repo, r.package, r.tag, r.digest) == want


@pytest.mark.parametrize("ref", ["docker.io/library/postgres:17", "gcr.io/serbito/app:1", "nginx"])
def test_parse_image_ref_ignores_other_registries(ref):
    assert C.parse_image_ref(ref) is None


def test_resolve_in_use_maps_tags_to_digests_in_this_repo_only():
    versions = buildx(1, 5, tag="v1") + [V("x", 9, ["latest"])]
    base = "europe-west1-docker.pkg.dev/serbito"
    refs = [("svc", f"{base}/app/app:v1"), ("job", f"{base}/app/app"), ("rev", f"{base}/app/app@{d('p1')}"),
            ("other", f"{base}/other/app:v1"), ("gone", f"{base}/app/app:missing")]
    digests, warnings = C.resolve_in_use(refs, versions, "serbito", "europe-west1", "app")
    assert digests == {d("i1"), d("x"), d("p1")}
    assert len(warnings) == 1 and "missing" in warnings[0]


def test_parse_docker_image():
    v = C.parse_docker_image({
        "name": f"projects/p/locations/l/repositories/r/dockerImages/team%2Fapp@{d('i1')}",
        "tags": ["v1"], "mediaType": INDEX, "uploadTime": "2026-09-29T09:46:45.460255Z",
        "updateTime": "2026-09-30T09:46:45Z", "imageManifests": [{"digest": d("p1")}]})
    assert (v.package, v.tags, v.children, v.is_index) == ("team/app", ("v1",), (d("p1"),), True)
    assert v.newest_time == datetime(2026, 9, 30, 9, 46, 45, tzinfo=timezone.utc)


# ------------------------------------------------------------------ end to end with a fake API

class FakeApi(C.Api):  # pages() from Api, request() fake
    """Cloud Run in europe-west1 (one service, one job), me-central2 closed, one AR repo."""

    def __init__(self, images, run_status=200):
        self.images = {i["name"].rsplit("@", 1)[1]: i for i in images}
        self.calls = []
        self.run_status = run_status
        self.ops_done = True

    def _err(self, status, body=""):
        raise C.ApiError(status, body, "fake")

    def request(self, method, url, headers=None):
        self.calls.append((method, url))
        path = url.split("?")[0]
        if method == "DELETE":
            digest = path.rsplit("/", 1)[1]
            if digest not in self.images:
                self._err(404)
            del self.images[digest]
            return {"name": "projects/p/locations/l/operations/x", "done": self.ops_done}
        if "/operations/" in path:
            self._err(403)  # the service account may not read operations
        if "/dockerImages/" in path:
            if path.rsplit("@", 1)[1] not in self.images:
                self._err(404)
            return self.images[path.rsplit("@", 1)[1]]
        if path.endswith("/locations") and "run.googleapis" in path:
            return {"locations": [{"locationId": "europe-west1"}, {"locationId": "me-central2"}]}
        if "/locations/me-central2/" in path:
            self._err(403, '{"reason": "LOCATION_POLICY_VIOLATED"}')
        if path.endswith("/services"):
            if self.run_status != 200:
                self._err(self.run_status)
            return {"services": [{
                "name": "projects/p/locations/europe-west1/services/web",
                "template": {"containers": [{"image": "europe-west1-docker.pkg.dev/serbito/app/app:sha13"}]},
                "latestReadyRevision": "projects/p/locations/europe-west1/services/web/revisions/web-2",
                "latestCreatedRevision": "projects/p/locations/europe-west1/services/web/revisions/web-2",
                "trafficStatuses": [{"revision": "web-1", "percent": 0, "tag": "old"},
                                    {"revision": "web-2", "percent": 100}]}]}
        if "/revisions/" in path:
            rev = path.rsplit("/", 1)[1]
            digest = {"web-1": d("p0"), "web-2": d("p13")}[rev]
            return {"containers": [{"image": f"europe-west1-docker.pkg.dev/serbito/app/app@{digest}"}]}
        if path.endswith("/jobs"):
            return {"jobs": [{"name": "projects/p/locations/europe-west1/jobs/migrate",
                              "template": {"template": {"containers": [
                                  {"image": "europe-west1-docker.pkg.dev/serbito/app/app:sha1"}]}},
                              "latestCreatedExecution": {"name": "migrate-abc"}}]}
        if "/executions/" in path:
            return {"template": {"containers": [{"image": f"europe-west1-docker.pkg.dev/serbito/app/app@{d('p2')}"}]}}
        if path.endswith("/dockerImages"):
            return {"dockerImages": list(self.images.values())}
        if path.endswith("/repositories/app"):
            return {"sizeBytes": "1000"}
        if "/manifests/" in path:
            digest = path.rsplit("/", 1)[1]
            if digest not in self.images:
                self._err(404)
            kids = self.images[digest].get("imageManifests")
            if kids:
                return {"manifests": [{"digest": k["digest"]} for k in kids]}
            return {"config": {"digest": d("cfg")}, "layers": [{"digest": d("layer")}]}
        if "/blobs/" in path:
            return {}
        raise AssertionError(f"unexpected call {method} {url}")


def _images():
    out = []
    for n in range(14):
        t = (NOW - timedelta(days=30 - n)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        base = "projects/serbito/locations/europe-west1/repositories/app/dockerImages/app@"
        out.append({"name": base + d(f"i{n}"), "tags": [f"sha{n}"], "mediaType": INDEX, "uploadTime": t,
                    "updateTime": t, "imageManifests": [{"digest": d(f"p{n}")}, {"digest": d(f"a{n}")}]})
        for k in "pa":
            out.append({"name": base + d(f"{k}{n}"), "mediaType": MANIFEST, "uploadTime": t, "updateTime": t})
    return out


def test_dry_run_is_the_default_and_deletes_nothing(capsys):
    api = FakeApi(_images())
    assert C.main(["--repo", "app"], api=api, now=NOW) == 0
    assert not [c for c in api.calls if c[0] == "DELETE"]
    assert "DRY RUN" in capsys.readouterr().out


def test_delete_keeps_what_cloud_run_uses(capsys):
    api = FakeApi(_images())
    assert C.main(["--repo", "app", "--no-dry-run", "--batch-size", "2"], api=api, now=NOW) == 0
    left = set(api.images)
    # in use: web-1 (tagged traffic, 0 %) -> p0, web-2 -> p13, job tag sha1 -> i1, execution -> p2
    for n in (0, 1, 2, 13):
        assert {d(f"i{n}"), d(f"p{n}"), d(f"a{n}")} <= left, n
    # the 10 newest tagged (i4..i13) stay; i3 is old, unused and the 11th: it goes with its children
    assert not {d("i3"), d("p3"), d("a3")} & left
    assert {d(f"i{n}") for n in range(4, 14)} <= left
    deletes = [c[1] for c in api.calls if c[0] == "DELETE"]
    assert all(u.endswith("?force=true") for u in deletes)
    assert "pull check after" in capsys.readouterr().out


def test_delete_waits_for_the_version_when_the_operation_is_not_readable(monkeypatch):
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    api = FakeApi(_images())
    api.ops_done = False
    assert C.main(["--repo", "app", "--no-dry-run"], api=api, now=NOW) == 0
    assert d("i3") not in api.images


def test_a_cloud_run_error_stops_the_run_before_any_delete():
    api = FakeApi(_images(), run_status=403)
    with pytest.raises(C.ApiError):
        C.main(["--repo", "app", "--no-dry-run"], api=api, now=NOW)
    assert not [c for c in api.calls if c[0] == "DELETE"]


def test_a_kept_version_that_disappears_stops_the_run():
    api = FakeApi(_images())
    real = api.request

    def request(method, url, headers=None):
        out = real(method, url, headers)
        if method == "DELETE":
            api.images.pop(d("i13"), None)  # something else deleted a kept image
        return out

    api.request = request
    assert C.main(["--repo", "app", "--no-dry-run", "--batch-size", "1"], api=api, now=NOW) == 2
    assert len([c for c in api.calls if c[0] == "DELETE"]) == 1


# ------------------------------------------------------------------------------- the workflow

WORKFLOW = yaml.safe_load((ROOT / ".github" / "workflows" / "artifact-cleanup.yml").read_text("utf-8"))


def test_workflow_runs_weekly_and_by_hand_with_least_permissions():
    on = WORKFLOW.get("on", WORKFLOW.get(True))
    assert on["schedule"] == [{"cron": "0 4 * * 0"}] and "workflow_dispatch" in on
    assert WORKFLOW["permissions"] == {"contents": "read", "id-token": "write"}
    steps = WORKFLOW["jobs"]["cleanup"]["steps"]
    uses = [s["uses"] for s in steps if "uses" in s]
    assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["persist-credentials"] is False
    run = next(s["run"] for s in steps if "cleanup.py" in s.get("run", ""))
    for repo in ("django-app", "gtd", "javi", "planning-poker", "tickets-booking", "cloud-run-source-deploy"):
        assert f"--repo {repo}" in run
