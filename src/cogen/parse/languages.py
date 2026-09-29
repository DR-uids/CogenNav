"""语言注册表：扩展名 / 文件名 / shebang → 语言 id，以及 tree-sitter 语法加载。

两层语法来源（见计划 §3）：
- **核心层**（默认）：随包安装的 per-language wheel，离线可用、无运行时下载；
- **扩展层**（可选 extra ``cogen[xlang]``）：``tree-sitter-language-pack`` 覆盖 371 门语言，
  但会在首次解析时联网下载**原生**解析器，因此默认关闭、按不可信代码对待。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import import_module
from pathlib import Path

from tree_sitter import Language

UNKNOWN_LANGUAGE = "unknown"


@dataclass(frozen=True)
class GrammarSpec:
    """一个语言的语法来源描述。"""

    language: str
    module: str
    function: str
    display: str


CORE_GRAMMARS: dict[str, GrammarSpec] = {
    spec.language: spec
    for spec in [
        GrammarSpec("python", "tree_sitter_python", "language", "Python"),
        GrammarSpec("javascript", "tree_sitter_javascript", "language", "JavaScript"),
        GrammarSpec("typescript", "tree_sitter_typescript", "language_typescript", "TypeScript"),
        GrammarSpec("tsx", "tree_sitter_typescript", "language_tsx", "TSX"),
        GrammarSpec("go", "tree_sitter_go", "language", "Go"),
        GrammarSpec("java", "tree_sitter_java", "language", "Java"),
        GrammarSpec("rust", "tree_sitter_rust", "language", "Rust"),
        GrammarSpec("c", "tree_sitter_c", "language", "C"),
        GrammarSpec("cpp", "tree_sitter_cpp", "language", "C++"),
        GrammarSpec("c_sharp", "tree_sitter_c_sharp", "language", "C#"),
        GrammarSpec("ruby", "tree_sitter_ruby", "language", "Ruby"),
        GrammarSpec("php", "tree_sitter_php", "language_php", "PHP"),
        GrammarSpec("kotlin", "tree_sitter_kotlin", "language", "Kotlin"),
        GrammarSpec("swift", "tree_sitter_swift", "language", "Swift"),
        GrammarSpec("bash", "tree_sitter_bash", "language", "Bash"),
        GrammarSpec("json", "tree_sitter_json", "language", "JSON"),
        GrammarSpec("yaml", "tree_sitter_yaml", "language", "YAML"),
        GrammarSpec("html", "tree_sitter_html", "language", "HTML"),
        GrammarSpec("css", "tree_sitter_css", "language", "CSS"),
    ]
}

# 语言 id → 各语言在扩展层（tree-sitter-language-pack）中的名字，通常与 id 相同。
XLANG_NAMES: dict[str, str] = {
    "c_sharp": "csharp",
}

_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".rake": "ruby",
    ".gemspec": "ruby",
    ".php": "php",
    ".phtml": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".sh": "bash",
    ".bash": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    # 以下仅有「分类」意义：当前核心层没有对应语法，M2 不会解析它们，
    # 但目录树/语言分布统计需要把它们从 unknown 里区分出来。
    ".md": "markdown",
    ".markdown": "markdown",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".txt": "text",
    ".rst": "rst",
    ".sql": "sql",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
}

#: 只用于分类展示、没有可用语法的语言 id
DISPLAY_ONLY_LANGUAGES: frozenset[str] = frozenset(
    {
        "markdown",
        "toml",
        "ini",
        "text",
        "rst",
        "sql",
        "xml",
        "protobuf",
        "graphql",
        "dockerfile",
        "make",
    }
)

_FILENAMES: dict[str, str] = {
    "rakefile": "ruby",
    "gemfile": "ruby",
    "vagrantfile": "ruby",
    "podfile": "ruby",
    ".bashrc": "bash",
    ".bash_profile": "bash",
    ".profile": "bash",
    ".zshrc": "bash",
    "dockerfile": "dockerfile",
    "makefile": "make",
    "gnumakefile": "make",
}

_SHEBANGS: tuple[tuple[str, str], ...] = (
    ("python", "python"),
    ("node", "javascript"),
    ("deno", "typescript"),
    ("bash", "bash"),
    ("/sh", "bash"),
    ("zsh", "bash"),
    ("ruby", "ruby"),
    ("php", "php"),
    ("swift", "swift"),
)


def detect_language(path: str | Path, first_line: str | None = None) -> str:
    """按文件名、扩展名、shebang 依次判断语言；无法判断时返回 ``"unknown"``。"""
    p = Path(path)
    by_name = _FILENAMES.get(p.name.lower())
    if by_name:
        return by_name
    by_ext = _EXTENSIONS.get(p.suffix.lower())
    if by_ext:
        return by_ext
    # 无扩展名（或未知扩展名）时看 shebang
    if first_line and first_line.startswith("#!"):
        lowered = first_line.lower()
        for needle, language in _SHEBANGS:
            if needle in lowered:
                return language
    return UNKNOWN_LANGUAGE


def core_languages() -> list[str]:
    return sorted(CORE_GRAMMARS)


def display_name(language: str) -> str:
    spec = CORE_GRAMMARS.get(language)
    return spec.display if spec else language


def has_core_grammar(language: str) -> bool:
    return language in CORE_GRAMMARS


def is_display_only(language: str) -> bool:
    """该语言只能用于分类展示（没有语法，M2 不会解析）。"""
    return language in DISPLAY_ONLY_LANGUAGES


@cache
def load_language(language: str) -> Language:
    """加载语法（进程内缓存）。核心层缺失时抛出 ``KeyError``，请先检查可用性。"""
    spec = CORE_GRAMMARS.get(language)
    if spec is None:
        raise KeyError(f"核心层没有该语言的语法: {language}（可用 cogen[xlang] 扩展层）")
    module = import_module(spec.module)
    loader = getattr(module, spec.function)
    return Language(loader())


def xlang_available() -> bool:
    """扩展层是否已安装（``pip install -e ".[xlang]"``）。"""
    try:
        import_module("tree_sitter_language_pack")
    except ImportError:
        return False
    return True


@cache
def load_xlang_language(language: str) -> Language:
    """从扩展层加载语法。首次调用会联网下载原生解析器（缓存目录见 README）。"""
    from tree_sitter_language_pack import get_language

    name = XLANG_NAMES.get(language, language)
    return get_language(name)
