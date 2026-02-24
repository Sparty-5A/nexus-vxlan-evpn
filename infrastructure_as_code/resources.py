"""
Resource type definitions for Nexus Dashboard infrastructure
"""
from dataclasses import dataclass, asdict, field
from typing import Dict, Any, List


@dataclass
class Resource:
    """Base resource class"""
    name: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)
    
    def resource_id(self) -> str:
        """Unique identifier for this resource"""
        return f"{self.__class__.__name__}:{self.name}"
    
    def create_payload(self) -> Dict[str, Any]:
        """Convert to NDFC API payload - must be implemented by subclass"""
        raise NotImplementedError(f"{self.__class__.__name__} must implement create_payload()")


@dataclass
class FabricInfo(Resource):
    """Fabric metadata (read-only for now)"""
    name: str
    asn: int = 65001
    fabric_type: str = "Switch_Fabric"
    
    def create_payload(self) -> Dict[str, Any]:
        # Fabric creation is complex and typically done via GUI
        # For this framework, we assume fabric already exists
        raise NotImplementedError("Fabric creation not supported in this version")


@dataclass
class VRF(Resource):
    """VRF resource"""
    name: str
    fabric: str
    vni: int
    vlan_id: int = 999
    
    def create_payload(self) -> Dict[str, Any]:
        # vrfTemplateConfig must be a JSON string, not object!
        vrf_template_config = {
            "vrfVlanName": "",
            "vrfIntfDescription": "",
            "vrfDescription": "",
            "mtu": "9216",
            "tag": "12345",
            "vrfRouteMap": "FABRIC-RMAP-REDIST-SUBNET",
            "maxBgpPaths": "1",
            "maxIbgpPaths": "2",
            "ipv6LinkLocalFlag": "true",
            "trmEnabled": "false",
            "isRPAbsent": False,
            "isRPExternal": False,
            "rpAddress": "",
            "loopbackNumber": "",
            "L3VniMcastGroup": "",
            "multicastGroup": "",
            "trmBGWMSiteEnabled": False,
            "advertiseHostRouteFlag": "false",
            "advertiseDefaultRouteFlag": "true",
            "configureStaticDefaultRouteFlag": "true",
            "bgpPassword": "",
            "bgpPasswordKeyType": "3",
            "ENABLE_NETFLOW": "false",
            "NETFLOW_MONITOR": "",
            "disableRtAuto": "false",
            "routeTargetImport": "",
            "routeTargetExport": "",
            "routeTargetImportEvpn": "",
            "routeTargetExportEvpn": "",
            "routeTargetImportMvpn": "",
            "routeTargetExportMvpn": "",
            "vrfName": self.name,
            "vrfVlanId": self.vlan_id,
            "vrfSegmentId": self.vni,
            "nveId": "1",
            "asn": ""
        }

        import json
        return {
            "fabric": self.fabric,
            "vrfName": self.name,
            "vrfId": self.vni,
            "vrfTemplate": "Default_VRF_Universal",
            "vrfExtensionTemplate": "Default_VRF_Extension_Universal",
            "vrfTemplateConfig": json.dumps(vrf_template_config)  # Must be string!
        }


@dataclass
class Network(Resource):
    """Network resource (VLAN + VNI + SVI)"""
    name: str
    fabric: str
    vrf: str
    vlan_id: int
    vni: int
    gateway: str
    mtu: int = 9216
    suppress_arp: bool = True

    def create_payload(self) -> Dict[str, Any]:
        # networkTemplateConfig must be a JSON string!
        # NOTE: This fabric uses MULTICAST, not Ingress Replication
        network_template_config = {
            "gatewayIpAddress": self.gateway,
            "gatewayIpV6Address": "",
            "vlanName": "",
            "intfDescription": "",
            "mtu": str(self.mtu),
            "secondaryGW1": "",
            "secondaryGW2": "",
            "secondaryGW3": "",
            "secondaryGW4": "",
            "type": "",
            "dhcpServerAddr1": "",
            "vrfDhcp": "",
            "dhcpServerAddr2": "",
            "vrfDhcp2": "",
            "dhcpServerAddr3": "",
            "vrfDhcp3": "",
            "suppressArp": "true" if self.suppress_arp else "false",
            "enableIR": "false",  # Changed from true - fabric uses multicast!
            "mcastGroup": "239.1.1.0",  # Required for multicast
            "dhcpServers": "",
            "loopbackId": "",
            "tag": "12345",
            "trmEnabled": "false",
            "rtBothAuto": "false",
            "ENABLE_NETFLOW": "false",
            "SVI_NETFLOW_MONITOR": "",
            "VLAN_NETFLOW_MONITOR": "",
            "enableL3OnBorder": "false",
            "vlanId": self.vlan_id,
            "segmentId": self.vni,
            "vrfName": self.vrf,
            "networkName": self.name,
            "nveId": "1",
            "isLayer2Only": False
        }

        import json
        return {
            "fabric": self.fabric,
            "networkName": self.name,
            "networkId": self.vni,
            "vrf": self.vrf,
            "networkTemplate": "Default_Network_Universal",
            "networkTemplateConfig": json.dumps(network_template_config)  # Must be string!
        }

    def dependencies(self) -> List[str]:
        """This network depends on its VRF existing first"""
        return [f"VRF:{self.vrf}"]