"""Plan/copy a saved Nexus session to an ASCII destination; verify SHA256.

Source parent directories may be Unicode; copied relative names must be ASCII.
No files are copied without --copy. SPDX-License-Identifier: MIT
"""
import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from NexusGapFill_Single import _ascii_text, ascii_path_guard


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(source):
    pending, files, directories = [source], [], []
    while pending:
        for item in pending.pop().iterdir():
            if item.is_symlink() or os.path.normcase(str(item.resolve())) != os.path.normcase(str(item.absolute())):
                raise RuntimeError("LINK_NOT_SUPPORTED: " + str(item).encode("unicode_escape").decode("ascii"))
            relative = item.relative_to(source)
            if not _ascii_text(str(relative)):
                raise RuntimeError("ASCII_RELATIVE_NAME_REQUIRED: " + str(relative).encode("unicode_escape").decode("ascii") +
                                   "; do not automatically rename session files.")
            if item.is_dir():
                directories.append(relative)
                pending.append(item)
            elif item.is_file():
                files.append(relative)
            else:
                raise RuntimeError("Unsupported filesystem entry: " + str(relative))
    return sorted(files), sorted(directories, key=lambda p: (len(p.parts), str(p)))


def selected_inventory(source, trials):
    """Only selected trial bundles and shared model files; no unrelated trials."""
    if len(set(trials)) != len(trials) or not trials:
        raise RuntimeError("Specify distinct trial basenames")
    for trial in trials:
        if not _ascii_text(trial) or Path(trial).name != trial or any(c in trial for c in '/\\:'):
            raise RuntimeError("Invalid ASCII trial basename")
    files = []
    shared = {".vsk", ".vst", ".mp", ".model", ".pycgm2", ".mkr"}
    suffixes = (".c3d", ".trial.enf", ".x2d", ".x1d", ".xcp", ".system",
                ".history", ".digitaldevices.xml")
    for item in source.iterdir():
        lower = item.name.lower()
        chosen = (item.suffix.lower() in shared or lower.endswith(".session.enf") or
                  (lower.endswith(".xml") and not lower.endswith(".digitaldevices.xml")))
        chosen = chosen or any(lower == (trial + suffix).lower()
                               for trial in trials for suffix in suffixes)
        chosen = chosen or (item.suffix.lower() in (".avi", ".mp4") and
                            any(item.name.startswith(trial + ".") for trial in trials))
        if not chosen:
            continue
        if not item.is_file() or item.is_symlink() or item.resolve() != item.absolute():
            raise RuntimeError("Selected session dependency is not a regular unlinked file")
        if not _ascii_text(item.name):
            raise RuntimeError("ASCII_RELATIVE_NAME_REQUIRED: " + item.name.encode("unicode_escape").decode("ascii"))
        files.append(Path(item.name))
    names = {p.name.lower() for p in files}
    for trial in trials:
        if (trial + ".Trial.enf").lower() not in names or not any(
                (trial + suffix).lower() in names for suffix in (".c3d", ".x2d")):
            raise RuntimeError("Selected trial needs its ENF and C3D or raw X2D: " + trial)
    if not any(p.suffix.lower() == ".vsk" for p in files):
        raise RuntimeError("Selected session needs a calibrated VSK")
    return sorted(files), []


def stage(source, destination, execute=False, trials=None):
    ascii_path_guard({"staging helper": Path(__file__), "destination": destination})
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_dir():
        raise RuntimeError("Source session directory does not exist.")
    if source == destination or source in destination.parents or destination in source.parents:
        raise RuntimeError("SOURCE_DESTINATION_OVERLAP: choose an independent directory.")
    if destination.exists():
        raise RuntimeError("DESTINATION_EXISTS: use a new destination; nothing is merged or overwritten.")
    scan = (lambda: selected_inventory(source, trials)) if trials is not None else (lambda: inventory(source))
    files, directories = scan()
    if trials is None and not {".c3d", ".enf", ".vsk"}.issubset({p.suffix.lower() for p in files}):
        raise RuntimeError("The saved session must include C3D, ENF and calibrated VSK files.")
    if (source / "ngf-copy-manifest.json").exists():
        raise RuntimeError("The source already contains an NGF staging manifest; choose the saved source session.")
    report = dict(status="plan", source=str(source), destination=str(destination), files=len(files),
                  bytes=sum((source / name).stat().st_size for name in files), references_not_rewritten=True,
                  selection="selected_trials" if trials is not None else "complete_session",
                  trials=trials)
    if not execute:
        return report
    hashes = {str(name): sha256(source / name) for name in files}
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for name in directories:
            (destination / name).mkdir(exist_ok=False)
        for name in files:
            shutil.copy2(str(source / name), str(destination / name))
            if sha256(destination / name) != hashes[str(name)]:
                raise RuntimeError("Copied file hash mismatch: " + str(name))
        after_files, after_directories = scan()
        if after_files != files or after_directories != directories:
            raise RuntimeError("Source directory changed during copying; this destination is not ready.")
        if any(sha256(source / name) != hashes[str(name)] for name in files):
            raise RuntimeError("Source file changed during copying; save/stop editing and create a new copy.")
        report.update(status="verified_copy", source_unchanged=True, sha256=hashes)
    except Exception as error:
        report.update(status="failed_copy_do_not_use", error=str(error))
        (destination / "ngf-copy-manifest.json").write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
        raise
    (destination / "ngf-copy-manifest.json").write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--trial", action="append", help="Repeat for selected trial basenames; omit to copy the full session")
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.destination, args.copy, args.trial), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
