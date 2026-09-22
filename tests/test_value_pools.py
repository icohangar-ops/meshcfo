from __future__ import annotations

import pytest

from cme.cfo_os import adapt_value_pool_packet


def _packet():
    return {
        "packetId": "vp-001",
        "decisionStatement": "Which governed value pools should enter the plan?",
        "recommendation": "Release the highest-confidence pool behind the listed gates.",
        "decisionOwner": "finance",
        "valuePools": [
            {
                "poolId": "pool-001",
                "name": "Terms and payment-cycle opportunity",
                "domain": "working_capital",
                "valueAmount": 125000,
                "valueCurrency": "USD",
                "valueBasis": "Verified invoice and payment history, trailing twelve months",
                "confidence": 0.84,
                "owner": "treasury",
                "evidence": [
                    {
                        "sourceId": "ledger-001",
                        "sourceType": "ledger_extract",
                        "asOf": "2026-06-30",
                        "confidence": 0.95,
                    }
                ],
                "assumptions": ["No material supplier dispute changes the baseline"],
                "risks": ["Operational change may delay realization"],
                "releaseGates": ["Confirm control owner and pilot scope"],
            }
        ],
    }


def test_adapter_returns_cfo_board_packet_with_evidence():
    packet = adapt_value_pool_packet(_packet())
    assert packet.to_dict()["valuePools"][0]["domain"] == "working_capital"
    assert packet.to_dict()["valuePools"][0]["evidence"][0]["sourceId"] == "ledger-001"


def test_adapter_rejects_unsupported_or_ungrounded_pool():
    data = _packet()
    data["valuePools"][0]["domain"] = "marketing"
    data["valuePools"][0]["evidence"] = []
    with pytest.raises(ValueError, match="domain must be one of|evidence"):
        adapt_value_pool_packet(data)
