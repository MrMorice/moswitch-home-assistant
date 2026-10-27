"""Create a reproducible integration archive from the current checkout."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = json.loads((root / "custom_components/moswitch/manifest.json").read_text())["version"]
output = root / "dist" / version
output.mkdir(parents=True, exist_ok=True)
archive = output / "moswitch.zip"
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
    for path in sorted((root / "custom_components/moswitch").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".json", ".js"):
            info = zipfile.ZipInfo(str(path.relative_to(root)), date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, path.read_bytes())
manifest = {
    "version": version,
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
    "dirty": bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
    ),
    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    "archive": archive.name,
}
(output / "build.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(archive)
