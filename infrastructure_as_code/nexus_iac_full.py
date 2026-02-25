#!/usr/bin/env python3
"""
Nexus Dashboard Infrastructure as Code Framework

Full IaC with apply/destroy for VRFs and Networks.

Usage:
    uv run nexus_iac_full.py inventory  # Show fabric inventory
    uv run nexus_iac_full.py sync       # Sync current state from NDFC
    uv run nexus_iac_full.py plan       # Show what will change
    uv run nexus_iac_full.py apply      # Apply changes
    uv run nexus_iac_full.py destroy    # Destroy all managed resources

Bugs fixed vs previous version:
  - VRF deploy payload was {"vrfNames": [...]} (array) → must be {"vrfName": "name"} (string)
  - VRF/Network attach payload was flat array → must be wrapped in lanAttachList with
    required fields: isAttached, freeformConfig, extensionValues, instanceValues
  - Step order corrected: create → attach → deploy (was: create → deploy → attach → deploy)
  - Serial numbers now fetched via fabric inventory endpoint (more reliable than allswitches)
  - Network deploy payload field confirmed as "networkNames" (array) — correct for networks
  - Removed broken deploy_vrf() stub from api_client — all deployment logic lives here
"""

import sys
import yaml
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
except ImportError:
    print("Installing required packages...")
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "pyyaml", "--break-system-packages", "-q"])
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box

from api_client import NDFCClient
from state_manager import StateManager
from resources import Resource, VRF, Network

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

FABRIC_NAME = "DevNet_VxLAN_Fabric"

# Fallback serial numbers for this reservation — update if sandbox is re-reserved.
# These are used if the inventory API returns an empty list.
FALLBACK_LEAF_SERIALS = [
    "99433ZAWNB5",  # site1-leaf1
    "9IN20QRUUYM",  # site1-leaf2
    "9SH6SMKS9CE",  # site1-leaf3
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def get_leaf_serials(client: NDFCClient, fabric_name: str) -> List[str]:
    """
    Fetch leaf serial numbers from NDFC.

    Tries the fabric-scoped inventory endpoint first (more reliable).
    Falls back to allswitches, then to the hardcoded FALLBACK_LEAF_SERIALS constant.
    Always returns at least one serial so the caller doesn't silently skip attachment.
    """
    # Attempt 1 — fabric-scoped inventory (returns only fabric members)
    try:
        switches = client.get(f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics" f"/{fabric_name}/inventory")
        if isinstance(switches, list) and switches:
            leafs = [
                s["serialNumber"] for s in switches if "leaf" in s.get("switchRole", "").lower() and s.get("serialNumber")
            ]
            if leafs:
                console.print(f"    [dim]Found {len(leafs)} leaf(s) via fabric inventory[/dim]")
                return leafs
    except Exception:
        pass

    # Attempt 2 — global allswitches endpoint
    try:
        switches = client.get("/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/allswitches")
        if isinstance(switches, list) and switches:
            leafs = [
                s["serialNumber"] for s in switches if "leaf" in s.get("switchRole", "").lower() and s.get("serialNumber")
            ]
            if leafs:
                console.print(f"    [dim]Found {len(leafs)} leaf(s) via allswitches[/dim]")
                return leafs
    except Exception:
        pass

    # Fallback — hardcoded serials for this reservation
    console.print(
        f"    [yellow]⚠ Could not fetch leaf serials from API — "
        f"using hardcoded fallback ({len(FALLBACK_LEAF_SERIALS)} leafs)[/yellow]"
    )
    console.print("    [dim]If sandbox was re-reserved, update FALLBACK_LEAF_SERIALS in this script[/dim]")
    return FALLBACK_LEAF_SERIALS


def build_vrf_attach_payload(fabric: str, vrf_name: str, serial_numbers: List[str]) -> List[Dict[str, Any]]:
    """
    Build the correct VRF attachment payload for the NDFC attachments API.

    NDFC expects a list where each element represents one VRF, containing a
    lanAttachList of per-switch attachment records.

    Correct structure (verified against live NDFC):
        [
          {
            "vrfName": "PRODUCTION",
            "lanAttachList": [
              {
                "fabric": "DevNet_VxLAN_Fabric",
                "vrfName": "PRODUCTION",
                "serialNumber": "99433ZAWNB5",
                "vlan": 0,
                "isAttached": true,
                "deployment": false,
                "freeformConfig": "",
                "extensionValues": "",
                "instanceValues": ""
              },
              ...
            ]
          }
        ]

    Common mistakes (both cause 500 errors):
      - Sending a flat array of individual dicts without the lanAttachList wrapper
      - Omitting required fields like isAttached, freeformConfig, extensionValues
    """
    return [
        {
            "vrfName": vrf_name,
            "lanAttachList": [
                {
                    "fabric": fabric,
                    "vrfName": vrf_name,
                    "serialNumber": serial,
                    "vlan": 0,  # 0 = use VRF's configured VLAN
                    "isAttached": True,
                    "deployment": False,  # Deployment is a separate step
                    "freeformConfig": "",
                    "extensionValues": "",
                    "instanceValues": "",
                }
                for serial in serial_numbers
            ],
        }
    ]


def build_network_attach_payload(
    fabric: str, network_name: str, vlan_id: int, serial_numbers: List[str]
) -> List[Dict[str, Any]]:
    """
    Build the correct Network attachment payload for the NDFC attachments API.

    Same lanAttachList wrapper structure as VRF, with network-specific fields.

    Correct structure (verified against live NDFC):
        [
          {
            "networkName": "Web_Servers",
            "lanAttachList": [
              {
                "fabric": "DevNet_VxLAN_Fabric",
                "networkName": "Web_Servers",
                "serialNumber": "99433ZAWNB5",
                "vlan": 100,
                "isAttached": true,
                "deployment": false,
                "freeformConfig": "",
                "extensionValues": "",
                "instanceValues": "",
                "dot1QVlan": 1,
                "untagged": false,
                "detachSwitchPorts": "",
                "switchPorts": "",
                "isTrunkAll": false
              },
              ...
            ]
          }
        ]
    """
    return [
        {
            "networkName": network_name,
            "lanAttachList": [
                {
                    "fabric": fabric,
                    "networkName": network_name,
                    "serialNumber": serial,
                    "vlan": vlan_id,
                    "isAttached": True,
                    "deployment": False,
                    "freeformConfig": "",
                    "extensionValues": "",
                    "instanceValues": "",
                    "dot1QVlan": 1,
                    "untagged": False,
                    "detachSwitchPorts": "",
                    "switchPorts": "",
                    "isTrunkAll": False,
                }
                for serial in serial_numbers
            ],
        }
    ]


# ─────────────────────────────────────────────────────────────────────────────
# IaC Framework
# ─────────────────────────────────────────────────────────────────────────────


class NexusIaCFull:
    """Complete IaC framework with apply/destroy"""

    def __init__(self, config_file: str = "desired_state_full.yaml"):
        self.config_file = Path(config_file)
        self.state_manager = StateManager()

        self.client = NDFCClient(url="https://10.10.20.60", username="admin", password="1vtG@lw@y")

        self.fabric_name = FABRIC_NAME

        # Cached leaf serials — fetched once per run, reused for all attachments
        self._leaf_serials: Optional[List[str]] = None

    def _get_leaf_serials(self) -> List[str]:
        """Fetch and cache leaf serial numbers"""
        if self._leaf_serials is None:
            self._leaf_serials = get_leaf_serials(self.client, self.fabric_name)
        return self._leaf_serials

    # ── Inventory ─────────────────────────────────────────────────────────────

    def inventory(self) -> None:
        """Show fabric inventory"""
        console.print("\n[bold cyan]═══ Nexus Dashboard Inventory ═══[/bold cyan]\n")

        try:
            fabrics = self.client.get_fabrics()
            switches = self.client.get_switches()

            if fabrics:
                fab_table = Table(show_header=True, box=box.SIMPLE)
                fab_table.add_column("Fabric", style="cyan")
                fab_table.add_column("Type", style="white")
                fab_table.add_column("ASN", style="yellow")
                fab_table.add_column("Status", style="green")

                for fabric in fabrics:
                    fab_table.add_row(
                        fabric.get("fabricName", "N/A"),
                        fabric.get("fabricTechnologyFriendly", "N/A"),
                        str(fabric.get("asn", "N/A")),
                        fabric.get("operStatus", "N/A"),
                    )
                console.print(fab_table)

            if switches:
                console.print()
                sw_table = Table(show_header=True, box=box.SIMPLE)
                sw_table.add_column("Hostname", style="cyan")
                sw_table.add_column("IP", style="white")
                sw_table.add_column("Role", style="yellow")
                sw_table.add_column("Serial", style="dim")
                sw_table.add_column("Status", style="green")

                for switch in switches:
                    hostname = switch.get("logicalName") or switch.get("hostName", "N/A")
                    status = switch.get("status", "unknown")
                    status_color = "green" if status == "ok" else "red"

                    sw_table.add_row(
                        hostname,
                        switch.get("ipAddress", "N/A"),
                        switch.get("switchRole", "N/A"),
                        switch.get("serialNumber", "N/A"),
                        f"[{status_color}]{status}[/{status_color}]",
                    )
                console.print(sw_table)

            console.print()
        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")

    # ── Sync ──────────────────────────────────────────────────────────────────

    def sync_from_ndfc(self) -> None:
        """Sync current state from NDFC"""
        console.print("\n[bold cyan]═══ Syncing State from NDFC ═══[/bold cyan]\n")

        try:
            self.state_manager.current_state = {}

            console.print("Fetching VRFs...", end=" ")
            vrfs = self.client.get_vrfs(self.fabric_name)
            console.print(f"[green]✓[/green] Found {len(vrfs)}")

            for vrf_data in vrfs:
                # vrfTemplateConfig is a JSON string nested inside the response —
                # parse it to get vrfVlanId (not available at the top level)
                import json as _json

                tpl_raw = vrf_data.get("vrfTemplateConfig", "{}")
                try:
                    tpl = _json.loads(tpl_raw) if isinstance(tpl_raw, str) else tpl_raw
                except Exception:
                    tpl = {}

                vrf = VRF(
                    name=vrf_data.get("vrfName"),
                    fabric=self.fabric_name,
                    vni=vrf_data.get("vrfId", 0),
                    vlan_id=int(tpl.get("vrfVlanId", 0)) or vrf_data.get("vrfVlanId", 0),
                )
                self.state_manager.update_resource(vrf)

            console.print("Fetching Networks...", end=" ")
            networks = self.client.get_networks(self.fabric_name)
            console.print(f"[green]✓[/green] Found {len(networks)}")

            for net_data in networks:
                # networkTemplateConfig is also a JSON string — gateway, vlanId,
                # and suppressArp are inside it, not at the top level of the response
                import json as _json

                tpl_raw = net_data.get("networkTemplateConfig", "{}")
                try:
                    tpl = _json.loads(tpl_raw) if isinstance(tpl_raw, str) else tpl_raw
                except Exception:
                    tpl = {}

                # suppress_arp comes back as string "true"/"false" — normalise to bool
                suppress_raw = tpl.get("suppressArp", "true")
                suppress_arp = suppress_raw is True or str(suppress_raw).lower() == "true"

                network = Network(
                    name=net_data.get("networkName"),
                    fabric=self.fabric_name,
                    vrf=net_data.get("vrf", ""),
                    vlan_id=int(tpl.get("vlanId", 0)) or net_data.get("vlanId", 0),
                    vni=net_data.get("networkId", 0),
                    gateway=tpl.get("gatewayIpAddress", ""),
                    mtu=int(tpl.get("mtu", 9216)),
                    suppress_arp=suppress_arp,
                )
                self.state_manager.update_resource(network)

            self.state_manager.save_current_state()
            console.print(f"\n[green]✓ State synchronized[/green]\n")

        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")

    # ── Plan ──────────────────────────────────────────────────────────────────

    def load_desired_config(self) -> List[Resource]:
        """Load desired configuration from YAML"""
        if not self.config_file.exists():
            console.print(f"[red]✗ Config file not found: {self.config_file}[/red]")
            return []

        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)

        resources = []
        for vrf_config in config.get("vrfs", []):
            resources.append(VRF(**vrf_config))
        for network_config in config.get("networks", []):
            resources.append(Network(**network_config))

        return resources

    def plan(self) -> Dict[str, Any]:
        """Show what will change"""
        console.print("\n[bold cyan]═══ Planning Changes ═══[/bold cyan]\n")

        desired_resources = self.load_desired_config()
        if not desired_resources:
            console.print("[yellow]No desired state loaded[/yellow]")
            return {}

        self.state_manager.set_desired_state(desired_resources)
        diff = self.state_manager.compute_diff()
        self._display_plan(diff)
        return diff

    def _display_plan(self, diff: Dict[str, Any]) -> None:
        """Display plan"""
        create_count = len(diff["create"])
        update_count = len(diff["update"])
        delete_count = len(diff["delete"])

        if create_count > 0:
            console.print("[bold green]Resources to CREATE:[/bold green]")
            for resource_id in diff["create"]:
                console.print(f"  [green]+ {resource_id}[/green]")

        if update_count > 0:
            console.print("\n[bold yellow]Resources to UPDATE:[/bold yellow]")
            for resource_id in diff["update"]:
                console.print(f"  [yellow]~ {resource_id}[/yellow]")

        if delete_count > 0:
            console.print("\n[bold red]Resources to DELETE:[/bold red]")
            for resource_id in diff["delete"]:
                console.print(f"  [red]- {resource_id}[/red]")

        if create_count == 0 and update_count == 0 and delete_count == 0:
            console.print("[green]✓ No changes needed — infrastructure is up to date[/green]")
        else:
            console.print(
                f"\nPlan: [green]{create_count} to create[/green], "
                f"[yellow]{update_count} to update[/yellow], "
                f"[red]{delete_count} to delete[/red]"
            )

    # ── Apply ─────────────────────────────────────────────────────────────────

    def apply(self) -> None:
        """Apply changes"""
        console.print("\n[bold cyan]═══ Applying Changes ═══[/bold cyan]\n")

        desired_resources = self.load_desired_config()
        if not desired_resources:
            return

        self.state_manager.set_desired_state(desired_resources)
        diff = self.state_manager.compute_diff()

        if not any([diff["create"], diff["update"], diff["delete"]]):
            console.print("[green]✓ Nothing to do[/green]\n")
            return

        # Pre-fetch leaf serials once for the entire apply run
        console.print("Fetching leaf serial numbers...", end=" ")
        serials = self._get_leaf_serials()
        console.print(f"[green]✓[/green] {serials}\n")

        # Create resources (VRFs before Networks — respects dependencies)
        vrfs_to_create = [r for r in diff["create"] if r.startswith("VRF:")]
        networks_to_create = [r for r in diff["create"] if r.startswith("Network:")]

        for resource_id in vrfs_to_create:
            self._create_resource(resource_id, serials)
            time.sleep(2)

        for resource_id in networks_to_create:
            self._create_resource(resource_id, serials)
            time.sleep(2)

        # Deletes
        for resource_id in diff["delete"]:
            self._delete_resource(resource_id)
            time.sleep(1)

        self.state_manager.save_current_state()
        console.print("\n[bold green]✓ Apply complete[/bold green]\n")

    def _create_resource(self, resource_id: str, leaf_serials: List[str]) -> None:
        """Create a single resource — full create → attach → deploy workflow"""
        resource_type, name = resource_id.split(":", 1)
        resource_data = self.state_manager.desired_state[resource_id]

        console.print(f"[green]Creating[/green] {resource_id}...")

        try:
            if resource_type == "VRF":
                self._create_vrf(VRF(**resource_data), leaf_serials)

            elif resource_type == "Network":
                self._create_network(Network(**resource_data), leaf_serials)

        except Exception as e:
            console.print(f"  [red]✗ {e}[/red]")
            import traceback

            console.print(f"  [dim]{traceback.format_exc()}[/dim]")

    def _create_vrf(self, vrf: VRF, leaf_serials: List[str]) -> None:
        """
        Full VRF lifecycle: create → attach → deploy

        Step order matters:
          1. Create the VRF definition in NDFC
          2. Attach to leaf switches (lanAttachList payload)
          3. Deploy — pushes config to the switches

        The previous code tried deploy → attach → deploy, which caused
        the attach to fail because NDFC wasn't ready for it yet.
        """
        base_url = f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down" f"/fabrics/{self.fabric_name}"

        # Step 1: Create
        console.print(f"  Step 1/3 — Creating VRF definition...", end=" ")
        self.client.create_vrf(vrf.create_payload())
        console.print("[green]✓[/green]")
        time.sleep(3)

        # Step 2: Attach to leaf switches
        console.print(f"  Step 2/3 — Attaching to {len(leaf_serials)} leaf(s)...", end=" ")
        attach_payload = build_vrf_attach_payload(self.fabric_name, vrf.name, leaf_serials)
        self.client.post(f"{base_url}/vrfs/attachments", json=attach_payload)
        console.print("[green]✓[/green]")
        time.sleep(3)

        # Step 3: Deploy — pushes config to switches
        # NOTE: Must be {"vrfName": "name"} string, NOT {"vrfNames": [...]} array
        console.print(f"  Step 3/3 — Deploying to switches...", end=" ")
        deploy_payload = {"vrfName": vrf.name}
        self.client.post(f"{base_url}/vrfs/deployments", json=deploy_payload)
        console.print("[green]✓[/green]")

        self.state_manager.update_resource(vrf)
        console.print(f"  [green]✓ VRF {vrf.name} complete[/green]")

    def _create_network(self, network: Network, leaf_serials: List[str]) -> None:
        """
        Full Network lifecycle: create → attach → deploy

        Same three-step pattern as VRF. Network deploy uses "networkNames" (array)
        which is different from VRF deploy which uses "vrfName" (string) — NDFC
        is inconsistent here, both forms have been verified against the live API.
        """
        base_url = f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down" f"/fabrics/{self.fabric_name}"

        # Step 1: Create
        console.print(f"  Step 1/3 — Creating Network definition...", end=" ")
        self.client.create_network(network.create_payload())
        console.print("[green]✓[/green]")
        time.sleep(3)

        # Step 2: Attach to leaf switches
        console.print(f"  Step 2/3 — Attaching to {len(leaf_serials)} leaf(s)...", end=" ")
        attach_payload = build_network_attach_payload(self.fabric_name, network.name, network.vlan_id, leaf_serials)
        self.client.post(f"{base_url}/networks/attachments", json=attach_payload)
        console.print("[green]✓[/green]")
        time.sleep(3)

        # Step 3: Deploy
        console.print(f"  Step 3/3 — Deploying to switches...", end=" ")
        deploy_payload = {"networkNames": [network.name]}
        self.client.post(f"{base_url}/networks/deployments", json=deploy_payload)
        console.print("[green]✓[/green]")

        self.state_manager.update_resource(network)
        console.print(f"  [green]✓ Network {network.name} complete[/green]")

    # ── Destroy ───────────────────────────────────────────────────────────────

    def _delete_resource(self, resource_id: str) -> None:
        """Delete a single resource"""
        resource_type, name = resource_id.split(":", 1)
        resource_data = self.state_manager.current_state[resource_id]

        console.print(f"[red]Deleting[/red] {resource_id}...", end=" ")

        try:
            if resource_type == "Network":
                self.client.delete_network(resource_data["fabric"], name)
                self.state_manager.delete_resource(resource_id)
                console.print("[green]✓[/green]")

            elif resource_type == "VRF":
                self.client.delete_vrf(resource_data["fabric"], name)
                self.state_manager.delete_resource(resource_id)
                console.print("[green]✓[/green]")

        except Exception as e:
            console.print(f"[red]✗ {e}[/red]")

    def destroy(self) -> None:
        """Destroy all managed resources"""
        console.print("\n[bold red]═══ DESTROY ALL RESOURCES ═══[/bold red]\n")
        console.print("[red]This will delete all VRFs and Networks managed by this tool![/red]\n")

        response = console.input("[yellow]Type 'yes' to confirm destruction:[/yellow] ")
        if response.lower() != "yes":
            console.print("[green]Destroy cancelled[/green]")
            return

        console.print("\n[bold red]Destroying resources...[/bold red]\n")

        resources = list(self.state_manager.current_state.keys())
        networks = [r for r in resources if r.startswith("Network:")]
        vrfs = [r for r in resources if r.startswith("VRF:")]

        # Delete networks before VRFs (dependency order)
        for resource_id in networks:
            self._delete_resource(resource_id)
            time.sleep(1)

        for resource_id in vrfs:
            self._delete_resource(resource_id)
            time.sleep(1)

        self.state_manager.save_current_state()
        console.print("\n[bold green]✓ Destroy complete[/bold green]\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────


def main():
    if len(sys.argv) < 2:
        console.print("\n[bold]Nexus Dashboard IaC[/bold]\n")
        console.print("[cyan]Commands:[/cyan]")
        console.print("  [yellow]inventory[/yellow] — Show fabrics and switches")
        console.print("  [yellow]sync[/yellow]      — Sync current state from NDFC")
        console.print("  [yellow]plan[/yellow]      — Show planned changes")
        console.print("  [yellow]apply[/yellow]     — Apply changes")
        console.print("  [yellow]destroy[/yellow]   — Destroy all managed resources")
        console.print("\n[dim]Example: uv run nexus_iac_full.py plan[/dim]\n")
        sys.exit(0)

    command = sys.argv[1].lower()
    iac = NexusIaCFull()

    try:
        if command == "inventory":
            iac.inventory()
        elif command == "sync":
            iac.sync_from_ndfc()
        elif command == "plan":
            iac.plan()
        elif command == "apply":
            iac.apply()
        elif command == "destroy":
            iac.destroy()
        else:
            console.print(f"[red]Unknown command: {command}[/red]")
            sys.exit(1)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)
    except Exception as e:
        console.print(f"\n[red]✗ Fatal error: {e}[/red]")
        import traceback

        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    main()
