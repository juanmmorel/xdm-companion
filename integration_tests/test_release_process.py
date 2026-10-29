import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release


def test_patch_bump_uses_newest_stable_tag_not_only_manifest_version():
  assert release.next_version("1.1.30", ["v1.1.29", "v1.1.31", "v1.2.0-rc.1"], "patch") == "1.1.32"


def test_minor_and_major_bumps_reset_lower_segments():
  assert release.next_version("1.1.30", ["v1.1.31"], "minor") == "1.2.0"
  assert release.next_version("1.1.30", ["v1.1.31"], "major") == "2.0.0"


def test_release_tag_requires_both_manifests_to_match():
  assert release.validate_tag("v1.1.30", ["1.1.30", "1.1.30"]) == "1.1.30"

  try:
    release.validate_tag("v1.1.31", ["1.1.30", "1.1.30"])
  except release.ReleaseError as error:
    assert "no coincide" in str(error)
  else:
    raise AssertionError("A mismatched tag must be rejected.")


def test_release_tag_rejects_disagreeing_browser_manifests():
  try:
    release.validate_tag("v1.1.30", ["1.1.30", "1.1.29"])
  except release.ReleaseError as error:
    assert "no coinciden" in str(error)
  else:
    raise AssertionError("Different browser versions must be rejected.")


def test_bumping_updates_both_manifests_together(tmp_path):
  paths = [tmp_path / "chrome.json", tmp_path / "firefox.json"]
  for path in paths:
    path.write_text(json.dumps({"manifest_version": 3, "version": "1.1.30"}) + "\n")

  release.update_manifest_versions(paths, "1.1.32")

  assert [json.loads(path.read_text())["version"] for path in paths] == ["1.1.32", "1.1.32"]


def test_bump_command_uses_existing_tags_and_creates_matching_release_branch(tmp_path, monkeypatch):
  paths = [
      tmp_path / "browser-bridge" / "manifest.chrome.json",
      tmp_path / "browser-bridge" / "manifest.firefox.json",
  ]
  paths[0].parent.mkdir()
  for path in paths:
    path.write_text(json.dumps({"manifest_version": 3, "version": "1.1.30"}) + "\n")
  monkeypatch.setattr(release, "ROOT", tmp_path)
  monkeypatch.setattr(release, "MANIFESTS", tuple(paths))
  calls = []

  def fake_git(*arguments, capture_output=False, check=True):
    calls.append(arguments)
    output = ""
    if arguments == ("branch", "--show-current"):
      output = "main"
    elif arguments == ("rev-parse", "HEAD"):
      output = "same-commit"
    elif arguments == ("rev-parse", "origin/main"):
      output = "same-commit"
    elif arguments == ("tag", "--list", "v*"):
      output = "v1.1.31\n"
    return SimpleNamespace(stdout=output, returncode=0)

  monkeypatch.setattr(release, "run_git", fake_git)
  release.bump("patch")

  assert ("switch", "-c", "release/v1.1.32") in calls
  assert [json.loads(path.read_text())["version"] for path in paths] == ["1.1.32", "1.1.32"]
