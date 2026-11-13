#!/usr/bin/env python3
"""Local checks for this skill package. Run from anywhere: python3 scripts/check.py"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []

# Needles are split so this file itself does not contain those names.
INTERNAL_NEEDLES = (
    "prime" + "ak",
    "banrox-" + "review",
)

REQUIRED_README_HEADINGS = (
    "What it does",
    "Install",
    "Use",
    "Example",
    "Limitations",
    "License",
)

REQUIRED_SKILL_HEADINGS = (
    "Output format",
)

POSITIVE_COMPLIANCE = re.compile(
    r"(?i)\b(soc\s*2|pci(?:[\s-]?dss)?|iso\s*2700\d|fedramp)\b"
)
NEGATION = re.compile(
    r"(?i)\b(not|never|no|don't|do not|doesn't|does not|isn't|is not|neither|without)\b"
)
TRACTION_COUNT = re.compile(
    r"(?i)\b\d[\d,]*\+?\s*(users|customers|downloads|stars|members)\b"
)
TRACTION_PHRASE = re.compile(
    r"(?i)\b(our customers|our users|thousands of (?:users|customers)|millions of (?:users|customers))\b"
)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(
    r"(?<!\w)(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4})(?!\w)"
)
PRIVATE_IP = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})\b"
)
INTERNAL_PATH = re.compile("/" + "workspace" + "/|/" + "home" + "/box/")
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")
GITHUB_TOKEN = re.compile(r"\b(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{10,}\b")
GITLAB_TOKEN = re.compile(r"\b(?:glpat-|gldt-|glrt-|glptt-)[A-Za-z0-9_\-]{10,}\b")

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv"}
TEXT_SUFFIXES = {".md", ".py", ".txt", ".yml", ".yaml", ".sh", ".toml", ".json", ".gitignore", ""}


def error(msg: str) -> None:
    ERRORS.append(msg)


def iter_text_files() -> list[Path]:
    out: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in {".md", ".py", ".txt", ".yml", ".yaml", ".sh", ".toml", ".json"} or path.name in {
            "LICENSE",
            ".gitignore",
        }:
            out.append(path)
    return sorted(out)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter delimited by ---")
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        raise ValueError("SKILL.md frontmatter is not closed with ---")
    raw = rest[:end]
    body = rest[end + 4 :]
    data: dict[str, str] = {}
    key = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if key and (line.startswith("  ") or line.startswith("\t")):
            data[key] = data[key] + " " + line.strip()
            continue
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line[:40]}")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data, body


def heading_titles(text: str) -> set[str]:
    titles = set()
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if m:
            titles.add(m.group(1).strip())
    return titles


def check_skill() -> None:
    path = ROOT / "SKILL.md"
    if not path.is_file():
        error("missing SKILL.md")
        return
    text = path.read_text(encoding="utf-8")
    try:
        meta, body = parse_frontmatter(text)
    except ValueError as exc:
        error(f"SKILL.md frontmatter: {exc}")
        return
    if "name" not in meta or not meta["name"]:
        error("SKILL.md frontmatter missing name")
    elif meta["name"] != ROOT.name:
        error(f"SKILL.md name {meta['name']!r} does not match directory {ROOT.name!r}")
    if "description" not in meta or len(meta["description"].strip()) < 40:
        error("SKILL.md frontmatter description is missing or too short")
    titles = heading_titles(body)
    for required in REQUIRED_SKILL_HEADINGS:
        if required not in titles:
            error(f"SKILL.md missing required heading: {required}")
    if not re.search(r"^# .+", body, re.M):
        error("SKILL.md is missing a top-level heading")


def check_readme() -> set[str]:
    path = ROOT / "README.md"
    referenced: set[str] = set()
    if not path.is_file():
        error("missing README.md")
        return referenced
    text = path.read_text(encoding="utf-8")
    titles = heading_titles(text)
    for required in REQUIRED_README_HEADINGS:
        if required not in titles:
            error(f"README.md missing required heading: {required}")
    if "placeholder" not in text.lower() or "pending" not in text.lower():
        error("README.md License section must say the MIT text is a placeholder pending a final pick")
    if "MIT" not in text:
        error("README.md should mention MIT as the current placeholder licence")
    for match in MD_LINK.finditer(text):
        referenced.add(match.group(2).strip())
    example_links = [href for href in referenced if href.startswith("examples/")]
    if not example_links:
        error("README.md should link to at least one file under examples/")
    return referenced


def check_license() -> None:
    path = ROOT / "LICENSE"
    if not path.is_file():
        error("missing LICENSE")
        return
    text = path.read_text(encoding="utf-8")
    if not text.startswith("MIT License"):
        error("LICENSE does not start with 'MIT License'")
    if "Copyright (c) 2026 Banrox Inc." not in text:
        error("LICENSE must have copyright holder 'Banrox Inc.' and year 2026")
    if "Permission is hereby granted" not in text:
        error("LICENSE is missing expected MIT permission text")


def check_examples() -> None:
    folder = ROOT / "examples"
    if not folder.is_dir():
        error("missing examples/ directory")
        return
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")]
    if not files:
        error("examples/ has no files")


def check_links(referenced_from_readme: set[str]) -> None:
    # Collect markdown links from all markdown files and resolve local targets.
    for path in iter_text_files():
        if path.suffix.lower() != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for match in MD_LINK.finditer(text):
            href = match.group(2).strip()
            if href.startswith("#"):
                continue
            if " " in href and "://" not in href:
                # markdown image or title form: url plus optional title
                href = href.split()[0]
            if href.startswith("<") and href.endswith(">"):
                href = href[1:-1]
            if href.startswith(("http://", "https://", "mailto:")):
                # External links: syntax only (offline). Must be a well-formed URL.
                if href.startswith("mailto:"):
                    continue
                if not re.match(r"^https?://[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/.*)?$", href.split()[0]):
                    error(f"{path.relative_to(ROOT)}: malformed external link")
                continue
            if href.startswith("http:"):
                error(f"{path.relative_to(ROOT)}: malformed external link")
                continue
            target = href.split("#", 1)[0]
            if not target:
                continue
            dest = (path.parent / target).resolve()
            try:
                dest.relative_to(ROOT.resolve())
            except ValueError:
                error(f"{path.relative_to(ROOT)}: link escapes package root")
                continue
            if not dest.exists():
                error(f"{path.relative_to(ROOT)}: broken local link -> {target}")


def check_content_lint() -> None:
    for path in iter_text_files():
        rel = path.relative_to(ROOT)
        # Allow this checker to mention encoded policy needles only via concatenation.
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if path.name != "check.py":
                for needle in INTERNAL_NEEDLES:
                    if needle in lower:
                        error(f"{rel}:{i}: forbidden_internal_name")
            if INTERNAL_PATH.search(line):
                error(f"{rel}:{i}: internal_path")
            if AWS_KEY.search(line):
                error(f"{rel}:{i}: aws_access_key_id")
            if PRIVATE_KEY.search(line):
                error(f"{rel}:{i}: private_key_header")
            if GITHUB_TOKEN.search(line):
                error(f"{rel}:{i}: github_token")
            if GITLAB_TOKEN.search(line):
                error(f"{rel}:{i}: gitlab_token")
            if EMAIL.search(line) and path.name != "check.py":
                error(f"{rel}:{i}: email")
            if PHONE.search(line) and path.name != "check.py":
                error(f"{rel}:{i}: phone")
            if PRIVATE_IP.search(line) and path.name != "check.py":
                error(f"{rel}:{i}: private_ip")
            if path.name == "check.py":
                continue
            if POSITIVE_COMPLIANCE.search(line) and not NEGATION.search(line):
                error(f"{rel}:{i}: unsupported_compliance_claim")
            if TRACTION_COUNT.search(line) and not NEGATION.search(line):
                error(f"{rel}:{i}: traction_count_claim")
            if TRACTION_PHRASE.search(line) and not NEGATION.search(line):
                error(f"{rel}:{i}: traction_claim")


def main() -> int:
    if not (ROOT / "SKILL.md").exists() and not (ROOT / "README.md").exists():
        error(f"does not look like a skill package: {ROOT}")
    check_skill()
    referenced = check_readme()
    check_license()
    check_examples()
    check_links(referenced)
    check_content_lint()
    if ERRORS:
        print(f"FAIL {ROOT.name}: {len(ERRORS)} issue(s)")
        for item in ERRORS:
            print(f"  - {item}")
        return 1
    print(f"PASS {ROOT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
