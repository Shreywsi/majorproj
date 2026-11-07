"""Static, non-executing metadata extraction for official Finance tool modules."""

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


FINANCE_TOOL_MODULES = {
    "tools_identity_verification_agent.py": "Identity Verification Agent",
    "tools_market_analysis_agent.py": "Market Analysis Agent",
    "tools_risk_management_agent.py": "Risk Management Agent",
    "tools_trade_execution_agent.py": "Trade Execution Agent",
}


@dataclass(frozen=True)
class TAMASToolDefinition:
    agent_role: str
    name: str
    parameters: tuple[str, ...]
    return_annotation: str | None
    description: str | None
    source_file: str
    source_line: int
    is_async: bool
    duplicate_definition: bool
    classification: str = "unclassified"


def load_finance_tool_definitions(
    directory: str | Path,
) -> tuple[tuple[TAMASToolDefinition, ...], tuple[str, ...]]:
    """Extract function declarations using Python AST; never import tool code."""
    root = Path(directory)
    definitions = []
    missing = []

    for filename, role in FINANCE_TOOL_MODULES.items():
        path = root / filename
        if not path.is_file():
            missing.append(filename)
            continue
        try:
            source = path.read_text(encoding="utf-8")
            module = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ValueError(f"Unable to inspect TAMAS tool module {filename}: {exc}") from exc

        functions = [
            node for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        name_counts = Counter(node.name for node in functions)
        for node in functions:
            args = node.args
            parameters = tuple(
                arg.arg for arg in (
                    *args.posonlyargs,
                    *args.args,
                    *((args.vararg,) if args.vararg else ()),
                    *args.kwonlyargs,
                    *((args.kwarg,) if args.kwarg else ()),
                )
            )
            definitions.append(TAMASToolDefinition(
                agent_role=role,
                name=node.name,
                parameters=parameters,
                return_annotation=(
                    ast.unparse(node.returns) if node.returns is not None else None
                ),
                description=ast.get_docstring(node, clean=False),
                source_file=filename,
                source_line=node.lineno,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                duplicate_definition=name_counts[node.name] > 1,
            ))

    return tuple(definitions), tuple(missing)
