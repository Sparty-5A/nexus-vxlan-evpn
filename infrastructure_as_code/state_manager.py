"""
State management for tracking infrastructure state
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from resources import Resource


class StateManager:
    """Manages current and desired state, computes diffs"""

    def __init__(self, state_file: str = "state.json"):
        self.state_file = Path(state_file)
        self.current_state: Dict[str, Dict[str, Any]] = {}
        self.desired_state: Dict[str, Dict[str, Any]] = {}
        self.load_current_state()

    def load_current_state(self) -> None:
        """Load current state from disk"""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                self.current_state = json.load(f)
        else:
            self.current_state = {}

    def save_current_state(self) -> None:
        """Save current state to disk"""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.state_file, "w") as f:
            json.dump(self.current_state, f, indent=2)

    def set_desired_state(self, resources: List[Resource]) -> None:
        """Set desired state from resource list"""
        self.desired_state = {resource.resource_id(): resource.to_dict() for resource in resources}

    def compute_diff(self) -> Dict[str, Any]:
        """Compute differences between current and desired state"""
        current_ids = set(self.current_state.keys())
        desired_ids = set(self.desired_state.keys())

        to_create = desired_ids - current_ids
        to_delete = current_ids - desired_ids
        to_update = set()

        # Check for updates (simple equality check)
        for resource_id in current_ids & desired_ids:
            if self.current_state[resource_id] != self.desired_state[resource_id]:
                to_update.add(resource_id)

        return {
            "create": sorted(to_create),
            "update": sorted(to_update),
            "delete": sorted(to_delete),
            "unchanged": sorted(current_ids & desired_ids - to_update),
        }

    def update_resource(self, resource: Resource) -> None:
        """Update state for a single resource"""
        self.current_state[resource.resource_id()] = resource.to_dict()

    def delete_resource(self, resource_id: str) -> None:
        """Remove resource from state"""
        self.current_state.pop(resource_id, None)

    def get_resource(self, resource_id: str) -> Optional[Dict[str, Any]]:
        """Get resource from desired state"""
        return self.desired_state.get(resource_id)

    def clear_state(self) -> None:
        """Clear all state (for destroy)"""
        self.current_state = {}
        self.save_current_state()
