# Governed Value-Pool Packets

`cme.cfo_os.value_pools` is a generic adapter for moving evidence-backed procurement and working-capital opportunities into CFO or board decision packets.

Each packet requires:

- A decision statement, recommendation, and accountable decision owner
- One or more value pools with a supported domain, amount, currency, and value basis
- At least one evidence reference per pool, including source identity and confidence
- Explicit assumptions, risks, and release gates

The adapter rejects unsupported domains, missing evidence, invalid confidence values, and incomplete decision metadata. It emits the camelCase contract consumed by decision-packet applications. The contract is intentionally customer-neutral and does not imply that an estimate is realizable without passing its release gates.

```python
from cme.cfo_os import adapt_value_pool_packet

packet = adapt_value_pool_packet(source_mapping)
board_fragment = packet.to_dict()
```
