from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def step(agent: str, input_data: Any, output_data: Any) -> dict[str, Any]:
    return {
        "agent": agent,
        "input": input_data,
        "output": output_data,
        "at": datetime.now(timezone.utc).isoformat(),
    }

