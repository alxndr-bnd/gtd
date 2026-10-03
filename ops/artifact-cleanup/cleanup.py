#!/usr/bin/env python3
"""Delete old Docker images from Artifact Registry. Keep the images that matter (SERBITO-405).

Why our own script and not the cleanup policies of Artifact Registry:
- A policy does not know which image Cloud Run runs now. A service that has no new deploy for
  some time runs an image that a policy can delete.
- "Keep the N most recent versions" counts every manifest. One buildx push makes 3 versions
  (index, platform manifest, attestation), and a build cache push makes one more. So "keep 10"
  keeps only 3 or 4 images.

A version is one manifest digest in a package. The script keeps:
  in-use     every image that Cloud Run runs now: the revisions that have traffic or a traffic tag,
             the latest ready and the latest created revision of each service, the image of each
             job and of its latest execution. Also the parent indexes of an in-use manifest;
  protected  every version with a protected tag (default: buildcache, latest);
  newest     the --keep newest tagged versions per package (protected tags do not count);
  young      every version younger than --older-than-days (upload or update time);
  child      every child manifest of a kept index (platform manifest, attestation).
It deletes all other versions: indexes first, then manifests.

The default is a dry run: the script prints the plan and deletes nothing. --no-dry-run deletes.
Safety checks:
- check_plan() checks the plan again, apart from plan(): nothing in use, protected, young, or a
  child of a kept index is in the delete list.
- Before and after the deletes, a pull check of each in-use image: manifests and all blobs.
- After each batch, every kept version must still be in the registry. If one is missing, it stops.
- Cloud Run is read again before each repo: a deploy can run at the same time.
Artifact Registry deletes the children of an index together with the index, so a child delete
can answer 404: the script counts it as deleted. The script never deletes an index that shares a
child with a kept version.

Run it by hand:  python3 ops/artifact-cleanup/cleanup.py [--repo gtd] [--no-dry-run]

Auth: an access token from `gcloud auth print-access-token`. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

AR = "https://artifactregistry.googleapis.com/v1"
RUN = "https://run.googleapis.com"
DEFAULT_PROTECTED_TAGS = ("buildcache", "latest")
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")

# <location>-docker.pkg.dev/<project>/<repo>/<package>[:tag][@sha256:...]; a package can hold "/".
IMAGE_REF = re.compile(
    r"^(?P<location>[a-z0-9-]+)-docker\.pkg\.dev/(?P<project>[a-z0-9.:-]+)/(?P<repo>[a-z0-9-]+)/"
    r"(?P<package>[a-z0-9._/-]+?)(?::(?P<tag>[\w][\w.-]*))?(?:@(?P<digest>sha256:[0-9a-f]{64}))?$")


class ApiError(Exception):
    def __init__(self, status: int, body: str, url: str):
        super().__init__(f"HTTP {status} for {url}: {body[:500]}")
        self.status, self.body, self.url = status, body, url

    @property
    def location_unavailable(self) -> bool:
        # A region that the project cannot use (for example me-central2). Nothing can run there.
        return self.status == 403 and "LOCATION_POLICY_VIOLATED" in self.body


class SafetyError(Exception):
    """The plan or the registry is not safe. The run stops."""


# ---------------------------------------------------------------- data and the selection logic

def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # "2026-09-29T09:46:45.460255Z"; fromisoformat takes at most 6 fraction digits
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z", value)
    if not m:
        return None
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    return datetime.fromisoformat(f"{m.group(1)}.{frac}+00:00")


@dataclass(frozen=True)
class Version:
    package: str
    digest: str
    tags: tuple[str, ...] = ()
    media_type: str = ""
    uploaded: datetime | None = None
    updated: datetime | None = None
    children: tuple[str, ...] = ()
    size: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return self.package, self.digest

    @property
    def is_index(self) -> bool:
        return bool(self.children) or self.media_type in INDEX_TYPES

    @property
    def newest_time(self) -> datetime | None:
        times = [t for t in (self.uploaded, self.updated) if t]
        return max(times) if times else None

    @property
    def label(self) -> str:
        tag = f":{','.join(self.tags)}" if self.tags else ""
        return f"{self.package}{tag}@{self.digest[:19]}"


def parse_docker_image(item: dict) -> Version:
    """One item of the dockerImages.list response of the Artifact Registry API."""
    name = item["name"].split("/dockerImages/", 1)[1]
    package, digest = name.rsplit("@", 1)
    return Version(
        package=urllib.parse.unquote(package),
        digest=digest,
        tags=tuple(item.get("tags") or ()),
        media_type=item.get("mediaType", ""),
        uploaded=_ts(item.get("uploadTime")),
        updated=_ts(item.get("updateTime")),
        children=tuple(m["digest"] for m in item.get("imageManifests") or () if m.get("digest")),
        size=int(item.get("imageSizeBytes") or 0),
    )


@dataclass(frozen=True)
class ImageRef:
    location: str
    project: str
    repo: str
    package: str
    tag: str | None
    digest: str | None


def parse_image_ref(ref: str) -> ImageRef | None:
    """None for an image outside Artifact Registry Docker (docker.io, gcr.io, ...)."""
    m = IMAGE_REF.match(ref.strip())
    if not m:
        return None
    return ImageRef(**m.groupdict())


def resolve_in_use(refs: list[tuple[str, str]], versions: list[Version], project: str, location: str,
                   repo: str) -> tuple[set[str], list[str]]:
    """Digests in this repo that Cloud Run uses, and warnings. refs: (where, image) pairs."""
    tags = {(v.package, t): v.digest for v in versions for t in v.tags}
    digests: set[str] = set()
    warnings: list[str] = []
    for where, image in refs:
        ref = parse_image_ref(image)
        if not ref or (ref.project, ref.location, ref.repo) != (project, location, repo):
            continue
        if ref.digest:
            digests.add(ref.digest)
        elif (ref.package, ref.tag or "latest") in tags:
            digests.add(tags[(ref.package, ref.tag or "latest")])
        else:
            warnings.append(f"{where} uses {image}, but this tag is not in the registry")
    return digests, warnings


@dataclass
class Plan:
    delete: list[Version]
    reasons: dict[tuple[str, str], str]
    cutoff: datetime
    in_use: set[str] = field(default_factory=set)
    protected_tags: frozenset[str] = frozenset()

    def kept_by_reason(self) -> Counter:
        return Counter(r.split(" ", 1)[0] for r in self.reasons.values())


def plan(versions: list[Version], in_use: set[str], now: datetime, older_than_days: float, keep: int,
         protected_tags=DEFAULT_PROTECTED_TAGS) -> Plan:
    protected = frozenset(protected_tags)
    cutoff = now - timedelta(days=older_than_days)
    by_key = {v.key: v for v in versions}
    parents: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for v in versions:
        for child in v.children:
            parents[(v.package, child)].add(v.key)

    reasons: dict[tuple[str, str], str] = {}
    for v in versions:
        if v.digest in in_use:
            reasons[v.key] = "in-use"
        elif protected & set(v.tags):
            reasons[v.key] = f"protected tag {','.join(sorted(protected & set(v.tags)))}"
        elif v.newest_time is None:
            reasons[v.key] = "no-timestamp"
        elif v.newest_time >= cutoff:
            reasons[v.key] = "young"

    tagged: dict[str, list[Version]] = defaultdict(list)
    for v in versions:
        if v.tags and not protected & set(v.tags):
            tagged[v.package].append(v)
    floor = datetime.min.replace(tzinfo=timezone.utc)
    for package_versions in tagged.values():
        package_versions.sort(key=lambda v: v.newest_time or floor, reverse=True)
        for v in package_versions[:keep]:
            reasons.setdefault(v.key, "newest")

    # A manifest in use keeps the indexes that hold it (and so their other children too).
    for k in [k for k, r in reasons.items() if r == "in-use"]:
        for parent in parents.get(k, ()):
            reasons.setdefault(parent, f"parent of in-use {k[1][:19]}")

    # Children of kept indexes stay. Artifact Registry deletes an index together with its children,
    # so an index that shares a child with a kept version stays too. Repeat until nothing changes.
    changed = True
    while changed:
        stack = list(reasons)
        while stack:
            k = stack.pop()
            v = by_key.get(k)
            for child in v.children if v else ():
                ck = (v.package, child)
                if ck not in reasons:
                    reasons[ck] = f"child of kept {k[1][:19]}"
                    stack.append(ck)
        changed = False
        for v in versions:
            if v.key not in reasons and any((v.package, c) in reasons for c in v.children):
                reasons[v.key] = "shares a child with a kept version"
                changed = True

    delete = [v for v in versions if v.key not in reasons]
    # Indexes first: a child manifest goes after the index that holds it.
    delete.sort(key=lambda v: (not v.is_index, v.newest_time or floor, v.package, v.digest))
    return Plan(delete=delete, reasons=reasons, cutoff=cutoff, in_use=set(in_use), protected_tags=protected)


def check_plan(p: Plan, versions: list[Version]) -> None:
    """Check the plan again, independent of plan(). Raises SafetyError."""
    doomed = {v.key for v in p.delete}
    problems = []
    for v in p.delete:
        if v.digest in p.in_use:
            problems.append(f"{v.label} is in use")
        if p.protected_tags & set(v.tags):
            problems.append(f"{v.label} has a protected tag")
        if v.newest_time is None or v.newest_time >= p.cutoff:
            problems.append(f"{v.label} is younger than the cutoff")
        for child in v.children:
            if (v.package, child) not in doomed:
                problems.append(f"{v.label} holds kept child {child[:19]}")
    for v in versions:
        if v.key not in doomed:
            for child in v.children:
                if (v.package, child) in doomed:
                    problems.append(f"{v.package}@{child[:19]} is a child of kept {v.label}")
    if problems:
        raise SafetyError("unsafe plan: " + "; ".join(problems[:10]))


# ------------------------------------------------------------------------------------- the API

class Api:
    def __init__(self, token_command=("gcloud", "auth", "print-access-token")):
        self._token_command = token_command
        self._token: str | None = None
        self._token_at = 0.0

    def _auth(self) -> str:
        if not self._token or time.monotonic() - self._token_at > 1200:
            out = subprocess.run(self._token_command, check=True, capture_output=True, text=True)
            self._token, self._token_at = out.stdout.strip(), time.monotonic()
        return self._token

    def request(self, method: str, url: str, headers: dict | None = None) -> dict:
        for attempt in range(4):
            req = urllib.request.Request(url, method=method,
                                         headers={"Authorization": f"Bearer {self._auth()}", **(headers or {})})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read().decode()
                    return json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="replace")
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiError(e.code, body, url) from None
            except urllib.error.URLError:
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise AssertionError("unreachable")

    def pages(self, url: str, key: str) -> list[dict]:
        items, token = [], None
        while True:
            sep = "&" if "?" in url else "?"
            page = self.request("GET", f"{url}{sep}pageSize=1000" + (f"&pageToken={token}" if token else ""))
            items += page.get(key, [])
            token = page.get("nextPageToken")
            if not token:
                return items


def list_versions(api: Api, project: str, location: str, repo: str) -> list[Version]:
    url = f"{AR}/projects/{project}/locations/{location}/repositories/{repo}/dockerImages"
    return [parse_docker_image(i) for i in api.pages(url, "dockerImages")]


def repo_info(api: Api, project: str, location: str, repo: str) -> dict:
    return api.request("GET", f"{AR}/projects/{project}/locations/{location}/repositories/{repo}")


def docker_repos(api: Api, project: str, location: str) -> list[str]:
    repos = api.pages(f"{AR}/projects/{project}/locations/{location}/repositories", "repositories")
    return sorted(r["name"].rsplit("/", 1)[1] for r in repos if r.get("format") == "DOCKER")


def _short(name: str | None) -> str | None:
    return name.rsplit("/", 1)[-1] if name else None


def cloud_run_images(api: Api, project: str, locations: list[str] | None = None
                     ) -> tuple[list[tuple[str, str]], list[str]]:
    """(where, image) for every image that Cloud Run uses now, and the regions that have a service
    or a job. Default: every region of the project (about 2 minutes). Any API error stops the run:
    without the full list, nothing is safe to delete."""
    if locations is None:
        locations = [loc["locationId"] for loc in
                     api.pages(f"{RUN}/v1/projects/{project}/locations", "locations")]
    refs: list[tuple[str, str]] = []
    active: list[str] = []
    for loc in locations:
        base = f"{RUN}/v2/projects/{project}/locations/{loc}"
        try:
            services = api.pages(f"{base}/services", "services")
        except ApiError as e:
            if e.location_unavailable:
                continue
            raise
        jobs = api.pages(f"{base}/jobs", "jobs")
        if services or jobs:
            active.append(loc)
        for svc in services:
            name = _short(svc["name"])
            for c in svc.get("template", {}).get("containers", []):
                refs.append((f"service {name} template", c["image"]))
            revisions = {_short(svc.get("latestReadyRevision")), _short(svc.get("latestCreatedRevision"))}
            # trafficStatuses: what serves now; traffic: what the service asks for (can differ in a rollout)
            for t in svc.get("trafficStatuses", []) + svc.get("traffic", []):
                if t.get("revision") and (t.get("percent") or t.get("tag")):
                    revisions.add(t["revision"])
            for rev in sorted(r for r in revisions if r):
                try:
                    data = api.request("GET", f"{base}/services/{name}/revisions/{rev}")
                except ApiError as e:
                    if e.status == 404:  # the revision is gone; it serves nothing
                        print(f"warning: revision {rev} of {name} not found", file=sys.stderr)
                        continue
                    raise
                for c in data.get("containers", []):
                    refs.append((f"revision {rev}", c["image"]))
                for cs in data.get("containerStatuses", []) or []:
                    if cs.get("imageDigest") and "@" in cs["imageDigest"]:
                        refs.append((f"revision {rev}", cs["imageDigest"]))
        for job in jobs:
            name = _short(job["name"])
            for c in job.get("template", {}).get("template", {}).get("containers", []):
                refs.append((f"job {name}", c["image"]))
            execution = (job.get("latestCreatedExecution") or {}).get("name")
            if execution:
                try:
                    data = api.request("GET", f"{base}/jobs/{name}/executions/{_short(execution)}")
                except ApiError as e:
                    if e.status == 404:
                        continue
                    raise
                for c in data.get("template", {}).get("containers", []):
                    refs.append((f"execution {_short(execution)}", c["image"]))
    return refs, active


MANIFEST_TYPES = ", ".join(INDEX_TYPES + ("application/vnd.oci.image.manifest.v1+json",
                                         "application/vnd.docker.distribution.manifest.v2+json"))


def check_pull(api: Api, project: str, location: str, repo: str, package: str, digest: str) -> int:
    """Read the manifest the way `docker pull` does, then HEAD its config and layer blobs.
    For an index, check each child too. Returns the number of blobs checked; raises SafetyError."""
    base = f"https://{location}-docker.pkg.dev/v2/{project}/{repo}/{package}"
    try:
        manifest = api.request("GET", f"{base}/manifests/{digest}", {"Accept": MANIFEST_TYPES})
    except ApiError as e:
        raise SafetyError(f"cannot read manifest {repo}/{package}@{digest}: HTTP {e.status}") from None
    if "manifests" in manifest:
        return sum(check_pull(api, project, location, repo, package, m["digest"]) for m in manifest["manifests"])
    blobs = [b["digest"] for b in [manifest.get("config") or {}] + manifest.get("layers", []) if b.get("digest")]
    for blob in blobs:
        try:
            api.request("HEAD", f"{base}/blobs/{blob}")
        except ApiError as e:
            raise SafetyError(f"blob {blob} of {repo}/{package}@{digest[:19]}: HTTP {e.status}") from None
    return len(blobs)


def delete_version(api: Api, project: str, location: str, repo: str, v: Version) -> bool:
    """True: deleted now. False: it was already gone."""
    # force=true also deletes the tags of the version
    url = (f"{AR}/projects/{project}/locations/{location}/repositories/{repo}/packages/"
           f"{urllib.parse.quote(v.package, safe='')}/versions/{v.digest}?force=true")
    try:
        op = api.request("DELETE", url)
    except ApiError as e:
        if e.status == 404:  # already gone: deleting an index also deletes its own children
            return False
        raise
    deadline = time.monotonic() + 180
    while not op.get("done"):
        if time.monotonic() > deadline:
            raise TimeoutError(f"delete of {v.label} did not finish in 180 s")
        time.sleep(1)
        try:
            op = api.request("GET", f"{AR}/{op['name']}")
        except ApiError as e:
            if e.status not in (403, 404):
                raise
            # No access to the operation: wait until the version itself is gone
            image = (f"{AR}/projects/{project}/locations/{location}/repositories/{repo}/dockerImages/"
                     f"{urllib.parse.quote(v.package, safe='')}@{v.digest}")
            try:
                api.request("GET", image)
            except ApiError as gone:
                if gone.status == 404:
                    return True
                raise
    if op.get("error"):
        if op["error"].get("code") == 5:  # NOT_FOUND: gone with its parent index
            return False
        raise RuntimeError(f"delete of {v.label} failed: {op['error']}")
    return True


# ------------------------------------------------------------------------------------- the run

def mb(n: int | float) -> str:
    return f"{n / 1e6:,.1f} MB"


def verify_kept(api: Api, args, repo: str, kept: set[tuple[str, str]]) -> None:
    """Every version that the plan keeps must still be in the registry."""
    present = {v.key for v in list_versions(api, args.project, args.location, repo)}
    missing = sorted(kept - present)
    if missing:
        raise SafetyError(f"kept versions missing in {repo}: " + ", ".join(f"{p}@{d}" for p, d in missing[:10]))


def verify_in_use(api: Api, args, repo: str, versions: list[Version], in_use: set[str]) -> int:
    """Pull check (manifests and blobs) of every in-use image of the repo. Returns blobs checked."""
    packages = {v.digest: v.package for v in versions}
    return sum(check_pull(api, args.project, args.location, repo, packages[d], d)
               for d in sorted(in_use) if d in packages)


def run_repo(api: Api, args, repo: str, refs: list[tuple[str, str]], now: datetime) -> dict:
    size_before = int(repo_info(api, args.project, args.location, repo).get("sizeBytes") or 0)
    versions = list_versions(api, args.project, args.location, repo)
    in_use, warnings = resolve_in_use(refs, versions, args.project, args.location, repo)
    for w in warnings:
        print(f"warning: {repo}: {w}", file=sys.stderr)
    p = plan(versions, in_use, now, args.older_than_days, args.keep, args.protect_tag)
    check_plan(p, versions)

    kinds = Counter(("index" if v.is_index else "manifest") + ("/tagged" if v.tags else "/untagged")
                    for v in p.delete)
    print(f"\n== {repo}: {len(versions)} versions, {mb(size_before)}; in use: "
          f"{', '.join(d[:19] for d in sorted(in_use)) or 'none'}")
    print(f"   keep {len(p.reasons)} ({dict(sorted(p.kept_by_reason().items()))}); "
          f"delete {len(p.delete)} ({dict(sorted(kinds.items()))}), "
          f"manifest sizes {mb(sum(v.size for v in p.delete))} (shared layers counted more than once)")
    for v in p.delete[:args.sample]:
        print(f"   - {v.label}  {v.newest_time:%Y-%m-%d %H:%M}  {mb(v.size)}")
    if len(p.delete) > args.sample:
        print(f"   - ... and {len(p.delete) - args.sample} more")

    blobs = verify_in_use(api, args, repo, versions, in_use)
    print(f"   pull check before: {len(in_use)} in-use images, {blobs} blobs present")
    deleted, gone, failed = 0, 0, []
    if not args.dry_run and p.delete:
        kept = set(p.reasons) & {v.key for v in versions}
        verify_kept(api, args, repo, kept)
        for i in range(0, len(p.delete), args.batch_size):
            for v in p.delete[i:i + args.batch_size]:
                try:
                    if delete_version(api, args.project, args.location, repo, v):
                        deleted += 1
                    else:
                        gone += 1
                except (ApiError, RuntimeError, TimeoutError) as e:
                    failed.append(f"{v.label}: {e}")
                    print(f"   ! {v.label}: {e}", file=sys.stderr)
            verify_kept(api, args, repo, kept)
            print(f"   deleted {deleted + gone}/{len(p.delete)} ({gone} went with their index); "
                  f"all {len(kept)} kept versions present")
        blobs = verify_in_use(api, args, repo, versions, in_use)
        print(f"   pull check after: {len(in_use)} in-use images, {blobs} blobs present")
    size_after = int(repo_info(api, args.project, args.location, repo).get("sizeBytes") or 0)
    return {"repo": repo, "versions": len(versions), "in_use": sorted(in_use), "kept": len(p.reasons),
            "candidates": len(p.delete), "candidate_digests": [f"{v.package}@{v.digest}" for v in p.delete],
            "deleted": deleted + gone, "failed": failed,
            "size_before": size_before, "size_after": size_after}


def summary_table(results: list[dict], dry_run: bool) -> str:
    head = "| repo | versions | kept | " + ("would delete" if dry_run else "deleted") + " | size before | size now |"
    lines = [head, "|---|---:|---:|---:|---:|---:|"]
    for r in results:
        n = r["candidates"] if dry_run else r["deleted"]
        lines.append(f"| {r['repo']} | {r['versions']} | {r['kept']} | {n} | {mb(r['size_before'])} | "
                     f"{mb(r['size_after'])} |")
    return "\n".join(lines)


def main(argv=None, api: Api | None = None, now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", default="serbito")
    ap.add_argument("--location", default="europe-west1")
    ap.add_argument("--repo", action="append", help="repository; repeat; default: every Docker repo")
    ap.add_argument("--older-than-days", type=float, default=3)
    ap.add_argument("--keep", type=int, default=10, help="newest tagged versions to keep per package")
    ap.add_argument("--protect-tag", action="append", help=f"default: {', '.join(DEFAULT_PROTECTED_TAGS)}")
    ap.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=True,
                    help="default: print the plan only; --no-dry-run deletes")
    ap.add_argument("--batch-size", type=int, default=10)
    ap.add_argument("--sample", type=int, default=5, help="candidates to print per repo")
    ap.add_argument("--json", metavar="PATH", help="write the summary as JSON")
    args = ap.parse_args(argv)
    args.protect_tag = tuple(args.protect_tag or DEFAULT_PROTECTED_TAGS)
    if args.keep < 1 or args.older_than_days < 1:
        ap.error("--keep and --older-than-days must be at least 1")

    api = api or Api()
    now = now or datetime.now(timezone.utc)
    repos = args.repo or docker_repos(api, args.project, args.location)
    print(f"{'DRY RUN: nothing is deleted. ' if args.dry_run else ''}Project {args.project}, "
          f"{args.location}; delete versions older than {args.older_than_days:g} days; keep the "
          f"{args.keep} newest tagged per package, tags {', '.join(args.protect_tag)}, and what Cloud Run uses.")
    try:
        refs, active = cloud_run_images(api, args.project)
        print(f"Cloud Run uses {len({i for _, i in refs})} image references in {', '.join(active) or 'no region'}.")
        results = []
        for i, repo in enumerate(repos):
            if i:
                # A deploy can run at the same time: read Cloud Run again before each repo.
                refs, _ = cloud_run_images(api, args.project, active)
            results.append(run_repo(api, args, repo, refs, now))
    except SafetyError as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 2

    table = summary_table(results, args.dry_run)
    print("\n" + table)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Artifact Registry cleanup{' (dry run)' if args.dry_run else ''}\n\n{table}\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
    failed = [x for r in results for x in r["failed"]]
    if failed:
        print(f"{len(failed)} deletes failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
