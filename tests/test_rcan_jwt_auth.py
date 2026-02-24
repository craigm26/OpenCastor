import os
from unittest.mock import patch

import pytest

pytest.importorskip("jwt", reason="PyJWT not installed")


def test_rcan_token_manager_uses_kid_and_previous_key():
    from castor.rcan.jwt_auth import RCANTokenManager
    from castor.rcan.rbac import RCANRole
    from castor.secret_provider import get_jwt_secret_provider

    provider = get_jwt_secret_provider()
    with patch.dict(
        os.environ,
        {
            "OPENCASTOR_JWT_SECRET": "active-secret",
            "OPENCASTOR_JWT_KID": "active-kid",
            "OPENCASTOR_JWT_PREVIOUS_SECRET": "old-secret",
            "OPENCASTOR_JWT_PREVIOUS_KID": "old-kid",
        },
        clear=False,
    ):
        provider.invalidate()
        mgr = RCANTokenManager(issuer="rcan://unit.test.00000001")
        old_mgr = RCANTokenManager(secret="old-secret", issuer="rcan://unit.test.00000001")

        old_token = old_mgr.issue("legacy-user", role=RCANRole.USER)
        principal = mgr.verify(old_token)

        assert principal.name == "legacy-user"
