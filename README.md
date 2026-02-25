# VxLAN EVPN Lab — Cisco Nexus 9000

[![CI](https://github.com/Sparty-5A/nexus-vxlan-evpn/actions/workflows/ci.yml/badge.svg)](https://github.com/Sparty-5A/nexus-vxlan-evpn/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Full VxLAN EVPN spine-leaf fabric implementation with Python IaC automation, deployed
and verified end-to-end in Cisco's DevNet Nexus Dashboard sandbox.

---

## Overview

This project implements a production-style VxLAN EVPN data center fabric on Cisco Nexus
9000v hardware using NX-OS 10.6 and NDFC 12.x. It covers the full stack: underlay OSPF,
BGP EVPN overlay, multi-tenant VRFs, anycast gateway, ARP suppression, multicast BUM
handling, and a Python IaC framework for automated VRF and network deployment against
the NDFC REST API.

The lab is designed to mirror real enterprise DC deployments — not a simplified tutorial
setup. Every design decision, issue encountered, and fix applied is documented in the
troubleshooting log.

**What this project demonstrates:**
- VxLAN EVPN fabric design and implementation on Cisco NX-OS
- BGP EVPN control plane — Type-2, Type-3, Type-5 routes verified in production
- Multi-tenant network segmentation with VRFs and L3 VNIs
- Python IaC framework with idempotent plan/apply against NDFC REST API
- Systematic debugging of real hardware, API, and protocol issues
- End-to-end data plane verification — cross-VTEP pings, NVE peer validation

---

## Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                         Site 1 Fabric                            │
│                                                                  │
│         site1-spine1               site1-spine2                  │
│         10.2.0.1/32                10.2.0.2/32                   │
│         VTEP: 10.3.0.1             VTEP: 10.3.0.2                │
│         BGP RR + BGW               BGP RR + BGW                  │
│         Anycast RP: 10.254.254.1   Anycast RP: 10.254.254.1      │
│              |    \             /    |                            │
│              |     \           /     |                            │
│         ─────┼──────┼─────────┼──────┼─────                      │
│              |      |         |      |                            │
│         site1-leaf1    site1-leaf2    site1-leaf3                 │
│         10.2.0.4/32    10.2.0.3/32   10.2.0.5/32                 │
│         VTEP:10.3.0.4  VTEP:10.3.0.3 VTEP:10.3.0.5              │
│                                           |                      │
│                                      (border leaf)               │
└───────────────────────────────────────────┼──────────────────────┘
                                            │ Eth1/4
                                       site1-edge1
                                       (perimeter — TBD)
                                            │
                                      backbone-isn1
                                       /          \
┌─────────────────────────────────────/────────────\───────────────┐
│                     Site 2 Fabric  /              \              │
│                                                                  │
│                            site2-spine1                          │
│                            10.2.1.1/32                           │
│                            VTEP: 10.3.1.1                        │
│                            BGP RR + BGW                          │
│                                 |                                │
│                            site2-leaf1                           │
│                            10.2.1.2/32                           │
│                            VTEP: 10.3.1.2                        │
└──────────────────────────────────────────────────────────────────┘
```

**Underlay:** OSPF point-to-point links, /31 addressing, MTU 9216
**Overlay:** BGP EVPN ASN 65001, spine route reflectors with cluster-id
**BUM handling:** PIM sparse-mode multicast, anycast RP at 10.254.254.1
**DCI:** BGW multisite EVPN over backbone ISN (Site 2 in progress)

---

## Network Design

### VRFs and L3 VNIs

| VRF | L3 VNI | Transit VLAN | Purpose |
|-----|--------|-------------|---------|
| production | 50001 | 2001 | Production workloads |
| dev | 50002 | 2002 | Development workloads |

### Networks and L2 VNIs

| Network | VLAN | L2 VNI | Gateway | VRF |
|---------|------|--------|---------|-----|
| App_Servers | 30 | 30300 | 10.30.0.1/24 | production |
| Dev_Network | 40 | 30400 | 10.40.0.1/24 | dev |
| Web_Servers | 100 | 30100 | 192.168.100.1/24 | production |
| Database_Servers | 200 | 30200 | 192.168.200.1/24 | production |

All L2 VNIs use multicast group 239.1.1.0 for BUM traffic. ARP suppression enabled
on all VNIs — verified via `SA` flag in `show nve vni`.

### Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| BUM handling | Multicast (PIM) | Scales better than ingress replication for intra-site BUM |
| Gateway model | Anycast (same IP/MAC on all leafs) | Eliminates suboptimal routing — any leaf routes locally |
| RD format | loopback0:VRF-index (rd auto) | loopback0 is BGP RID, not loopback1 (VTEP) |
| Spine role | RR + BGW-capable VTEP | Spines have loopback1 for DCI — not pure RR-only |
| L3 VNI transit VLAN | 2001/2002 | Fabric-assigned — never used by hosts |
| Route redistribution tag | 12345 | Required to match route-map for redistribute direct |

---

## IaC Framework

Python-based declarative infrastructure management against the NDFC REST API.
Supports full create/attach/deploy lifecycle with idempotent plan/apply.

```
infrastructure_as_code/
├── nexus_iac_full.py        # Main IaC engine — plan/apply/destroy/sync
├── api_client.py            # NDFC REST API client with retry and auth
├── resources.py             # VRF and Network resource definitions + payloads
├── state_manager.py         # Current/desired state diff engine
└── desired_state_full.yaml  # Declarative desired state
```

### Usage

```bash
cd infrastructure_as_code

uv run nexus_iac_full.py sync        # Sync current state from NDFC
uv run nexus_iac_full.py plan        # Show planned changes
uv run nexus_iac_full.py apply       # Apply changes
uv run nexus_iac_full.py inventory   # Show fabric inventory
uv run nexus_iac_full.py destroy     # Destroy all managed resources
```

### Idempotent Plan/Apply

After sync against a correctly deployed fabric, plan returns clean:

```
═══ Planning Changes ═══

✓ No changes needed — infrastructure is up to date
```

The tool correctly parses `networkTemplateConfig` and `vrfTemplateConfig` JSON strings
from the NDFC API response — these are serialized JSON embedded inside the response
object, not top-level fields. Getting this right was one of the harder debugging
challenges (see Troubleshooting Log, Issue 7).

### Desired State (YAML)

```yaml
vrfs:
  - name: PRODUCTION
    fabric: DevNet_VxLAN_Fabric
    vni: 50001
    vlan_id: 2001

networks:
  - name: Web_Servers
    fabric: DevNet_VxLAN_Fabric
    vrf: PRODUCTION
    vlan_id: 100
    vni: 30100
    gateway: 192.168.100.1/24
    suppress_arp: true
```

---

## Verified Outputs

### BGP EVPN Summary (site1-leaf1)

```
BGP router identifier 10.2.0.4, local AS number 65001
18 network entries and 30 paths using 6968 bytes of memory
BGP clusterlist entries [2/8]   ← both spines acting as RR cluster

Neighbor     V    AS    MsgRcvd  MsgSent  TblVer  State/PfxRcd
10.2.0.1     4 65001      1109     1094      26    8
10.2.0.2     4 65001      1109     1096      26    8

Type-1  Type-2  Type-3  Type-4  Type-5
0       0       0       0       8         ← all routes are Type-5 (no physical hosts)
```

30 paths for 18 entries = dual-path redundancy via both spine RRs working correctly.

### NVE Peers (site1-leaf1)

```
Interface  Peer-IP    State  LearnType  Uptime    Router-Mac
nve1       10.3.0.3   Up     CP         15:24:26  520c.a97b.1b08
nve1       10.3.0.5   Up     CP         00:17:36  5201.7ee9.1b08
```

`LearnType: CP` — MAC/IP learning via BGP EVPN control plane, not data plane flooding.

### NVE VNI Status (site1-leaf1)

```
Interface  VNI    Multicast-group  State  Mode  Type [BD/VRF]   Flags
nve1       30100  239.1.1.0        Up     CP    L2 [100]        SA
nve1       30200  239.1.1.0        Up     CP    L2 [200]        SA
nve1       30300  239.1.1.0        Up     CP    L2 [30]         SA
nve1       30400  239.1.1.0        Up     CP    L2 [40]         SA
nve1       50001  n/a              Up     CP    L3 [production]
nve1       50002  n/a              Up     CP    L3 [dev]
```

`SA` = ARP Suppression active on all L2 VNIs. Required TCAM carving on Nexus 9000
hardware (see Troubleshooting Log, Issue 5).

### Cross-VTEP Ping — Data Plane Verified

```
site1-leaf1# ping 10.100.0.2 vrf production source 10.100.0.1
PING 10.100.0.2 (10.100.0.2) from 10.100.0.1: 56 data bytes
64 bytes from 10.100.0.2: icmp_seq=0 ttl=254 time=9.74 ms
64 bytes from 10.100.0.2: icmp_seq=1 ttl=254 time=3.091 ms
64 bytes from 10.100.0.2: icmp_seq=2 ttl=254 time=2.865 ms
5 packets transmitted, 5 packets received, 0.00% packet loss
```

Traffic path: leaf1 loopback → VRF PRODUCTION route lookup → L3 VNI 50001 VxLAN
encap → spine (outer IP forward only, no VNI inspection) → leaf2 decap → VRF
PRODUCTION lookup → leaf2 loopback delivery.

---

## Troubleshooting Log

Seven real issues encountered and resolved, documented in
[TROUBLESHOOTING_LOG.md](TROUBLESHOOTING_LOG.md).

| # | Phase | Issue | Fix |
|---|-------|-------|-----|
| 1 | Config extraction | NDFC inventory misses non-fabric devices | Hardcoded fallback device list |
| 2 | Config extraction | Empty config files from SSH timeout | Content validation + increased timeout |
| 3 | IaC API | VRF deploy payload wrong type | `vrfName` string not `vrfNames` array |
| 4 | IaC API | Attachment API requires switch serial numbers | Fetch serials dynamically, fix payload structure |
| 5 | Switch hardware | ARP suppression blocked — TCAM region size 0 | Reduce racl 1536→1024, allocate arp-ether 256, reload |
| 6 | IaC API | Attach payload missing `lanAttachList` wrapper | Restructure payload, add required fields |
| 7 | IaC API | Sync parsing wrong fields — templateConfig is a JSON string | Parse templateConfig on read, normalise bool types |

Issue 5 is particularly worth reading — it covers NX-OS TCAM region sizing rules,
carving space from an existing region, and why this requires a reload. A common
production issue when enabling ARP suppression after initial fabric deployment.

---

## Project Structure

```
nexus-vxlan-evpn/
├── configs/                       # Extracted device configs (9 devices)
│   ├── site1-spine1.txt
│   ├── site1-spine2.txt
│   ├── site1-leaf1.txt
│   ├── site1-leaf2.txt
│   ├── site1-leaf3.txt
│   └── ...
├── infrastructure_as_code/        # IaC framework
│   ├── nexus_iac_full.py          # Main engine
│   ├── api_client.py              # NDFC REST client
│   ├── resources.py               # Resource definitions and API payloads
│   ├── state_manager.py           # State diff engine
│   ├── desired_state_full.yaml    # Declarative desired state
│   └── state.json                 # Current state cache
├── scripts/                       # Utility scripts
│   ├── verify_fabric.py           # Fabric health verification
│   ├── extract_configs.py         # NDFC API config extraction
│   └── extract_configs_ssh.py     # SSH-based config extraction
├── tests/                         # Test suite
├── .github/workflows/             # CI pipeline
├── README.md
├── TROUBLESHOOTING_LOG.md         # 7-issue documented debug log
└── pyproject.toml
```

---

## Installation

### Prerequisites

- Python 3.13+
- `uv` package manager
- VPN access to Cisco DevNet sandbox (openconnect)

### Setup

```bash
git clone https://github.com/Sparty-5A/nexus-vxlan-evpn.git
cd nexus-vxlan-evpn

# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv pip install -e .
```

### Connecting to the Sandbox

```bash
sudo openconnect devnetsandbox-usw1-reservation.cisco.com:PORT
```

---

## Key Commands Reference

### Fabric Verification

```
show bgp l2vpn evpn summary          # BGP EVPN neighbor status and prefix counts
show bgp l2vpn evpn                  # Full EVPN table with route types
show nve peers                       # VTEP peer state and learn type
show nve vni                         # VNI status, mode, and flags (SA = ARP suppression)
show ip route vrf production         # Per-VRF routing table with VxLAN encap details
show l2route evpn mac all            # MAC table learned via EVPN
show ip arp suppression-cache detail # ARP suppression cache entries
show hardware access-list tcam region # TCAM allocation verification
```

---

## SP-to-DC Conceptual Mapping

Coming from a Service Provider background? VxLAN EVPN maps directly to familiar concepts:

| MPLS Concept | VxLAN EVPN Equivalent |
|-------------|----------------------|
| MPLS VPN label | L3 VNI (50xxx) — fabric-wide significance vs MPLS local significance |
| PE loopback (tunnel endpoint) | Leaf loopback1 (VTEP source) |
| VRF | VRF (identical) |
| Route Distinguisher | Route Distinguisher (identical mechanism) |
| Route Target | Route Target (identical mechanism) |
| iBGP Route Reflector | Spine as BGP RR (identical config) |
| P router (label swap only) | Spine (IP forward only, no VNI awareness) |
| VPLS VSI + pseudowire mesh | L2 VNI + NVE peer tunnels |
| VPLS flood-and-learn | BGP EVPN Type-2 — control plane MAC distribution, no flooding |
| VPNv4 prefix advertisement | BGP EVPN Type-5 IP prefix route |

The L3 VNI is the VPN label. The VTEP loopback is the PE loopback. The spine is the
P router. BGP EVPN is VPNv4 and VPLS combined. RT/RD mechanisms are identical.

---

## Work in Progress

- [ ] Multi-site DCI — BGW config on site1 spines, full site2 fabric, cross-site pings
- [ ] Palo Alto VM-Series NGFW at perimeter (site1-edge1 position)
- [ ] FRRouting VM for open-source BGP EVPN implementation comparison
- [ ] verify_fabric.py output captured against live fabric for documentation
- [ ] GitHub Actions CI — YAML linting and config validation

---

## Author

**Scott Penry**

Network engineer with 10+ years in service provider networking (MPLS L3VPN, VPLS,
BGP, carrier operations), building hands-on depth in data center networking and
infrastructure automation. This project represents real deployment and debugging
work — not tutorial reproduction.

- [GitHub](https://github.com/Sparty-5A)

---

## Resources

- [Cisco NDFC Configuration Guide](https://www.cisco.com/c/en/us/support/cloud-systems-management/prime-data-center-network-manager/products-installation-and-configuration-guides-list.html)
- [BGP EVPN RFC 7432](https://datatracker.ietf.org/doc/html/rfc7432)
- [VxLAN RFC 7348](https://datatracker.ietf.org/doc/html/rfc7348)
- [Cisco DevNet Sandbox](https://devnetsandbox.cisco.com/)
- [NX-OS VxLAN Configuration Guide](https://www.cisco.com/c/en/us/td/docs/switches/datacenter/nexus9000/sw/vxlan_evpn.html)