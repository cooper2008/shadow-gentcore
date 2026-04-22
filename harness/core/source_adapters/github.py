"""GitHub adapter — fetches a repo as a local directory for scanning.

URI format:
    github://<org>/<repo>                     — default branch, full tree
    github://<org>/<repo>@<ref>               — specific branch / tag / SHA
    github://<org>/<repo>?path=<subdir>       — subdirectory only
    github://<org>/<repo>@<ref>?path=<subdir> — combined

Strategy:
    Download the tarball via GitHub API (`GET /repos/{org}/{repo}/tarball/{ref}`)
    with `Authorization: token $GITHUB_TOKEN`. Extract to cache. Public
    repos work without a token if `GITHUB_TOKEN` is unset, but rate limits
    are much stricter — we warn the first time an unauthenticated call
    happens.

Caching:
    One extraction dir per `(org, repo, ref)` tuple. Re-runs skip
    re-download if the directory exists AND the API's `commits/{ref}` sha
    matches the cached `.materialized_sha` file. Set
    `GENTCORE_SOURCE_REFRESH=1` to force re-download.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from harness.core.source_adapters.base import SourceAdapter, SourceSpec

logger = logging.getLogger(__name__)


def _parse_github_uri(uri: str) -> dict[str, Any]:
    """Extract org, repo, ref, subpath from a github:// URI.

    Returns a dict with keys: org, repo, ref (None if unspecified), subpath
    (None if unspecified).
    """
    if not uri.startswith("github://"):
        raise ValueError(f"Not a github:// URI: {uri!r}")
    # urlparse treats github://org/repo as netloc=org, path=/repo.
    parsed = urlparse(uri)
    org = parsed.netloc
    path = parsed.path.lstrip("/")
    # path may be "repo@ref" or just "repo"
    ref: str | None = None
    if "@" in path:
        path, ref = path.split("@", 1)
    repo = path
    if not org or not repo:
        raise ValueError(
            f"Malformed github URI: {uri!r}. Expected github://<org>/<repo>[@<ref>]."
        )
    subpath: str | None = None
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "path" in qs:
            subpath = qs["path"][0].strip("/")
    return {"org": org, "repo": repo, "ref": ref, "subpath": subpath}


class GitHubAdapter(SourceAdapter):
    scheme = "github"
    required_credentials: list[str] = []  # optional — public repos work

    async def materialize(
        self,
        spec: SourceSpec,
        credentials: dict[str, str],
        cache_dir: Path,
    ) -> Path:
        parsed = _parse_github_uri(spec.uri)
        org = parsed["org"]
        repo = parsed["repo"]
        ref = parsed["ref"] or "HEAD"
        subpath = parsed["subpath"]

        token = (
            credentials.get("GITHUB_TOKEN")
            or credentials.get("GH_TOKEN")
            or os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or ""
        )
        if not token:
            logger.warning(
                "GitHub adapter running unauthenticated (no GITHUB_TOKEN). "
                "Rate limits apply (60 req/hour); private repos will 404."
            )

        # Resolve ref to a commit SHA so the cache key is deterministic.
        resolved_sha = await self._resolve_ref(org, repo, ref, token)
        dest = cache_dir / "github" / org / repo / resolved_sha

        refresh = os.environ.get("GENTCORE_SOURCE_REFRESH", "").strip() not in ("", "0", "false", "no")
        if dest.exists() and (dest / ".materialized_sha").exists() and not refresh:
            cached_sha = (dest / ".materialized_sha").read_text().strip()
            if cached_sha == resolved_sha:
                logger.info("Using cached GitHub materialization: %s", dest)
                return (dest / subpath) if subpath else dest
            # Cache mismatch — fall through to re-download
            shutil.rmtree(dest, ignore_errors=True)

        dest.mkdir(parents=True, exist_ok=True)
        await self._download_tarball(org, repo, resolved_sha, token, dest)
        (dest / ".materialized_sha").write_text(resolved_sha)

        final = (dest / subpath) if subpath else dest
        if not final.exists():
            raise FileNotFoundError(
                f"Subpath {subpath!r} not found after materialization of "
                f"{spec.uri}. Extracted into {dest}."
            )
        return final

    async def _resolve_ref(
        self,
        org: str,
        repo: str,
        ref: str,
        token: str,
    ) -> str:
        """Resolve a branch/tag/HEAD ref to a commit SHA for deterministic caching.

        GitHub's `GET /repos/{org}/{repo}/commits/{ref}` returns the commit
        object for the tip of the branch/tag, or the commit itself for a SHA.
        """
        import httpx

        url = f"https://api.github.com/repos/{org}/{repo}/commits/{ref}"
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"GitHub repo or ref not found: {org}/{repo}@{ref} "
                f"(or you lack access — check GITHUB_TOKEN)."
            )
        if resp.status_code == 401:
            raise PermissionError(
                f"GitHub auth failed for {org}/{repo}@{ref}. Check GITHUB_TOKEN value/scope."
            )
        resp.raise_for_status()
        return resp.json()["sha"]

    async def _download_tarball(
        self,
        org: str,
        repo: str,
        sha: str,
        token: str,
        dest: Path,
    ) -> None:
        """Download `sha` as a tarball and extract into `dest`."""
        import httpx

        url = f"https://api.github.com/repos/{org}/{repo}/tarball/{sha}"
        headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()

        # GitHub wraps everything in a single top-level dir like
        # `org-repo-<shortsha>/…`. We extract into a temp dir, then move
        # the single child up so `dest` contains repo contents directly.
        with tempfile.TemporaryDirectory() as tmpd:
            with tarfile.open(fileobj=BytesIO(resp.content), mode="r:gz") as tf:
                # Safe-extract guard — CVE-class hardening:
                # (1) Reject absolute paths and `..` traversal.
                # (2) Reject symlinks / hardlinks (tarfile.extractall honors
                #     them; a malicious repo could plant a link to
                #     ~/.ssh/id_rsa or /etc/passwd then write through it).
                # (3) Reject device/fifo/char special files — never appear
                #     in legitimate source tarballs.
                # (4) Reject absolute symlink/hardlink targets too.
                for member in tf.getmembers():
                    name = member.name
                    if name.startswith("/") or ".." in Path(name).parts:
                        raise RuntimeError(
                            f"Refusing to extract unsafe tar member: {name!r}"
                        )
                    if member.issym() or member.islnk():
                        raise RuntimeError(
                            f"Refusing symlink/hardlink tar member: {name!r} "
                            f"→ {member.linkname!r} (TarSlip guard)"
                        )
                    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
                        raise RuntimeError(
                            f"Refusing non-regular tar member: {name!r} "
                            f"(type={member.type!r})"
                        )
                # Python 3.12+: extra belt-and-suspenders via extraction filter.
                # On 3.11 this is a no-op.
                try:
                    tf.extractall(tmpd, filter="data")  # type: ignore[call-arg]
                except TypeError:
                    tf.extractall(tmpd)
            children = [p for p in Path(tmpd).iterdir() if p.is_dir()]
            if len(children) != 1:
                raise RuntimeError(
                    f"Unexpected tarball shape for {org}/{repo}@{sha}: "
                    f"{len(children)} top-level dirs"
                )
            inner = children[0]
            # Move contents of inner to dest
            for item in inner.iterdir():
                target = dest / item.name
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                shutil.move(str(item), str(target))
