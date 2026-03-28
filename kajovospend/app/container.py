from __future__ import annotations

from dataclasses import dataclass

from kajovospend.app.config import AppConfig
from kajovospend.app.settings import AppSettings, SettingsStore
from kajovospend.integrations.ares_client import AresClient
from kajovospend.integrations.openai_client import OpenAIClient
from kajovospend.integrations.secret_store import SecretStore
from kajovospend.processing.service import ProcessingService
from kajovospend.project.project_service import ProjectService
from kajovospend.services.supplier_service import SupplierService
from kajovospend.services.reporting_service import ReportingService


@dataclass(slots=True)
class ServiceContainer:
    config: AppConfig
    settings_store: SettingsStore
    settings: AppSettings
    secret_store: SecretStore
    openai_client: OpenAIClient
    ares_client: AresClient
    project_service: ProjectService
    processing_service: ProcessingService
    supplier_service: SupplierService
    reporting_service: ReportingService

    @classmethod
    def build(cls) -> 'ServiceContainer':
        config = AppConfig.load()
        settings_store = SettingsStore(config.settings_file)
        settings = settings_store.load()
        secret_store = SecretStore()
        openai_client = OpenAIClient()
        ares_client = AresClient()
        return cls(
            config=config,
            settings_store=settings_store,
            settings=settings,
            secret_store=secret_store,
            openai_client=openai_client,
            ares_client=ares_client,
            project_service=ProjectService(),
            processing_service=ProcessingService(openai_client=openai_client, ares_client=ares_client, secret_store=secret_store),
            supplier_service=SupplierService(ares_client),
            reporting_service=ReportingService(),
        )
