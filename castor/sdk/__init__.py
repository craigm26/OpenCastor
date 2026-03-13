"""castor.sdk — OpenAPI Python client SDK for the OpenCastor RCAN broker.

Usage::

    from castor.sdk.client import CastorClient

    client = CastorClient(base_url="http://localhost:8000", token="...")
    health = client.get_health()
"""
