"""Shared DoIP protocol constants, dataclasses and framing helpers."""

from dataclasses import dataclass

DOIP_PORT = 13400
DOIP_HEADER_SIZE = 8
PROTOCOL_VERSION = 0x02

PT_VEHICLE_IDENT_REQUEST = 0x0001
PT_VEHICLE_IDENT_RESPONSE = 0x0004
PT_ROUTING_ACTIVATION_REQUEST = 0x0005
PT_ROUTING_ACTIVATION_RESPONSE = 0x0006
PT_ALIVE_CHECK_REQUEST = 0x0007
PT_ALIVE_CHECK_RESPONSE = 0x0008
PT_DIAGNOSTIC_MESSAGE = 0x8001
PT_DIAGNOSTIC_POSITIVE_ACK = 0x8002
PT_DIAGNOSTIC_NEGATIVE_ACK = 0x8003

TESTER_ADDRESS = 0x0E80
GATEWAY_ADDRESS = 0x1000
ECU_ADDRESS = 0x2001


@dataclass(frozen=True)
class DoIPMessage:
    """A parsed DoIP message."""

    version: int
    inverse_version: int
    payload_type: int
    payload: bytes


def build_frame(payload_type: int, payload: bytes) -> bytes:
    """Build a complete DoIP frame."""

    header = struct_pack_header(payload_type, len(payload))
    return header + payload


def struct_pack_header(payload_type: int, payload_length: int) -> bytes:
    """Build the fixed eight-byte DoIP header."""

    import struct

    return struct.pack(
        "!BBHI",
        PROTOCOL_VERSION,
        (~PROTOCOL_VERSION) & 0xFF,
        payload_type,
        payload_length,
    )


def parse_frame(data: bytes) -> DoIPMessage:
    """Parse a complete DoIP frame."""

    import struct

    if len(data) < DOIP_HEADER_SIZE:
        raise ValueError("Truncated DoIP frame.")

    version, inverse_version, payload_type, payload_length = struct.unpack(
        "!BBHI",
        data[:DOIP_HEADER_SIZE],
    )

    payload = data[DOIP_HEADER_SIZE:]

    if len(payload) != payload_length:
        raise ValueError("Invalid DoIP payload length.")

    return DoIPMessage(
        version=version,
        inverse_version=inverse_version,
        payload_type=payload_type,
        payload=payload,
    )
