#!/usr/bin/env python3
"""Convert MOV videos to MP4 using H.264 CRF 22."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_CRF = 22
DEFAULT_PRESET = "medium"


def find_ffmpeg(explicit_path: str | None) -> str:
    if explicit_path:
        return explicit_path

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg was not found on PATH. Install ffmpeg, pass --ffmpeg-bin, "
            "or install the Python fallback with: python3 -m pip install imageio-ffmpeg"
        ) from exc

    return imageio_ffmpeg.get_ffmpeg_exe()


def discover_inputs(paths: list[Path]) -> list[Path]:
    inputs: list[Path] = []
    for path in paths:
        if path.is_dir():
            inputs.extend(
                sorted(
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() == ".mov"
                )
            )
            continue

        if not path.exists():
            raise FileNotFoundError(f"Input does not exist: {path}")
        if not path.is_file():
            raise RuntimeError(f"Input is not a file: {path}")
        if path.suffix.lower() != ".mov":
            raise RuntimeError(f"Input is not a MOV file: {path}")
        inputs.append(path)

    return inputs


def default_output_path(source: Path) -> Path:
    return source.with_suffix(".mp4")


def convert_video(
    ffmpeg_bin: str,
    source: Path,
    destination: Path,
    crf: int,
    preset: str,
    force: bool,
) -> None:
    if destination.exists() and not force:
        raise FileExistsError(f"Output already exists: {destination}. Use --force to replace it.")

    destination.parent.mkdir(parents=True, exist_ok=True)

    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-y" if force else "-n",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "0",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(destination),
    ]

    print(f"Converting {source} -> {destination}", flush=True)
    subprocess.run(command, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert MOV files to MP4 using H.264 CRF 22 by default."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="MOV file(s) or directory/directories to search recursively for MOV files.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output MP4 path. Only valid when converting a single input file.",
    )
    parser.add_argument("--crf", type=int, default=DEFAULT_CRF, help="H.264 CRF value.")
    parser.add_argument(
        "--preset",
        default=DEFAULT_PRESET,
        help="libx264 preset to use. Defaults to medium.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing MP4 files.")
    parser.add_argument("--ffmpeg-bin", help="Path to an ffmpeg executable.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        inputs = discover_inputs(args.inputs)
        if not inputs:
            raise RuntimeError("No MOV files found.")
        if args.output and len(inputs) != 1:
            raise RuntimeError("--output can only be used with one input file.")

        ffmpeg_bin = find_ffmpeg(args.ffmpeg_bin)
        for source in inputs:
            destination = args.output if args.output else default_output_path(source)
            convert_video(
                ffmpeg_bin=ffmpeg_bin,
                source=source,
                destination=destination,
                crf=args.crf,
                preset=args.preset,
                force=args.force,
            )
    except (FileNotFoundError, FileExistsError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
