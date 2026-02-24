#!/usr/bin/env python3
"""
VxLAN EVPN Fabric Verification Script
======================================
Uses Nexus Dashboard (ND) REST API to verify fabric health.
Single API endpoint aggregates data from ALL switches - no
per-switch NXAPI connections required.

API Flow:
  1. POST /login          → JWT token
  2. GET  /fabrics        → Fabric config + health
  3. GET  /inventory      → All switches (role, status, resources)
  4. GET  /switches/roles → Serial → role mapping
  5. GET  /links          → Underlay topology + link state
  6. GET  /interface      → VTEP (nve1) details per switch
"""

import os
import sys
import httpx
from typing import Any
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.columns import Columns
from rich.text import Text

load_dotenv()

console = Console()

# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

FABRIC_NAME = "DevNet_VxLAN_Fabric"
BASE_URL = "https://{host}"
LOGIN_PATH = "/login"
FABRIC_PATH = "/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics"
INVENTORY_PATH = "/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics/{fabric}/inventory"
ROLES_PATH = "/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/switches/roles"
LINKS_PATH = "/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/links"
INTERFACE_PATH = "/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/interface"

# Role display mapping
ROLE_DISPLAY = {
    "leaf": "Leaf (VTEP)",
    "border": "Border Leaf",
    "border gateway spine": "Border-GW Spine",
    "spine": "Spine",
}

# Status colors
STATUS_COLOR = {
    "ok": "green",
    "timeout": "red",
    "unreachable": "red",
    "HEALTHY": "green",
    "MINOR": "yellow",
    "MAJOR": "red",
    "CRITICAL": "red",
}


# ─────────────────────────────────────────────
# ND API Client
# ─────────────────────────────────────────────


class NexusDashboard:
    """
    Nexus Dashboard REST API client.
    Authenticates once and reuses JWT token for all requests.
    Uses httpx for modern async-capable HTTP client.
    """

    def __init__(self, host: str, username: str, password: str):
        self.base_url = BASE_URL.format(host=host)
        self.username = username
        self.password = password
        self.token: str | None = None
        # Single persistent client - more efficient than per-request clients
        self.client = httpx.Client(verify=False, timeout=30.0)

    def login(self) -> bool:
        """Authenticate and store JWT token."""
        url = self.base_url + LOGIN_PATH
        payload = {
            "userName": self.username,
            "userPasswd": self.password,
            "domain": "local",
        }
        try:
            response = self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            self.token = data.get("token") or data.get("jwttoken")
            if self.token:
                self.client.headers.update({"Authorization": f"Bearer {self.token}"})
                return True
            console.print("[red]✗ Login failed: No token in response[/red]")
            return False
        except httpx.HTTPError as e:
            console.print(f"[red]✗ Login HTTP error: {e}[/red]")
            return False
        except Exception as e:
            console.print(f"[red]✗ Login failed: {e}[/red]")
            return False

    def get(self, path: str, params: dict | None = None) -> Any:
        """Make authenticated GET request."""
        url = self.base_url + path
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            # 405 = endpoint doesn't support GET, return None gracefully
            if e.response.status_code == 405:
                return None
            console.print(f"[yellow]⚠ API error {e.response.status_code} for {path}[/yellow]")
            return None
        except Exception as e:
            console.print(f"[red]✗ Request failed for {path}: {e}[/red]")
            return None

    def close(self):
        """Close the HTTP client."""
        self.client.close()


# ─────────────────────────────────────────────
# Data Fetchers
# ─────────────────────────────────────────────


def get_fabric_info(nd: NexusDashboard) -> dict | None:
    """Get fabric configuration and health status."""
    fabrics = nd.get(FABRIC_PATH)
    if not fabrics:
        return None
    for fabric in fabrics:
        if fabric.get("fabricName") == FABRIC_NAME:
            return fabric
    return None


def get_inventory(nd: NexusDashboard) -> list[dict]:
    """Get all switches in the fabric with full details."""
    path = INVENTORY_PATH.format(fabric=FABRIC_NAME)
    result = nd.get(path)
    return result if isinstance(result, list) else []


def get_switch_roles(nd: NexusDashboard) -> list[dict]:
    """Get serial number to role mapping."""
    result = nd.get(ROLES_PATH)
    return result if isinstance(result, list) else []


def get_links(nd: NexusDashboard) -> list[dict]:
    """Get all fabric links (underlay topology)."""
    result = nd.get(LINKS_PATH, params={"fabric-name": FABRIC_NAME})
    return result if isinstance(result, list) else []


def get_vtep_interface(nd: NexusDashboard, serial: str) -> dict | None:
    """Get NVE (VTEP) interface details for a specific switch."""
    result = nd.get(INTERFACE_PATH, params={"serialNumber": serial})
    if not isinstance(result, list):
        return None
    for policy_group in result:
        if policy_group.get("policy") == "int_nve":
            interfaces = policy_group.get("interfaces", [])
            if interfaces:
                return interfaces[0]
    return None


# ─────────────────────────────────────────────
# Display Sections
# ─────────────────────────────────────────────


def display_fabric_summary(fabric: dict):
    """Display fabric-level health and configuration."""
    console.print()

    nv = fabric.get("nvPairs", {})

    status = fabric.get("operStatus", "UNKNOWN")
    status_color = STATUS_COLOR.get(status, "yellow")

    # Build two info panels side by side
    identity = (
        f"[bold]Fabric:[/bold]      {fabric.get('fabricName')}\n"
        f"[bold]Technology:[/bold]  {fabric.get('fabricTechnologyFriendly')}\n"
        f"[bold]Template:[/bold]    {fabric.get('templateFabricType')}\n"
        f"[bold]BGP ASN:[/bold]     {fabric.get('asn')}\n"
        f"[bold]Site ID:[/bold]     {fabric.get('siteId')}\n"
        f"[bold]Status:[/bold]      [{status_color}]{status}[/{status_color}]"
    )

    config = (
        f"[bold]Underlay:[/bold]    OSPF (area {nv.get('OSPF_AREA_ID', '0.0.0.0')})\n"
        f"[bold]Replication:[/bold] {nv.get('REPLICATION_MODE', 'N/A')}\n"
        f"[bold]Multicast:[/bold]   {nv.get('MULTICAST_GROUP_SUBNET', 'N/A')}\n"
        f"[bold]Fabric MTU:[/bold]  {nv.get('FABRIC_MTU', 'N/A')}\n"
        f"[bold]EVPN:[/bold]        {'[green]Enabled[/green]' if nv.get('ENABLE_EVPN') == 'true' else '[red]Disabled[/red]'}\n"
        f"[bold]NXAPI:[/bold]       {'[green]Enabled[/green]' if nv.get('ENABLE_NXAPI') == 'true' else '[red]Disabled[/red]'}"
    )

    console.print(
        Columns(
            [
                Panel(identity, title="[bold cyan]Fabric Identity[/bold cyan]", border_style="cyan"),
                Panel(config, title="[bold cyan]Fabric Configuration[/bold cyan]", border_style="cyan"),
            ]
        )
    )


def display_inventory(inventory: list[dict], roles: list[dict]):
    """Display all switches with health, resources, and roles."""
    console.print("\n[bold cyan]═══ SWITCH INVENTORY ═══[/bold cyan]")

    # Build serial → role map
    role_map = {r["serialNumber"]: r["role"] for r in roles}

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold magenta", show_lines=False)
    table.add_column("Hostname", style="cyan", no_wrap=True)
    table.add_column("IP Address", style="white")
    table.add_column("Role", style="yellow")
    table.add_column("Model", style="dim")
    table.add_column("NX-OS", style="dim")
    table.add_column("Uptime", style="white")
    table.add_column("CPU%", justify="right")
    table.add_column("Mem%", justify="right")
    table.add_column("Health", justify="right")
    table.add_column("Status", justify="center")

    # Sort: spines first, then leafs
    def sort_key(sw):
        role = sw.get("switchRole", "")
        if "spine" in role:
            return (0, sw.get("hostName", ""))
        elif "border" in role:
            return (1, sw.get("hostName", ""))
        return (2, sw.get("hostName", ""))

    for sw in sorted(inventory, key=sort_key):
        status = sw.get("status", "unknown")
        status_color = STATUS_COLOR.get(status, "yellow")

        hostname = sw.get("hostName") or sw.get("logicalName", "N/A")
        ip = sw.get("ipAddress", "N/A")
        role = sw.get("switchRole", "N/A")
        role_display = ROLE_DISPLAY.get(role, role.title())
        model = sw.get("model", "N/A")
        release = sw.get("release", "N/A")
        uptime = sw.get("upTimeStr", "N/A")

        cpu = sw.get("cpuUsage", 0)
        mem = sw.get("memoryUsage", 0)
        health = sw.get("health", 0)

        # Color CPU/memory based on thresholds
        cpu_color = "red" if cpu > 80 else "yellow" if cpu > 60 else "green"
        mem_color = "red" if mem > 85 else "yellow" if mem > 70 else "green"
        health_color = "red" if health < 50 else "yellow" if health < 80 else "green"

        table.add_row(
            hostname,
            ip,
            role_display,
            model,
            release,
            uptime,
            f"[{cpu_color}]{cpu}%[/{cpu_color}]",
            f"[{mem_color}]{mem}%[/{mem_color}]",
            f"[{health_color}]{health}[/{health_color}]",
            f"[{status_color}]{status}[/{status_color}]",
        )

    console.print(table)

    # Summary counts
    online = sum(1 for sw in inventory if sw.get("status") == "ok")
    total = len(inventory)
    spines = sum(1 for sw in inventory if "spine" in sw.get("switchRole", ""))
    leafs = sum(1 for sw in inventory if "spine" not in sw.get("switchRole", ""))

    summary = f"  Total: {total}  |  Online: [green]{online}[/green]  |  Offline: [red]{total - online}[/red]  |  Spines: {spines}  |  Leafs: {leafs}"
    console.print(summary)


def display_topology(links: list[dict]):
    """Display fabric underlay topology and link states."""
    console.print("\n[bold cyan]═══ UNDERLAY TOPOLOGY & LINK STATE ═══[/bold cyan]")

    # Separate intra-fabric links from external neighbor links
    fabric_links = [l for l in links if l.get("link-type") == "ethisl"]
    external_links = [l for l in links if l.get("link-type") == "lan_neighbor_link"]

    # ── Intra-Fabric Links ──
    table = Table(
        title="Intra-Fabric Links (Underlay)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Switch A", style="cyan", no_wrap=True)
    table.add_column("Interface A", style="white")
    table.add_column("IP A", style="dim")
    table.add_column("  ", justify="center")
    table.add_column("Switch B", style="cyan", no_wrap=True)
    table.add_column("Interface B", style="white")
    table.add_column("IP B", style="dim")
    table.add_column("State", justify="center")

    for link in fabric_links:
        sw1 = link.get("sw1-info", {})
        sw2 = link.get("sw2-info", {})
        nv = link.get("nvPairs", {})
        is_present = link.get("is-present", False)

        sw1_name = sw1.get("sw-sys-name", "N/A")
        sw1_intf = sw1.get("if-name", "N/A")
        sw1_ip = nv.get("PEER1_IP", "")
        sw2_name = sw2.get("sw-sys-name", "N/A")
        sw2_intf = sw2.get("if-name", "N/A")
        sw2_ip = nv.get("PEER2_IP", "")

        # Determine link state from interface op-status
        sw1_up = sw1.get("if-op-status", "Down") == "Up"
        sw2_up = sw2.get("if-op-status", "Down") == "Up"
        both_up = sw1_up and sw2_up and is_present

        state_str = "[green]● Up[/green]" if both_up else "[red]● Down[/red]"
        arrow = "[green]──[/green]" if both_up else "[red]──[/red]"

        table.add_row(sw1_name, sw1_intf, sw1_ip, arrow, sw2_name, sw2_intf, sw2_ip, state_str)

    console.print(table)

    # ── External / Multi-Site Links ──
    if external_links:
        ext_table = Table(
            title="External Links (Multi-Site / DCI)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
        )
        ext_table.add_column("Fabric Switch", style="cyan", no_wrap=True)
        ext_table.add_column("Role", style="yellow")
        ext_table.add_column("Interface", style="white")
        ext_table.add_column("  ", justify="center")
        ext_table.add_column("External Device", style="cyan")
        ext_table.add_column("Interface", style="white")
        ext_table.add_column("State", justify="center")

        for link in external_links:
            sw1 = link.get("sw1-info", {})
            sw2 = link.get("sw2-info", {})
            is_present = link.get("is-present", False)

            sw1_op = sw1.get("if-op-status", "Down") == "Up"
            state_str = "[green]● Up[/green]" if (sw1_op and is_present) else "[red]● Down[/red]"
            arrow = "[green]──[/green]" if (sw1_op and is_present) else "[red]──[/red]"

            ext_table.add_row(
                sw1.get("sw-sys-name", "N/A"),
                sw1.get("switch-role", "N/A").title(),
                sw1.get("if-name", "N/A"),
                arrow,
                sw2.get("sw-sys-name", "N/A"),
                sw2.get("if-name", "N/A"),
                state_str,
            )

        console.print()
        console.print(ext_table)

    # Link summary
    fabric_up = sum(1 for l in fabric_links if l.get("is-present") and l.get("sw1-info", {}).get("if-op-status") == "Up")
    fabric_down = len(fabric_links) - fabric_up
    ext_up = sum(1 for l in external_links if l.get("is-present") and l.get("sw1-info", {}).get("if-op-status") == "Up")

    console.print(
        f"\n  Fabric links: [green]{fabric_up} up[/green]  [red]{fabric_down} down[/red]  |  "
        f"External links: [green]{ext_up} up[/green]"
    )


def display_vtep_info(nd: NexusDashboard, inventory: list[dict]):
    """Display VTEP (NVE) interface details for VTEP-capable switches."""
    console.print("\n[bold cyan]═══ VTEP (NVE) INTERFACE STATUS ═══[/bold cyan]")

    # Only check switches that are VTEPs (leafs and border leafs, not spines)
    vtep_switches = [
        sw for sw in inventory
        if "spine" not in sw.get("switchRole", "") and sw.get("status") == "ok"
    ]

    if not vtep_switches:
        console.print("[yellow]⚠ No reachable VTEP switches found[/yellow]")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold magenta")
    table.add_column("Switch", style="cyan")
    table.add_column("Role", style="yellow")
    table.add_column("NVE Interface", style="white")
    table.add_column("Source Loopback", style="white")
    table.add_column("Loopback0 (BGP RID)", style="dim")
    table.add_column("Loopback1 (VTEP IP)", style="dim")
    table.add_column("Admin State", justify="center")

    for sw in vtep_switches:
        serial = sw.get("serialNumber")
        hostname = sw.get("hostName") or sw.get("logicalName", "N/A")
        role = ROLE_DISPLAY.get(sw.get("switchRole", ""), sw.get("switchRole", "N/A"))

        nve_data = get_vtep_interface(nd, serial)

        if nve_data:
            nv = nve_data.get("nvPairs", {})
            nve_intf = nv.get("INTF_NAME", "nve1")
            source_intf = nv.get("SOURCE_INTF_NAME", "N/A")
            admin_up = nv.get("ADMIN_STATE", "false") == "true"
            admin_str = "[green]Up[/green]" if admin_up else "[red]Down[/red]"

            # Get loopback IPs from interfaces list
            lo0_ip = lo1_ip = "N/A"
            interfaces = get_loopback_ips(nd, serial)
            lo0_ip = interfaces.get("loopback0", "N/A")
            lo1_ip = interfaces.get("loopback1", "N/A")

            table.add_row(hostname, role, nve_intf, source_intf, lo0_ip, lo1_ip, admin_str)
        else:
            table.add_row(hostname, role, "nve1", "N/A", "N/A", "N/A", "[yellow]Unknown[/yellow]")

    console.print(table)
    console.print(
        "\n  [dim]Loopback0 = BGP Router ID (underlay) | "
        "Loopback1 = VTEP source IP (VxLAN encapsulation endpoint)[/dim]"
    )


def get_loopback_ips(nd: NexusDashboard, serial: str) -> dict:
    """Extract loopback0 and loopback1 IPs from interface data."""
    result = nd.get(INTERFACE_PATH, params={"serialNumber": serial})
    loopbacks = {}
    if not isinstance(result, list):
        return loopbacks
    for policy_group in result:
        if policy_group.get("policy") == "int_fabric_loopback_11_1":
            for intf in policy_group.get("interfaces", []):
                nv = intf.get("nvPairs", {})
                name = nv.get("INTF_NAME", "")
                ip = nv.get("IP", "")
                if name and ip:
                    loopbacks[name] = ip
    return loopbacks


def display_fabric_config_summary(fabric: dict):
    """Display key fabric design parameters useful for learning."""
    console.print("\n[bold cyan]═══ FABRIC DESIGN PARAMETERS ═══[/bold cyan]")

    nv = fabric.get("nvPairs", {})

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Parameter", style="bold cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_column("Notes", style="dim")

    rows = [
        ("BGP AS Number", nv.get("BGP_AS", "N/A"), "Same AS = iBGP fabric"),
        ("Route Reflectors", f"{nv.get('RR_COUNT', '2')} spines", "Spines are BGP RR for EVPN"),
        ("Loopback0 Range", nv.get("LOOPBACK0_IP_RANGE", "N/A"), "BGP Router ID pool"),
        ("Loopback1 Range", nv.get("LOOPBACK1_IP_RANGE", "N/A"), "VTEP source IP pool"),
        ("Underlay Subnet", nv.get("SUBNET_RANGE", "N/A"), "Point-to-point links"),
        ("VRF VLAN Range", nv.get("VRF_VLAN_RANGE", "N/A"), "L3 VNI VLANs"),
        ("Network VLAN Range", nv.get("NETWORK_VLAN_RANGE", "N/A"), "L2 VNI VLANs"),
        ("L2 Segment ID Range", nv.get("L2_SEGMENT_ID_RANGE", "N/A"), "VxLAN VNI range for L2"),
        ("L3 Partition Range", nv.get("L3_PARTITION_ID_RANGE", "N/A"), "VxLAN VNI range for L3"),
        ("Anycast RP Range", nv.get("ANYCAST_RP_IP_RANGE", "N/A"), "Multicast RP (PIM ASM)"),
        ("Multicast Group", nv.get("MULTICAST_GROUP_SUBNET", "N/A"), "BUM traffic replication"),
        ("Anycast GW MAC", nv.get("ANYCAST_GW_MAC", "N/A"), "Distributed gateway MAC"),
        ("Fabric MTU", nv.get("FABRIC_MTU", "N/A"), "+50 bytes for VxLAN header"),
        ("NVE Loopback ID", nv.get("NVE_LB_ID", "N/A"), "loopback{ID} = VTEP source"),
    ]

    for param, value, note in rows:
        table.add_row(param, value, note)

    console.print(table)


def display_health_summary(inventory: list[dict], links: list[dict], fabric: dict):
    """Display overall health summary with pass/fail checks."""
    console.print("\n[bold cyan]═══ FABRIC HEALTH SUMMARY ═══[/bold cyan]")

    checks = []

    # Check 1: Fabric operational status
    op_status = fabric.get("operStatus", "UNKNOWN")
    checks.append(("Fabric Operational Status", op_status == "HEALTHY", op_status))

    # Check 2: All switches reachable
    reachable = [sw for sw in inventory if sw.get("status") == "ok"]
    checks.append((
        "Switch Reachability",
        len(reachable) == len(inventory),
        f"{len(reachable)}/{len(inventory)} reachable"
    ))

    # Check 3: Fabric links up
    fabric_links = [l for l in links if l.get("link-type") == "ethisl"]
    expected_links = fabric_links  # All configured links
    up_links = [l for l in fabric_links if l.get("is-present") and l.get("sw1-info", {}).get("if-op-status") == "Up"]
    # We expect the non-VPC peer links to be up (exclude admin-down VPC links)
    active_links = [l for l in fabric_links if l.get("nvPairs")]  # Links with config = intended to be up
    checks.append(("Underlay Links", len(up_links) == len(active_links), f"{len(up_links)}/{len(active_links)} up"))

    # Check 4: EVPN enabled
    evpn_enabled = fabric.get("nvPairs", {}).get("ENABLE_EVPN") == "true"
    checks.append(("EVPN Enabled", evpn_enabled, "Enabled" if evpn_enabled else "Disabled"))

    # Check 5: External connectivity
    ext_links = [l for l in links if l.get("link-type") == "lan_neighbor_link"]
    ext_up = [l for l in ext_links if l.get("is-present") and l.get("sw1-info", {}).get("if-op-status") == "Up"]
    checks.append(("External/DCI Links", len(ext_up) > 0, f"{len(ext_up)}/{len(ext_links)} up"))

    # Display checks
    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Result", justify="center")
    table.add_column("Detail", style="dim")

    all_pass = True
    for check_name, passed, detail in checks:
        if passed:
            result = "[bold green]✓ PASS[/bold green]"
        else:
            result = "[bold red]✗ FAIL[/bold red]"
            all_pass = False
        table.add_row(check_name, result, str(detail))

    console.print(table)

    # Overall verdict
    if all_pass:
        console.print(Panel(
            "[bold green]✓ ALL CHECKS PASSED - FABRIC HEALTHY[/bold green]",
            border_style="green"
        ))
    else:
        console.print(Panel(
            "[bold yellow]⚠ SOME CHECKS FAILED - REVIEW ABOVE[/bold yellow]",
            border_style="yellow"
        ))


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────


def main():
    # SSL warnings suppressed via verify=False in httpx client (lab environment)

    console.print("\n[bold green]╔══════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║   VxLAN EVPN FABRIC VERIFICATION        ║[/bold green]")
    console.print("[bold green]║   Nexus Dashboard API · Site 1           ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════╝[/bold green]")

    # Get credentials from environment
    nd_host = os.getenv("ND_HOST", "10.10.20.60")
    nd_user = os.getenv("ND_USERNAME", "admin")
    nd_pass = os.getenv("ND_PASSWORD", "1vtG@lw@y")

    # Create ND client
    nd = NexusDashboard(nd_host, nd_user, nd_pass)

    # Authenticate
    console.print(f"\n[dim]Connecting to Nexus Dashboard ({nd_host})...[/dim]")
    if not nd.login():
        console.print("[red]✗ Authentication failed. Check credentials in .env[/red]")
        sys.exit(1)
    console.print("[green]✓ Authenticated successfully[/green]")

    # Fetch all data
    console.print("[dim]Fetching fabric data...[/dim]")
    fabric = get_fabric_info(nd)
    inventory = get_inventory(nd)
    roles = get_switch_roles(nd)
    links = get_links(nd)

    if not fabric:
        console.print(f"[red]✗ Fabric '{FABRIC_NAME}' not found[/red]")
        nd.close()
        sys.exit(1)

    console.print(f"[green]✓ Data retrieved: {len(inventory)} switches, {len(links)} links[/green]")

    # Display all sections
    display_fabric_summary(fabric)
    display_inventory(inventory, roles)
    display_topology(links)
    display_vtep_info(nd, inventory)
    display_fabric_config_summary(fabric)
    display_health_summary(inventory, links, fabric)

    nd.close()

    console.print("\n[dim]Tip: Run with 'watch -n 60 uv run scripts/verify_fabric.py' for live monitoring[/dim]\n")


if __name__ == "__main__":
    main()