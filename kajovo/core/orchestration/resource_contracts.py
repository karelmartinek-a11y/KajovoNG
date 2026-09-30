"""Distribuované masky odpovídají kontraktům skutečných runtime spotřebitelů."""
from __future__ import annotations


def physical_contract_schemas() -> dict[str, dict]:
    from ..structured_output import file_content_format
    from .batch_manifest import BATCH_MANIFEST_V4_SCHEMA
    from .manual_resources import MANUAL_RESOURCE_BINDINGS_V1_SCHEMA
    from .run_config import RUN_CONFIG_V2_SCHEMA
    from .verification import VERIFICATION_REPORT_V3_SCHEMA
    from .work_order import WORK_ORDER_V2_SCHEMA, WORK_ORDER_V3_SCHEMA

    return {
        "local/MANUAL_RESOURCE_BINDINGS_V1": MANUAL_RESOURCE_BINDINGS_V1_SCHEMA,
        "local/BATCH_MANIFEST_V4": BATCH_MANIFEST_V4_SCHEMA,
        "local/RUN_CONFIG_V2": RUN_CONFIG_V2_SCHEMA,
        "local/VERIFICATION_REPORT_V3": VERIFICATION_REPORT_V3_SCHEMA,
        "local/WORK_ORDER_V2": WORK_ORDER_V2_SCHEMA,
        "local/WORK_ORDER_V3": WORK_ORDER_V3_SCHEMA,
        "wire/FILE_CONTENT_V1": file_content_format()["format"]["schema"],
    }
