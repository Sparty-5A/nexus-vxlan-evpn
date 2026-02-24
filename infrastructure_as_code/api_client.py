"""
NDFC REST API client with full error handling and retry logic
"""
import httpx
import time
from typing import Dict, Any, Optional, List
import urllib3

# Disable SSL warnings for lab
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class NDFCClient:
    """Nexus Dashboard Fabric Controller API client"""

    def __init__(self, url: str, username: str, password: str):
        self.url = url
        self.username = username
        self.password = password
        self.token: Optional[str] = None
        self.client = httpx.Client(verify=False, timeout=60)

    def login(self) -> None:
        """Authenticate and get JWT token"""
        response = self.client.post(
            f'{self.url}/login',
            json={
                'userName': self.username,
                'userPasswd': self.password,
                'domain': 'local'
            }
        )
        response.raise_for_status()
        self.token = response.json().get('token') or response.json().get('jwttoken')
        if not self.token:
            raise Exception("No token in login response")

    def _headers(self) -> Dict[str, str]:
        """Get authorization headers"""
        if not self.token:
            self.login()
        return {'Authorization': f'Bearer {self.token}'}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make authenticated request with retry"""
        if 'headers' not in kwargs:
            kwargs['headers'] = self._headers()

        for attempt in range(3):
            try:
                response = self.client.request(method, f'{self.url}{path}', **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                # Log response body for debugging
                if e.response.status_code == 400:
                    print(f"\n[DEBUG] 400 Bad Request Details:")
                    print(f"  URL: {e.response.url}")
                    print(f"  Request Body: {kwargs.get('json', 'N/A')}")
                    print(f"  Response Body: {e.response.text[:1000]}")
                    print()
                if e.response.status_code == 401:  # Token expired
                    self.login()
                    kwargs['headers'] = self._headers()
                    continue
                raise
            except httpx.RequestError:
                if attempt < 2:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                raise

    def get(self, path: str, **kwargs) -> Any:
        """GET request"""
        response = self._request('GET', path, **kwargs)
        if response.text:
            return response.json()
        return None

    def post(self, path: str, **kwargs) -> Any:
        """POST request"""
        response = self._request('POST', path, **kwargs)
        if response.text:
            return response.json()
        return None

    def put(self, path: str, **kwargs) -> Any:
        """PUT request"""
        response = self._request('PUT', path, **kwargs)
        if response.text:
            return response.json()
        return None

    def delete(self, path: str, **kwargs) -> None:
        """DELETE request"""
        self._request('DELETE', path, **kwargs)

    # High-level fabric queries

    def get_fabrics(self) -> List[Dict[str, Any]]:
        """Get all fabrics"""
        result = self.get('/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/control/fabrics')
        return result if isinstance(result, list) else []

    def get_switches(self) -> List[Dict[str, Any]]:
        """Get all switches"""
        result = self.get('/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/allswitches')
        return result if isinstance(result, list) else []

    def get_switch_config(self, ip: str) -> str:
        """Get switch running config (if API supports it)"""
        # Note: This endpoint may not exist in all NDFC versions
        # This is for demonstration
        try:
            result = self.get(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/inventory/config/{ip}')
            return result if isinstance(result, str) else ""
        except:
            return ""

    # VRF management

    def get_vrfs(self, fabric: str) -> List[Dict[str, Any]]:
        """Get all VRFs in fabric"""
        try:
            result = self.get(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/vrfs')
            return result if isinstance(result, list) else []
        except:
            return []

    def create_vrf(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create VRF"""
        fabric = payload['fabric']
        return self.post(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/vrfs', json=payload)

    def delete_vrf(self, fabric: str, vrf_name: str) -> None:
        """Delete VRF"""
        self.delete(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/vrfs/{vrf_name}')

    def deploy_vrf(self, fabric: str, vrf_name: str) -> None:
        """Deploy VRF to all leafs"""
        # This triggers NDFC to push config to switches
        self.post(
            f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/vrfs/attachments',
            json={"vrfName": vrf_name}
        )

    # Network management

    def get_networks(self, fabric: str) -> List[Dict[str, Any]]:
        """Get all networks in fabric"""
        try:
            result = self.get(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/networks')
            return result if isinstance(result, list) else []
        except:
            return []

    def create_network(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create network"""
        fabric = payload['fabric']
        return self.post(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/networks',
                         json=payload)

    def delete_network(self, fabric: str, network_name: str) -> None:
        """Delete network"""
        self.delete(f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/networks/{network_name}')

    def deploy_network(self, fabric: str, network_name: str) -> None:
        """Deploy network to leafs"""
        # This triggers NDFC to push config to switches
        self.post(
            f'/appcenter/cisco/ndfc/api/v1/lan-fabric/rest/top-down/fabrics/{fabric}/networks/attachments',
            json={"networkName": network_name}
        )