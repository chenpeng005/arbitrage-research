"""Validator dispatch for AI Runtime task specs."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any


def run_validator(
    validator_ref: str,
    run_dir: Path,
    structured_output_path: Path,
) -> dict[str, Any]:
    if "." not in validator_ref:
        raise ValueError(f"invalid validator reference: {validator_ref!r}")
    module_name, function_name = validator_ref.rsplit(".", 1)
    module = importlib.import_module(module_name)
    function = getattr(module, function_name, None)
    if function is None or not callable(function):
        raise ValueError(f"validator is not callable: {validator_ref}")
    result = function(run_dir, structured_output_path)
    if not isinstance(result, dict):
        raise RuntimeError(f"validator returned non-object: {validator_ref}")
    return result
