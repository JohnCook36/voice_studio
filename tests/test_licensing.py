import pytest
from voice_studio.licensing import LicenseRecord, LicenseStatus, LicenseError, authorize, BUILTIN_LICENSES


def test_builtins_are_not_commercially_authorized():
    for model, record in BUILTIN_LICENSES.items():
        authorize(record, model, commercial=False)
        with pytest.raises(LicenseError):
            authorize(record, model, commercial=True)


def test_commercial_requires_all_evidence():
    name = "test/model"
    record = LicenseRecord(name, LicenseStatus.COMMERCIAL_ALLOWED, "Apache-2.0")
    with pytest.raises(LicenseError):
        authorize(record, name, True)
    record = LicenseRecord(name, LicenseStatus.COMMERCIAL_ALLOWED, "Apache-2.0", "abcdef12345", True, True, "https://example.test/license", "2026-11-14")
    authorize(record, name, True)
    with pytest.raises(LicenseError):
        authorize(record, "another/model", False)


def test_noncommercial_is_blocked_for_commercial():
    record = LicenseRecord("m", LicenseStatus.NONCOMMERCIAL_ONLY, "CC-BY-NC")
    authorize(record, "m", False)
    with pytest.raises(LicenseError):
        authorize(record, "m", True)
