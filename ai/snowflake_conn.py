"""Snowflake connections for the AI code and the app, using key-pair auth.

The private key comes from SNOWFLAKE_PRIVATE_KEY_PATH (a file, for local runs and Airflow) or
SNOWFLAKE_PRIVATE_KEY (the PEM text, for Streamlit Cloud secrets).
"""

from __future__ import annotations

import os


def _private_key_der() -> bytes | None:
    pem = os.environ.get("SNOWFLAKE_PRIVATE_KEY")
    if not pem:
        return None
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(pem.encode(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(role: str | None = None, warehouse: str | None = None):
    import snowflake.connector

    kwargs = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=role or os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),
        warehouse=warehouse or os.environ.get("SNOWFLAKE_WAREHOUSE", "PIPELINE_WH"),
        database="VEHICLE_SAFETY",
    )
    der = _private_key_der()
    if der:
        kwargs["private_key"] = der
    else:
        kwargs["private_key_file"] = os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"])
    return snowflake.connector.connect(**kwargs)
