#!/usr/bin/env python3
"""Build a Chrome Load unpacked directory with a root-level manifest.json."""

import argparse
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "installer"))

from browser_bridge import DOMAINS_FILE, SOURCE_DIR, build_extension  # noqa: E402


def main():
  parser = argparse.ArgumentParser(
      description="Genera la carpeta descomprimida del puente para Chrome."
  )
  parser.add_argument(
      "--output",
      type=Path,
      default=PROJECT_DIR / "dist" / "local" / "xdm-companion-chrome-unpacked",
      help="carpeta de salida (por defecto: dist/local/xdm-companion-chrome-unpacked)",
  )
  args = parser.parse_args()

  output_dir = args.output.expanduser().resolve()
  build_extension(SOURCE_DIR, DOMAINS_FILE, output_dir, "chrome")
  manifest = output_dir / "manifest.json"
  print(f"Carpeta lista para Chrome: {output_dir}")
  print(f"Manifiesto: {manifest}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
