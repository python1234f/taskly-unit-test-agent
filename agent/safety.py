import ast
import hashlib
import re
from collections import Counter
from pathlib import Path, PurePosixPath

from .contracts import MAX_CHANGES, MAX_FILE_BYTES, AgentError, require


def safe_path(path: str, test_only: bool = False) -> str:
    require(type(path) is str and 0 < len(path) <= 240, "Invalid file path.", "UNSAFE_PATH")
    require(re.fullmatch(r"[A-Za-z0-9_./-]+", path) is not None, "Unsupported characters in file path.", "UNSAFE_PATH")
    parts = path.split("/")
    require(not path.startswith("/") and all(part not in {"", ".", ".."} for part in parts), "Non-canonical file path.", "UNSAFE_PATH")
    require(not any(part.startswith(".") for part in parts), "Hidden paths are not allowed.", "UNSAFE_PATH")
    allowed = path.startswith(("backend/app/", "backend/tests/", "frontend/src/")) or path in {
        "backend/pyproject.toml", "frontend/vite.config.ts", "frontend/tsconfig.json", "README.md",
    }
    require(allowed, "File is outside the context allowlist.", "UNSAFE_PATH")
    require(path.endswith((".py", ".ts", ".tsx", ".css", ".svg", ".json", ".md", ".toml")), "Only supported text context files may be read.", "UNSAFE_PATH")
    if test_only:
        permitted = (path.startswith("backend/tests/") and PurePosixPath(path).name.startswith("test_") and path.endswith(".py")) or (
            path.startswith("frontend/src/") and path.endswith((".test.ts", ".test.tsx")))
        require(permitted, "Only unit test files may be changed.", "UNSAFE_PATH")
    return path


def checked_local_path(root: Path, path: str, test_only: bool = False) -> Path:
    safe_path(path, test_only)
    root = root.resolve()
    target = root / path
    current = root
    for part in PurePosixPath(path).parts:
        current = current / part
        require(not current.is_symlink(), "Symlink paths are not allowed.", "UNSAFE_PATH")
    require(target.resolve().is_relative_to(root), "Path escapes the workspace.", "UNSAFE_PATH")
    return target


def digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def artifact_path(output: Path, name: str) -> Path:
    require(name in {"state.json", "report.md", "generated-tests.patch"}, "Invalid artifact filename.", "UNSAFE_PATH")
    output = output.absolute()
    require(not any(directory.is_symlink() for directory in (output, *output.parents)), "Artifact directories cannot be symlinks.", "UNSAFE_PATH")
    output.mkdir(parents=True, exist_ok=True)
    target = output / name
    require(not target.is_symlink() and (not target.exists() or target.is_file()), "Invalid artifact output path.", "UNSAFE_PATH")
    return target


def assertion_inventory(content: str, backend: bool) -> Counter:
    if backend:
        try:
            tree = ast.parse(content)
        except (SyntaxError, RecursionError) as error:
            raise AgentError("INVALID_TESTS", "Generated Python tests have invalid syntax.") from error
        protected = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                protected.append(ast.dump(node, include_attributes=False))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"raises", "fail"} or node.func.attr.startswith("assert_"):
                    protected.append(ast.dump(node, include_attributes=False))
        return Counter(protected)
    expressions = []
    for match in re.finditer(r"\bexpect\s*\(", content):
        start = match.start()
        position = match.end() - 1
        depth = 0
        quote = None
        escaped = False
        while position < len(content):
            character = content[position]
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character in "\"'`":
                quote = character
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
            if depth == 0 and position > match.end() and character in {";", "\n", "}"}:
                break
            position += 1
        expressions.append(content[start:position].strip())
    return Counter(expressions)


def test_names(content: str, backend: bool) -> list[str]:
    if backend:
        tree = ast.parse(content)
        return [node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")]
    return re.findall(r"\b(?:it|test)\s*\(\s*['\"]([^'\"\n]+)['\"]", content)


def oracle_inventory(content: str, backend: bool) -> Counter:
    protected = assertion_inventory(content, backend)
    if backend:
        for node in ast.walk(ast.parse(content)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "parametrize":
                protected[ast.dump(node, include_attributes=False)] += 1
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    continue
                protected[ast.dump(node, include_attributes=False)] += 1
    return protected


def validate_frontend_append(content: str) -> None:
    position = 0
    while position < len(content):
        while position < len(content) and content[position] in " \t\r\n;":
            position += 1
        if position == len(content):
            return
        start = re.match(r"(?:it|test)\s*\(", content[position:])
        require(start is not None, "Append only direct test calls; use a new file for imports or setup.", "INVALID_TESTS")
        position += start.end()
        depth = 1
        quote = None
        escaped = False
        while position < len(content) and depth:
            character = content[position]
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character in "\"'`":
                quote = character
            elif content[position:position + 2] == "//":
                position = content.find("\n", position)
                require(position >= 0, "Ambiguous appended test syntax.", "INVALID_TESTS")
                continue
            elif content[position:position + 2] == "/*":
                position = content.find("*/", position + 2)
                require(position >= 0, "Ambiguous appended test syntax.", "INVALID_TESTS")
                position += 2
                continue
            elif character in "([{":
                depth += 1
            elif character in ")]}":
                depth -= 1
            position += 1
        require(depth == 0, "Ambiguous appended test syntax.", "INVALID_TESTS")


def validate_test_content(content: str, backend: bool) -> None:
    require(0 < len(content.encode("utf-8")) <= MAX_FILE_BYTES, "Invalid test content size.", "INVALID_TESTS")
    forbidden = r"\b(?:skip|skipif|xfail|xdescribe|xit|xtest|fit|fdescribe)\b|\.(?:only|todo)\s*\(|istanbul\s+ignore|v8\s+ignore|@ts-nocheck"
    require(re.search(forbidden, content) is None, "Skipping tests or disabling checks is not allowed.", "INVALID_TESTS")
    assertions = assertion_inventory(content, backend)
    require(bool(assertions), "Generated tests need explicit assertions.", "INVALID_TESTS")
    names = test_names(content, backend)
    require(bool(names) and len(names) == len(set(names)), "Generated tests need unique literal names.", "INVALID_TESTS")
    if backend:
        tree = ast.parse(content)
        require(not any(isinstance(node, ast.ClassDef) for node in ast.walk(tree)), "Generate function-based pytest tests.", "INVALID_TESTS")
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                require(node.name.startswith(("test_", "fixture_")), "Only test functions and named fixtures may be added.", "INVALID_TESTS")
    else:
        require(not re.search(r"\b(?:it|test|describe)\s*\.\s*(?:each|concurrent|runIf|skipIf)", content),
                "Generate ordinary literal Vitest cases.", "INVALID_TESTS")


def validate_changes(changes: list[dict], originals: dict[str, str], previous: list[dict] | None = None) -> list[dict]:
    require(type(changes) is list and 0 < len(changes) <= MAX_CHANGES, "Invalid number of test changes.", "INVALID_TESTS")
    result = []
    seen = set()
    for change in changes:
        require(type(change) is dict and set(change) == {"path", "operation", "content"}, "Invalid test change.")
        path = safe_path(change["path"], test_only=True)
        require(path not in seen, "Duplicate test path.", "INVALID_TESTS")
        seen.add(path)
        operation = change["operation"]
        require(operation in {"create", "append"}, "Unsupported file operation.", "INVALID_TESTS")
        require((operation == "append") == (path in originals), "File operation does not match the original tree.", "INVALID_TESTS")
        content = change["content"]
        require(type(content) is str, "Test content must be text.", "INVALID_TESTS")
        backend = path.endswith(".py")
        validate_test_content(content, backend)
        original = originals.get(path, "")
        if original:
            require(not re.search(r"\b(?:autouse|beforeEach|afterEach|beforeAll|afterAll)\b|\bvi\s*\.\s*(?:mock|stubGlobal|spyOn)", content),
                    "Appending global setup could change existing tests; use a new file.", "INVALID_TESTS")
            if backend:
                require(all(isinstance(node, (ast.Import, ast.ImportFrom)) or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
                            for node in ast.parse(content).body), "Append only imports and test functions; use a new file for shared setup.", "INVALID_TESTS")
                def bindings(source: str) -> set[str]:
                    names = set()
                    for node in ast.parse(source).body:
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            names.add(node.name)
                        elif isinstance(node, (ast.Import, ast.ImportFrom)):
                            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
                        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                            names.update(child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store))
                    return names
                require(not bindings(original).intersection(bindings(content)), "Appending would redefine an existing binding; use a new file.", "INVALID_TESTS")
            else:
                validate_frontend_append(content)
        names = test_names(original, backend) if original else []
        require(not set(names).intersection(test_names(content, backend)), "An existing test name would be duplicated.", "INVALID_TESTS")
        combined = original + ("\n\n" if original else "") + content
        require(len(combined.encode("utf-8")) <= 2 * MAX_FILE_BYTES, "Updated test file is too large.", "INVALID_TESTS")
        if backend:
            try:
                ast.parse(combined)
            except SyntaxError as error:
                raise AgentError("INVALID_TESTS", "Updated Python file has invalid syntax.") from error
        result.append({**change, "original_sha256": digest(original), "result_sha256": digest(combined)})
    if previous is not None:
        require({item["path"] for item in previous} == seen, "Repair cannot remove or move generated files.", "ORACLE_CONFLICT")
        for old in previous:
            new = next(item for item in changes if item["path"] == old["path"])
            backend = old["path"].endswith(".py")
            require(old["operation"] == new["operation"], "Repair cannot change file operations.", "ORACLE_CONFLICT")
            require(set(test_names(old["content"], backend)) <= set(test_names(new["content"], backend)), "Repair cannot remove or rename test cases.", "ORACLE_CONFLICT")
            before = oracle_inventory(old["content"], backend)
            after = oracle_inventory(new["content"], backend)
            require(not (before - after), "Repair changed or removed an assertion; human review is required.", "ORACLE_CONFLICT")
    return result
