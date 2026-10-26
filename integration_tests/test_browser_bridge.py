import json
import os
import shutil
import sys
import threading
import zipfile
from types import SimpleNamespace

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../installer"))
)

import browser_bridge
import install


def test_browser_report_identifies_chrome_as_ready_and_firefox_signature_blocker():
  statuses = [
      ("Chrome", "integración instalada; se activa al abrir Chrome de nuevo"),
      ("Firefox", "pendiente de firma de Mozilla; el XPI para publicar está en /tmp/bridge.xpi"),
  ]

  assert install.browser_integration_report(statuses) == [
      ("Chrome", True, "detección automática registrada"),
      (
          "Firefox",
          False,
          "requiere un XPI firmado por Mozilla; este paquete todavía no incluye esa firma",
      ),
  ]
  assert not install.browser_integrations_ready(statuses)


def test_browser_integrations_ready_when_all_reported_browsers_are_installed():
  statuses = [
      ("Chrome", "integración instalada; se activa al abrir Chrome de nuevo"),
      ("Firefox", "política registrada; Firefox activa el puente al reiniciarse"),
  ]

  assert install.browser_integrations_ready(statuses)


def test_build_extension_scopes_permissions_to_configured_sites(tmp_path):
  source = tmp_path / "source"
  source.mkdir()
  for filename in ("background.js", "content.js", "manifest.chrome.json", "manifest.firefox.json"):
    (source / filename).write_text(
        (browser_bridge.SOURCE_DIR / filename).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
  shutil.copytree(browser_bridge.SOURCE_DIR / "icons", source / "icons")
  shutil.copytree(browser_bridge.SOURCE_DIR / "providers", source / "providers")
  domains_file = tmp_path / "domains.json"
  domains_file.write_text('{"complex_domains": ["youtube.com", "youtube.com", "tiktok.com"]}')

  chrome_dir = browser_bridge.build_extension(source, domains_file, tmp_path / "chrome", "chrome")
  firefox_dir = browser_bridge.build_extension(source, domains_file, tmp_path / "firefox", "firefox")
  chrome = json.loads((chrome_dir / "manifest.json").read_text())
  firefox = json.loads((firefox_dir / "manifest.json").read_text())

  assert "*://youtube.com/*" in chrome["content_scripts"][0]["matches"]
  assert chrome["content_scripts"][0]["run_at"] == "document_start"
  assert chrome["content_scripts"][0]["all_frames"] is True
  assert chrome["content_scripts"][0]["js"] == ["site-config.js", "content.js"]
  assert chrome["version"] == "1.1.18"
  assert firefox["version"] == "1.1.18"
  assert chrome["manifest_version"] == 3
  assert firefox["manifest_version"] == 3
  assert chrome["permissions"] == ["cookies", "scripting"]
  assert firefox["permissions"] == ["cookies", "scripting"]
  assert "action" not in chrome
  assert "browser_action" not in firefox
  assert "service_worker" not in firefox["background"]
  assert "scripts" in firefox["background"]
  assert firefox["browser_specific_settings"]["gecko"]["strict_min_version"] == "140.0"
  assert firefox["browser_specific_settings"]["gecko_android"]["strict_min_version"] == "142.0"
  assert firefox["browser_specific_settings"]["gecko"]["data_collection_permissions"]["required"] == [
      "browsingActivity", "websiteActivity", "websiteContent",
  ]
  assert chrome["icons"]["128"] == "icons/companion-128.png"
  assert (chrome_dir / "icons" / "companion-128.png").is_file()
  assert firefox["icons"]["128"] == "icons/companion-128.png"
  assert (firefox_dir / "icons" / "companion-128.png").is_file()
  assert "*://*.youtube.com/*" in chrome["host_permissions"]
  assert "http://127.0.0.1:21121/*" in firefox["host_permissions"]
  assert "*://*.tiktok.com/*" in firefox["content_scripts"][0]["matches"]
  assert len([item for item in chrome["host_permissions"] if "youtube.com" in item]) == 2
  assert (chrome_dir / "providers" / "youtube.js").is_file()
  assert (chrome_dir / "site-config.js").is_file()


def test_firefox_xpi_packages_the_generated_manifest(tmp_path):
  xpi = browser_bridge.create_firefox_xpi(
      browser_bridge.SOURCE_DIR,
      browser_bridge.DOMAINS_FILE,
      tmp_path / "bridge.xpi",
  )

  with zipfile.ZipFile(xpi) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    assert "*://*.youtube.com/*" in manifest["content_scripts"][0]["matches"]
    assert "background.js" in archive.namelist()
    assert "providers/youtube.js" in archive.namelist()
    assert "providers/youtube.js" in manifest["background"]["scripts"]
    assert "site-config.js" in archive.namelist()
    assert "icons/companion-128.png" in archive.namelist()
  assert not browser_bridge.contains_firefox_signature_metadata(xpi)


def test_provider_browser_adapter_module_is_loaded_from_catalog_configuration(tmp_path):
  source = tmp_path / "source"
  source.mkdir()
  for filename in ("background.js", "content.js", "manifest.chrome.json", "manifest.firefox.json"):
    (source / filename).write_text(
        (browser_bridge.SOURCE_DIR / filename).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
  shutil.copytree(browser_bridge.SOURCE_DIR / "icons", source / "icons")
  (source / "providers").mkdir()
  (source / "providers" / "sample.js").write_text("globalThis.XDM_SITE_ADAPTERS.sample = {};\n")
  domains_file = tmp_path / "domains.json"
  domains_file.write_text(json.dumps({"providers": [{
      "id": "sample-site",
      "domains": ["sample.test"],
      "browser": {"adapter": "sample", "module": "sample.js"},
      "backend": {},
  }]}))

  firefox_dir = browser_bridge.build_extension(
      source, domains_file, tmp_path / "sample-firefox", "firefox",
  )
  manifest = json.loads((firefox_dir / "manifest.json").read_text())
  assert manifest["background"]["scripts"] == [
      "site-config.js", "providers/sample.js", "background.js",
  ]


def test_firefox_package_check_requires_signature_metadata_files(tmp_path):
  unsigned = tmp_path / "unsigned.xpi"
  signed = tmp_path / "signed.xpi"
  with zipfile.ZipFile(unsigned, "w") as archive:
    archive.writestr("manifest.json", "{}")
  with zipfile.ZipFile(signed, "w") as archive:
    archive.writestr("META-INF/mozilla.rsa", b"signature")
    archive.writestr("META-INF/mozilla.sf", b"signature file")

  assert not browser_bridge.contains_firefox_signature_metadata(unsigned)
  assert browser_bridge.contains_firefox_signature_metadata(signed)


def test_chrome_store_archive_contains_manifest_at_zip_root(tmp_path):
  package = browser_bridge.create_chrome_store_zip(
      browser_bridge.SOURCE_DIR,
      browser_bridge.DOMAINS_FILE,
      tmp_path / "chrome-store.zip",
  )

  with zipfile.ZipFile(package) as archive:
    names = set(archive.namelist())
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["manifest_version"] == 3
    assert manifest["permissions"] == ["cookies", "scripting"]
    assert manifest["content_scripts"][0]["js"] == ["site-config.js", "content.js"]
    assert "background.js" in names
    assert "site-config.js" in names
    assert "providers/youtube.js" in names
    assert "manifest/manifest.json" not in names


def test_firefox_install_payload_path_is_visible_to_snap_home_interface(tmp_path):
  path = browser_bridge.firefox_install_xpi_path(tmp_path)

  assert path == tmp_path / "XDM Companion" / "browser-bridge" / "xdm-companion-bridge-firefox.xpi"
  assert not any(part.startswith(".") for part in path.relative_to(tmp_path).parts)


def test_linux_installer_reports_firefox_signature_as_pending(tmp_path, monkeypatch):
  monkeypatch.setattr(browser_bridge, "find_chrome_binary", lambda: None)

  statuses = browser_bridge.install_linux_browser_bridges(tmp_path / "companion")

  assert any(name == "Firefox" and "pendiente de firma" in status for name, status in statuses)
  assert (tmp_path / "companion/browser-bridge/xdm-companion-bridge-firefox-unlisted.xpi").is_file()


def test_installer_distinguishes_full_browser_setup_from_partial_setup():
  statuses = [
      ("Chrome", "integración instalada; se activa al reiniciar"),
      ("Firefox", "política registrada; Firefox verifica la firma"),
  ]
  runtime = {"name": "node", "path": "/usr/bin/node"}

  assert install.browser_integrations_ready(statuses)
  assert install.installation_ready(runtime, statuses)
  assert not install.installation_ready(None, statuses)
  assert not install.browser_integrations_ready([
      ("Chrome", "integración instalada; se activa al reiniciar"),
      ("Firefox", "pendiente de firma de Mozilla"),
  ])


def test_linux_installer_copies_signed_firefox_xpi_to_policy_accessible_path(tmp_path, monkeypatch):
  monkeypatch.setattr(browser_bridge, "find_chrome_binary", lambda: None)
  monkeypatch.setattr(browser_bridge, "contains_firefox_signature_metadata", lambda _path: True)
  configured = {}

  def fake_install_policy(xpi_path, policy_path):
    configured["xpi"] = xpi_path
    configured["policy"] = policy_path

  monkeypatch.setattr(browser_bridge, "install_firefox_policy", fake_install_policy)
  signed_xpi = tmp_path / "signed.xpi"
  signed_xpi.write_bytes(b"signed package fixture")
  destination = tmp_path / "XDM Companion" / "browser-bridge" / "bridge.xpi"

  statuses = browser_bridge.install_linux_browser_bridges(
      tmp_path / "companion",
      firefox_signed_xpi=signed_xpi,
      firefox_payload_path=destination,
  )

  assert destination.read_bytes() == signed_xpi.read_bytes()
  assert configured["xpi"] == destination
  assert any(name == "Firefox" and "política registrada" in status for name, status in statuses)
  state = json.loads((tmp_path / "companion/browser-bridge/install-state.json").read_text())
  assert state["firefox"]["xpi_file"] == str(destination)


def test_firefox_policy_installs_signed_addon_and_preserves_other_policies(tmp_path):
  xpi = tmp_path / "bridge.xpi"
  with zipfile.ZipFile(xpi, "w") as archive:
    archive.writestr("META-INF/mozilla.rsa", b"signature marker")
    archive.writestr("META-INF/mozilla.sf", b"signature marker")
  policy_file = tmp_path / "firefox" / "policies.json"
  policy_file.parent.mkdir()
  policy_file.write_text(json.dumps({"policies": {"DisableAppUpdate": True}}))

  browser_bridge.install_firefox_policy(xpi, policy_file)
  installed = json.loads(policy_file.read_text())
  addon = installed["policies"]["ExtensionSettings"]["xdm-companion-bridge@xdm.local"]
  assert addon["installation_mode"] == "force_installed"
  assert addon["install_url"] == xpi.resolve().as_uri()
  assert installed["policies"]["DisableAppUpdate"] is True

  assert browser_bridge.remove_firefox_policy(policy_file) is True
  restored = json.loads(policy_file.read_text())
  assert restored == {"policies": {"DisableAppUpdate": True}}


def test_chrome_external_manifest_points_to_local_crx(tmp_path):
  crx = tmp_path / "bridge.crx"
  value = json.loads(browser_bridge.chrome_external_manifest("unused", crx, "1.0.0"))

  assert value == {"external_crx": str(crx.resolve()), "external_version": "1.0.0"}


def test_chrome_extension_id_is_derived_from_public_key_bytes():
  def fake_runner(*args, **kwargs):
    return SimpleNamespace(stdout=bytes(range(32)))

  extension_id = browser_bridge.chrome_extension_id("key.pem", runner=fake_runner)
  assert len(extension_id) == 32
  assert all("a" <= char <= "p" for char in extension_id)


def test_register_chrome_external_manifest_without_privilege_when_directory_is_writable(tmp_path):
  install_dir = tmp_path / "chrome" / "extensions"
  destination = browser_bridge.write_chrome_external_manifest(
      "a" * 32, tmp_path / "bridge.crx", "1.0.0", install_dir
  )

  assert destination.exists()
  assert destination.stat().st_mode & 0o777 == 0o644
  data = json.loads(destination.read_text())
  assert data["external_version"] == "1.0.0"


def test_main_intercept_requires_extension_origin_and_supports_preflight(monkeypatch):
  sys.path.insert(
      0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../core"))
  )
  import main

  client = main.app.test_client()
  denied = client.post(
      "/intercept",
      json={"url": "https://www.youtube.com/watch?v=test"},
      headers={"Origin": "https://www.youtube.com"},
  )
  assert denied.status_code == 403

  origin = "chrome-extension://" + "a" * 32
  preflight = client.options(
      "/intercept",
      headers={"Origin": origin, "Access-Control-Request-Method": "POST"},
  )
  assert preflight.status_code == 204
  assert preflight.headers["Access-Control-Allow-Origin"] == origin

  monkeypatch.setattr(main, "_ensure_xdm_ready", lambda: None)
  monkeypatch.setattr(
      main,
      "_extract_media",
      lambda page_url, cookies, user_agent, title, *audio_preferences: {
          "title": "test video",
          "page_url": page_url,
          "user_agent": user_agent,
      },
  )
  received = {}
  finished = threading.Event()

  def fake_send_to_xdm(media):
    received.update(media)
    finished.set()
    return "queued"

  monkeypatch.setattr(main, "_send_to_xdm", fake_send_to_xdm)
  handoff = client.post(
      "/intercept",
      json={
          "url": "https://www.youtube.com/watch?v=test",
          "cookies": [],
          "user_agent": "test-browser",
          "title": "test video",
      },
      headers={"Origin": origin},
  )
  assert handoff.status_code == 202
  assert handoff.headers["Access-Control-Allow-Origin"] == origin
  assert handoff.json["status"] == "accepted"
  assert finished.wait(timeout=1)
  assert received["page_url"] == "https://www.youtube.com/watch?v=test"
