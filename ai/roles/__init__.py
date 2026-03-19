"""
ai/roles/
=========
Role-specific system prompt overlays for AI Teams.

Each role imports the base standards from ai/standards.py and adds
role-specific focus, constraints, and success criteria.

Usage
-----
    from ai.roles import get_role_prompt
    prompt = get_role_prompt("reviewer")
"""
from __future__ import annotations
from .definitions import get_role_prompt, ROLES, RoleDefinition

__all__ = ["get_role_prompt", "ROLES", "RoleDefinition"]
