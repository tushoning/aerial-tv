#!/usr/bin/env python3
"""Prepare the full-screen films in a user-supplied snoopy.saver.zip for Android TVs.

Requires ffmpeg on PATH. Does not download, distribute, or upload any media.
"""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

# The twelve opaque recordings added in 0.2.2. The four older AS files are HEVC with alpha too.
RECORDINGS = {f"101_AS{n:03d}" for n in (3, 4, *range(7, 17))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="snoopy.saver.zip from hitnology/snoopy")
    parser.add_argument("output", type=Path, help="a new directory for the prepared MP4 files")
    args = parser.parse_args()
    if not shutil.which("ffmpeg"):
        parser.error("ffmpeg must be on PATH")
    if args.output.exists():
        parser.error("the output directory already exists; choose a new one")
    with zipfile.ZipFile(args.archive) as archive:
        # The old layered scenes, including four AS files, need Apple's alpha renderer.
        films = [info for info in archive.infolist()
                 if Path(info.filename).stem in RECORDINGS and Path(info.filename).suffix == ".mov"
                 and "/Resources/" in info.filename and not info.filename.startswith("__MACOSX/")]
        if not films:
            parser.error("no supported 0.2.2 recordings found in this archive")
        if any(info.file_size > 200 * 1024 * 1024 for info in films):
            parser.error("an input film exceeds 200 MB")
        names = [Path(info.filename).stem for info in films]
        if len(names) != len(set(names)):
            parser.error("the archive has duplicate film names")
        args.output.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="aerial-snoopy-") as tmp:
            for info in sorted(films, key=lambda entry: entry.filename):
                name = Path(info.filename).stem
                source = Path(tmp) / (name + ".mov")
                target = args.output / (name + ".mp4")
                # Never extract an archive path: only these validated basenames reach disk.
                with archive.open(info) as incoming, source.open("wb") as out:
                    shutil.copyfileobj(incoming, out)
                print(f"Preparing {name}", flush=True)
                try:
                    subprocess.run([
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin", "-n",
                        "-i", str(source), "-map", "0:v:0", "-an",
                        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", "2",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
                    ], check=True)
                except (subprocess.CalledProcessError, KeyboardInterrupt):
                    target.unlink(missing_ok=True)
                    raise
                finally:
                    source.unlink(missing_ok=True)
    print(f"Prepared {len(films)} films in {args.output}. Add them through Aerial's phone page.")


if __name__ == "__main__":
    main()
