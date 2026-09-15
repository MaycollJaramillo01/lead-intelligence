"""Software bill of materials of what ships (phase 14): CycloneDX 1.5 JSON under `dist/sbom/`.

- desktop core: `cargo tree` over normal and build dependencies for Windows x64;
- research worker: `uv export --no-dev` (the locked packages the sidecar bundles);
- interface: `pnpm sbom --sbom-format cyclonedx --prod`.

Run from the repository root: `uv run --directory services/research-engine python ../../scripts/sbom.py`.
"""

import json
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "sbom"


def run(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(  # noqa: S603 - fixed arguments, no shell
        args, cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def bom(name: str, version: str, components: list[tuple[str, str, str]]) -> dict[str, object]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
            "component": {"type": "application", "name": name, "version": version},
        },
        "components": [
            {"type": "library", "name": n, "version": v, "purl": f"pkg:{kind}/{n}@{v}"}
            for n, v, kind in sorted(set(components))
        ],
    }


def rust() -> dict[str, object]:
    text = run(
        "cargo",
        "tree",
        "--manifest-path",
        "src-tauri/Cargo.toml",
        "-e",
        "normal,build",
        "--target",
        "x86_64-pc-windows-msvc",
        "--prefix",
        "none",
    )
    found = []
    for line in text.splitlines():
        match = re.match(r"^(\S+) v(\S+)", line)
        if match and match.group(1) != "lead-intelligence":
            found.append((match.group(1), match.group(2), "cargo"))
    return bom("lead-intelligence", "desktop core", found)


def python() -> dict[str, object]:
    text = run(
        "uv",
        "export",
        "--no-dev",
        "--no-hashes",
        "--frozen",
        "--format",
        "requirements-txt",
        cwd=ROOT / "services" / "research-engine",
    )
    found = [
        (match.group(1).lower(), match.group(2), "pypi")
        for line in text.splitlines()
        if (match := re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line))
    ]
    return bom("research-engine", "worker sidecar", found)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, document in (("desktop-core", rust()), ("research-engine", python())):
        (OUT / f"{name}.cdx.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        counts[name] = len(document["components"])  # type: ignore[arg-type]
    pnpm = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    interface = run(pnpm, "--filter", "@lead/desktop", "sbom", "--sbom-format", "cyclonedx", "--prod")
    (OUT / "interface.cdx.json").write_text(interface, encoding="utf-8")
    counts["interface"] = len(json.loads(interface).get("components", []))
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
