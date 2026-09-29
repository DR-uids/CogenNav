"""跨文件引用解析：把名字引用绑定到符号，并给出三档置信度。

- ``EXTRACTED``：源码里显式且唯一可判（同文件命中、``self.x`` 命中本类方法）
- ``INFERRED``：经导入图唯一解析（被导入文件里只有一个同名符号、导入别名 → 模块 → 符号、
  同包/同目录隐式可见）
- ``AMBIGUOUS``：多个候选或信息不足；前端默认隐藏
- ``dropped``：内建名 / ``self`` 上找不到的成员 —— 直接丢弃并计数，避免图谱被噪声淹没
- ``unresolved``：其余无法归因的引用（局部变量上的方法调用等）

导入 → 文件的解析按语言分策略；不认识的导入只计数，不做猜测。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .base import FileExtraction, Reference, builtin_globals, file_node_id, symbol_node_id

CONFIDENCE_EXTRACTED = "EXTRACTED"
CONFIDENCE_INFERRED = "INFERRED"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"

#: 外部符号节点的数量上限（按引用次数取最高的那批）
MAX_EXTERNAL_NODES = 400

_RELATION_BY_KIND = {
    "call": "calls",
    "extends": "extends",
    "implements": "implements",
    "instantiates": "instantiates",
    "type_uses": "type_uses",
}

#: 各语言的源码扩展名（把导入字符串映射到仓库内文件时用）
_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyi"),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "typescript": (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx"),
    "tsx": (".tsx", ".ts", ".jsx", ".js"),
    "go": (".go",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "rust": (".rs",),
    "c": (".c", ".h"),
    "cpp": (".cpp", ".cc", ".cxx", ".hpp", ".h"),
    "c_sharp": (".cs",),
    "ruby": (".rb",),
    "php": (".php",),
    "swift": (".swift",),
}


@dataclass(frozen=True)
class ResolvedRef:
    """一条解析后的引用（会被翻译成图谱边）。"""

    source_id: str
    target_id: str
    relation: str
    confidence: str
    file: str
    start: tuple[int, int]
    end: tuple[int, int]


@dataclass
class ResolutionStats:
    calls_total: int = 0
    calls_extracted: int = 0
    calls_inferred: int = 0
    calls_ambiguous: int = 0
    calls_dropped: int = 0
    calls_unresolved: int = 0
    external_nodes: int = 0

    @property
    def resolved_calls(self) -> int:
        return self.calls_extracted + self.calls_inferred

    @property
    def resolved_rate(self) -> float | None:
        """解析成功率 = 已解析 /（总数 − 内建与局部方法噪声）。"""
        denominator = self.calls_total - self.calls_dropped
        if denominator <= 0:
            return None
        return round(self.resolved_calls / denominator, 4)

    def to_api(self) -> dict[str, object]:
        return {
            "callsTotal": self.calls_total,
            "callsExtracted": self.calls_extracted,
            "callsInferred": self.calls_inferred,
            "callsAmbiguous": self.calls_ambiguous,
            "callsUnresolved": self.calls_unresolved,
            "callsDropped": self.calls_dropped,
            "resolvedCallRate": self.resolved_rate,
            "externalNodes": self.external_nodes,
        }


@dataclass
class ResolutionResult:
    references: list[ResolvedRef] = field(default_factory=list)
    stats: ResolutionStats = field(default_factory=ResolutionStats)
    #: ``ext:...`` 节点 id → 引用次数（只保留 top N，调用与导入合并）
    external_targets: dict[str, int] = field(default_factory=dict)
    #: 实际保留的「仓库外模块」id（导入边用它过滤）
    external_modules_kept: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Outcome:
    target_id: str | None
    confidence: str
    kind: str  # extracted | inferred | ambiguous | dropped | unresolved


_DROPPED = _Outcome(None, CONFIDENCE_AMBIGUOUS, "dropped")
_UNRESOLVED = _Outcome(None, CONFIDENCE_AMBIGUOUS, "unresolved")


class ReferenceResolver:
    """把一个仓库内所有文件的引用解析成边。"""

    def __init__(self, extractions: dict[str, FileExtraction]) -> None:
        self.extractions = extractions
        self.paths = set(extractions)
        self._symbols_by_file: dict[str, dict[str, list[tuple[str, str]]]] = {}
        self._qualified_index: dict[str, str] = {}
        self._name_files: dict[str, set[str]] = {}
        self._import_targets: dict[str, dict[str, set[str]]] = {}
        self._external_aliases: dict[str, set[str]] = {}
        self._sibling_files: dict[str, set[str]] = {}
        self._build_index()

    # ── 索引 ────────────────────────────────────────────────────────
    def _build_index(self) -> None:
        by_dir: dict[str, set[str]] = {}
        for path, extraction in self.extractions.items():
            by_dir.setdefault(str(PurePosixPath(path).parent), set()).add(path)
            table: dict[str, list[tuple[str, str]]] = {}
            for symbol in extraction.symbols:
                ordinal = int(symbol.extra.get("ordinal", 0))
                node_id = symbol_node_id(
                    extraction.language, path, symbol.qualified, symbol.kind, ordinal
                )
                table.setdefault(symbol.name, []).append((node_id, symbol.kind))
                self._qualified_index[f"{path}#{symbol.qualified}"] = node_id
                self._name_files.setdefault(symbol.name, set()).add(path)
            self._symbols_by_file[path] = table

        for path, extraction in self.extractions.items():
            self._sibling_files[path] = by_dir.get(str(PurePosixPath(path).parent), set()) - {path}

            targets: dict[str, set[str]] = {}
            external: set[str] = set()
            for imp in extraction.imports:
                resolved = self._resolve_import(path, extraction.language, imp.module, imp.level)
                aliases = {imp.alias} if imp.alias else set()
                if imp.module:
                    aliases.add(imp.module.split(".")[-1] if "." in imp.module else imp.module)
                    aliases.add(imp.module)
                for name in imp.names:
                    aliases.add(name)
                if resolved:
                    targets.setdefault("", set()).update(resolved)
                    for alias in aliases:
                        if alias:
                            targets.setdefault(alias, set()).update(resolved)
                else:
                    for alias in aliases:
                        if alias and alias != "*":
                            external.add(alias)
            self._import_targets[path] = targets
            self._external_aliases[path] = external

    def _resolve_import(self, path: str, language: str, module: str, level: int) -> set[str]:
        extensions = _EXTENSIONS.get(language, ())
        if language == "python":
            return self._resolve_python_import(path, module, level)
        if language in ("javascript", "typescript", "tsx"):
            return self._resolve_js_import(path, module, extensions)
        if language == "go":
            return self._resolve_go_import(path, module)
        if language == "java":
            return self._resolve_java_import(path, module)
        return self._resolve_generic_import(path, module, extensions)

    def _resolve_python_import(self, path: str, module: str, level: int) -> set[str]:
        base = PurePosixPath(path).parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        parts = [p for p in module.split(".") if p]
        if level == 0 and not parts:
            return set()

        if level > 0:
            # 相对导入：相对当前文件所在目录
            prefix = base.joinpath(*parts) if parts else base
            candidates = [
                f"{prefix}.py",
                f"{prefix}.pyi",
                str(prefix / "__init__.py"),
            ]
            if parts:
                candidates.append(str(prefix))
            return {c for c in candidates if c in self.paths}

        # 绝对导入：先按仓库根解析，再兼容 src-layout（cogen.x → src/cogen/x.py）
        module_path = "/".join(parts)
        exact = {
            candidate
            for candidate in (
                f"{module_path}.py",
                f"{module_path}.pyi",
                f"{module_path}/__init__.py",
            )
            if candidate in self.paths
        }
        if exact:
            return exact
        suffixes = (
            f"/{module_path}.py",
            f"/{module_path}.pyi",
            f"/{module_path}/__init__.py",
        )
        matches = {p for p in self.paths if p.endswith(suffixes)}
        return matches if len(matches) <= 3 else set()

    def _resolve_js_import(self, path: str, module: str, extensions: tuple[str, ...]) -> set[str]:
        if not module.startswith("."):
            return set()
        base = PurePosixPath(path).parent
        target = str(base.joinpath(module))
        while target.startswith("../"):
            target = target[3:]
        candidates: list[str] = [target + ext for ext in extensions]
        candidates += [f"{target}/index{ext}" for ext in extensions]
        if PurePosixPath(target).suffix:
            candidates.append(target)
        return {c for c in candidates if c in self.paths}

    def _resolve_go_import(self, path: str, module: str) -> set[str]:
        """Go 的导入是包路径：用最后一段目录名匹配仓库内同名目录。"""
        tail = module.rstrip("/").split("/")[-1]
        if not tail:
            return set()
        matches = {
            candidate for candidate in self.paths if PurePosixPath(candidate).parent.name == tail
        }
        # 同包文件（同目录）隐式可见，一并算进去
        matches |= self._sibling_files.get(path, set())
        return matches

    def _resolve_java_import(self, path: str, module: str) -> set[str]:
        cleaned = module[:-2] if module.endswith(".*") else module
        parts = [p for p in cleaned.split(".") if p]
        if not parts:
            return set()
        package = "/".join(parts[:-1])
        tail = parts[-1]
        matches: set[str] = set()
        for candidate in self.paths:
            posix = PurePosixPath(candidate)
            if package and str(posix.parent).endswith(package) and posix.stem == tail:
                matches.add(candidate)
        if not matches:
            matches = {c for c in self.paths if PurePosixPath(c).stem == tail}
        matches |= self._sibling_files.get(path, set())
        return matches

    def _resolve_generic_import(
        self, path: str, module: str, extensions: tuple[str, ...]
    ) -> set[str]:
        if not module:
            return set()
        cleaned = module.strip("\"'<>")
        parts = [p for p in cleaned.replace("::", "/").replace(".", "/").split("/") if p]
        if not parts:
            return set()
        base = PurePosixPath(path).parent
        target = base.joinpath(*parts) if module.startswith(".") else PurePosixPath(*parts)
        candidates = [f"{target}{ext}" for ext in (extensions or ("",))]
        matches = {c for c in candidates if c in self.paths}
        if not matches:
            # 只接受**同目录**下的唯一同名文件，避免跨目录误连（曾把 index.css 连到 main.tsx）
            tail = PurePosixPath(*parts).stem or PurePosixPath(*parts).name
            same_dir = {
                candidate
                for candidate in self._sibling_files.get(path, set())
                if PurePosixPath(candidate).stem == tail
            }
            matches = same_dir
        return matches

    # ── 单条引用解析 ────────────────────────────────────────────────
    def _resolve_one(self, path: str, language: str, ref: Reference) -> _Outcome:
        builtins = builtin_globals(language)
        name = ref.name
        receiver = ref.receiver

        if ref.self_receiver:
            enclosing = ref.scope.rsplit(".", 1)[0] if ref.scope and "." in ref.scope else None
            if enclosing:
                members = self._self_member(path, enclosing, name)
                if len(members) == 1:
                    return _Outcome(members[0], CONFIDENCE_EXTRACTED, "extracted")
                if len(members) > 1:
                    return _Outcome(members[0], CONFIDENCE_AMBIGUOUS, "ambiguous")
            return _DROPPED

        same_file = self._symbols_by_file.get(path, {}).get(name, [])
        if len(same_file) == 1:
            return _Outcome(same_file[0][0], CONFIDENCE_EXTRACTED, "extracted")
        if len(same_file) > 1:
            return _Outcome(same_file[0][0], CONFIDENCE_AMBIGUOUS, "ambiguous")

        imported = self._imported_candidates(path, name, receiver)
        if len(imported) == 1:
            return _Outcome(imported[0][0], CONFIDENCE_INFERRED, "inferred")
        if len(imported) > 1:
            return _Outcome(imported[0][0], CONFIDENCE_AMBIGUOUS, "ambiguous")

        if receiver:
            head = receiver.split(".")[0]
            if receiver in builtins or head in builtins:
                return _DROPPED
            if receiver in self._external_aliases.get(path, set()):
                # 明确来自仓库外依赖（import requests → requests.get）
                return _Outcome(f"ext:{receiver}.{name}", CONFIDENCE_INFERRED, "inferred")
            # 变量/未导入对象上的方法调用：不猜，也不建噪声边
            return _UNRESOLVED

        if name in builtins:
            return _DROPPED

        defining_files = self._name_files.get(name, set())
        if len(defining_files) == 1:
            target = next(iter(defining_files))
            if target in self._sibling_files.get(path, set()):
                entries = self._symbols_by_file.get(target, {}).get(name, [])
                if len(entries) == 1:
                    return _Outcome(entries[0][0], CONFIDENCE_INFERRED, "inferred")

        return _UNRESOLVED

    def _self_member(self, path: str, class_qualified: str, name: str) -> list[str]:
        found: list[str] = []
        for symbol_name, entries in self._symbols_by_file.get(path, {}).items():
            if symbol_name != name:
                continue
            for node_id, _kind in entries:
                if f"{class_qualified}." in node_id.split("#", 1)[-1]:
                    found.append(node_id)
        return found

    def _imported_candidates(
        self, path: str, name: str, receiver: str | None
    ) -> list[tuple[str, str]]:
        targets = self._import_targets.get(path, {})
        files: set[str] = set()
        if receiver:
            files = targets.get(receiver, set())
            if not files and "." in receiver:
                files = targets.get(receiver.split(".")[0], set())
        if not files:
            files = targets.get("", set())
        found: list[tuple[str, str]] = []
        for target in files:
            found.extend(self._symbols_by_file.get(target, {}).get(name, []))
        return found

    # ── 主流程 ──────────────────────────────────────────────────────
    def resolve(self) -> ResolutionResult:
        result = ResolutionResult()
        external_counter: Counter[str] = Counter()

        # 1) 导入边：文件 → 文件（仓库内）或 → 外部模块节点（仓库外，只保留 top N）
        pending_external: list[ResolvedRef] = []
        external_modules: Counter[str] = Counter()
        for path, extraction in self.extractions.items():
            for imp in extraction.imports:
                targets = self._resolve_import(path, extraction.language, imp.module, imp.level)
                for target in sorted(targets):
                    result.references.append(
                        ResolvedRef(
                            source_id=file_node_id(path),
                            target_id=file_node_id(target),
                            relation="imports",
                            confidence=CONFIDENCE_EXTRACTED,
                            file=path,
                            start=imp.start,
                            end=imp.start,
                        )
                    )
                if not targets and imp.module:
                    module_id = f"ext:{imp.module}"
                    external_modules[module_id] += 1
                    pending_external.append(
                        ResolvedRef(
                            source_id=file_node_id(path),
                            target_id=module_id,
                            relation="imports",
                            confidence=CONFIDENCE_EXTRACTED,
                            file=path,
                            start=imp.start,
                            end=imp.start,
                        )
                    )
        keep_modules = {module for module, _ in external_modules.most_common(MAX_EXTERNAL_NODES)}
        result.references.extend(r for r in pending_external if r.target_id in keep_modules)
        result.external_targets.update(dict(external_modules.most_common(MAX_EXTERNAL_NODES)))

        # 2) 引用边（calls / extends / implements / instantiates / type_uses）
        for path, extraction in self.extractions.items():
            language = extraction.language
            for ref in extraction.references:
                outcome = self._resolve_one(path, language, ref)
                is_call = ref.kind == "call"
                if is_call:
                    result.stats.calls_total += 1
                    if outcome.kind == "extracted":
                        result.stats.calls_extracted += 1
                    elif outcome.kind == "inferred":
                        result.stats.calls_inferred += 1
                    elif outcome.kind == "ambiguous":
                        result.stats.calls_ambiguous += 1
                    elif outcome.kind == "dropped":
                        result.stats.calls_dropped += 1
                    else:
                        result.stats.calls_unresolved += 1
                if outcome.target_id is None:
                    continue
                if outcome.target_id.startswith("ext:") and not is_call:
                    continue  # extends/implements 指向仓库外类型时没有意义
                result.references.append(
                    ResolvedRef(
                        source_id=self._source_id(path, ref.scope),
                        target_id=outcome.target_id,
                        relation=_RELATION_BY_KIND.get(ref.kind, "references"),
                        confidence=outcome.confidence,
                        file=path,
                        start=ref.start,
                        end=ref.end,
                    )
                )
                if outcome.target_id.startswith("ext:"):
                    external_counter[outcome.target_id] += 1

        for target, count in external_counter.most_common(MAX_EXTERNAL_NODES):
            result.external_targets[target] = result.external_targets.get(target, 0) + count
        result.external_modules_kept = keep_modules
        result.stats.external_nodes = len(result.external_targets)
        return result

    def _source_id(self, path: str, scope: str | None) -> str:
        if scope:
            found = self._qualified_index.get(f"{path}#{scope}")
            if found:
                return found
        return file_node_id(path)
