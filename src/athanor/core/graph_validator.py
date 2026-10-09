"""
ATHANOR Graph Validator.

This module provides the GraphValidator class, which ensures the integrity of
the research workflow DAG by checking for cycles, dependency existence, and
data contract consistency between tasks.

Errors vs. warnings:
  - Errors: cycles, missing dependencies. These make the DAG unusable — Stage 4
    and onwards refuse to run.
  - Warnings: data contract mismatches (an input whose name/format doesn't
    exactly match any output from its declared source node). These usually
    reflect the LLM naming outputs less precisely than inputs and don't break
    downstream consumption — the proposal writer and paper writer work at the
    node description level, not the artifact level. Surfaced in the Plan
    tab's validation panel so the user can fix via Re-plan, but do NOT block
    pipeline progression.

For backward compatibility, .validate() returns the combined errors+warnings
list. Callers that care about the distinction should use .validate_full().
"""
from typing import List, Dict, Set, Tuple
from .state import DAGNode, Artifact


class GraphValidator:
    """Validates the topology and data contracts of the Workflow DAG."""

    @staticmethod
    def validate(nodes: List[DAGNode]) -> List[str]:
        """Return the combined list of errors + warnings (legacy API).

        Empty list means fully valid. Callers that need to distinguish
        blocking errors from soft warnings should use validate_full().
        """
        errors, warnings = GraphValidator.validate_full(nodes)
        return errors + warnings

    @staticmethod
    def validate_errors_only(nodes: List[DAGNode]) -> List[str]:
        """Return only the blocking errors (cycles + missing deps).

        Used by the Stage 4/5/6 entry gate in engine.py — the engine
        refuses to proceed on blocking errors but tolerates soft
        warnings like data contract mismatches.
        """
        errors, _ = GraphValidator.validate_full(nodes)
        return errors

    @staticmethod
    def validate_full(nodes: List[DAGNode]) -> Tuple[List[str], List[str]]:
        """Return (errors, warnings).

        Errors block downstream stages. Warnings are surfaced in the
        Plan tab but don't block. Kept separate so the engine's entry
        gate can gate on errors only.
        """
        errors: List[str] = []
        warnings: List[str] = []
        node_map = {n.id: n for n in nodes}
        
        # 1. Cycle Detection (DFS)
        visited = set()
        rec_stack = set()
        
        def has_cycle(node_id):
            visited.add(node_id)
            rec_stack.add(node_id)
            
            node = node_map.get(node_id)
            if node:
                for neighbor_id in node.dependencies:
                    if neighbor_id not in visited:
                        if has_cycle(neighbor_id):
                            return True
                    elif neighbor_id in rec_stack:
                        return True
            
            rec_stack.remove(node_id)
            return False

        for node in nodes:
            if node.id not in visited:
                if has_cycle(node.id):
                    errors.append(f"Cycle detected involving Node {node.name}")
                    break
        
        # 2. Dependency Existence
        for node in nodes:
            for dep_id in node.dependencies:
                if dep_id not in node_map:
                    errors.append(f"Node '{node.name}' depends on non-existent Node ID {dep_id}")

        # 3. Type Safety / Data Contract
        # Check that every Input exists as an Output from its source
        for node in nodes:
            for inp in node.inputs:
                if not inp.source_node_id:
                    # Generic input (e.g. from user context)?
                    continue
                
                source = node_map.get(inp.source_node_id)
                if not source:
                    errors.append(f"Node '{node.name}' input '{inp.name}' claims source {inp.source_node_id} which does not exist.")
                    continue
                
                # Check if source actually outputs this
                found_match = False
                for out in source.outputs:
                    # We match mainly by name or ID. Since ID might be new, matching by name + format is safer for now
                    if out.name == inp.name and out.format == inp.format:
                        found_match = True
                        break
                
                if not found_match:
                    # Data contract mismatches are warnings, not errors.
                    # The downstream writers operate at the node description
                    # level, not the artifact level, so an imprecise output
                    # name doesn't break the pipeline. The Plan tab still
                    # surfaces these so the user can tighten via Re-plan.
                    warnings.append(f"Data Contract Violation: Node '{node.name}' expects input '{inp.name}' ({inp.format}) from Node '{source.name}', but '{source.name}' does not output it.")

        return errors, warnings
