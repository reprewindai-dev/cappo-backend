import pytest
from fastapi import HTTPException
from unittest.mock import patch
from cappo_backend.api.routers.license_router import _verify_admin_token

def test_verify_admin_token_no_key_in_production():
    with patch("cappo_backend.api.routers.license_router.settings") as mock_settings:
        mock_settings.license_admin_key = ""
        mock_settings.is_production = True

        with pytest.raises(HTTPException) as exc_info:
            _verify_admin_token(x_license_admin_key="some-key")

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "License admin key not configured in production"

def test_verify_admin_token_no_key_not_in_production():
    with patch("cappo_backend.api.routers.license_router.settings") as mock_settings:
        mock_settings.license_admin_key = ""
        mock_settings.is_production = False

        # Should return None without raising exception
        assert _verify_admin_token(x_license_admin_key="some-key") is None

def test_verify_admin_token_valid_key():
    with patch("cappo_backend.api.routers.license_router.settings") as mock_settings:
        mock_settings.license_admin_key = "valid-key"
        mock_settings.is_production = True

        # Should return None without raising exception
        assert _verify_admin_token(x_license_admin_key="valid-key") is None

def test_verify_admin_token_invalid_key():
    with patch("cappo_backend.api.routers.license_router.settings") as mock_settings:
        mock_settings.license_admin_key = "valid-key"
        mock_settings.is_production = True

        with pytest.raises(HTTPException) as exc_info:
            _verify_admin_token(x_license_admin_key="invalid-key")

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Invalid license admin key"
