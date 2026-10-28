# XDM Companion

XDM Companion improves the XDM download workflow with a floating button on supported video pages. The button helps users review the formats available for a page and send a selected format to the XDM desktop application.

Availability depends on the site, page, session, and formats made available by the site. XDM Companion does not download video on YouTube or video on other sites. 

The XDM desktop application and the XDM Companion background service are required. XDM Companion is an independent project and is not affiliated with XDM.

For browser detection and the floating button to work, users must also install the browser extension **XDM Companion Bridge** from the Chrome Web Store or Firefox Add-ons once it is approved and published. The Linux install script below installs only the background service; it does not install the browser extension or the XDM desktop application.

## Install on Linux

After a GitHub release has been published, run this command on Linux to install the background service:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/juanmmorel/xdm-companion/main/scripts/install.sh) --repo juanmmorel/xdm-companion
```

This command requires Bash and a supported Linux distribution. Running it from WSL installs the service inside WSL; it does not install or connect the service to browsers running directly on Windows.

## Install on Windows

Open PowerShell and run this command after a release with the Windows package has been published:

```powershell
irm https://raw.githubusercontent.com/juanmmorel/xdm-companion/main/scripts/install.ps1 | iex
```

The installer prepares the background service, verifies the downloaded package, and starts the service. It uses Python 3.10 or newer and installs Python with WinGet when it is missing and WinGet is available. Install **XDM Companion Bridge** separately from the Chrome Web Store or Firefox Add-ons. To remove the service, run `& "$env:LOCALAPPDATA\XDM Companion\uninstall.ps1"` in PowerShell.

## Load a local Chrome build

For local testing from a source checkout, run `python3 scripts/build-chrome-unpacked.py`. In Chrome, open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select `dist/local/xdm-companion-chrome-unpacked`. In an extracted Linux or Windows release package, select its `browser-bridge` folder. Select a folder that contains `manifest.json` at its root.
