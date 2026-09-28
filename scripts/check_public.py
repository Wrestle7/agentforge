"""Inspect staged blobs before publishing. Never print matched secret values."""
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SECRETS = [v.encode() for k, v in dotenv_values(ROOT / ".env").items()
           if v and len(v) >= 8 and any(word in k for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))]
PATTERNS = [
    rb"\bsk-[A-Za-z0-9_-]{24,}",
    rb"\blsv2_[a-z]+_[A-Za-z0-9_-]{20,}",
    rb"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})",
    rb"\btvly-[A-Za-z0-9_-]{20,}",
    rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    rb"smith\.langchain\.com/o/[a-f0-9-]{32,}",
]
FORBIDDEN = {".venv", "venv", "data", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
issues = []


def scan(name, data, depth=0):
    path = PurePosixPath(name)
    if any(part in FORBIDDEN or part.endswith(".egg-info") for part in path.parts):
        issues.append((name, "runtime or private directory"))
    if (path.name == ".env" or path.name.startswith(".env.")) and path.name != ".env.example":
        issues.append((name, "private environment file"))
    if path.suffix in {".sqlite", ".db", ".pyc", ".log", ".pem", ".key"}:
        issues.append((name, "runtime or credential file"))
    if any(secret in data for secret in SECRETS):
        issues.append((name, "configured private credential"))
    if any(re.search(pattern, data) for pattern in PATTERNS):
        issues.append((name, "credential pattern or private trace URL"))
    if path.suffix == ".zip":
        if depth >= 2:
            issues.append((name, "nested archive depth"))
            return
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    scan(name + "/" + member.filename, archive.read(member), depth + 1)


def main():
    names = subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=ROOT).split(b"\0")
    count = 0
    for raw in filter(None, names):
        name = raw.decode("utf-8")
        blob = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
        scan(name, blob)
        count += 1
    if issues:
        print(json.dumps({"passed": False, "issues": issues}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"passed": True, "staged_files": count,
        "checks": ["configured credentials", "credential patterns", "private trace URLs", "runtime paths", "nested archives"]}))


if __name__ == "__main__":
    main()
