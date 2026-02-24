# Troubleshooting Log — nexus-vxlan-evpn-lab

Real problems encountered during deployment and automation development.
This documents the actual debugging process rather than just the final working state.

Each entry includes the error, root cause, fix, and lesson learned.

---

## Phase 1 Issues — Config Extraction

---

### Issue 1 — NDFC Inventory Only Returns Fabric Switches

**Phase:** 1
**Component:** `scripts/extract_configs_ssh.py`

**Problem:**
Running `extract_configs_ssh.py` against the NDFC inventory API only returned 5 switches
instead of the expected 9 devices in the full sandbox topology. The edge router, backbone
switch, and Site 2 devices were never attempted.

**Root Cause:**
The NDFC inventory endpoint `/inventory/allswitches` only returns devices that are
members of a managed fabric. The sandbox topology includes devices outside any fabric:
- `s1-edge1` (10.10.20.176) — external edge router, not in DevNet_VxLAN_Fabric
- `backbone` (10.10.20.177) — inter-site backbone, not in any fabric
- `s2-spine1` (10.10.20.178) — Site 2 devices, fabric not yet onboarded
- `s2-leaf1` (10.10.20.179) — Site 2 devices, fabric not yet onboarded

**Fix:**
Added a fallback device list hardcoded from the sandbox topology documentation.
If NDFC inventory returns fewer than 9 devices, the script falls back to the
complete list and connects via SSH directly:

```python
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

if len(switches) < 9:
    print(f"⚠️  Expected 9 devices, got {len(switches)}")
    print(f"⚠️  Using fallback device list for complete extraction")
    switches = FALLBACK_DEVICES
```

**Result:** 9/9 switches extracted successfully on next run.

**Lesson:**
NDFC inventory APIs only surface devices under active fabric management. Always
maintain a topology-level device list independent of the controller for complete
visibility. In production, unmanaged or out-of-band devices would also be missed
by controller-based inventory queries.

---

### Issue 2 — SSH Config Extraction Producing Empty Files

**Phase:** 1
**Component:** `scripts/extract_configs_ssh.py`

**Problem:**
Previous extraction run produced empty `leaf2.txt` and `leaf3.txt` files in
`configs_final/`. The files existed (SSH connected successfully) but contained
no configuration data.

**Root Cause:**
The `send_command('show running-config')` call was timing out on slower virtualised
NX-OS instances before the full config was returned. The script saved the empty
response to a file without checking content length, making it appear the extraction
succeeded when it had silently failed.

**Diagnosis:**
```bash
ls -lh configs_final/
# leaf2.txt and leaf3.txt showed 0 bytes
```

**Fix:**
Added content validation before saving — reject responses under 100 characters:

```python
if not config or len(config.strip()) < 100:
    print(f"❌ Empty or too-short response ({len(config)} chars)")
    return False

# Only save if content is valid
output_file = output_dir / f'{name}.txt'
with open(output_file, 'w') as f:
    f.write(config)
```

Also increased `read_timeout` from 90 to 180 seconds and added explicit prompt
matching to handle slow virtualised NX-OS response times.

Also improved error reporting to show exception type:
```python
# Before - too vague:
except Exception as e:
    print(f"❌ Error: {e}")

# After - shows what kind of failure:
except Exception as e:
    print(f"❌ Error ({type(e).__name__}): {e}")
```

**Result:** All 9 configs extracted successfully with correct file sizes (20-24KB
for fabric switches, 3.1KB for unconfigured edge/backbone/site2 devices).

**Lesson:**
Always validate response content before writing to disk. Silent empty-file failures
are worse than explicit errors because they appear successful in summary output.
The size difference between fabric switches (20KB+) and unconfigured devices (3KB)
is also a useful signal — a config under 5KB on a device that should be configured
warrants investigation.

---

## Phase 2 Issues — IaC Framework / NDFC API

---

### Issue 3 — VRF Deployment API Payload Format Wrong

**Phase:** 2
**Component:** `infrastructure_as_code/api_client.py` — `deploy_vrf()`

**Problem:**
VRF deployment to switches failed with a Java deserialization error after VRFs
were successfully created in NDFC.

**Error:**
```
400 Bad Request
Cannot deserialize value of type `java.lang.String` from Array value
(token `JsonToken.START_ARRAY`) through reference chain:
VRFInfo["vrfNames"]
```

**Root Cause:**
The deployment endpoint expects `vrfName` as a single string, but the code was
sending `vrfNames` as a list:

```python
# Wrong — array with wrong key name:
json={"vrfNames": ["PRODUCTION"]}

# Correct — string with singular key:
json={"vrfName": "PRODUCTION"}
```

**How it was diagnosed:**
The NDFC OpenAPI specification was obtained and the schema definition for the
VRF deployment endpoint was analyzed. The spec showed `vrfName` typed as a string,
not an array — confirming the payload type mismatch.

**Fix:**
Updated `deploy_vrf()` in `api_client.py`:
```python
def deploy_vrf(self, fabric: str, vrf_name: str) -> None:
    """Deploy attached VRF to switches - pushes config"""
    self.post(
        f'.../{fabric}/vrfs/deployments',
        json={"vrfName": vrf_name}   # Singular string, not array
    )
```

**Lesson:**
Always check the vendor OpenAPI spec for exact field names and types. NDFC uses
inconsistent naming conventions — some endpoints use plural (`vrfNames`) and some
use singular (`vrfName`). A 400 with a Java deserialization error almost always
means a type mismatch in the payload — check string vs array vs object.
Reading the OpenAPI spec directly is faster than trial-and-error when the error
message references internal Java class names.

---

### Issue 4 — VRF and Network Attachment API Requires Switch Serial Numbers

**Phase:** 2
**Component:** `infrastructure_as_code/api_client.py` — `attach_vrf()`, network attachments

**Problem:**
Both VRF and Network attachment API calls returned 500 Internal Server Error.
The resources were created in NDFC but could not be pushed to switches via API.

**Error:**
```
500 Internal Server Error
POST .../vrfs/attachments
POST .../networks/attachments
```

**Root Cause:**
The NDFC attachment APIs require a structured payload that includes the serial
number of each switch the resource should be attached to. The code was sending
only the resource name — NDFC had no way to know which switches to target.

**Correct VRF attachment payload format:**
```python
payload = [
    {
        "vrfName": vrf_name,
        "lanAttachList": [
            {
                "fabric": fabric,
                "vrfName": vrf_name,
                "serialNumber": "99433ZAWNB5",   # site1-leaf1
                "vlan": 0,
                "isAttached": True,
                "deployment": False,
                "freeformConfig": "",
                "extensionValues": "",
                "instanceValues": ""
            },
            # ... repeat for each switch
        ]
    }
]
```

**Serial numbers for this sandbox reservation:**
```
site1-leaf1:   99433ZAWNB5
site1-leaf2:   9IN20QRUUYM
site1-leaf3:   9SH6SMKS9CE
site1-spine1:  9B2ZMHTPK1S
site1-spine2:  9CZK8PGDWF0
```

**Note:** Serial numbers change with every new sandbox reservation.
Must be fetched dynamically using:
```python
switches = client.get('.../inventory/allswitches')
serials = [s['serialNumber'] for s in switches if s['switchRole'] == 'leaf']
```

**Workaround used:** GUI attach and deploy via NDFC web interface.

**Fix status:** `attach_vrf()` method rewritten with correct payload structure.
Network attachment fix pending — same pattern applies.

**Lesson:**
NDFC has a mandatory three-step workflow for all resources:
1. Create the resource definition in NDFC
2. Attach to specific switches by serial number
3. Deploy to push config to switches

Skipping or incorrectly implementing step 2 always causes 500 errors at step 3.
Never hardcode serial numbers — always fetch dynamically since they change between
sandbox reservations and in production when hardware is replaced.

---

## Phase 3 Issues — Network Deployment / Switch Configuration

---

### Issue 5 — ARP Suppression Blocked by TCAM Region Not Configured

**Phase:** 3
**Component:** Switch hardware configuration — site1-leaf1, site1-leaf2

**Problem:**
Network attachment to site1-leaf1 and site1-leaf2 failed during deploy. site1-leaf3
(border leaf) succeeded. ARP suppression was enabled in the network definitions
(`suppress_arp: true` in `desired_state_full.yaml`).

**Error (from NDFC GUI):**
```
Delivery failed with message: ERROR: Please configure TCAM region for
Ingress ARP-Ether ACL before configuring ARP suppression.
```

**Initial verification:**
```
show hardware access-list tcam region | inc arp
```
```
Ingress ARP-Ether ACL [arp-ether] size =    0
N9K ARP ACL [n9k-arp-acl] size =    0
```

Size = 0 confirms no TCAM allocated for ARP suppression on these leafs.

**Root Cause:**
ARP suppression on Cisco Nexus 9000 requires dedicated TCAM (Ternary Content
Addressable Memory) space to be carved out for the `arp-ether` region before the
feature can be enabled. TCAM is fixed hardware used for fast packet classification.
By default this region is allocated 0 entries. NX-OS will not enable ARP suppression
until the region has explicit allocation.

**Why leaf3 succeeded:**
Border leafs may have different default TCAM profiles in NDFC depending on
the switch role assigned during fabric onboarding.

---

**Debugging Path — Three Attempts Required:**

**Attempt 1 — Wrong command syntax:**
```
hardware access-list tcam region ing-arp-ether-acl 256
```
```
% Invalid command at '^' marker.
```
The `ing-` prefix used in documentation and error messages is NOT the command
keyword. The actual region name used in configuration is the short name shown
in the TCAM table: `arp-ether`.

**Attempt 2 — Correct name but TCAM full:**
```
hardware access-list tcam region arp-ether 256
```
```
ERROR: Aggregate TCAM region configuration exceeded the available
Ingress TCAM slices. Please re-configure.
```
Total TCAM is fully allocated. Adding 256 entries for `arp-ether` requires
freeing space from an existing region first.

**Attempt 3 — Reduce racl, allocate arp-ether (SUCCESS):**

Reviewed full TCAM allocation table. `racl` (IPv4 Routed ACL) was allocated
1536 entries — the largest region and unused in this fabric. NX-OS requires
regions over 256 to be in multiples of 512, so valid sizes are 512, 1024, 1536.
Reduced `racl` from 1536 to 1024 to free 512 entries, then allocated 256 to
`arp-ether`:

```
hardware access-list tcam region racl 1024
hardware access-list tcam region arp-ether 256
copy running-config startup-config
reload
```

Post-reload verification confirmed both changes took effect:
```
IPV4 RACL [racl]              size = 1024   ← reduced from 1536
Ingress ARP-Ether ACL [arp-ether] size =  256   ← allocated
```

---

**NX-OS TCAM sizing rules learned:**
- Regions with size > 256 must be multiples of 512 (512, 1024, 1536, etc.)
- Regions with size ≤ 256 can be set to 0, 128, or 256
- Total ingress TCAM slices are fixed — adding to one region requires reducing another
- All TCAM changes require `copy running-config startup-config` then `reload`
- Changes show in `show hardware access-list tcam region` immediately but
  do not take effect until after reload

**Production impact of ARP suppression:**
Without ARP suppression, unknown ARP requests are flooded to all VTEPs in the
VNI as BUM (Broadcast Unknown Multicast) traffic. With ARP suppression enabled,
the local VTEP responds to ARP requests on behalf of known hosts using the BGP
EVPN MAC/IP table — eliminating flood traffic entirely. At scale this significantly
reduces unnecessary encapsulation and bandwidth across the fabric.

**Other features requiring TCAM pre-allocation on Nexus 9000:**
- `racl` — IPv4 ingress Routed ACL
- `e-racl` — IPv4 egress Routed ACL
- `ifacl` — IPv4 Port ACL
- `vacl` — IPv4 VLAN ACL
- `arp-ether` — ARP suppression for VxLAN EVPN (this issue)
- `ipsg` — IP Source Guard

**Lesson:**
TCAM carving must be planned before fabric deployment — not discovered during
network attachment. The correct workflow is:
1. Identify all features required (ARP suppression, RACLs, PACLs, etc.)
2. Calculate TCAM requirements for each
3. Configure and reload switches during pre-staging
4. Onboard into NDFC fabric only after TCAM is correctly allocated

In production a TCAM change after go-live requires a maintenance window reload.
Always check TCAM allocations as part of the switch acceptance testing process
before fabric onboarding.

**Interview talking point:**
*"One issue I hit deploying VxLAN EVPN was ARP suppression failing on two leaf
switches with an error saying the TCAM region wasn't configured. I ran
'show hardware access-list tcam region' and confirmed the arp-ether region was
size 0. To fix it I had to reduce the racl region from 1536 to 1024 — NX-OS
requires sizes in multiples of 512 for larger regions — which freed enough space
to allocate 256 entries to arp-ether. After saving and reloading both switches
the network deployment succeeded. The bigger lesson is that TCAM planning has to
happen before fabric onboarding, not after, because any change requires a reload."*

---

## Summary Table

| # | Phase | Component | Issue | Fix |
|---|-------|-----------|-------|-----|
| 1 | 1 | extract_configs_ssh.py | NDFC inventory misses non-fabric devices | Hardcoded fallback device list |
| 2 | 1 | extract_configs_ssh.py | Empty config files from SSH timeout | Content validation + increased timeout |
| 3 | 2 | api_client.py | VRF deploy payload wrong type | `vrfName` string not `vrfNames` array |
| 4 | 2 | api_client.py | Attachment API needs serial numbers | Fetch serials dynamically, fix payload |
| 5 | 3 | Switch hardware | ARP suppression blocked by TCAM=0 | Reduce racl 1536→1024, allocate arp-ether 256, reload |

---

*This log will be updated as additional phases are implemented.*