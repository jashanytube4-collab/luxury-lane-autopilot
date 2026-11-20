"""Copy every official hamdan.ae photo into the private library (release tag 'photos', zips of ~400 photos + index.json),
because hamdan.ae blocks GitHub's servers. No Claude needed. Resumable. Run from the home connection:

    python -m tools.mirror_photos <owner/library-repo> <path-to-gh> [max_zips]
"""
import io
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

from lane.hamdan import UA, Library

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "work" / "photo_mirror"
PER_ZIP = 400


def gh(path: str, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run([path, *a], capture_output=True, text=True)


def main() -> None:
    repo, ghp = sys.argv[1], sys.argv[2]
    max_zips = int(sys.argv[3]) if len(sys.argv) > 3 else 999
    TMP.mkdir(parents=True, exist_ok=True)
    if gh(ghp, "release", "view", "photos", "-R", repo).returncode != 0:
        gh(ghp, "release", "create", "photos", "-R", repo, "-t", "photos", "-n", "Official photo mirror")
    idx_file = TMP / "index.json"
    p = gh(ghp, "release", "download", "photos", "-R", repo, "-p", "index.json", "-D", str(TMP), "--clobber")
    index: dict[str, str] = json.loads(idx_file.read_text()) if p.returncode == 0 and idx_file.exists() else {}
    events = json.loads((ROOT / "state" / "events.json").read_text(encoding="utf-8"))
    urls = [u for ev in events.values() for u in ev.get("photos", [])]
    todo = [u for u in dict.fromkeys(urls) if Library.photo_name(u) not in index]
    print(f"{len(urls)} photos known, {len(index)} mirrored, {len(todo)} to do", flush=True)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    zips = 0
    while todo and zips < max_zips:
        batch, todo = todo[:PER_ZIP], todo[PER_ZIP:]
        zname = f"photos-{int(time.time())}.zip"
        buf, got = io.BytesIO(), {}
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for u in batch:
                try:
                    r = s.get(u, timeout=60)
                    if r.status_code == 200 and len(r.content) > 20_000:
                        n = Library.photo_name(u)
                        z.writestr(n, r.content)
                        got[n] = zname
                except requests.RequestException:
                    pass
                time.sleep(0.3)
        if not got:
            print("nothing downloaded for this batch; stopping", flush=True)
            break
        zp = TMP / zname
        zp.write_bytes(buf.getvalue())
        if gh(ghp, "release", "upload", "photos", str(zp), "-R", repo).returncode != 0:
            print("upload failed; stopping", flush=True)
            break
        index.update(got)
        idx_file.write_text(json.dumps(index))
        gh(ghp, "release", "upload", "photos", str(idx_file), "-R", repo, "--clobber")
        zp.unlink(missing_ok=True)
        zips += 1
        print(f"zip {zips}: {len(got)} photos uploaded, {len(todo)} left", flush=True)


if __name__ == "__main__":
    main()
