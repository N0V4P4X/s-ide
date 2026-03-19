"""
ai/models.py
============
Registry of models specialized for different agent roles.
"""

from dataclasses import dataclass

@dataclass
class ModelSpec:
    name: str
    tags: list[str]
    description: str

# Default models
ROLE_MODELS = {
    "manager":     "llama3.2",
    "architect":   "llama3.2",
    "implementer": "codellama",
    "reviewer":    "llama3.2",
    "tester":      "codellama",
    "optimizer":   "codellama",
}

def get_model_for_role(role: str) -> str:
    return ROLE_MODELS.get(role.lower(), "llama3.2")
