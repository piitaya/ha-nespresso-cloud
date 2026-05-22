"""Live TUI inspector for a Nespresso machine.

Auth via refresh_token, pick machine, polled live debug view. Polls
/status, /presence and /machineInfo on an interval, refreshes a Rich TUI
in place, and logs field-level diffs in a change panel.

    python scripts/debug.py

Reads credentials from .env at the repo root. The refresh_token rotates
on every refresh, so a rotated value is written back to .env in place.
Read-only against the API. Ctrl+C to exit.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import secrets
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Tuple

import aiohttp
from dotenv import load_dotenv
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# The library lives under custom_components/nespresso_cloud/lib/. We
# import it as a top-level `lib` package so we bypass the integration's
# __init__.py, which pulls in `homeassistant` at import time.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "nespresso_cloud"))

from lib import (  # noqa: E402
    Machine,
    MachineInfo,
    MachinePresence,
    MachineStatus,
    NespressoApiClient,
    NespressoAuthError,
    Tokens,
    async_exchange_authorization_code,
)
from lib.api import (  # noqa: E402
    error_origin_label,
    machine_status_label,
    milk_unit_status_label,
)

POLL_INTERVAL_S = 5
console = Console()


async def main() -> int:
    load_dotenv(ENV_PATH)

    country = os.environ.get("NESPRESSO_COUNTRY", "fr")
    device_id = os.environ.get("NESPRESSO_DEVICE_ID")
    if not device_id:
        device_id = str(uuid.uuid4())
        _persist_env("NESPRESSO_DEVICE_ID", device_id)
        console.print(f"[dim]generated device_id and saved to .env: {device_id}[/]")

    refresh_token = os.environ.get("NESPRESSO_REFRESH_TOKEN")

    async with aiohttp.ClientSession() as session:
        if not refresh_token:
            console.print("[yellow]No refresh_token in .env — running bootstrap login.[/]")
            tokens = await _bootstrap(session, device_id)
            _persist_env("NESPRESSO_REFRESH_TOKEN", tokens.refresh_token)
        else:
            tokens = Tokens(
                access_token="",
                refresh_token=refresh_token,
                expires_at=0,  # forces refresh on first call
            )

        async def on_token_update(updated: Tokens) -> None:
            _persist_env("NESPRESSO_REFRESH_TOKEN", updated.refresh_token)
            console.log("[dim]rotated refresh_token persisted to .env[/]")

        client = NespressoApiClient(
            session=session,
            tokens=tokens,
            country=country,
            device_id=device_id,
            token_updated=on_token_update,
        )
        try:
            return await _run(client)
        except NespressoAuthError as err:
            console.print(f"[yellow]Auth failed ({err}). Re-running bootstrap login.[/]")
            tokens = await _bootstrap(session, device_id)
            _persist_env("NESPRESSO_REFRESH_TOKEN", tokens.refresh_token)
            client = NespressoApiClient(
                session=session,
                tokens=tokens,
                country=country,
                device_id=device_id,
                token_updated=on_token_update,
            )
            try:
                return await _run(client)
            except NespressoAuthError as err2:
                console.print(f"[red]Auth still failing: {err2}[/]")
                return 2


# ---------------------------------------------------------------------------
# Bootstrap login — mirrors custom_components/nespresso_cloud/config_flow.py
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _snippet(challenge: str) -> str:
    return (
        "fetch('/ecapi/identityprovider/v1/web-accounts/me/authorize"
        f"?response_type=code&code_challenge={challenge}"
        "&client_id=native-apps', "
        "{method:'POST',credentials:'include',"
        "headers:{'Content-Type':'application/json'}})"
        ".then(r=>r.json())"
        ".then(d=>prompt('Copy this code:', d.authorization_code));"
    )


async def _bootstrap(session: aiohttp.ClientSession, device_id: str) -> Tokens:
    verifier, challenge = _make_pkce()
    snippet = _snippet(challenge)
    instructions = Text.from_markup(
        "1. Log in at [bold]https://www.nespresso.com[/]\n"
        "2. Open DevTools ([bold]Cmd+Opt+I[/] / [bold]F12[/]) → Console\n"
        "3. Paste the snippet and press Enter\n"
        "4. Copy the [italic]authorization_code[/] from the popup and paste it here"
    )
    console.print(Panel(instructions, title="Bootstrap login", border_style="cyan"))
    # Print bare (no Panel) with soft_wrap so the terminal soft-wraps for
    # display but the snippet stays one logical line — selection copies
    # it without inserted newlines. A Panel border forces hard wraps.
    console.print("\n[dim]Browser snippet:[/]")
    console.print(snippet, soft_wrap=True, highlight=False)
    console.print()
    code = Prompt.ask("authorization_code").strip()
    return await async_exchange_authorization_code(
        session,
        code=code,
        code_verifier=verifier,
        device_id=device_id,
    )


async def _run(client: NespressoApiClient) -> int:
    owner_id = os.environ.get("NESPRESSO_OWNER_ID")
    if not owner_id:
        with console.status("Fetching ownerId…"):
            info = await client.async_get_personal_info()
            owner_id = info.member_number
        console.print(f"[dim]ownerId: {owner_id}[/]")

    with console.status("Listing machines…"):
        machines = await client.async_list_machines(owner_id)

    if not machines:
        console.print("[red]No machines on this account.[/]")
        return 1

    machine = _resolve_machine(machines, os.environ.get("NESPRESSO_MACHINE_ID"))
    console.print(
        f"[dim]watching machine[/] {machine.custom_name or machine.id} "
        f"[dim]({machine.type})[/]"
    )

    log: Deque[Tuple[str, str]] = deque(maxlen=18)
    prev: dict[str, Any] = {}

    with Live(
        _render(machine, None, None, None, log),
        console=console,
        refresh_per_second=4,
        screen=False,
    ) as live:
        while True:
            try:
                status, presence, info = await asyncio.gather(
                    client.async_get_status(owner_id, machine.id),
                    client.async_get_presence(owner_id, machine.id),
                    client.async_get_machine_info(owner_id, machine.id),
                )
            except NespressoAuthError as err:
                console.print(f"[red]Auth lost: {err}[/]")
                return 2
            except Exception as err:  # noqa: BLE001 — surface, don't crash
                ts = datetime.now().strftime("%H:%M:%S")
                log.append((ts, f"[red]poll error:[/] {err}"))
                live.update(_render(machine, None, None, None, log))
                await asyncio.sleep(POLL_INTERVAL_S)
                continue

            snapshot = _snapshot(status, presence, info)
            ts = datetime.now().strftime("%H:%M:%S")
            # Skip the first-fill so we don't dump every field into the
            # log as a "None → value" diff.
            if prev:
                for key, value in snapshot.items():
                    if prev.get(key) != value:
                        log.append((ts, f"{key}: {prev.get(key)!r} → {value!r}"))
            prev = snapshot
            live.update(_render(machine, status, presence, info, log))
            await asyncio.sleep(POLL_INTERVAL_S)


def _resolve_machine(machines: list[Machine], env_id: str | None) -> Machine:
    if env_id:
        match = next((m for m in machines if m.id == env_id), None)
        if match is not None:
            return match
        console.print(f"[yellow]NESPRESSO_MACHINE_ID={env_id} not on account; picking manually[/]")
    if len(machines) == 1:
        return machines[0]
    table = Table(title="Machines on account")
    table.add_column("#", justify="right")
    table.add_column("name")
    table.add_column("type")
    table.add_column("id")
    for idx, m in enumerate(machines, start=1):
        table.add_row(str(idx), m.custom_name or "—", m.type, m.id)
    console.print(table)
    choice = IntPrompt.ask(
        "Pick machine", choices=[str(i) for i in range(1, len(machines) + 1)]
    )
    return machines[choice - 1]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(
    machine: Machine,
    status: MachineStatus | None,
    presence: MachinePresence | None,
    info: MachineInfo | None,
    log: Deque[Tuple[str, str]],
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="top", ratio=2),
        Layout(_log_panel(log), name="log", ratio=1),
    )
    layout["top"].split_row(
        Layout(_machine_panel(machine, info), name="left"),
        Layout(name="right"),
    )
    layout["top"]["right"].split_column(
        Layout(_status_panel(status), name="status"),
        Layout(_presence_panel(presence), name="presence"),
    )
    return layout


def _machine_panel(machine: Machine, info: MachineInfo | None) -> Panel:
    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("name", machine.custom_name or "—")
    t.add_row("type", machine.type)
    t.add_row("id", machine.id)
    t.add_row("serial", machine.serial_number or "—")
    t.add_row("mac", machine.mac_address or "—")
    t.add_row("wifi", machine.wifi_name or "—")
    if info is not None:
        t.add_row("brand", info.brand or "—")
        t.add_row("machine fw", info.machine_fw or "—")
        t.add_row("connectivity fw", info.connectivity_fw or "—")
        t.add_row("recipe", info.recipe_version or "—")
    return Panel(t, title="Machine", border_style="cyan")


def _status_panel(status: MachineStatus | None) -> Panel:
    if status is None:
        return Panel(Text("waiting…", style="dim"), title="Status")
    r = status.reported
    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim")
    t.add_column()
    t.add_row("sync", str(status.sync))
    t.add_row("last update", _fmt_ts(status.last_reported_update))
    t.add_row("machineStatus", _fmt_enum(r.machine_status, machine_status_label))
    t.add_row("milkUnitStatus", _fmt_enum(r.milk_unit_status, milk_unit_status_label))
    t.add_row("errorOrigin", _fmt_enum(r.error_origin, error_origin_label))
    t.add_row("errorCode", str(r.error_code))
    t.add_row("descalingAlert", str(r.descaling_alert))
    t.add_row("waterHardness", str(r.water_hardness))
    t.add_row("firstCoffee", str(r.first_coffee))
    t.add_row("firstRinsing", str(r.first_rinsing))
    t.add_row("recipeTag", str(r.recipe_tag))
    return Panel(t, title="Status", border_style="green")


def _presence_panel(presence: MachinePresence | None) -> Panel:
    if presence is None:
        return Panel(Text("waiting…", style="dim"), title="Presence")
    t = Table.grid(padding=(0, 1))
    t.add_column(style="dim")
    t.add_column()
    online = "[green]true[/]" if presence.connected else "[red]false[/]"
    t.add_row("connected", online)
    t.add_row("last update", _fmt_ts(presence.last_update))
    t.add_row("brand", str(presence.brand))
    t.add_row("machine id", presence.machine_id or "—")
    return Panel(t, title="Presence", border_style="magenta")


def _log_panel(log: Deque[Tuple[str, str]]) -> Panel:
    if not log:
        return Panel(Text("waiting for changes…", style="dim"), title="Change log")
    text = Text()
    for ts, line in log:
        text.append(f"{ts}  ", style="dim")
        text.append_text(Text.from_markup(line))
        text.append("\n")
    return Panel(text, title="Change log", border_style="yellow")


# ---------------------------------------------------------------------------
# Snapshot + diff
# ---------------------------------------------------------------------------


def _snapshot(
    status: MachineStatus,
    presence: MachinePresence,
    info: MachineInfo,
) -> dict[str, Any]:
    r = status.reported
    return {
        "sync": status.sync,
        "machineStatus": _fmt_enum(r.machine_status, machine_status_label),
        "milkUnitStatus": _fmt_enum(r.milk_unit_status, milk_unit_status_label),
        "errorOrigin": _fmt_enum(r.error_origin, error_origin_label),
        "errorCode": r.error_code,
        "descalingAlert": r.descaling_alert,
        "waterHardness": r.water_hardness,
        "firstCoffee": r.first_coffee,
        "firstRinsing": r.first_rinsing,
        "recipeTag": r.recipe_tag,
        "presence.connected": presence.connected,
        "info.machineFw": info.machine_fw,
        "info.connectivityFw": info.connectivity_fw,
        "info.recipeVersion": info.recipe_version,
    }


def _fmt_enum(value: int | None, labeler: Callable[[int | None], str | None]) -> str:
    if value is None:
        return "—"
    label = labeler(value)
    return f"{label} ({value})" if label else str(value)


def _fmt_ts(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    delta = (datetime.now(timezone.utc) - ts).total_seconds()
    if delta < 60:
        rel = f"{int(delta)}s ago"
    elif delta < 3600:
        rel = f"{int(delta / 60)}m ago"
    else:
        rel = f"{int(delta / 3600)}h ago"
    return f"{ts.strftime('%H:%M:%S')}  ({rel})"


# ---------------------------------------------------------------------------
# .env persistence
# ---------------------------------------------------------------------------


def _persist_env(key: str, value: str) -> None:
    """Set ``key=value`` in .env, replacing any existing line for that key.

    Comments and blank lines are preserved. The file is created if it
    doesn't already exist.
    """
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = f"{prefix}{value}"
            break
    else:
        lines.append(f"{prefix}{value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
