#!/usr/bin/env python3
"""
Nexus Dashboard Infrastructure as Code Framework - COMPLETE VERSION

Full IaC with apply/destroy for VRFs and Networks

Usage:
    python nexus_iac_full.py inventory  # Show fabric inventory
    python nexus_iac_full.py sync       # Sync current state from NDFC
    python nexus_iac_full.py plan       # Show what will change
    python nexus_iac_full.py apply      # Apply changes
    python nexus_iac_full.py destroy    # Destroy all managed resources
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List

import yaml

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    print("Installing required packages...")
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "pyyaml", "--break-system-packages", "-q"])
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

from api_client import NDFCClient
from resources import VRF, Network, Resource
from state_manager import StateManager

console = Console()


class NexusIaCFull:
    """Complete IaC framework with apply/destroy"""

    def __init__(self, config_file: str = "desired_state_full.yaml"):
        self.config_file = Path(config_file)
        self.state_manager = StateManager()

        # DevNet sandbox credentials
        self.client = NDFCClient(url="https://10.10.20.60", username="admin", password="1vtG@lw@y")

        self.fabric_name = "DevNet_VxLAN_Fabric"

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
                sw_table.add_column("Status", style="green")

                for switch in switches:
                    hostname = switch.get("logicalName") or switch.get("hostName", "N/A")
                    status = switch.get("status", "unknown")
                    status_color = "green" if status == "ok" else "red"

                    sw_table.add_row(
                        hostname,
                        switch.get("ipAddress", "N/A"),
                        switch.get("switchRole", "N/A"),
                        f"[{status_color}]{status}[/{status_color}]",
                    )
                console.print(sw_table)

            console.print()
        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")

    def sync_from_ndfc(self) -> None:
        """Sync current state from NDFC"""
        console.print("\n[bold cyan]═══ Syncing State from NDFC ═══[/bold cyan]\n")

        try:
            # Clear existing state first (fresh sync)
            self.state_manager.current_state = {}

            # Get VRFs
            console.print("Fetching VRFs...", end=" ")
            vrfs = self.client.get_vrfs(self.fabric_name)
            console.print(f"[green]✓[/green] Found {len(vrfs)}")

            for vrf_data in vrfs:
                vrf = VRF(
                    name=vrf_data.get("vrfName"),
                    fabric=self.fabric_name,
                    vni=vrf_data.get("vrfId", 0),
                    vlan_id=vrf_data.get("vrfVlanId", 999),
                )
                self.state_manager.update_resource(vrf)

            # Get Networks
            console.print("Fetching Networks...", end=" ")
            networks = self.client.get_networks(self.fabric_name)
            console.print(f"[green]✓[/green] Found {len(networks)}")

            for net_data in networks:
                network = Network(
                    name=net_data.get("networkName"),
                    fabric=self.fabric_name,
                    vrf=net_data.get("vrf", ""),
                    vlan_id=net_data.get("vlanId", 0),
                    vni=net_data.get("networkId", 0),
                    gateway=net_data.get("gatewayIpAddress", ""),
                    mtu=net_data.get("mtu", 9216),
                    suppress_arp=net_data.get("suppressArp", True),
                )
                self.state_manager.update_resource(network)

            # Save state (only VRFs and Networks, no FabricInfo)
            self.state_manager.save_current_state()
            console.print("\n[green]✓ State synchronized[/green]\n")
            console.print("[dim]Note: Fabric metadata is not managed (read-only)[/dim]\n")

        except Exception as e:
            console.print(f"[red]✗ Error: {e}[/red]")

    def load_desired_config(self) -> List[Resource]:
        """Load desired configuration from YAML"""
        if not self.config_file.exists():
            console.print(f"[red]✗ Config file not found: {self.config_file}[/red]")
            return []

        with open(self.config_file, "r") as f:
            config = yaml.safe_load(f)

        resources = []

        # Parse VRFs
        for vrf_config in config.get("vrfs", []):
            resources.append(VRF(**vrf_config))

        # Parse Networks
        for network_config in config.get("networks", []):
            resources.append(Network(**network_config))

        return resources

    def plan(self) -> Dict[str, Any]:
        """Show what will change"""
        console.print("\n[bold cyan]═══ Planning Changes ═══[/bold cyan]\n")

        # Load desired state
        desired_resources = self.load_desired_config()
        if not desired_resources:
            console.print("[yellow]No desired state loaded[/yellow]")
            return {}

        self.state_manager.set_desired_state(desired_resources)

        # Compute diff
        diff = self.state_manager.compute_diff()

        # Display
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
                console.print(f"  [green]+[/green] {resource_id}")
            console.print()

        if update_count > 0:
            console.print("[bold yellow]Resources to UPDATE:[/bold yellow]")
            for resource_id in diff["update"]:
                console.print(f"  [yellow]~[/yellow] {resource_id}")
            console.print()

        if delete_count > 0:
            console.print("[bold red]Resources to DELETE:[/bold red]")
            for resource_id in diff["delete"]:
                console.print(f"  [red]-[/red] {resource_id}")
            console.print()

        total_changes = create_count + update_count + delete_count
        if total_changes == 0:
            console.print(
                Panel("[green]✓ No changes needed. Infrastructure matches desired state.[/green]", border_style="green")
            )
        else:
            console.print(
                Panel(
                    f"[yellow]Plan: {create_count} to create, {update_count} to update, {delete_count} to delete[/yellow]",
                    border_style="yellow",
                )
            )

    def apply(self) -> None:
        """Apply changes"""
        # Show plan first
        diff = self.plan()

        total_changes = len(diff.get("create", [])) + len(diff.get("update", [])) + len(diff.get("delete", []))
        if total_changes == 0:
            return

        # Confirm
        console.print()
        response = console.input("[yellow]Apply these changes? (yes/no):[/yellow] ")
        if response.lower() != "yes":
            console.print("[red]Apply cancelled[/red]")
            return

        console.print("\n[bold cyan]═══ Applying Changes ═══[/bold cyan]\n")

        try:
            # Create resources (in dependency order: VRFs before Networks)
            for resource_id in diff.get("create", []):
                self._create_resource(resource_id)
                time.sleep(1)  # Rate limiting

            # Updates
            for resource_id in diff.get("update", []):
                console.print(f"[yellow]Updating {resource_id}...[/yellow] (not implemented)")

            # Deletes
            for resource_id in diff.get("delete", []):
                self._delete_resource(resource_id)
                time.sleep(1)

            # Save state
            self.state_manager.save_current_state()

            console.print("\n[bold green]✓ Apply complete![/bold green]\n")
            console.print("[dim]Note: It may take 30-60 seconds for config to fully propagate to switches[/dim]\n")

        except Exception as e:
            console.print(f"\n[red]✗ Apply failed: {e}[/red]")
            import traceback

            console.print(f"[dim]{traceback.format_exc()}[/dim]")

    def _create_resource(self, resource_id: str) -> None:
        """Create a single resource"""
        resource_data = self.state_manager.get_resource(resource_id)
        resource_type = resource_id.split(":")[0]

        console.print(f"[green]Creating[/green] {resource_id}...", end=" ")

        try:
            if resource_type == "VRF":
                vrf = VRF(**resource_data)
                # Step 1: Create VRF
                self.client.create_vrf(vrf.create_payload())
                console.print("[green]✓[/green]")

                # Step 2: Deploy VRF FIRST (makes it available for attachments)
                console.print("  Deploying VRF...", end=" ")
                time.sleep(3)  # Wait for creation to settle
                try:
                    deploy_payload = {"vrfNames": [vrf.name]}
                    self.client.post(
                        f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{self.fabric_name}/vrfs/deployments",
                        json=deploy_payload,
                    )
                    console.print("[green]✓[/green]")
                    time.sleep(5)  # Wait for deployment
                except Exception as e:
                    console.print("[yellow]⚠ Deploy may need manual intervention[/yellow]")
                    console.print(f"    {e}")

                # Step 3: Try to attach to switches (may need to be done via GUI)
                console.print("  Attaching to switches...", end=" ")
                switches = self.client.get_switches()
                leaf_switches = [s for s in switches if "leaf" in s.get("switchRole", "").lower()]

                if leaf_switches:
                    try:
                        # Payload is an ARRAY of individual attachments
                        attach_payload = [
                            {
                                "fabric": self.fabric_name,
                                "vrfName": vrf.name,
                                "serialNumber": switch.get("serialNumber"),
                                "vlan": vrf.vlan_id,
                                "deployment": True,
                            }
                            for switch in leaf_switches
                        ]
                        self.client.post(
                            f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{self.fabric_name}/vrfs/attachments",
                            json=attach_payload,
                        )
                        console.print("[green]✓[/green]")

                        # Deploy attachments
                        console.print("  Deploying attachments...", end=" ")
                        time.sleep(2)
                        self.client.post(
                            f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{self.fabric_name}/vrfs/deployments",
                            json=deploy_payload,
                        )
                        console.print("[green]✓[/green]")
                    except Exception as e:
                        console.print(f"[yellow]⚠ {e}[/yellow]")
                        console.print("    [dim]VRF created but attachment failed. May need GUI to attach/deploy.[/dim]")

                self.state_manager.update_resource(vrf)

            elif resource_type == "Network":
                network = Network(**resource_data)
                # Step 1: Create Network
                self.client.create_network(network.create_payload())
                console.print("[green]✓[/green]")

                # Step 2: Attach to all leafs
                console.print("  Attaching to switches...", end=" ")
                time.sleep(2)

                switches = self.client.get_switches()
                leaf_switches = [s for s in switches if "leaf" in s.get("switchRole", "").lower()]

                if leaf_switches:
                    # Payload is an ARRAY of individual attachments
                    attach_payload = [
                        {
                            "fabric": self.fabric_name,
                            "networkName": network.name,
                            "serialNumber": switch.get("serialNumber"),
                            "vlan": network.vlan_id,
                            "deployment": True,
                        }
                        for switch in leaf_switches
                    ]
                    self.client.post(
                        f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{self.fabric_name}/networks/attachments",
                        json=attach_payload,
                    )
                    console.print("[green]✓[/green]")

                    # Step 3: Deploy
                    console.print("  Deploying...", end=" ")
                    time.sleep(2)
                    deploy_payload = {"networkNames": [network.name]}
                    self.client.post(
                        f"/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{self.fabric_name}/networks/deployments",
                        json=deploy_payload,
                    )
                    console.print("[green]✓[/green]")

                self.state_manager.update_resource(network)

        except Exception as e:
            console.print(f"[red]✗ {e}[/red]")
            import traceback

            console.print(f"[dim]{traceback.format_exc()}[/dim]")

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

        # Delete in reverse order (Networks before VRFs)
        resources = list(self.state_manager.current_state.keys())
        networks = [r for r in resources if r.startswith("Network:")]
        vrfs = [r for r in resources if r.startswith("VRF:")]

        for resource_id in networks:
            self._delete_resource(resource_id)
            time.sleep(1)

        for resource_id in vrfs:
            self._delete_resource(resource_id)
            time.sleep(1)

        self.state_manager.save_current_state()
        console.print("\n[bold green]✓ Destroy complete![/bold green]\n")


def main():
    if len(sys.argv) < 2:
        console.print("\n[bold]Nexus Dashboard IaC - FULL VERSION[/bold]\n")
        console.print("[cyan]Commands:[/cyan]")
        console.print("  [yellow]inventory[/yellow] - Show fabrics and switches")
        console.print("  [yellow]sync[/yellow]      - Sync current state from NDFC")
        console.print("  [yellow]plan[/yellow]      - Show planned changes")
        console.print("  [yellow]apply[/yellow]     - Apply changes")
        console.print("  [yellow]destroy[/yellow]   - Destroy all managed resources")
        console.print("\n[dim]Example: python nexus_iac_full.py plan[/dim]\n")
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
