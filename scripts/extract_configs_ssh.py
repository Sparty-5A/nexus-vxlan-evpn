#!/usr/bin/env python3
"""
Extract running configurations from all switches in the fabric.
Uses SSH to connect to switches directly and run 'show running-config'.
"""
import httpx
import json
from pathlib import Path
import urllib3
from netmiko import ConnectHandler
import concurrent.futures

# Disable SSL warnings for lab environment
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Nexus Dashboard connection
BASE_URL = 'https://10.10.20.60'
USERNAME = 'admin'
PASSWORD = '1vtG@lw@y'

# SSH credentials for switches (usually same as ND, but can be different)
SWITCH_USERNAME = 'admin'
SWITCH_PASSWORD = 'C1sco12345'

# Fallback device list if NDFC inventory is incomplete
# Includes all devices from sandbox topology
FALLBACK_DEVICES = [
    {"logicalName": "s1-spine1", "ipAddress": "10.10.20.171", "switchRole": "spine"},
    {"logicalName": "s1-spine2", "ipAddress": "10.10.20.172", "switchRole": "spine"},
    {"logicalName": "s1-leaf1",  "ipAddress": "10.10.20.173", "switchRole": "leaf"},
    {"logicalName": "s1-leaf2",  "ipAddress": "10.10.20.174", "switchRole": "leaf"},
    {"logicalName": "s1-leaf3",  "ipAddress": "10.10.20.175", "switchRole": "border"},
    {"logicalName": "s1-edge1",  "ipAddress": "10.10.20.176", "switchRole": "edge"},
    {"logicalName": "backbone",  "ipAddress": "10.10.20.177", "switchRole": "backbone"},
    {"logicalName": "s2-spine1", "ipAddress": "10.10.20.178", "switchRole": "spine"},
    {"logicalName": "s2-leaf1",  "ipAddress": "10.10.20.179", "switchRole": "leaf"},
]


def main():
    print("=" * 60)
    print("VxLAN EVPN Fabric Configuration Extractor")
    print("=" * 60)

    # Authenticate (using same method as verify_fabric.py)
    print("\n[1/3] Authenticating to Nexus Dashboard...")
    auth_response = httpx.post(
        f'{BASE_URL}/login',
        json={
            'userName': USERNAME,
            'userPasswd': PASSWORD,
            'domain': 'local'
        },
        verify=False,
        timeout=30
    )

    if auth_response.status_code != 200:
        print(f"❌ Authentication failed: {auth_response.status_code}")
        print(f"Response: {auth_response.text}")
        return

    # Get JWT token from response
    data = auth_response.json()
    token = data.get('token') or data.get('jwttoken')

    if not token:
        print("❌ Authentication failed: No token in response")
        return

    print("✓ Authenticated successfully")

    # Create headers with Bearer token for subsequent requests
    headers = {'Authorization': f'Bearer {token}'}

    # Get all switches
    print("\n[2/3] Getting switch inventory...")
    switches_response = httpx.get(
        f'{BASE_URL}/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/allswitches',
        headers=headers,
        verify=False,
        timeout=30
    )

    switches = switches_response.json()
    print(f"✓ Found {len(switches)} switches")

    if len(switches) < 9:
        print(f"⚠️  Expected 9+ devices, got {len(switches)}")
        print(f"⚠️  Using fallback device list for complete extraction")
        switches = FALLBACK_DEVICES

    # Create output directory
    output_dir = Path('configs_new')
    output_dir.mkdir(exist_ok=True)
    print(f"✓ Created output directory: {output_dir}")

    # Extract configs via SSH
    print("\n[3/3] Extracting configurations via SSH...")
    print("-" * 60)

    def extract_config(switch):
        """Extract config from a single switch via SSH."""
        name = switch['logicalName']
        ip = switch['ipAddress']
        role = switch.get('switchRole', 'unknown')

        print(f"\n{name}")
        print(f"  IP Address: {ip}")
        print(f"  Role: {role}")
        print(f"  Connecting via SSH...", end=" ", flush=True)

        try:
            # Connect to switch via SSH
            device = {
                'device_type': 'cisco_nxos',
                'host': ip,
                'username': SWITCH_USERNAME,
                'password': SWITCH_PASSWORD,
                'timeout': 60,
                'session_log': None,
            }

            connection = ConnectHandler(**device)

            # Run show running-config
            config = connection.send_command(
                'show running-config',
                read_timeout=180,  # 3 minutes
                expect_string=r'#',  # Wait for prompt
            )

            connection.disconnect()

            # Don't save empty configs
            if not config or len(config.strip()) < 100:
                print(f"❌ Empty or too-short response ({len(config)} chars)")
                return False

            # Save to file
            output_file = output_dir / f'{name}.txt'
            with open(output_file, 'w') as f:
                f.write(config)

            # Get file size
            size_kb = len(config) / 1024
            print(f"✓ ({size_kb:.1f} KB)")
            print(f"  Saved to: {output_file}")
            return True

        except Exception as e:
            print(f"❌ Error ({type(e).__name__}): {e}")
            return False

    # Extract configs (parallel for speed)
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(extract_config, switches))

    successful = sum(results)

    print("\n" + "=" * 60)
    print("EXTRACTION COMPLETE")
    print("=" * 60)
    print(f"\nSuccessfully extracted: {successful}/{len(switches)} switches")
    print(f"Configs saved in: {output_dir.absolute()}")
    print("\nNext steps:")
    print("1. Review configs: ls -lh configs/")
    print("2. Quick peek: cat configs/site1-leaf1.txt | head -50")
    print("3. Search for OSPF: grep -A 20 'router ospf' configs/site1-leaf1.txt")
    print("4. Search for BGP: grep -A 50 'router bgp' configs/site1-leaf1.txt")
    print("5. Search for NVE: grep -A 20 'interface nve1' configs/site1-leaf1.txt")


if __name__ == '__main__':
    main()