"""Fail-closed model/output licensing. No automatic legal claims."""
from dataclasses import dataclass
from enum import Enum


class LicenseStatus(str, Enum):
    COMMERCIAL_ALLOWED = "commercial_allowed"
    NONCOMMERCIAL_ONLY = "noncommercial_only"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


class LicenseError(ValueError):
    pass


@dataclass(frozen=True)
class LicenseRecord:
    model_id: str
    status: LicenseStatus
    license_name: str
    weights_revision: str = ""
    output_commercial_allowed: bool = False
    code_and_dependencies_verified: bool = False
    evidence_url: str = ""
    verified_on: str = ""

    def validate(self) -> None:
        if not self.model_id:
            raise LicenseError("Не указан идентификатор модели")
        if self.status == LicenseStatus.COMMERCIAL_ALLOWED:
            if not all((self.weights_revision, self.output_commercial_allowed,
                        self.code_and_dependencies_verified, self.evidence_url, self.verified_on)):
                raise LicenseError("Коммерческий статус требует проверенных весов, выходного аудио и зависимостей")
            if self.weights_revision.lower() in ("main", "master", "latest"):
                raise LicenseError("Коммерческий статус требует неизменяемой ревизии")


def authorize(record: LicenseRecord, model_id: str, commercial: bool) -> None:
    record.validate()
    if record.model_id != model_id:
        raise LicenseError("Лицензия не соответствует выбранной модели")
    if record.status == LicenseStatus.BLOCKED:
        raise LicenseError("Использование модели заблокировано лицензией")
    if commercial and record.status != LicenseStatus.COMMERCIAL_ALLOWED:
        raise LicenseError("Коммерческая генерация запрещена: лицензия модели не подтверждена")


# Provisional catalog; labels are not a commercial rights audit.
BUILTIN_LICENSES: dict[str, LicenseRecord] = {
    name: LicenseRecord(name, LicenseStatus.REVIEW_REQUIRED, "Apache-2.0 (предварительно)",
                        evidence_url="https://github.com/QwenLM/Qwen3-TTS")
    for name in (
        "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
    )
}
