"""Create private candidate files from tracked, generic examples.

Run from any directory: python scripts/init_personal_files.py
Existing files are never overwritten.
"""

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "examples" / "personal_files"
DEST = ROOT / "personal_files"


def main() -> None:
    copied = []
    for source in SOURCE.rglob("*"):
        if not source.is_file():
            continue
        dest = DEST / source.relative_to(SOURCE)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(source, dest)
            copied.append(dest.relative_to(ROOT))
    (DEST / "cv_generated").mkdir(parents=True, exist_ok=True)
    if copied:
        print("Created:")
        for path in copied:
            print(f"  {path}")
    else:
        print("Personal files already exist; nothing overwritten.")
    print("Edit personal_files/profile_brief.md, target_job.md, and cv_sections/ before AI scoring or CV generation.")


if __name__ == "__main__":
    main()
