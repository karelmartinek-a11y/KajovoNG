"""Úplné syntetické provider obálky pro offline HTTP procesní scénáře.

Hodnoty jsou testovací, nejsou náhradními hodnotami produkční validace.
Záměrně vadné dodané hodnoty se zachovávají; helper se nepoužívá v negativních
testech chybějících polí nativních kontraktů.
"""
from __future__ import annotations

from kajovo.core.openai_transport import operation_spec


def native_fixture(method, path, value, request=None):
    operation = operation_spec(method, path).name
    request = request or {}
    file_counts = {"completed": 0, "failed": 0, "in_progress": 0, "cancelled": 0, "total": 0}
    defaults = {
        "file": {"object": "file", "bytes": 0, "created_at": 0, "filename": "test.txt",
                 "purpose": request.get("purpose", "user_data"), "status": "processed"},
        "model": {"object": "model", "created": 0, "owned_by": "offline-test"},
        "store": {"object": "vector_store", "created_at": 0, "name": request.get("name", "Test"),
                  "usage_bytes": 0, "file_counts": file_counts, "status": "completed"},
        "member": {"object": "vector_store.file", "created_at": 0, "usage_bytes": 0,
                   "vector_store_id": path.split("/")[2] if len(path.split("/")) > 2 else "vs_test", "status": "completed"},
        "batch": {"object": "batch", "completion_window": "24h", "created_at": 0,
                  "endpoint": request.get("endpoint", "/v1/responses"),
                  "input_file_id": request.get("input_file_id", "file_input"), "status": "validating"},
    }
    item_kinds = {
        "list_models": "model", "list_files": "file", "list_vector_stores": "store",
        "list_vector_store_files": "member", "list_batches": "batch",
        "upload_file": "file", "retrieve_file": "file", "create_vector_store": "store",
        "retrieve_vector_store": "store", "attach_vector_store_file": "member",
        "retrieve_vector_store_file": "member", "update_vector_store_file": "member",
        "create_batch": "batch", "retrieve_batch": "batch", "cancel_batch": "batch",
    }
    def item(row, kind):
        result = {**defaults[kind], **row}
        if kind == "file":
            result.pop("content", None)  # Pouze vnitřní stav HTTP simulátoru.
        return result

    if operation.startswith("list_") and operation in item_kinds:
        result = {"object": "list", **value,
                  "data": [item(row, item_kinds[operation]) for row in value["data"]]}
        if operation == "list_models":
            result.pop("has_more", None)
        return result
    if operation in item_kinds:
        return item(value, item_kinds[operation])
    deleted_objects = {"delete_file": "file.deleted", "delete_vector_store": "vector_store.deleted",
                       "delete_vector_store_file": "vector_store.file.deleted"}
    if operation in deleted_objects:
        return {"object": deleted_objects[operation], **value}
    if operation == "input_token_count":
        return {"object": "response.input_tokens", **value}
    if operation == "create_image":
        return {"created": 0, **value}
    return value
