"""Inert local adapters for TAMAS Finance tool declarations.

These adapters never import or call upstream tool code and never perform
financial, network, identity, logging, or data-changing side effects.
"""

from dataclasses import dataclass
import json
from typing import Any

from dataset.tool_catalog import TAMASToolDefinition


@dataclass(frozen=True)
class SimulatedToolResponse:
    tool_name: str
    status: str
    message: str
    task_completion: str = "not_evaluated"


class SafeFinanceToolRegistry:
    """Registry of all catalogued definitions backed only by no-op simulators."""

    def __init__(self, definitions: tuple[TAMASToolDefinition, ...]):
        if not definitions:
            raise ValueError("At least one Finance tool definition is required.")
        self._definitions = tuple(definitions)
        self._keys = {
            self._key(definition): definition
            for definition in self._definitions
        }
        if len(self._keys) != len(self._definitions):
            raise ValueError("Tool definitions must have unique source locations.")

    @staticmethod
    def _key(definition: TAMASToolDefinition) -> tuple[str, str, int]:
        return definition.source_file, definition.name, definition.source_line

    @property
    def definitions(self) -> tuple[TAMASToolDefinition, ...]:
        return self._definitions

    def for_role(self, role: str) -> tuple[TAMASToolDefinition, ...]:
        return tuple(
            definition for definition in self._definitions
            if definition.agent_role == role
        )

    def invoke(
        self,
        definition: TAMASToolDefinition,
        arguments: dict[str, Any],
    ) -> SimulatedToolResponse:
        """Run an inert local adapter and return an explicitly simulated result."""
        registered = self._keys.get(self._key(definition))
        if registered != definition:
            raise ValueError("Tool definition is not registered in this local environment.")
        if not isinstance(arguments, dict):
            raise ValueError("Simulation arguments must be a JSON object.")
        try:
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Simulation arguments must be finite JSON data.") from exc

        return SimulatedToolResponse(
            tool_name=definition.name,
            status="simulated_response",
            message=(
                f"Local simulation stub for {definition.name}; no upstream code, "
                "external service, account, financial transaction, or persistent "
                "data was accessed or changed."
            ),
        )
