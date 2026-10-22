import ast
import re
from pathlib import Path, PurePosixPath

from .contracts import MAX_FILE_BYTES, AgentError, require
from .safety import checked_local_path, safe_path

MAX_CONTEXT_BYTES = 48_000
MAX_CONTEXT_FILES = 24


class LocalRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        paths = []
        for prefix in ("backend/app", "backend/tests", "frontend/src"):
            paths.extend(path for path in (self.root / prefix).rglob("*") if path.is_symlink() or path.is_file())
        paths.extend(self.root / name for name in ("backend/pyproject.toml", "frontend/vite.config.ts", "frontend/tsconfig.json", "README.md")
                     if (self.root / name).is_file())
        self.entries = {str(path.relative_to(self.root)): {"mode": "120000" if path.is_symlink() else "100644", "type": "blob"} for path in paths
                        if "__pycache__" not in path.parts}

    def read(self, path: str) -> str:
        target = checked_local_path(self.root, path)
        require(target.is_file() and target.stat().st_size <= MAX_FILE_BYTES, "Invalid local context file.", "CONTEXT_LIMIT")
        return target.read_text(encoding="utf-8")


def related_imports(path: str, source: str) -> list[str]:
    parent = PurePosixPath(path).parent
    candidates = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError):
            return []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = parent
                for unused in range(node.level - 1):
                    base = base.parent
                module = base / (node.module or "").replace(".", "/")
            elif node.module and node.module.startswith(("app.", "tests.")):
                module = PurePosixPath("backend") / node.module.replace(".", "/")
            else:
                continue
            candidates.extend([str(module) + ".py", str(module / "__init__.py")])
    else:
        for imported in re.findall(r"(?:from\s*|import\s*)['\"]([^'\"]+)['\"]", source):
            if not imported.startswith("."):
                continue
            parts = list(parent.parts)
            for part in imported.split("/"):
                if part == "..":
                    if parts:
                        parts.pop()
                elif part != ".":
                    parts.append(part)
            module = PurePosixPath(*parts)
            candidates.extend(str(module) + suffix for suffix in (".ts", ".tsx", "/index.ts", "/index.tsx"))
    return candidates


def symbol_names(path: str, source: str) -> list[str]:
    if path.endswith(".py"):
        try:
            return [node.name for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))][:30]
        except (SyntaxError, RecursionError):
            return []
    return re.findall(r"(?:function|class|(?:const|let)\s+)\s*([A-Za-z_$][\w$]*)", source)[:30]


class Context:
    def __init__(self, repository, changes: list[dict]):
        self.repository = repository
        self.changes = changes
        self.files = {}
        self.total_bytes = 0
        self.extra_requests = 0

    def add(self, path: str, required: bool = False) -> None:
        safe_path(path)
        if path in self.files:
            return
        if path not in self.repository.entries:
            require(not required, "Requested context file does not exist.", "CONTEXT_UNAVAILABLE")
            return
        if len(self.files) >= MAX_CONTEXT_FILES or self.total_bytes >= MAX_CONTEXT_BYTES:
            require(not required, "Required context exceeds the budget.", "CONTEXT_LIMIT")
            return
        declared_size = self.repository.entries[path].get("size")
        if type(declared_size) is int and self.total_bytes + declared_size > MAX_CONTEXT_BYTES:
            require(not required, "Required context exceeds the budget.", "CONTEXT_LIMIT")
            return
        content = self.repository.read(path)
        size = len(content.encode("utf-8"))
        require(size <= MAX_FILE_BYTES, "Context file exceeds the size limit.", "CONTEXT_LIMIT")
        if self.total_bytes + size > MAX_CONTEXT_BYTES or len(self.files) >= MAX_CONTEXT_FILES:
            require(not required, "Required context exceeds the budget.", "CONTEXT_LIMIT")
            return
        self.files[path] = content
        self.total_bytes += size

    def gather(self) -> dict:
        source_paths = [item["filename"] for item in self.changes if item["filename"].startswith(("backend/app/", "frontend/src/"))]
        require(len(source_paths) <= 12, "Too many changed source files for an automatic run.", "CONTEXT_LIMIT")
        for path in source_paths:
            if path in self.repository.entries:
                self.add(path, required=True)
        seeds = []
        if any(path.startswith("backend/") for path in source_paths):
            seeds.extend(["backend/tests/conftest.py", "backend/tests/mocks.py", "backend/pyproject.toml"])
        if any(path.startswith("frontend/") for path in source_paths):
            seeds.extend(["frontend/src/test/setup.ts", "frontend/src/test/todoFixture.ts", "frontend/src/design-system/mocks.tsx",
                          "frontend/src/app/AppProviders.tsx", "frontend/vite.config.ts"])
        for path in source_paths:
            if path.startswith("backend/app/"):
                relative = PurePosixPath(path.removeprefix("backend/app/"))
                seeds.append(str(PurePosixPath("backend/tests") / relative.parent / ("test_" + relative.name)))
            else:
                seeds.append(re.sub(r"\.(tsx?)$", r".test.\1", path))
        for path in seeds:
            self.add(path)
        for unused in range(2):
            for path, source in list(self.files.items()):
                for imported in related_imports(path, source):
                    try:
                        self.add(imported)
                    except AgentError as error:
                        if error.code != "UNSAFE_PATH":
                            raise
        return self.payload()

    def request_files(self, paths: list[str]) -> None:
        self.extra_requests += len(paths)
        require(self.extra_requests <= 8, "Additional context request budget exhausted.", "CONTEXT_LIMIT")
        for path in paths:
            self.add(path, required=True)

    def payload(self) -> dict:
        return {
            "changed_files": self.changes,
            "files": self.files,
            "symbols": {path: symbol_names(path, content) for path, content in self.files.items()},
            "available_test_files": [path for path in self.repository.entries if path.startswith(("backend/tests/", "frontend/src/"))
                                     and (path.endswith((".test.ts", ".test.tsx")) or PurePosixPath(path).name.startswith("test_"))][:100],
        }

    def originals(self, changes: list[dict]) -> dict[str, str]:
        return {item["path"]: self.repository.read(item["path"]) for item in changes if item["path"] in self.repository.entries}
