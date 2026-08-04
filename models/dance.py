from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DanceCount:
    name: str
    count: int = 0
    category: str = "dance"
    last_executed: Optional[str] = None


@dataclass
class SequenceStep:
    type: str = "dance"
    name: str = ""
    delay_ms: int = 1000
    vx: float = 0.0
    vy: float = 0.0
    omega: float = 0.0

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "name": self.name,
            "delay_ms": self.delay_ms,
            "vx": self.vx,
            "vy": self.vy,
            "omega": self.omega,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SequenceStep":
        return cls(
            type=d.get("type", "dance"),
            name=d.get("name", ""),
            delay_ms=d.get("delay_ms", 1000),
            vx=d.get("vx", 0.0),
            vy=d.get("vy", 0.0),
            omega=d.get("omega", 0.0),
        )


@dataclass
class DanceSequence:
    name: str = ""
    steps: list[SequenceStep] = field(default_factory=list)
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
        }
