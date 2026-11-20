import pytest
from pydantic import ValidationError

from algorythm5.config import Settings


def test_defaults_match_open_decisions() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.default_phone_region == "AL"
    assert settings.unknown_consent_policy == "include"
    assert settings.required_channel == "email"
    assert settings.date_order == "DMY"
    assert settings.max_upload_bytes == 10 * 1024 * 1024


def test_region_is_normalised_to_upper_case() -> None:
    assert Settings(_env_file=None, default_phone_region="xk").default_phone_region == "XK"  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_phone_region", "ZZ"),
        ("unknown_consent_policy", "maybe"),
        ("date_order", "YMD"),
        ("max_upload_mb", 0),
        ("csv_delimiter", "|"),
    ],
)
def test_invalid_settings_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})  # type: ignore[call-arg]
