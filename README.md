# VxLAN EVPN Lab - Cisco Nexus 9000

[![CI](https://github.com/YOUR_USERNAME/nexus-vxlan-evpn-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/nexus-vxlan-evpn-lab/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Hands-on VxLAN EVPN implementation and automation using Cisco Nexus 9000v in DevNet sandbox.

## 🎯 Overview

This repository documents my learning journey with VxLAN EVPN data center networking. Using Cisco's DevNet Nexus Dashboard sandbox, I implemented and automated a spine-leaf fabric with BGP EVPN control plane.

**Key Learning Areas:**
- VxLAN overlay networks (VTEP, VNI, encapsulation)
- EVPN control plane (BGP l2vpn evpn)
- Spine-leaf architecture
- Python automation with NXAPI
- Multi-site data center connectivity

## 🏗️ Topology
```
Site 1 - VxLAN EVPN Fabric
    Spine1 (10.10.20.171) -------- Spine2 (10.10.20.172)
       |    \        /    |           (BGP Route Reflectors)
       |     \      /     |
       |      \    /      |
    Leaf1   Leaf2   Leaf3            (VTEPs)
(10.10.20.173) (10.10.20.174) (10.10.20.175)
```

**Components:**
- 2 Spine switches (BGP Route Reflectors)
- 3 Leaf switches (VTEPs with NVE interfaces)
- Underlay: OSPF or BGP
- Overlay: BGP EVPN (address-family l2vpn evpn)

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- Access to Cisco DevNet Nexus Dashboard sandbox
- VPN connection to sandbox environment

### Installation
```bash
# Clone repository
git clone https://github.com/YOUR_USERNAME/nexus-vxlan-evpn-lab.git
cd nexus-vxlan-evpn-lab

# Install uv (if not installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -e .

# Configure credentials
cp .env.example .env
# Edit .env with your sandbox credentials
```

### Usage
```bash
# Verify fabric health
python scripts/verify_fabric.py

# Add a new VNI to fabric
python scripts/add_vni.py --vlan 200 --vni 10200 --name "Python_VLAN"

# Get fabric topology
python scripts/get_topology.py
```

## 📚 Scripts

### Core Scripts
- `verify_fabric.py` - Check VxLAN fabric operational status
- `add_vni.py` - Automate VNI deployment across leafs
- `get_topology.py` - Retrieve and display fabric topology
- `show_evpn.py` - Display BGP EVPN routes and MAC table

### Learning Scripts
- `explore_nxapi.py` - Interactive NXAPI examples
- `netconf_example.py` - NETCONF configuration examples (future)

## 🎓 What I Learned

### VxLAN Concepts
- **VTEP** (Virtual Tunnel Endpoint): Leaf switches that encapsulate/decapsulate L2 frames
- **VNI** (VxLAN Network Identifier): 24-bit identifier similar to VLAN ID for overlay
- **Encapsulation**: L2 Ethernet frames wrapped in UDP (port 4789) for L3 transport
- **Why VxLAN**: Overcomes 4096 VLAN limitation, enables multi-tenancy, scales data centers

### EVPN Control Plane
- **BGP EVPN**: Uses MP-BGP to distribute MAC/IP information
- **Route Types**: Type 2 (MAC/IP), Type 3 (IMET), Type 5 (IP Prefix)
- **Advantages**: More efficient than flood-and-learn multicast
- **Spines**: Act as BGP Route Reflectors, not VTEPs

### Architecture Patterns
- **Underlay vs Overlay**: Underlay provides IP reachability (OSPF/BGP), overlay provides L2 extension (VxLAN)
- **Symmetric vs Asymmetric IRB**: Inter-subnet routing strategies
- **Multi-site**: Extending L2 domains across data centers

### Automation
- **NXAPI**: REST API for Nexus configuration
- **httpx**: Modern async-capable HTTP client
- **Idempotency**: Scripts can run multiple times safely

## 🔧 Technologies Used

- **Cisco Nexus 9000** (NX-OS)
- **VxLAN** with EVPN control plane
- **BGP** (address-family l2vpn evpn)
- **Python 3.13** (httpx, rich, python-dotenv)
- **NXAPI** (Nexus REST API)
- **DevNet Sandbox** (lab environment)

## 📊 Key Commands Reference

### Verification
```bash
# Check VTEP peers
show nve peers

# BGP EVPN status
show bgp l2vpn evpn summary
show bgp l2vpn evpn

# VNI mappings
show vxlan
show nve vni

# MAC addresses learned via EVPN
show l2route evpn mac all
```

### Configuration
```bash
# Enable VxLAN feature
feature nv overlay
feature vn-segment-vlan-based

# Create VLAN with VNI
vlan 100
  vn-segment 10100

# Configure NVE interface
interface nve1
  source-interface loopback0
  member vni 10100
    ingress-replication protocol bgp
```

## 🎤 Interview Talking Points

**"What's your VxLAN experience?"**

> "I implemented VxLAN EVPN in Cisco's DevNet sandbox using Nexus 9000 switches. I worked with a spine-leaf fabric - 2 spines acting as BGP route reflectors and 3 leafs configured as VTEPs.
>
> I configured VNIs, mapped them to VLANs, set up the BGP EVPN control plane, and verified overlay connectivity. I also built Python automation using NXAPI to deploy VNIs programmatically.
>
> The concepts actually map closely to my MPLS L3VPN experience - both use BGP for control plane and create overlay networks for multi-tenancy. While it's lab experience, I understand the architecture and could work with VxLAN EVPN in production."

## 📝 Project Structure
```
nexus-vxlan-evpn-lab/
├── scripts/
│   ├── verify_fabric.py
│   ├── add_vni.py
│   ├── get_topology.py
│   └── show_evpn.py
├── tests/
│   └── test_basic.py
├── docs/
│   ├── learning-notes.md
│   └── commands.md
├── .github/
│   └── workflows/
│       └── ci.yml
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```

## 🤝 Contributing

This is a personal learning project, but suggestions and improvements are welcome!

## 📖 Resources

- [Cisco VxLAN Documentation](https://www.cisco.com/c/en/us/products/switches/what-is-vxlan.html)
- [EVPN Introduction](https://www.cisco.com/c/en/us/td/docs/dcn/whitepapers/ethernet-vpn-evpn-intro.html)
- [DevNet Nexus Dashboard Sandbox](https://devnetsandbox.cisco.com/)
- [NXAPI Developer Guide](https://developer.cisco.com/docs/nx-os/)

## 👤 Author

**Scott Penry**
- Network automation engineer with 10+ years in service provider networking
- Expanding expertise into data center networking and automation
- Focus: Infrastructure as Code, SD-WAN, VxLAN/EVPN

## 📈 Learning Timeline

- **Hours 1-3**: Conceptual learning (VxLAN, EVPN fundamentals)
- **Hours 4-6**: Hands-on CLI exploration (verify existing fabric)
- **Hours 7-10**: Python automation (NXAPI scripts)
- **Hours 11-12**: Documentation and GitHub

**Total Time Investment**: ~12 hours for functional VxLAN/EVPN knowledge

---

**Built as part of continuous learning in data center networking** 🚀