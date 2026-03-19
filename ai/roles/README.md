# ai/roles/

Role definitions for AI team agents. Each role specialises a base agent with specific focus, tool permissions, and expected outputs.

## definitions.py

```python
from ai.roles import get_role_prompt, ROLES, RoleDefinition

# Get the full system prompt for a role (base standards + role overlay)
prompt = get_role_prompt("reviewer")

# Inspect a role
role = ROLES["tester"]
role.permitted_tools   # frozenset of allowed tool names
role.output_paths      # expected session workspace output paths
role.prompt            # role-specific system prompt overlay
```

## Roles

| Role | Focus | Cannot do |
|---|---|---|
| `architect` | Structure, interfaces, development plan | Write source, run code |
| `implementer` | Writing code to spec | Nothing — full access |
| `reviewer` | Code quality, standards, correctness | Write source, run code |
| `tester` | Correctness, edge cases, regressions | Write source |
| `optimizer` | Performance, size reduction | Nothing — full access |
| `documentarian` | Docstrings, READMEs, CHANGELOG | Write source, run code |

All roles can write to the session workspace via `write_session_file`.

## Adding a role

```python
from ai.roles.definitions import ROLES, RoleDefinition

ROLES["my_role"] = RoleDefinition(
    name="my_role",
    title="My Role",
    description="Does a specific thing",
    focus="what it optimises for",
    permitted_tools=frozenset(["read_file", "write_session_file", ...]),
    output_paths=["my_role/output.md"],
    prompt=_BASE + """
## Your role: MY ROLE
...
""",
)
```
