from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMqttMessage:
    kind: str
    topic: str
    payload: bytes
