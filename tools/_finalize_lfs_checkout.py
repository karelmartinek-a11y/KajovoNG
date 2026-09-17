from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def enable_lfs(relative: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    checkout_indent: int | None = None
    with_indent: int | None = None
    block_has_lfs = False
    inserted = 0

    def finish_block() -> None:
        nonlocal checkout_indent, with_indent, block_has_lfs, inserted
        if checkout_indent is not None and with_indent is not None and not block_has_lfs:
            out.append(" " * (with_indent + 2) + "lfs: true")
            inserted += 1
        checkout_indent = None
        with_indent = None
        block_has_lfs = False

    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if checkout_indent is not None and stripped.startswith("- uses:") and indent <= checkout_indent:
            finish_block()
        if stripped == "- uses: actions/checkout@v7":
            if checkout_indent is not None:
                finish_block()
            checkout_indent = indent
            out.append(line)
            continue
        if checkout_indent is not None:
            if stripped == "with:" and indent > checkout_indent:
                with_indent = indent
            elif with_indent is not None and stripped.startswith("lfs:"):
                block_has_lfs = True
            elif indent <= checkout_indent and stripped and not stripped.startswith("#"):
                finish_block()
        out.append(line)
    if checkout_indent is not None:
        finish_block()

    if inserted == 0 and "lfs: true" not in text:
        raise RuntimeError(f"No checkout block patched in {relative}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


for workflow in (".github/workflows/ci.yml", ".github/workflows/release.yml"):
    enable_lfs(workflow)

print("Git LFS hydration enabled for CI and release checkouts")
