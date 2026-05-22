# Nespresso Cloud, Home Assistant integration

Home Assistant integration for the Nespresso Vertuo line over Nespresso's
cloud API. No Bluetooth pairing, no broker to run.

For local BLE control, see [renaudallard/homeassistant_nespresso_smart](https://github.com/renaudallard/homeassistant_nespresso_smart).

## Status

Read-only for now. Tested against a Vertuo Lattissima. The other Vertuo
models should work; the API surface is the same across the line.

## Supported machines

The Vertuo line. This matches what the official Nespresso Smart app
supports.

| Model              | Milk frother | Tested |
| ------------------ | ------------ | ------ |
| Vertuo Next        | no           |        |
| Vertuo Pop         | no           |        |
| Vertuo Pop+        | no           |        |
| Vertuo Up          | no           |        |
| Vertuo Creatista   | yes          |        |
| Vertuo Lattissima  | yes          | ✓      |

The Original line (Prodigio, Expert, Lattissima Original, etc.) is not
supported. Those machines use a different protocol that does not go
through this cloud backend.

## Entities

Per machine:

| Entity        | Type          | Description                                              |
| ------------- | ------------- | -------------------------------------------------------- |
| Machine state | sensor (enum) | Primary state (`ready`, `brewing`, `descaling`, …)       |
| Milk unit     | sensor (enum) | Milk frother state on 1+1 models (Lattissima, Creatista) |

Diagnostic:

| Entity             | Type          | Description                                  |
| ------------------ | ------------- | -------------------------------------------- |
| Error              | sensor (enum) | Current error labelled (`lever_open`, …)     |
| Last seen          | sensor (time) | Last cloud ping timestamp                    |
| Connected          | binary sensor | Cloud connectivity                           |
| Water tank empty   | binary sensor |                                              |
| Descaling required | binary sensor |                                              |
| Old capsule        | binary sensor |                                              |
| Error code         | sensor (int)  | Raw error code, disabled by default          |
| Error origin       | sensor (enum) | Error subsystem, disabled by default         |

Firmware version is exposed as the device's `sw_version` on the device
page, not as a separate entity.

## Install

1. Copy `custom_components/nespresso_cloud/` into your HA `custom_components/`.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**, then pick
   "Nespresso Cloud".
4. Follow the on-screen instructions (see "Authentication" below).

HACS: add this repo as a custom repository, category "Integration".

## Authentication

Setup uses a one-time browser bridge to your existing nespresso.com
session. Your password is never entered in Home Assistant.

1. Open `https://www.nespresso.com` in a browser and log in.
2. Open DevTools (F12) and switch to the Console tab.
3. Paste the snippet shown in the integration dialog, then press Enter.
4. A `prompt` dialog appears with an `authorization_code`. Copy it.
5. Paste it back into the HA dialog.

HA stores the resulting refresh token and rotates it on each call. If
it ever gets revoked (for example because you logged out everywhere),
HA triggers a reauth that replays the same browser flow.

## Limitations

- **Read-only**. The cloud exposes `descaling`, `rinsing`, `emptying`,
  `cupCustomization`, and `waterHardness` commands. They are not wired
  up yet.
- **Water hardness** can be set through the cloud API but its current
  value is not cloud-readable. Even with the setter wired, the
  integration cannot display the value the machine currently has stored
  unless you set it through HA at least once.
- **No "brew now"**. The cloud API has no remote-brew endpoint on the
  Vertuo line. Pressing the machine's lever is required as a hardware
  safety step.

## Development

A standalone debug script lives at `scripts/debug.py`. It uses the same
library code as the integration, drives it against your real account,
and renders a live Rich-powered TUI with machine state, presence, and a
diff log of every field change. Read-only, no HA needed.

```bash
uv venv
uv pip install -r requirements_dev.txt
.venv/bin/python scripts/debug.py
```

On first run it walks you through the same browser bootstrap as the HA
config flow, then writes the refresh token to a local `.env` file that
subsequent runs reuse.

## License

[MIT](LICENSE).

## Disclaimer

Unofficial integration, not affiliated with Nespresso. The cloud API
surface is undocumented and may change without notice. Use at your own
risk.
