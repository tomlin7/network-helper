import asyncio
import json
import struct
import unittest
from network_helper import Proxy, dns_packet, dns_answers


class DNS(unittest.TestCase):
    def test_rejects_placeholder_truncation_and_wrong_transaction(self):
        query = dns_packet('api.github.com', 42)
        def answer(value):
            return struct.pack('!HHHHHH', 42, 0x8180, 1, 1, 0, 0) + query[12:] + b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 60, 4) + bytes(value)
        self.assertEqual(dns_answers(answer([20, 207, 73, 85]), query), (['20.207.73.85'], 60))
        with self.assertRaises(OSError):
            dns_answers(answer([192, 0, 2, 1]), query)
        with self.assertRaises(ValueError):
            dns_answers(answer([20, 207, 73, 85])[:-1], query)
        with self.assertRaises(ValueError):
            dns_answers(answer([20, 207, 73, 85]), dns_packet('api.github.com', 43))


class Flows(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.proxy = Proxy(0)
        self.server = await asyncio.start_server(self.proxy.accept, '127.0.0.1', 0, limit=16384)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.proxy.clients):
            task.cancel()
        await asyncio.gather(*self.proxy.clients, return_exceptions=True)

    async def exchange(self, request):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(request)
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), 3)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def test_health_and_invalid_request(self):
        response = await self.exchange(b'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n')
        self.assertEqual(json.loads(response.split(b'\r\n\r\n')[1])['maxConnections'], 32)
        self.assertIn(b'502 Bad Gateway', await self.exchange(b'invalid\r\n\r\n'))

    async def test_connection_cap_rejects_excess_without_spawning_workers(self):
        held = []
        try:
            for _ in range(32):
                held.append(await asyncio.open_connection('127.0.0.1', self.port))
            await asyncio.sleep(.02)
            try:
                response = await self.exchange(b'GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n')
                self.assertIn(b'503 Busy', response)
            except ConnectionResetError:
                pass  # Windows may reset a connection rejected before reading its request.
            self.assertLessEqual(len(self.proxy.clients), 32)
        finally:
            for _, writer in held:
                writer.close()
            await asyncio.gather(*(writer.wait_closed() for _, writer in held), return_exceptions=True)

    async def test_connect_is_bidirectional_and_survives_client_half_close(self):
        async def echo(reader, writer):
            value = await reader.read()
            writer.write(b'reply:' + value)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        target = await asyncio.start_server(echo, '127.0.0.1', 0)
        try:
            port = target.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
            writer.write(f'CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: localhost\r\n\r\n'.encode())
            self.assertIn(b'200 Connection Established', await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'opaque encrypted bytes')
            await writer.drain()
            writer.write_eof()
            self.assertEqual(await asyncio.wait_for(reader.read(), 3), b'reply:opaque encrypted bytes')
            writer.close()
            await writer.wait_closed()
        finally:
            target.close()
            await target.wait_closed()

    async def test_http_rewrites_absolute_uri_and_removes_proxy_credentials(self):
        seen = []
        async def serve(reader, writer):
            seen.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        target = await asyncio.start_server(serve, '127.0.0.1', 0)
        try:
            port = target.sockets[0].getsockname()[1]
            reply = await self.exchange(f'GET http://127.0.0.1:{port}/path?q=1 HTTP/1.1\r\nHost: localhost\r\nProxy-Authorization: private\r\n\r\n'.encode())
            self.assertTrue(reply.endswith(b'ok'))
            self.assertTrue(seen[0].startswith(b'GET /path?q=1 HTTP/1.1'))
            self.assertNotIn(b'private', seen[0])
        finally:
            target.close()
            await target.wait_closed()


if __name__ == '__main__':
    unittest.main()
