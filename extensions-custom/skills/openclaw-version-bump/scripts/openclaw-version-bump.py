#!/usr/bin/env python3
"""
OpenClaw version sync utility.

Commands:
  latest  -> print the latest available OpenClaw release (tag/version)
  upgrade -> fetch and switch to a target OpenClaw version (tag/branch)
  show  -> print current tracked versions
  bump  -> update versions across tracked files (supports --dry-run)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shlex
import subprocess
import urllib.request
from urllib.error import URLError
from pathlib import Path
from typing import Dict, Optional, Tuple


def find_repo_root(start: Path) -> Path:
    path = start.resolve()
    for _ in range(12):
        if (path / "package.json").exists():
            return path
        path = path.parent
    raise SystemExit("Failed to locate OpenClaw repo root.")


def require(msg: str) -> None:
    raise SystemExit(msg)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def dump_diff(path: Path, before: str, after: str) -> str:
    if before == after:
        return ""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path.as_posix()}",
            tofile=f"b/{path.as_posix()}",
            lineterm="",
        )
    )


def parse_package_version(repo_root: Path) -> Tuple[str, str]:
    path = repo_root / "package.json"
    data = json.loads(read_text(path))
    version = data.get("version")
    if not isinstance(version, str):
        require(f"{path}: missing or invalid version")
    return version, read_text(path)


def set_package_version(repo_root: Path, new_version: str) -> Tuple[bool, str, str, str]:
    path = repo_root / "package.json"
    before = read_text(path)
    data = json.loads(before)
    before_version = data.get("version", "")
    if not isinstance(before_version, str):
        require(f"{path}: missing or invalid version")
    if before_version == new_version:
        return False, before_version, new_version, before
    data["version"] = new_version
    after = json.dumps(data, ensure_ascii=False, indent=2)
    after += "\n"
    return True, before_version, new_version, after


def set_gradle_versions(
    repo_root: Path,
    android_version_name: str,
    android_version_code: Optional[str],
) -> Dict[str, Tuple[bool, str, str]]:
    path = repo_root / "apps/android/app/build.gradle.kts"
    changes: Dict[str, Tuple[bool, str, str]] = {}

    name_before = re.compile(r'versionName\s*=\s*"([^"]+)"')
    before = read_text(path)
    match = name_before.search(before)
    if not match:
        require(f"{path}: unable to locate versionName")
    old_name = match.group(1)
    if old_name != android_version_name:
        new_text = re.sub(r'versionName\s*=\s*"[^"]+"', f'versionName = "{android_version_name}"', before, count=1)
        changes["versionName"] = (True, old_name, android_version_name)
    else:
        new_text = before
        changes["versionName"] = (False, old_name, android_version_name)

    if android_version_code is None:
        changes["versionCode"] = (False, "", "")
        return {**changes, "text": (True, before, new_text)}  # type: ignore

    code_before_re = re.compile(r"versionCode\s*=\s*(\d+)")
    match_code = code_before_re.search(new_text)
    if not match_code:
        require(f"{path}: unable to locate versionCode")
    old_code = match_code.group(1)
    if old_code != android_version_code:
        after_text = re.sub(r"versionCode\s*=\s*\d+", f"versionCode = {android_version_code}", new_text, count=1)
        changes["versionCode"] = (True, old_code, android_version_code)
        return {"text": (True, before, after_text), **changes}

    return {"text": (False, before, new_text), **changes}


def set_plist_string(path: Path, key: str, new_value: str) -> Tuple[bool, str, str, str]:
    before = read_text(path)
    regex = re.compile(
        rf"(<key>{re.escape(key)}</key>\s*\n\s*<string>)([^<]+)(</string>)",
        re.MULTILINE,
    )
    match = regex.search(before)
    if not match:
        require(f"{path}: unable to locate {key}")
    old = match.group(2)
    if old == new_value:
        return False, old, new_value, before
    after = regex.sub(rf"\g<1>{new_value}\g<3>", before, count=1)
    return True, old, new_value, after


def get_versions(repo_root: Path) -> Dict[str, str]:
    # package
    pkg_version, _ = parse_package_version(repo_root)

    # android
    android_path = repo_root / "apps/android/app/build.gradle.kts"
    android_text = read_text(android_path)
    android_name = re.search(r'versionName\s*=\s*"([^"]+)"', android_text)
    if not android_name:
        require(f"{android_path}: unable to locate versionName")
    android_code = re.search(r"versionCode\s*=\s*(\d+)", android_text)
    if not android_code:
        require(f"{android_path}: unable to locate versionCode")

    # iOS/mac plist
    ios_path = repo_root / "apps/ios/Sources/Info.plist"
    ios_test_path = repo_root / "apps/ios/Tests/Info.plist"
    mac_path = repo_root / "apps/macos/Sources/OpenClaw/Resources/Info.plist"

    def pick(path: Path, key: str) -> str:
        text = read_text(path)
        match = re.search(
            rf"<key>{re.escape(key)}</key>\s*\n\s*<string>([^<]+)</string>",
            text,
        )
        if not match:
            require(f"{path}: unable to locate {key}")
        return match.group(1)

    return {
        "package.version": pkg_version,
        "android.versionName": android_name.group(1),
        "android.versionCode": android_code.group(1),
        "ios.version": pick(ios_path, "CFBundleShortVersionString"),
        "ios.build": pick(ios_path, "CFBundleVersion"),
        "ios-test.version": pick(ios_test_path, "CFBundleShortVersionString"),
        "ios-test.build": pick(ios_test_path, "CFBundleVersion"),
        "mac.version": pick(mac_path, "CFBundleShortVersionString"),
        "mac.build": pick(mac_path, "CFBundleVersion"),
    }


def show_versions(repo_root: Path) -> None:
    versions = get_versions(repo_root)
    for k, v in versions.items():
        print(f"{k}={v}")


def run_git_command(
    repo_root: Path,
    args: list[str],
    *,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=False,
        capture_output=capture_output,
        text=True,
    )
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() if completed.stderr else ""
        command_str = shlex.join(["git", *args])
        raise SystemExit(f"Git command failed: {command_str}\n{stderr}")
    return completed


def assert_clean_or_confirm(repo_root: Path, *, auto_confirm: bool) -> None:
    status = run_git_command(
        repo_root,
        ["status", "--porcelain"],
        capture_output=True,
        check=True,
    ).stdout.strip()
    if not status:
        return

    print("Working tree has uncommitted changes:")
    print(status)
    if auto_confirm:
        return

    confirm = input("Continue and force switch anyway? [yes/N] ")
    if confirm.strip().lower() != "yes":
        raise SystemExit("Canceled by user.")


def _collect_release_tags(raw_lines: list[str]) -> list[str]:
    release_re = re.compile(r"^v?\d+\.\d+\.\d+$")
    tags: list[str] = []
    seen = set[str]()
    for item in raw_lines:
        tag = item.strip()
        if not tag:
            continue
        if release_re.match(tag) and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tags


def _pick_latest_release_tag(tags: list[str]) -> Optional[str]:
    def parse_version(tag: str) -> tuple[int, int, int]:
        parts = tag.lstrip("v").split(".")
        return (int(parts[0]), int(parts[1]), int(parts[2]))

    if not tags:
        return None
    return sorted(tags, key=parse_version, reverse=True)[0]


def _parse_remote_tag_lines(raw_lines: list[str]) -> Optional[str]:
    # ls-remote returns:
    # <sha>\trefs/tags/<tag> and annotated tags may also include \trefs/tags/<tag>^{} entries.
    tags: list[str] = []
    seen: set[str] = set()
    for line in raw_lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        match = re.match(r"^refs/tags/(.+?)(?:\^\{\})?$", ref)
        if not match:
            continue
        tag = match.group(1)
        if re.match(r"^v?\d+\.\d+\.\d+$", tag) and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return _pick_latest_release_tag(tags)


def _resolve_origin_urls(repo_root: Path) -> list[str]:
    # Read origin URL then normalize to candidate remote strings.
    candidates: list[str] = []
    remote_url = run_git_command(
        repo_root,
        ["remote", "get-url", "origin"],
        check=False,
        capture_output=True,
    )
    if remote_url.returncode == 0:
        value = remote_url.stdout.strip()
        if value:
            candidates.append(value)

    # Fallback to env override and known upstream.
    explicit = os.getenv("OPENCLAW_GITHUB_REPO_URL", "")
    if explicit:
        candidates.append(explicit.strip())
    # Default upstream fallback for this skill.
    if not candidates or all("openclaw/openclaw" not in item for item in candidates):
        candidates.append("https://github.com/openclaw/openclaw.git")

    # De-dup
    uniq: list[str] = []
    seen = set[str]()
    for url in candidates:
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        uniq.append(url)
    return uniq


def find_latest_release_tag(repo_root: Path) -> Optional[str]:
    candidates = run_git_command(
        repo_root,
        [
            "for-each-ref",
            "--sort=-creatordate",
            "--format=%(refname:short)",
            "refs/tags",
        ],
        check=True,
        capture_output=True,
    ).stdout.splitlines()
    local_candidates = _collect_release_tags(candidates)
    latest = _pick_latest_release_tag(local_candidates)
    if latest:
        return latest

    return None


def find_latest_remote_tag(repo_root: Path) -> Optional[str]:
    remote_candidates = _resolve_origin_urls(repo_root)
    for remote in remote_candidates:
        ls_remote = run_git_command(
            repo_root,
            ["ls-remote", "--tags", remote],
            check=False,
            capture_output=True,
        )
        if ls_remote.returncode != 0:
            continue
        latest = _parse_remote_tag_lines(ls_remote.stdout.splitlines())
        if latest:
            return latest
    return None


def find_latest_remote_tag_from_github_api(repo_root: Path) -> Optional[str]:
    # Public endpoint, works for public repo without credentials.
    api_url = "https://api.github.com/repos/openclaw/openclaw/tags?per_page=50"
    try:
        with urllib.request.urlopen(api_url, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, TimeoutError, ValueError):
        return None
    except Exception:
        return None

    if not isinstance(payload, list):
        return None

    tags: list[str] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        tag = item.get("name")
        if not isinstance(tag, str):
            continue
        if not re.match(r"^v?\d+\.\d+\.\d+$", tag) or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
    return _pick_latest_release_tag(tags)


def find_latest_version_from_package(repo_root: Path) -> Optional[str]:
    version, _ = parse_package_version(repo_root)
    if re.match(r"^\d+\.\d+\.\d+$", version):
        return version
    return None


def find_latest_release_tag_with_fallback(repo_root: Path) -> str:
    latest = find_latest_release_tag(repo_root)
    if latest:
        return latest

    latest = find_latest_remote_tag(repo_root)
    if latest:
        return latest

    latest = find_latest_remote_tag_from_github_api(repo_root)
    if latest:
        return latest

    latest = find_latest_version_from_package(repo_root)
    if latest:
        print(
            "No valid release tags found locally or on origin; "
            f"using package.json version as fallback ({latest}).",
        )
        return latest

    require("No valid OpenClaw release tags found locally or on origin.")

def run_latest(_: argparse.Namespace, repo_root: Path) -> None:
    latest = find_latest_release_tag_with_fallback(repo_root)
    print(f"latest_version={latest}")
    print(f"latest_version_without_prefix={latest.lstrip('v')}")


def run_upgrade(args: argparse.Namespace, repo_root: Path) -> None:
    target = args.target.strip()
    if not target:
        require("upgrade requires a target version/tag/branch")

    print(f"Upgrading OpenClaw repo at {repo_root}")
    print(f"Fetching latest refs from origin...")
    run_git_command(repo_root, ["fetch", "--tags", "--force", "origin"], check=True, capture_output=False)

    def _resolve_ref(ref_candidate: str) -> Optional[str]:
        direct = f"refs/tags/{ref_candidate}"
        if run_git_command(
            repo_root,
            ["show-ref", "--verify", "--quiet", direct],
            check=False,
            capture_output=True,
        ).returncode == 0:
            return direct
        # Branch heads are also supported as explicit override when user passes branch style.
        branch_ref = f"refs/heads/{ref_candidate}"
        if run_git_command(
            repo_root,
            ["show-ref", "--verify", "--quiet", branch_ref],
            check=False,
            capture_output=True,
        ).returncode == 0:
            return branch_ref
        origin_head = f"refs/remotes/origin/{ref_candidate}"
        if run_git_command(
            repo_root,
            ["show-ref", "--verify", "--quiet", origin_head],
            check=False,
            capture_output=True,
        ).returncode == 0:
            return origin_head
        return None

    candidates = [target]
    if not target.startswith("v"):
        candidates.append(f"v{target}")
    else:
        candidates.append(target.lstrip("v"))

    resolved_ref: Optional[str] = None
    for candidate in candidates:
        resolved = _resolve_ref(candidate)
        if resolved:
            resolved_ref = resolved
            resolved_label = candidate
            break

    if not resolved_ref:
        candidates_text = ", ".join(candidates)
        require(
            f"Could not resolve target '{target}'. Tried: {candidates_text}. "
            "Ensure this tag/branch exists locally or on origin after fetch.",
        )

    if not args.yes:
        assert_clean_or_confirm(repo_root, auto_confirm=False)
    else:
        assert_clean_or_confirm(repo_root, auto_confirm=True)

    if args.branch:
        branch_name = args.branch
        print(f"Switching to branch {branch_name} and hard-resetting to {resolved_label}")
        run_git_command(repo_root, ["checkout", branch_name], check=False, capture_output=False)
        run_git_command(repo_root, ["reset", "--hard", resolved_ref], check=True, capture_output=False)
        print(f"Branch {branch_name} updated -> {resolved_ref}")
    else:
        print(f"Checking out {resolved_label} in detached HEAD mode")
        run_git_command(repo_root, ["checkout", "--detach", "-q", resolved_ref], check=True, capture_output=False)
        print(f"Detached at {resolved_label} ({resolved_ref})")

    current = run_git_command(repo_root, ["rev-parse", "--short", "HEAD"], capture_output=True, check=True).stdout.strip()
    print(f"Current HEAD: {current}")
    print("Run `openclaw-version-bump show` to verify synced versions.")


def apply_text_if_changed(files: Dict[str, str], dry_run: bool) -> bool:
    any_changed = False
    for path_str, text in files.items():
        path = Path(path_str)
        before = read_text(path)
        if before == text:
            continue
        diff = dump_diff(path, before, text)
        if dry_run:
            if diff:
                print(diff)
        else:
            write_text(path, text)
            print(f"updated: {path}")
        any_changed = True
    return any_changed


def run_bump(args: argparse.Namespace, repo_root: Path) -> None:
    version = args.version
    if not version:
        require("bump requires a version value")

    # Resolve each target value.
    targets = {
        "package": version,
        "android_version_name": args.android_version_name or version,
        "ios_version": args.ios_version or version,
        "ios_test_version": args.ios_test_version or args.ios_version or version,
        "mac_version": args.mac_version or version,
    }
    if args.android_version_code is not None:
        if not args.android_version_code.isdigit():
            require("--android-version-code must be digits")

    package_changed, pkg_old, pkg_new, package_after = set_package_version(repo_root, targets["package"])
    print(f"package.json: version {pkg_old} -> {pkg_new}" if package_changed else "package.json: unchanged")

    gradle_path = repo_root / "apps/android/app/build.gradle.kts"
    gradle_result = set_gradle_versions(
        repo_root=repo_root,
        android_version_name=targets["android_version_name"],
        android_version_code=args.android_version_code,
    )
    gradle_text = gradle_result["text"][2]  # type: ignore[index]
    print(
        "android versionName: "
        f"{gradle_result['versionName'][1]} -> {gradle_result['versionName'][2]}"
        + (" (changed)" if gradle_result["versionName"][0] else " (unchanged)")
    )
    if args.android_version_code is not None:
        vc_changed, vc_old, vc_new, _ = gradle_result["versionCode"]  # type: ignore
        print(f"android versionCode: {vc_old} -> {vc_new}" + (" (changed)" if vc_changed else " (unchanged)"))
    else:
        print("android versionCode: unchanged (not specified)")

    ios_path = repo_root / "apps/ios/Sources/Info.plist"
    ios_test_path = repo_root / "apps/ios/Tests/Info.plist"
    mac_path = repo_root / "apps/macos/Sources/OpenClaw/Resources/Info.plist"

    ios_text = read_text(ios_path)
    ios_changed_ver, ios_old_ver, ios_new_ver, ios_text = set_plist_string(
        ios_path,
        "CFBundleShortVersionString",
        targets["ios_version"],
    )
    print(f"ios CFBundleShortVersionString: {ios_old_ver} -> {ios_new_ver}" + (" (changed)" if ios_changed_ver else " (unchanged)"))
    ios_build_text = ios_text
    if args.ios_build is not None:
        ios_changed_build, ios_old_build, ios_new_build, ios_build_text = set_plist_string(
            ios_path,
            "CFBundleVersion",
            args.ios_build,
        )
        print(f"ios CFBundleVersion: {ios_old_build} -> {ios_new_build}" + (" (changed)" if ios_changed_build else " (unchanged)"))
    else:
        print("ios CFBundleVersion: unchanged (not specified)")

    ios_test_ver_text = ios_build_text
    ios_test_changed_ver, ios_test_old_ver, ios_test_new_ver, ios_test_ver_text = set_plist_string(
        ios_test_path,
        "CFBundleShortVersionString",
        targets["ios_test_version"],
    )
    print(
        f"ios tests CFBundleShortVersionString: {ios_test_old_ver} -> {ios_test_new_ver}"
        + (" (changed)" if ios_test_changed_ver else " (unchanged)")
    )
    if args.ios_test_build is not None:
        ios_test_changed_build, ios_test_old_build, ios_test_new_build, ios_test_build_text = set_plist_string(
            ios_test_path,
            "CFBundleVersion",
            args.ios_test_build,
        )
        print(
            f"ios tests CFBundleVersion: {ios_test_old_build} -> {ios_test_new_build}"
            + (" (changed)" if ios_test_changed_build else " (unchanged)")
        )
        ios_test_ver_text = ios_test_build_text
    else:
        print("ios tests CFBundleVersion: unchanged (not specified)")

    mac_text = read_text(mac_path)
    mac_changed_ver, mac_old_ver, mac_new_ver, mac_text = set_plist_string(
        mac_path,
        "CFBundleShortVersionString",
        targets["mac_version"],
    )
    print(f"mac CFBundleShortVersionString: {mac_old_ver} -> {mac_new_ver}" + (" (changed)" if mac_changed_ver else " (unchanged)"))
    if args.mac_build is not None:
        mac_changed_build, mac_old_build, mac_new_build, mac_text = set_plist_string(
            mac_path,
            "CFBundleVersion",
            args.mac_build,
        )
        print(f"mac CFBundleVersion: {mac_old_build} -> {mac_new_build}" + (" (changed)" if mac_changed_build else " (unchanged)"))
    else:
        print("mac CFBundleVersion: unchanged (not specified)")

    plans = {
        str(repo_root / "package.json"): package_after,
        str(gradle_path): gradle_text,
        str(ios_path): ios_build_text,
        str(ios_test_path): ios_test_ver_text,
        str(mac_path): mac_text,
    }

    if args.dry_run:
        print("\n[plan] Dry-run output:")
        changed = apply_text_if_changed(plans, True)
        if not changed:
            print("No changes to apply.")
        return

    if not args.yes:
        confirm = input("Apply changes to all listed files? [yes/N] ")
        if confirm.strip().lower() != "yes":
            print("Canceled by user.")
            return

    _ = apply_text_if_changed(plans, False)
    print("Done.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw version maintenance helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    show_parser = subparsers.add_parser("show", help="Display tracked OpenClaw versions")
    show_parser.set_defaults(func=lambda args, repo_root: show_versions(repo_root))

    latest_parser = subparsers.add_parser("latest", help="Print latest OpenClaw tag")
    latest_parser.set_defaults(func=run_latest)

    upgrade_parser = subparsers.add_parser("upgrade", help="Fetch and switch to a target OpenClaw version")
    upgrade_parser.add_argument("target", help="Target version/tag/branch, e.g. 2026.3.8 or v2026.3.8")
    upgrade_parser.add_argument(
        "--branch",
        help="Switch this local branch first, then hard-reset it to target (instead of detached checkout)",
    )
    upgrade_parser.add_argument("--yes", action="store_true", help="Apply without confirmation")
    upgrade_parser.set_defaults(func=run_upgrade)

    bump_parser = subparsers.add_parser("bump", help="Update tracked OpenClaw version files")
    bump_parser.add_argument("version", help="Target version, e.g. 2026.3.4")
    bump_parser.add_argument("--android-version-name", help="Override Android versionName")
    bump_parser.add_argument("--android-version-code", help="Override Android versionCode")
    bump_parser.add_argument("--ios-version", help="Override iOS short version")
    bump_parser.add_argument("--ios-test-version", help="Override iOS test bundle short version")
    bump_parser.add_argument("--mac-version", help="Override macOS short version")
    bump_parser.add_argument("--ios-build", help="Override iOS CFBundleVersion")
    bump_parser.add_argument("--ios-test-build", help="Override iOS tests CFBundleVersion")
    bump_parser.add_argument("--mac-build", help="Override macOS CFBundleVersion")
    bump_parser.add_argument("--dry-run", action="store_true", help="Print planned file diffs without writing")
    bump_parser.add_argument("--yes", action="store_true", help="Apply without confirmation")
    bump_parser.set_defaults(func=run_bump)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    repo_root = find_repo_root(Path(__file__).resolve())
    args.func(args, repo_root)


if __name__ == "__main__":
    main()
