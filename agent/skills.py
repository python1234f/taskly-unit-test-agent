from pathlib import Path

from .contracts import require
from .safety import digest

CATALOG = {
    "analyze-pr": "Compare behavioral changes with existing tests and decide whether tests are necessary.",
    "frontend-testing": "Write React 19, Vitest 4 and Testing Library tests using Taskly providers and mocks.",
    "backend-testing": "Write pytest tests for FastAPI, Pydantic and synchronous SQLAlchemy sessions.",
    "verify-tests": "Assess actual execution, distinguish technical failures from oracle conflicts and constrain repairs.",
}


class Skills:
    def __init__(self, root: Path, records: list[dict] | None = None):
        self.root = root
        self.records = list(records or [])

    def load(self, name: str, stage: str, reason: str) -> str:
        require(name in CATALOG, "Unknown skill.")
        path = self.root / name / "SKILL.md"
        require(path.is_file() and not path.is_symlink(), "Trusted skill file is missing.", "MISSING_CONFIGURATION")
        content = path.read_text(encoding="utf-8")
        require(len(content.encode("utf-8")) <= 12_000, "Skill exceeds the instruction limit.")
        self.records.append({"name": name, "stage": stage, "reason": reason[:1000], "sha256": digest(content)})
        return content
