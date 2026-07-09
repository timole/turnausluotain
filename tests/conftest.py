"""Lataa projektin juuren .env-tiedoston ympäristömuuttujiksi ennen testejä.

Jo asetettuja ympäristömuuttujia ei ylikirjoiteta.
"""

import os
from pathlib import Path


def _lataa_env() -> None:
    env_tiedosto = Path(__file__).resolve().parent.parent / ".env"
    if not env_tiedosto.exists():
        return
    for rivi in env_tiedosto.read_text(encoding="utf-8").splitlines():
        rivi = rivi.strip()
        if not rivi or rivi.startswith("#") or "=" not in rivi:
            continue
        avain, _, arvo = rivi.partition("=")
        os.environ.setdefault(avain.strip(), arvo.strip())


_lataa_env()
