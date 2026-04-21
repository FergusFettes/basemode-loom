from __future__ import annotations

from typing import Any

from ..session import SessionState
from ..store import Node


def node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "root_id": node.root_id,
        "text": node.text,
        "model": node.model,
        "strategy": node.strategy,
        "max_tokens": node.max_tokens,
        "temperature": node.temperature,
        "branch_index": node.branch_index,
        "created_at": node.created_at,
        "metadata": node.metadata,
    }


def state_to_dict(state: SessionState) -> dict[str, Any]:
    return {
        "current_node_id": state.current_node_id,
        "current_node": node_to_dict(state.current_node),
        "full_text": state.full_text,
        "children": [node_to_dict(n) for n in state.children],
        "selected_child_idx": state.selected_child_idx,
        "descendant_counts": state.descendant_counts,
        "continuation_text": state.continuation_text,
        "model": state.model,
        "max_tokens": state.max_tokens,
        "temperature": state.temperature,
        "n_branches": state.n_branches,
        "model_plan": [
            {
                "model": p.model,
                "n_branches": p.n_branches,
                "max_tokens": p.max_tokens,
                "temperature": p.temperature,
                "enabled": p.enabled,
            }
            for p in state.model_plan
        ],
        "context": state.context,
        "root_id": state.root_id,
        "view_mode": state.view_mode,
        "hoisted_node_id": state.hoisted_node_id,
        "tree_nodes": [node_to_dict(n) for n in state.tree_nodes]
        if state.tree_nodes is not None
        else None,
        "show_model_names": state.show_model_names,
    }
