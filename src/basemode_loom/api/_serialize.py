from __future__ import annotations

from typing import Any

from ..session import SessionState
from ..store import Node, Tree


def node_to_dict(node: Node) -> dict[str, Any]:
    return {
        "id": node.id,
        "parent_id": node.parent_id,
        "tree_id": node.tree_id,
        "kind": node.kind,
        "text": node.text,
        "context_id": node.context_id,
        "model": node.model,
        "strategy": node.strategy,
        "max_tokens": node.max_tokens,
        "temperature": node.temperature,
        "checked_out": node.checked_out,
        "created_at": node.created_at,
        "metadata": node.metadata,
    }


def tree_to_dict(tree: Tree) -> dict[str, Any]:
    return {
        "id": tree.id,
        "current_node_id": tree.current_node_id,
        "name": tree.name,
        "show_model_names": tree.show_model_names,
        "rewind_split_tokens": tree.rewind_split_tokens,
        "model_plan": tree.model_plan,
        "created_at": tree.created_at,
        "updated_at": tree.updated_at,
        "metadata": tree.metadata,
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
        "tree_prompt_tokens": state.tree_prompt_tokens,
        "tree_completion_tokens": state.tree_completion_tokens,
        "tree_total_tokens": state.tree_total_tokens,
        "tree_cost_usd": state.tree_cost_usd,
        "tree_pricing_complete": state.tree_pricing_complete,
    }
