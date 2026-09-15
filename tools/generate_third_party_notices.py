"""Generate deterministic ASCII HTML notices for the ARM64 Rust binary."""

import argparse
import subprocess
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG = _ROOT / "licenses" / "about.toml"
_TEMPLATE = _ROOT / "licenses" / "third-party-notices.hbs"
_OUTPUT = _ROOT / "licenses" / "THIRD_PARTY_NOTICES.html"
_CARGO_ABOUT_VERSION = "cargo-about 0.9.2"


def _generate() -> bytes:
    version = subprocess.run(
        ["cargo", "about", "--version"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != _CARGO_ABOUT_VERSION:
        raise RuntimeError(
            f"expected {_CARGO_ABOUT_VERSION}, found {version or 'no version output'}"
        )
    with tempfile.TemporaryDirectory(prefix="lidar-notices-") as directory:
        generated = Path(directory) / "THIRD_PARTY_NOTICES.html"
        subprocess.run(
            [
                "cargo",
                "about",
                "generate",
                "--locked",
                "--fail",
                "--all-features",
                "--target",
                "aarch64-unknown-linux-musl",
                "--config",
                str(_CONFIG),
                "--output-file",
                str(generated),
                str(_TEMPLATE),
            ],
            cwd=_ROOT,
            check=True,
        )
        rendered = generated.read_text(encoding="utf-8")
    return rendered.encode("ascii", errors="xmlcharrefreplace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    expected = _generate()
    if arguments.check:
        try:
            current = _OUTPUT.read_bytes()
        except OSError as error:
            raise SystemExit(f"cannot read {_OUTPUT.relative_to(_ROOT)}: {error}") from error
        if current != expected:
            raise SystemExit(
                f"{_OUTPUT.relative_to(_ROOT)} is stale; regenerate it without --check"
            )
        return 0
    _OUTPUT.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
