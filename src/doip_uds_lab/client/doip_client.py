"""Minimal asynchronous DoIP client for UDS requests."""

import asyncio
import struct
from dataclasses import dataclass

from doip_uds_lab.protocol import (
    DOIP_HEADER_SIZE,
    DOIP_PORT,
    ECU_ADDRESS,
    PT_ALIVE_CHECK_REQUEST,
    PT_ALIVE_CHECK_RESPONSE,
    PT_DIAGNOSTIC_MESSAGE,
    PT_DIAGNOSTIC_NEGATIVE_ACK,
    PT_DIAGNOSTIC_POSITIVE_ACK,
    PT_ROUTING_ACTIVATION_REQUEST,
    PT_ROUTING_ACTIVATION_RESPONSE,
    TESTER_ADDRESS,
    build_frame,
    parse_frame,
)


@dataclass(frozen=True)
class UdsResult:
    """Result of one UDS request."""

    request: bytes
    response: bytes


class DoIPClient:
    """Small asynchronous DoIP client for local testing."""

    def __init__(
        self,
        gateway_ip: str = "127.0.0.1",
        gateway_port: int = DOIP_PORT,
        tester_address: int = TESTER_ADDRESS,
    ) -> None:
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self.tester_address = tester_address

        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

        self.routing_active = asyncio.Event()
        self.closed = asyncio.Event()

        self.routing_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.ack_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.uds_queue: asyncio.Queue[bytes] = asyncio.Queue()

        self.reader_task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(
            self.gateway_ip,
            self.gateway_port,
        )

        self.closed.clear()
        self.reader_task = asyncio.create_task(self._reader_loop())

        await self._activate_routing()

    async def _activate_routing(self) -> None:
        payload = struct.pack(
            "!HBI",
            self.tester_address,
            0x00,
            0x00000000,
        )

        await self._send(
            PT_ROUTING_ACTIVATION_REQUEST,
            payload,
        )

        response = await asyncio.wait_for(
            self.routing_queue.get(),
            timeout=5.0,
        )

        _source, _target, routing_code = struct.unpack(
            "!HHB",
            response[:5],
        )

        if routing_code != 0x10:
            raise RuntimeError(
                f"DoIP routing activation refused: 0x{routing_code:02X}"
            )

        self.routing_active.set()

    async def send_uds(
        self,
        target_address: int = ECU_ADDRESS,
        uds_request: bytes = b"",
        timeout: float = 5.0,
    ) -> UdsResult:
        if not self.routing_active.is_set():
            raise RuntimeError("DoIP routing is not active.")

        while not self.ack_queue.empty():
            self.ack_queue.get_nowait()

        while not self.uds_queue.empty():
            self.uds_queue.get_nowait()

        payload = (
            struct.pack("!HH", self.tester_address, target_address)
            + uds_request
        )

        await self._send(PT_DIAGNOSTIC_MESSAGE, payload)

        ack_payload = await asyncio.wait_for(
            self.ack_queue.get(),
            timeout=1.0,
        )

        _source, _target, ack_code = struct.unpack("!HHB", ack_payload[:5])

        if ack_code != 0x00:
            raise RuntimeError(f"DoIP ACK refused: 0x{ack_code:02X}")

        current_timeout = timeout
        pending_count = 0

        while True:
            try:
                uds_response = await asyncio.wait_for(
                    self.uds_queue.get(),
                    timeout=current_timeout,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"UDS response timeout after {current_timeout:.3f}s"
                )

            if (
                len(uds_response) >= 3
                and uds_response[0] == 0x7F
                and uds_response[2] == 0x78
            ):
                pending_count += 1

                if pending_count > 255:
                    raise RuntimeError("Too many 0x78 responses.")

                current_timeout = 5.0
                continue

            return UdsResult(
                request=uds_request,
                response=uds_response,
            )

    async def _reader_loop(self) -> None:
        assert self.reader is not None

        try:
            while not self.closed.is_set():
                header = await self.reader.readexactly(DOIP_HEADER_SIZE)
                version, inverse, payload_type, length = struct.unpack(
                    "!BBHI",
                    header,
                )

                payload = await self.reader.readexactly(length) if length else b""
                message = parse_frame(header + payload)

                if payload_type == PT_ALIVE_CHECK_REQUEST:
                    asyncio.create_task(self._answer_alive_check())
                    continue

                if payload_type == PT_ROUTING_ACTIVATION_RESPONSE:
                    await self.routing_queue.put(message.payload)
                    continue

                if payload_type == PT_DIAGNOSTIC_POSITIVE_ACK:
                    await self.ack_queue.put(message.payload)
                    continue

                if payload_type == PT_DIAGNOSTIC_NEGATIVE_ACK:
                    await self.ack_queue.put(message.payload)
                    continue

                if payload_type == PT_DIAGNOSTIC_MESSAGE:
                    if len(message.payload) < 5:
                        continue

                    source, destination = struct.unpack(
                        "!HH",
                        message.payload[:4],
                    )

                    if destination != self.tester_address:
                        continue

                    await self.uds_queue.put(message.payload[4:])

        except (
            asyncio.IncompleteReadError,
            ConnectionResetError,
            BrokenPipeError,
        ):
            self.closed.set()
            self.routing_active.clear()

    async def _answer_alive_check(self) -> None:
        await self._send(
            PT_ALIVE_CHECK_RESPONSE,
            struct.pack("!H", self.tester_address),
        )

    async def _send(
        self,
        payload_type: int,
        payload: bytes,
    ) -> None:
        if self.writer is None or self.closed.is_set():
            raise ConnectionError("DoIP client is not connected.")

        self.writer.write(build_frame(payload_type, payload))
        await self.writer.drain()

    async def close(self) -> None:
        self.closed.set()
        self.routing_active.clear()

        if self.writer is not None:
            self.writer.close()

            try:
                await self.writer.wait_closed()
            except ConnectionError:
                pass

        if self.reader_task is not None:
            self.reader_task.cancel()

            try:
                await self.reader_task
            except asyncio.CancelledError:
                pass


async def main() -> None:
    client = DoIPClient()

    await client.connect()

    result = await client.send_uds(
        target_address=ECU_ADDRESS,
        uds_request=bytes.fromhex("19 02 08"),
    )

    print("UDS request :", result.request.hex(" ").upper())
    print("UDS response:", result.response.hex(" ").upper())

    result = await client.send_uds(
        target_address=ECU_ADDRESS,
        uds_request=bytes.fromhex("31 01 FF 00"),
    )

    print("UDS request :", result.request.hex(" ").upper())
    print("UDS response:", result.response.hex(" ").upper())

    await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nDoIP client stopped.")
