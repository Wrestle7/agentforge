"""Package only distributable project files, never private .env or runtime data."""
import hashlib
import json
import re
import zipfile
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
directories = {"src", "tests", "scripts", "configs", "fixtures", "docs", "reports"}
top_files = {"README.md", "pyproject.toml", "requirements.lock", ".env.example", ".dockerignore",
             "Dockerfile", "compose.yaml"}
excluded = {"__pycache__", ".pytest_cache", ".ruff_cache", ".git", ".venv", "data"}
private_values = [v.encode() for k, v in dotenv_values(root / ".env").items()
                  if v and (k.endswith("API_KEY") or k.endswith("TOKEN"))]
pattern = rb"(?:sk-[A-Za-z0-9_-]{24,}|lsv2_[a-z]+_[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{30,})"
payloads = {}
for path in root.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(root)
    if any(part in excluded or part.endswith(".egg-info") for part in relative.parts):
        continue
    if relative.parts[0] not in directories and relative.as_posix() not in top_files:
        continue
    if path.suffix in {".pyc", ".log"} or (path.suffix == ".zip" and path.name != "source.zip"):
        continue
    data = path.read_bytes()
    if any(value in data for value in private_values) or re.search(pattern, data):
        raise SystemExit(f"Possible credential in {relative}; value not displayed")
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as snapshot:
            for entry in snapshot.infolist():
                content = snapshot.read(entry)
                if Path(entry.filename).name == ".env" or any(value in content for value in private_values) or re.search(pattern, content):
                    raise SystemExit(f"Possible credential in nested source snapshot {relative}")
    payloads[relative.as_posix()] = data
manifest = {"files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payloads.items())},
    "excluded": ["private .env", "virtual environments", "runtime databases", "model cache", "bytecode"],
    "secret_scan": "passed"}
manifest_data = json.dumps(manifest, indent=2).encode()
(root / "delivery-manifest.json").write_bytes(manifest_data)
payloads["delivery-manifest.json"] = manifest_data
output = root.parent / "agentforge-delivery.zip"
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, data in sorted(payloads.items()):
        archive.writestr("agentforge/" + name, data)
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
print(json.dumps({"archive": output.name, "files": len(payloads), "secret_scan": "passed"}))
