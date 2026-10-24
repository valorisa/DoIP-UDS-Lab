"""Minimal asynchronous mock DoIP gateway and UDS ECU."""

import asyncio
import struct

from doip_uds_lab.protocol import (
    DOIP_HEADER_SIZE,
    DOIP_PORT,
    ECU_ADDRESS,
    GATEWAY_ADDRESS,
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


class MockEcu:
    """Very small UDS ECU simulator."""

    def __init__(self) -> None:
        self.pending_response: bytes | None = None
        self.expected_block_counter = 0x01

    async def handle_uds(self, request: bytes) -> bytes | None:
        if not request:
            return bytes([0x7F, 0x00, 0x13])

        sid = request[0]

        if sid == 0x3E and len(request) >= 2 and request[1] & 0x80:
            return None

        if request[:3] == bytes.fromhex("19 02 08"):
            return bytes.fromhex("59 02 37 C1 23 87 09")

        if request[:2] == bytes.fromhex("10 03"):
            return bytes.fromhex("50 03 00 32 01 F4")

        if request[:4] == bytes.fromhex("31 01 FF 00"):
            self.pending_response = bytes.fromhex("71 01 FF 00 00")
            return bytes([0x7F, 0x31, 0x78])

        if sid == 0x36:
            return self._handle_transfer_data(request)

        return bytes([0x7F, sid, 0x31])

    def _handle_transfer_data(self, request: bytes) -> bytes:
        if len(request) < 2:
            return bytes([0x7F, 0x36, 0x13])

        block_counter = request[1]

        if block_counter != self.expected_block_counter:
            self.expected_block_counter = 0x01
            return bytes([0x7F, 0x36, 0x73])

        response = bytes([0x76, block_counter])
        self.expected_block_counter += 1

        if self.expected_block_counter > 0xFF:
            self.expected_block_counter = 0x01

        return response


class MockDoIPServer:
    """TCP server emulating a DoIP gateway and one ECU."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DOIP_PORT,
        pending_delay: float = 1.0,
    ) -> None:
        self.host = host
        self.port = port
        self.pending_delay = pending_delay
        self.ecu = MockEcu()
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )

        print(f"Mock DoIP gateway listening on {self.host}:{self.port}")
        print(f"Simulated ECU address: 0x{ECU_ADDRESS:04X}")

        async with self.server:
            await self.server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        print(f"Client connected: {peer}")

        routing_active = False

        try:
            while True:
                header = await reader.readexactly(DOIP_HEADER_SIZE)
                version, inverse, payload_type, length = struct.unpack(
                    "!BBHI",
                    header,
                )

                payload = await reader.readexactly(length) if length else b""
                message = parse_frame(header + payload)

                if payload_type == PT_ALIVE_CHECK_REQUEST:
                    await self._send(
                        writer,
                        PT_ALIVE_CHECK_RESPONSE,
                        struct.pack("!H", TESTER_ADDRESS),
                    )
                    continue

                if payload_type == PT_ROUTING_ACTIVATION_REQUEST:
                    routing_active = await self._handle_routing_activation(
                        writer,
                        message,
                    )
                    continue

                if payload_type != PT_DIAGNOSTIC_MESSAGE:
                    continue

                if not routing_active:
                    await self._send_diagnostic_nack(
                        writer,
                        message,
                        0x05,
                    )
                    continue

                await self._handle_diagnostic_message(writer, message)

        except (
            asyncio.IncompleteReadError,
            ConnectionResetError,
            BrokenPipeError,
        ):
            print(f"Client disconnected: {peer}")

        finally:
            writer.close()

            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def _handle_routing_activation(
        self,
        writer: asyncio.StreamWriter,
        message: object,
    ) -> bool:
        payload = message.payload  # type: ignore[attr-defined]

        if len(payload) < 7:
            await self._send_routing_response(writer, 0x02)
            return False

        source, _activation_type, _reserved = struct.unpack(
            "!HBI",
            payload[:7],
        )

        if source != TESTER_ADDRESS:
            await self._send_routing_response(writer, 0x02)
            return False

        await self._send_routing_response(writer, 0x10)
        print(f"Routing activated for tester 0x{source:04X}.")
        return True

    async def _handle_diagnostic_message(
        self,
        writer: asyncio.StreamWriter,
        message: object,
    ) -> None:
        payload = message.payload  # type: ignore[attr-defined]

        if len(payload) < 5:
            await self._send_diagnostic_nack(
                writer,
                message,
                0x02,
            )
            return

        source, target = struct.unpack("!HH", payload[:4])
        uds_request = payload[4:]

        if target != ECU_ADDRESS:
            await self._send_diagnostic_nack(
                writer,
                message,
                0x02,
            )
            return

        await self._send_diagnostic_ack(writer, source, target)

        uds_response = await self.ecu.handle_uds(uds_request)

        if uds_response is None:
            return

        if uds_response == bytes([0x7F, 0x31, 0x78]):
            await asyncio.sleep(self.pending_delay)
            uds_response = self.ecu.pending_response or b""

        await self._send_diagnostic_response(
            writer,
            source,
            target,
            uds_response,
        )

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        payload_type: int,
        payload: bytes,
    ) -> None:
        writer.write(build_frame(payload_type, payload))
        await writer.drain()

    async def _send_routing_response(
        self,
        writer: asyncio.StreamWriter,
        code: int,
    ) -> None:
        payload = struct.pack(
            "!HHB",
            GATEWAY_ADDRESS,
            TESTER_ADDRESS,
            code,
        )

        await self._send(
            writer,
            PT_ROUTING_ACTIVATION_RESPONSE,
            payload,
        )

    async def _send_diagnostic_ack(
        self,
        writer: asyncio.StreamWriter,
        source: int,
        target: int,
    ) -> None:
        payload = struct.pack(
            "!HHB",
            source,
            target,
            0x00,
        )

        await self._send(
            writer,
            PT_DIAGNOSTIC_POSITIVE_ACK,
            payload,
        )

    async def _send_diagnostic_nack(
        self,
        writer: asyncio.StreamWriter,
        message: object,
        code: int,
    ) -> None:
        payload = message.payload  # type: ignore[attr-defined]

        if len(payload) >= 4:
            source, target = struct.unpack("!HH", payload[:4])
        else:
            source, target = TESTER_ADDRESS, ECU_ADDRESS

        response_payload = struct.pack(
            "!HHB",
            source,
            target,
            code,
        )

        await self._send(
            writer,
            PT_DIAGNOSTIC_NEGATIVE_ACK,
            response_payload,
        )

    async def _send_diagnostic_response(
        self,
        writer: asyncio.StreamWriter,
        source: int,
        target: int,
        uds_response: bytes,
    ) -> None:
        payload = struct.pack("!HH", target, source) + uds_response

        await self._send(
            writer,
            PT_DIAGNOSTIC_MESSAGE,
            payload,
        )


async def main() -> None:
    server = MockDoIPServer()
    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMock DoIP server stopped.")
