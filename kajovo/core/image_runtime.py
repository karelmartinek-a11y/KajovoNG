"""Společná obrazová validace; transport vlastní výhradně OpenAIClient."""
from __future__ import annotations

import io
import re
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from .comic_types import ComicError, IMAGE_MODEL
from .model_registry import model_spec
from .orchestration.image_slots import image_policy


def image_capability(model=IMAGE_MODEL):
    result = model_spec(model).get("image_capabilities")
    if not result:
        raise ComicError("unsupported_model", "Model nemá ověřený obrazový kontrakt.")
    return result


def preferred_input_fidelity(model=IMAGE_MODEL):
    """Vrátí jedinou povolenou aplikační volbu fidelity, nebo None pro omit."""
    cap = image_capability(model)
    policy = image_policy().get("input_fidelity")
    if policy == "high_if_supported_else_omit" and "high" in cap.get("input_fidelity", []):
        return "high"
    if policy in (None, "omit", "high_if_supported_else_omit"):
        return None
    raise ComicError("unsupported_parameter", "Neznámá politika věrnosti vstupních obrázků.")


def inspect_image(data, model=IMAGE_MODEL):
    cap = image_capability(model)
    policy = image_policy()
    if not data or len(data) > min(cap["max_input_bytes"], policy["max_input_bytes_policy"]):
        raise ComicError("image_too_large", "Obrázek musí být menší než 50 MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in cap["input_formats"] or getattr(im, "n_frames", 1) != 1:
                raise ComicError("unsupported_image_format", "Použijte statický PNG, JPEG nebo WebP.")
            if im.width * im.height > min(cap["max_input_pixels"], policy["max_decoded_pixels_policy"]):
                raise ComicError("image_too_large", "Obrázek přesahuje aplikační limit 64 MP.")
            info = {"format": im.format, "width": im.width, "height": im.height, "mime": Image.MIME[im.format]}
            im.verify()
        with Image.open(io.BytesIO(data)) as im:
            im.load()
        return info
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError) as exc:
        if isinstance(exc, ComicError):
            raise
        raise ComicError("corrupt_image", "Obrázek není úplný nebo jej nelze dekódovat.") from exc


def source_bytes(path):
    source = Path(path).expanduser()
    if source.is_symlink() or source.is_junction() or not source.is_file():
        raise ComicError("invalid_input", "Vyberte existující běžný obrázkový soubor.")
    if source.stat().st_size > image_capability()["max_input_bytes"]:
        raise ComicError("image_too_large", "Obrázek musí být menší než 50 MB.")
    data = source.read_bytes()
    inspect_image(data)
    return data


def normalized_image(data):
    inspect_image(data)
    with Image.open(io.BytesIO(data)) as im:
        oriented = ImageOps.exif_transpose(im)
        transparent = "A" in oriented.getbands() or "transparency" in oriented.info
        pixels = oriented.convert("RGBA" if transparent else "RGB")
        # Nový pixelový objekt nepřenáší EXIF, ICC ani textová metadata zdroje.
        image = Image.frombytes(pixels.mode, pixels.size, pixels.tobytes())
        output = io.BytesIO()
        image.save(output, format="PNG")
    result = output.getvalue()
    inspect_image(result)
    return result


def validate_image_request(endpoint, body):
    if not isinstance(body, dict):
        raise ComicError("invalid_input", "Neplatný obrazový požadavek.")
    cap = image_capability(body.get("model"))
    required = {"model", "prompt", "n", "size", "quality", "output_format", "background"}
    allowed = required | ({"images", "input_fidelity"} if endpoint == "/v1/images/edits" else set())
    if endpoint not in cap["endpoints"] or not required <= set(body) or set(body) - allowed:
        raise ComicError("unsupported_parameter", "Nepodporovaný endpoint nebo obrazový parametr.")
    if type(body["n"]) is not int or body["n"] != 1:
        raise ComicError("invalid_input", "Obrazový požadavek vyžaduje celé číslo n=1.")
    if (
        not isinstance(body["prompt"], str)
        or not body["prompt"].strip()
        or len(body["prompt"]) > cap["max_prompt_chars"]
    ):
        raise ComicError(
            "invalid_input",
            f"Požadavek potřebuje neprázdný prompt do {cap['max_prompt_chars']} znaků.",
        )
    for field, options in (("quality", "quality"), ("output_format", "output_formats"), ("background", "background")):
        if body[field] not in cap[options]:
            raise ComicError("unsupported_parameter", f"Nepodporovaná hodnota {field}.")
    if body["background"] == "transparent" and body["output_format"] == "jpeg":
        raise ComicError("unsupported_parameter", "Průhlednost vyžaduje PNG nebo WebP.")
    if "input_fidelity" in body and body["input_fidelity"] not in cap["input_fidelity"]:
        raise ComicError("unsupported_parameter", "Nepodporovaná věrnost reference.")
    fixed_sizes = cap.get("sizes")
    if fixed_sizes is not None:
        if (
            not isinstance(fixed_sizes, list)
            or not fixed_sizes
            or any(not isinstance(value, str) or not value for value in fixed_sizes)
            or body["size"] not in fixed_sizes
        ):
            raise ComicError("invalid_format", "Generovací velikost není podporována vybraným modelem.")
    elif body["size"] != "auto":
        if not isinstance(body["size"], str) or not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", body["size"]):
            raise ComicError("invalid_format", "Rozměry musí být kanonické šířkaxvýška v pixelech.")
        try:
            w, h = map(int, body["size"].split("x"))
        except (ValueError, AttributeError) as exc:
            raise ComicError("invalid_format", "Neplatná generovací velikost.") from exc
        if not (w > 0 and h > 0 and w % cap["multiple"] == h % cap["multiple"] == 0 and max(w, h) <= cap["max_edge"]
                and max(w, h) <= cap["max_ratio"] * min(w, h) and cap["min_pixels"] <= w * h <= cap["max_pixels"]):
            raise ComicError("invalid_format", "Generovací velikost překračuje možnosti modelu.")
    if endpoint.endswith("edits"):
        refs = body.get("images")
        if not isinstance(refs, list) or not 1 <= len(refs) <= cap["max_references"]:
            raise ComicError("too_many_references", "Obrazová editace vyžaduje 1 až 16 referencí.")
        from .openai_client import OpenAIClient
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != {"file_id"}:
                raise ComicError("invalid_input", "Reference musí obsahovat pouze file_id.")
            OpenAIClient._validate_resource_id(ref["file_id"])
    return body


def postprocess(data, target):
    inspect_image(data)
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
        original = im.size
        if target.fit == "crop":
            image = ImageOps.fit(im, (target.width, target.height), method=Image.Resampling.LANCZOS)
        else:
            scaled = ImageOps.contain(im, (target.width, target.height), method=Image.Resampling.LANCZOS)
            image = Image.new("RGBA", (target.width, target.height), (255, 255, 255, 255))
            image.alpha_composite(scaled, ((target.width - scaled.width) // 2, (target.height - scaled.height) // 2))
        out = io.BytesIO()
        image.save(out, "PNG", dpi=(target.dpi, target.dpi))
    return out.getvalue(), {"source_size": original, "target_size": [target.width, target.height], "fit": target.fit, "dpi": target.dpi}
