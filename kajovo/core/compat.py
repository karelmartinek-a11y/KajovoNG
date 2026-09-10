from __future__ import annotations

from pathlib import Path

# Formáty indexované nástrojem file_search; Files API přijímá i jiné formáty.
FILE_SEARCH_EXTENSIONS = frozenset({
    ".c", ".cpp", ".cs", ".css", ".doc", ".docx", ".go", ".html", ".java",
    ".js", ".json", ".md", ".pdf", ".php", ".pptx", ".py", ".rb", ".sh",
    ".tex", ".ts", ".txt",
})
ALLOWED_EXTS = FILE_SEARCH_EXTENSIONS
MAX_INPUT_FILE_BYTES = 50_000_000


def is_compatible_path(path: str) -> bool:
    """Určí podporu formátu pro indexaci ve vector store."""
    return Path(path).suffix.lower() in FILE_SEARCH_EXTENSIONS


def validate_input_file_sizes(metadata: list[dict]) -> None:
    total = 0
    for item in metadata:
        size = item.get("bytes")
        if type(size) is not int or size < 0:
            raise ValueError("Nelze ověřit velikost připojeného souboru.")
        if size >= MAX_INPUT_FILE_BYTES:
            raise ValueError("Každá přímá příloha musí být menší než 50 MB.")
        total += size
    if total > MAX_INPUT_FILE_BYTES:
        raise ValueError("Přímé přílohy dohromady překračují 50 MB.")


SUPPORTED_INPUT_FILE_EXTS = {
    ".art", ".bat", ".brf", ".c", ".cls", ".css", ".csv", ".diff", ".doc", ".docx", ".dot", ".eml", ".es",
    ".h", ".hs", ".htm", ".html", ".hwp", ".hwpx", ".ics", ".ifb", ".java", ".js", ".json", ".keynote",
    ".ksh", ".ltx", ".mail", ".markdown", ".md", ".mht", ".mhtml", ".mjs", ".nws", ".odt", ".pages", ".patch",
    ".pdf", ".pl", ".pm", ".pot", ".ppa", ".pps", ".ppt", ".pptx", ".pwz", ".py", ".rst", ".rtf", ".scala",
    ".sh", ".shtml", ".srt", ".sty", ".tex", ".text", ".txt", ".vcf", ".vtt", ".wiz",
    ".xla", ".xlb", ".xlc", ".xlm", ".xls", ".xlsx", ".xlt", ".xlw", ".xml", ".yaml", ".yml",
}
SUPPORTED_INPUT_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
