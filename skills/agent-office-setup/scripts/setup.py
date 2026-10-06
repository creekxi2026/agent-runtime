#!/usr/bin/env python3
"""Per-HOME office dependencies and one-time skill retirement; no credentials."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


NAME = "agent-office-setup"
PACKAGES = {
    "basic": None,
    "xlsx": "openpyxl>=3.1,<4",
    "docx": "python-docx>=1.2,<2",
    "pdf": "pypdf>=6,<7",
}
PROBES = {
    "basic": '''import csv, io, json
s = io.StringIO(); csv.writer(s).writerow(["name", "办公"])
assert list(csv.reader(io.StringIO(s.getvalue())))[0][1] == "办公"
assert json.loads(json.dumps({"ok": True}))["ok"]
''',
    "xlsx": '''from openpyxl import Workbook, load_workbook
from io import BytesIO
f = BytesIO(); w = Workbook(); w.active["A1"] = "办公"; w.save(f)
f.seek(0); assert load_workbook(f).active["A1"].value == "办公"
''',
    "docx": '''from docx import Document
from io import BytesIO
f = BytesIO(); d = Document(); d.add_paragraph("办公"); d.save(f)
f.seek(0); assert Document(f).paragraphs[0].text == "办公"
''',
    "pdf": '''from pypdf import PdfWriter, PdfReader
from io import BytesIO
f = BytesIO(); w = PdfWriter(); w.add_blank_page(72, 72); w.write(f)
f.seek(0); assert len(PdfReader(f).pages) == 1
''',
}


class Setup:
    def __init__(self, home, skill):
        self.home = Path(home).resolve()
        self.skill = Path(skill).absolute()
        self.expected_skill = self.home / ".agents/skills" / NAME
        self.root = self.home / ".local/share" / NAME
        self.state_file = self.root / "state.json"
        self.python = self.root / "venv/bin/python"

    def private_path(self, path):
        relative = path.relative_to(self.home)
        current = self.home
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                raise ValueError("Refusing symlink in private setup path: " + str(current))

    def check_installation(self):
        if self.skill != self.expected_skill:
            raise ValueError("Install a private copy at " + str(self.expected_skill))
        self.private_path(self.skill)
        if not (self.skill / "SKILL.md").is_file():
            raise ValueError("Installed SKILL.md is missing")

    @contextmanager
    def exclusive(self):
        self.check_installation()
        lock = self.root / "setup.lock"
        self.private_path(lock)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another conversation is configuring this HOME; resume later")
            yield
        finally:
            os.close(descriptor)

    def read(self):
        self.private_path(self.state_file)
        if not self.state_file.exists():
            return {"schema": 1, "phase": "not_started", "features": []}
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema") != 1 or
                not isinstance(data.get("phase"), str) or data.get("phase") not in
                {"installing", "installed", "verified", "complete"} or
                not isinstance(data.get("features"), list) or
                any(not isinstance(x, str) or x not in PACKAGES for x in data["features"])):
            raise ValueError("Unknown or damaged setup state; preserve it for review")
        return data

    def write(self, data):
        self.private_path(self.state_file)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root,
                                         delete=False) as output:
            json.dump(data, output, ensure_ascii=False, indent=2)
            output.write("\n")
            temporary = Path(output.name)
        try:
            os.replace(temporary, self.state_file)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def run(argv, **kwargs):
        return subprocess.run(argv, check=True, timeout=300, **kwargs)

    def install(self, features):
        self.check_installation()
        data = self.read()
        if data["phase"] == "complete":
            raise ValueError("Setup is complete; do not reinstall on later conversations")
        chosen = sorted(set(data["features"]) | set(features))
        if not chosen or any(x not in PACKAGES for x in chosen):
            raise ValueError("Select at least one supported feature (basic requires no packages)")
        data.update(phase="installing", features=chosen)
        data.pop("verified_features", None)
        self.write(data)  # Failed installation remains resumable, never complete.
        packages = [PACKAGES[x] for x in chosen if PACKAGES[x]]
        if packages:
            self.private_path(self.root / "venv")
            if not self.python.exists():
                self.run([sys.executable, "-m", "venv", str(self.root / "venv")])
            self.run([str(self.python), "-m", "pip", "install",
                      "--disable-pip-version-check", "--only-binary=:all:",
                      "--retries", "1", "--timeout", "30", *packages])
            result = self.run([str(self.python), "-m", "pip", "list", "--format=json",
                               "--disable-pip-version-check"],
                              capture_output=True, text=True)
            data["installed_versions"] = json.loads(result.stdout)
        data["phase"] = "installed"
        self.write(data)
        return data

    def verify(self):
        self.check_installation()
        data = self.read()
        if data["phase"] == "complete":
            return data
        if data["phase"] not in {"installed", "verified"} or not data["features"]:
            raise ValueError("Run install successfully before verification")
        self.private_path(self.root / "venv")
        # Mark stale previous checks unverified before trying them again.
        data.update(phase="installed", verified_features=[])
        self.write(data)
        for feature in data["features"]:
            executable = sys.executable if feature == "basic" else str(self.python)
            self.run([executable, "-I", "-c", PROBES[feature]])
        data.update(phase="verified", verified_features=list(data["features"]))
        self.write(data)
        return data

    def finish(self, task):
        self.check_installation()  # Never retire repository/shared/source copies.
        if not task.strip():
            raise ValueError("Record the actual verified and delivered user task")
        data = self.verify()
        if data["phase"] != "complete":
            data.update(phase="complete", verified_task=task.strip(),
                        completed_at=datetime.now(timezone.utc).isoformat())
            self.write(data)
        # Write before removing ourselves: an interruption cannot restart onboarding.
        # shutil.rmtree removes contained links, without following their targets.
        shutil.rmtree(self.skill)
        return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    install = commands.add_parser("install")
    install.add_argument("--features", nargs="+", choices=PACKAGES, required=True)
    commands.add_parser("verify")
    finish = commands.add_parser("finish")
    finish.add_argument("--verified-task", required=True)
    args = parser.parse_args()
    setup = Setup(Path.home(), Path(__file__).absolute().parent.parent)
    if args.command == "status":
        data = setup.read()
    else:
        with setup.exclusive():
            if args.command == "install":
                data = setup.install(args.features)
            elif args.command == "verify":
                data = setup.verify()
            else:
                data = setup.finish(args.verified_task)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("Office setup not finished: " + str(error), file=sys.stderr)
        sys.exit(1)
