"""Local replay driver for the grid-tools spec (design §15.4).

``gateway/local`` holds the offline, deterministic replay driver that reads the
committed storm fixture and drives the tools end to end against the ``local``
backend — no sockets, no ``boto3``. It exists so the safety-critical behaviours
(dedupe, flood transitions, the energise veto, one dispatch-to-approval cycle,
outage closure) can be demonstrated and asserted on a laptop with no AWS account
(R17.1, R17.5, R18.1, R18.3).
"""
