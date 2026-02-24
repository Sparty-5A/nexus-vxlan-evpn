#!/usr/bin/env python3
"""
Extract running configurations from all switches in the fabric.
Saves configs to configs/ directory for analysis.
"""

from pathlib import Path

import httpx
import urllib3

# Disable SSL warnings for lab environment
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Nexus Dashboard connection
BASE_URL = "https://10.10.20.60"
USERNAME = "admin"
PASSWORD = "1vtG@lw@y"


def main():
    print("=" * 60)
    print("VxLAN EVPN Fabric Configuration Extractor")
    print("=" * 60)

    # Authenticate (using same method as verify_fabric.py)
    print("\n[1/4] Authenticating to Nexus Dashboard...")
    auth_response = httpx.post(
        f"{BASE_URL}/login", json={"userName": USERNAME, "userPasswd": PASSWORD, "domain": "local"}, verify=False, timeout=30
    )

    if auth_response.status_code != 200:
        print(f"❌ Authentication failed: {auth_response.status_code}")
        print(f"Response: {auth_response.text}")
        return

    # Get JWT token from response
    data = auth_response.json()
    token = data.get("token") or data.get("jwttoken")

    if not token:
        print("❌ Authentication failed: No token in response")
        return

    print("✓ Authenticated successfully")

    # Create headers with Bearer token for subsequent requests
    headers = {"Authorization": f"Bearer {token}"}

    # Get fabric info
    print("\n[2/4] Getting fabric information...")
    fabric_response = httpx.get(
        f"{BASE_URL}/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics", headers=headers, verify=False, timeout=30
    )

    fabrics = fabric_response.json()
    if not fabrics:
        print("❌ No fabrics found")
        return

    fabric_name = fabrics[0]["fabricName"]
    print(f"✓ Found fabric: {fabric_name}")

    # Get all switches
    print("\n[3/4] Getting switch inventory...")
    switches_response = httpx.get(
        f"{BASE_URL}/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/allswitches",
        headers=headers,
        verify=False,
        timeout=30,
    )

    switches = switches_response.json()
    print(f"✓ Found {len(switches)} switches")

    # Create output directory
    output_dir = Path("configs")
    output_dir.mkdir(exist_ok=True)
    print(f"✓ Created output directory: {output_dir}")

    # Extract configs
    print("\n[4/4] Extracting configurations...")
    print("-" * 60)

    for switch in switches:
        name = switch["logicalName"]
        ip = switch["ipAddress"]
        role = switch.get("switchRole", "unknown")

        print(f"\n{name}")
        print(f"  IP Address: {ip}")
        print(f"  Role: {role}")
        print("  Extracting config...", end=" ")

        try:
            # Get running config
            config_response = httpx.get(
                f"{BASE_URL}/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/config/{ip}",
                headers=headers,
                verify=False,
                timeout=60,
            )

            if config_response.status_code == 200:
                config = config_response.text

                # Save to file
                output_file = output_dir / f"{name}.txt"
                with open(output_file, "w") as f:
                    f.write(config)

                # Get file size
                size_kb = len(config) / 1024
                print(f"✓ ({size_kb:.1f} KB)")
                print(f"  Saved to: {output_file}")
            else:
                print(f"❌ Failed (HTTP {config_response.status_code})")

        except Exception as e:
            print(f"❌ Error: {e}")

    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"\nConfigs saved in: {output_dir.absolute()}")
    print("\nNext steps:")
    print("1. Review configs: ls -lh configs/")
    print("2. Quick peek: cat configs/site1-leaf1.txt | head -50")
    print("3. Search for OSPF: grep -A 20 'router ospf' configs/site1-leaf1.txt")
    print("4. Search for BGP: grep -A 50 'router bgp' configs/site1-leaf1.txt")
    print("5. Search for NVE: grep -A 20 'interface nve1' configs/site1-leaf1.txt")


if __name__ == "__main__":
    main()
