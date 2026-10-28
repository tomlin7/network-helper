"""Loopback HTTP/CONNECT proxy with bounded public-DNS resolution. Stdlib only."""
import argparse
import asyncio
import ctypes
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import struct
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SERVICE = "network-helper-v1"
MAX_CLIENTS = 32
MAX_HEADER = 16384
JOB = None


def contain_memory():
    """One process, 256 MiB hard Windows Job limit; no restart/spawn loop."""
    global JOB
    if os.name != "nt":
        return
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", w.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                    ("count", w.DWORD), ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("a", "b", "c", "d", "e", "f")]
    class Limits(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.GetCurrentProcess.restype = w.HANDLE
    JOB = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x2000 | 0x200 | 0x8
    limits.basic.count = 1
    limits.job_memory = 256 * 1024 * 1024
    if not JOB or not kernel.SetInformationJobObject(JOB, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(JOB, kernel.GetCurrentProcess()):
        raise RuntimeError("Could not install memory/process limits; startup cancelled")


def dns_packet(domain, identifier):
    labels = domain.rstrip('.').encode('idna').split(b'.')
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise ValueError("Invalid hostname")
    name = b''.join(bytes([len(label)]) + label for label in labels) + b'\0'
    if len(name) > 255:
        raise ValueError("Hostname too long")
    question = name + struct.pack('!HH', 1, 1)
    return struct.pack('!HHHHHH', identifier, 0x0100, 1, 0, 0, 0) + question


def skip_name(data, offset):
    for _ in range(128):
        if offset >= len(data):
            raise ValueError("Truncated DNS name")
        size = data[offset]
        if size & 0xc0 == 0xc0:
            if offset + 1 >= len(data):
                raise ValueError("Truncated DNS pointer")
            return offset + 2
        if size > 63:
            raise ValueError("Invalid DNS label")
        offset += 1
        if size == 0:
            return offset
        offset += size
    raise ValueError("DNS name exceeds limit")


def dns_answers(data, query):
    if len(data) < 12:
        raise ValueError("Truncated DNS reply")
    identifier, flags, questions, answers, _, _ = struct.unpack('!HHHHHH', data[:12])
    if identifier != struct.unpack('!H', query[:2])[0] or flags & 0x820f != 0x8000 or questions != 1 or answers > 64:
        raise ValueError("Invalid DNS reply")
    offset = skip_name(data, 12) + 4
    if data[12:offset] != query[12:]:
        raise ValueError("Mismatched DNS question")
    results, ttl = [], 300
    for _ in range(answers):
        offset = skip_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("Truncated DNS record")
        kind, family, seconds, length = struct.unpack('!HHIH', data[offset:offset+10])
        offset += 10
        if offset + length > len(data):
            raise ValueError("Truncated DNS value")
        if kind == 1 and family == 1 and length == 4:
            address = socket.inet_ntoa(data[offset:offset+4])
            if ipaddress.ip_address(address).is_global:
                results.append(address)
                ttl = min(ttl, seconds)
        offset += length
    if not results:
        raise OSError("No usable public IPv4 DNS answer")
    return list(dict.fromkeys(results)), max(1, ttl)


class Resolver:
    def __init__(self, servers=('8.8.8.8', '1.1.1.1')):
        self.servers, self.cache, self.pending = servers, {}, {}

    async def resolve(self, host):
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            pass
        host = host.rstrip('.').lower()
        cached = self.cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        if host in self.pending:
            return await asyncio.shield(self.pending[host])
        task = asyncio.create_task(self.query(host))
        self.pending[host] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done():
                self.pending.pop(host, None)

    async def query(self, host):
        loop = asyncio.get_running_loop()
        packet = dns_packet(host, secrets.randbits(16))
        for server in self.servers:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.setblocking(False)
                    await loop.sock_connect(sock, (server, 53))
                    await loop.sock_sendall(sock, packet)
                    data = await asyncio.wait_for(loop.sock_recv(sock, 4096), 2.5)
                addresses, ttl = dns_answers(data, packet)
                if len(self.cache) >= 2048:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[host] = (time.monotonic() + ttl, addresses)
                return addresses
            except (OSError, ValueError, asyncio.TimeoutError):
                continue
        raise OSError("Public DNS unavailable; refusing the network's placeholder DNS answer")


class Proxy:
    def __init__(self, port):
        self.port, self.resolver, self.clients = port, Resolver(), set()

    def accept(self, reader, writer):
        if len(self.clients) >= MAX_CLIENTS:
            writer.write(b'HTTP/1.1 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            writer.close()
            return
        task = asyncio.create_task(self.handle(reader, writer))
        self.clients.add(task)
        task.add_done_callback(self.clients.discard)

    async def reply(self, writer, status, body):
        payload = body.encode()
        writer.write(f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode() + payload)
        await writer.drain()

    async def handle(self, reader, writer):
        remote = None
        tunnel = False
        try:
            header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 10)
            if len(header) > MAX_HEADER:
                raise ValueError("Header too large")
            lines = header.decode('iso-8859-1').split('\r\n')
            method, target, version = lines[0].split(' ')
            if version not in ('HTTP/1.0', 'HTTP/1.1'):
                raise ValueError("Unsupported HTTP version")
            if method == 'GET' and target == '/health':
                await self.reply(writer, '200 OK', json.dumps({'service': SERVICE, 'pid': os.getpid(), 'connections': len(self.clients), 'maxConnections': MAX_CLIENTS, 'memoryCapMB': 256}))
                return
            if method == 'CONNECT':
                authority = urlsplit('//' + target)
                host, port = authority.hostname, authority.port or 443
                if authority.username or authority.password or authority.path:
                    raise ValueError("Invalid CONNECT authority")
            else:
                url = urlsplit(target)
                if url.scheme != 'http' or url.username or url.password:
                    raise ValueError("Use CONNECT for HTTPS")
                host, port = url.hostname, url.port or 80
            if not host or not 1 <= port <= 65535:
                raise ValueError("Invalid target")
            addresses = await self.resolver.resolve(host)
            incoming = None
            for address in addresses[:3]:
                try:
                    incoming, remote = await asyncio.wait_for(asyncio.open_connection(address, port, limit=MAX_HEADER), 8)
                    break
                except (OSError, asyncio.TimeoutError):
                    continue
            if incoming is None:
                raise OSError("Destination connection failed")
            if method == 'CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                tunnel = True
            else:
                request_path = url.path or '/'
                if url.query:
                    request_path += '?' + url.query
                forwarded = [f'{method} {request_path} {version}']
                for line in lines[1:]:
                    if line and line.split(':', 1)[0].lower() not in ('proxy-connection', 'proxy-authorization', 'connection'):
                        forwarded.append(line)
                remote.write(('\r\n'.join(forwarded) + '\r\nConnection: close\r\n\r\n').encode('iso-8859-1'))
                await remote.drain()
                tunnel = True
            async def pump(source, destination):
                while True:
                    chunk = await asyncio.wait_for(source.read(32768), 300)
                    if not chunk:
                        if destination.can_write_eof():
                            destination.write_eof()
                        return
                    destination.write(chunk)
                    await destination.drain()
            flows = [asyncio.create_task(pump(reader, remote)), asyncio.create_task(pump(incoming, writer))]
            try:
                await asyncio.gather(*flows)
            finally:
                for flow in flows:
                    flow.cancel()
                await asyncio.gather(*flows, return_exceptions=True)
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as error:
            logging.warning('Request failed: %s', type(error).__name__)
            if not tunnel:
                try:
                    await self.reply(writer, '502 Bad Gateway', '{"error":"DNS or destination unavailable; see network-helper.log"}')
                except OSError:
                    pass
        finally:
            for stream in (remote, writer):
                if stream:
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 2)
                    except (OSError, asyncio.TimeoutError):
                        pass

    async def run(self):
        # OS bind is the single-instance guard. An occupied port fails once.
        server = await asyncio.start_server(self.accept, '127.0.0.1', self.port, limit=MAX_HEADER, backlog=16)
        logging.info('Listening on 127.0.0.1:%d; max clients=%d; RAM cap=256 MiB', self.port, MAX_CLIENTS)
        async with server:
            await server.serve_forever()


if __name__ == '__main__':
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--port', type=int, default=18888)
    options = args.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[RotatingFileHandler(ROOT / 'network-helper.log', maxBytes=524288, backupCount=2)])
    try:
        contain_memory()
        asyncio.run(Proxy(options.port).run())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception('Helper stopped; no automatic restart loop')
        raise
