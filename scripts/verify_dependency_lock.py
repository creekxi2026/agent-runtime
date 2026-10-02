"""Fail closed if either build job sees different dependency resolution."""
import hashlib
import json
import os
from pathlib import Path


def verify(path, expected_digest, expected_base):
    data = Path(path).read_bytes()
    actual = "sha256:" + hashlib.sha256(data).hexdigest()
    if actual != expected_digest:
        raise ValueError("Dependency artifact digest mismatch")
    lock = json.loads(data)
    if lock["base_image"] != expected_base:
        raise ValueError("Dependency artifact base mismatch")
    return lock


if __name__ == "__main__":
    verify("runtime-deps.json", os.environ["DEPENDENCY_DIGEST"], os.environ["BASE_IMAGE"])
    print("PASS common immutable dependency artifact")
