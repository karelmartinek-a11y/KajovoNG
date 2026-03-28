# Dual-DB model

Working DB obsahuje workflow-only data a auditní záznamy.
Production DB obsahuje pouze finální business data.

## Working DB
- files
- processing_documents
- processing_items
- processing_suppliers
- extraction_attempts
- ares_validations
- promotion_audit
- operational_log
- errors
- document_results
- audit_changes
- system_state
- visual_patterns
- item_groups
- item_catalog

## Production DB
- final_suppliers
- final_documents
- final_items

## Separation check

`ProjectRepository.separation_report()` vrací důkazní report o fyzickém a logickém oddělení obou databází.
