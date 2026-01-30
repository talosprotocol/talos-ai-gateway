#!/usr/bin/env python3
"""
Gateway Performance Benchmark Suite

Measures p50/p95/p99 latencies for critical operations:
- Authentication
- Tool calls (MCP)
- A2A session management
- Database operations (read/write)

Usage:
    python scripts/benchmark_gateway.py --url http://localhost:8000 --duration 60
"""

import argparse
import asyncio
import statistics
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List
import aiohttp
import uuid


class BenchmarkResults:
    """Track latency metrics."""
    
    def __init__(self):
        self.latencies: List[float] = []
        self.errors: List[str] = []
        self.start_time = time.time()
        
    def add(self, latency_ms: float):
        self.latencies.append(latency_ms)
        
    def add_error(self, error: str):
        self.errors.append(error)
        
    def stats(self) -> Dict:
        if not self.latencies:
            return {"error": "No successful requests"}
            
        latencies = sorted(self.latencies)
        return {
            "count": len(latencies),
            "errors": len(self.errors),
            "duration_sec": time.time() - self.start_time,
            "throughput_rps": len(latencies) / (time.time() - self.start_time),
            "latency_ms": {
                "min": min(latencies),
                "max": max(latencies),
                "mean": statistics.mean(latencies),
                "median": statistics.median(latencies),
                "p50": latencies[int(len(latencies) * 0.50)],
                "p95": latencies[int(len(latencies) * 0.95)],
                "p99": latencies[int(len(latencies) * 0.99)],
            }
        }


async def benchmark_auth(session: aiohttp.ClientSession, url: str, results: BenchmarkResults):
    """Benchmark authentication endpoint."""
    start = time.time()
    try:
        async with session.post(
            f"{url}/v1/auth",
            json={
                "did": f"did:key:test-{uuid.uuid4().hex[:8]}",
                "signature": "dummy-sig-for-benchmarking"
            },
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            await resp.text()
            latency_ms = (time.time() - start) * 1000
            results.add(latency_ms)
    except Exception as e:
        results.add_error(str(e))


async def benchmark_tool_call(session: aiohttp.ClientSession, url: str, token: str, results: BenchmarkResults):
    """Benchmark MCP tool call endpoint."""
    start = time.time()
    try:
        async with session.post(
            f"{url}/v1/mcp/call-tool",
            json={
                "server_id": "test-server",
                "tool_name": "echo",
                "arguments": {"message": "benchmark"}
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            await resp.text()
            latency_ms = (time.time() - start) * 1000
            results.add(latency_ms)
    except Exception as e:
        results.add_error(str(e))


async def benchmark_a2a_session(session: aiohttp.ClientSession, url: str, token: str, results: BenchmarkResults):
    """Benchmark A2A session creation."""
    start = time.time()
    try:
        async with session.post(
            f"{url}/v1/a2a/sessions",
            json={
                "peer_did": f"did:key:peer-{uuid.uuid4().hex[:8]}",
                "metadata": {"purpose": "benchmark"}
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            await resp.text()
            latency_ms = (time.time() - start) * 1000
            results.add(latency_ms)
    except Exception as e:
        results.add_error(str(e))


async def benchmark_health_check(session: aiohttp.ClientSession, url: str, results: BenchmarkResults):
    """Benchmark health check endpoint."""
    start = time.time()
    try:
        async with session.get(
            f"{url}/health/ready",
            timeout=aiohttp.ClientTimeout(total=2)
        ) as resp:
            await resp.text()
            latency_ms = (time.time() - start) * 1000
            results.add(latency_ms)
    except Exception as e:
        results.add_error(str(e))


async def run_benchmark(url: str, duration_sec: int, concurrency: int):
    """Run all benchmarks."""
    print(f"Starting benchmark: {url}")
    print(f"Duration: {duration_sec}s, Concurrency: {concurrency}")
    print("-" * 60)
    
    results = {
        "health_check": BenchmarkResults(),
        "auth": BenchmarkResults(),
        # Skip tool calls and A2A if auth not working
    }
    
    async with aiohttp.ClientSession() as session:
        # 1. Health Check Benchmark
        print("\n1. Health Check Benchmark...")
        end_time = time.time() + duration_sec
        tasks = []
        while time.time() < end_time:
            if len(tasks) < concurrency:
                tasks.append(benchmark_health_check(session, url, results["health_check"]))
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks = []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        print_results("Health Check", results["health_check"])
        
        # 2. Auth Benchmark
        print("\n2. Auth Benchmark...")
        end_time = time.time() + duration_sec
        tasks = []
        while time.time() < end_time:
            if len(tasks) < concurrency:
                tasks.append(benchmark_auth(session, url, results["auth"]))
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks = []
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        print_results("Auth", results["auth"])
        
        # Note: Tool calls and A2A would require valid auth setup
        # Skipping for now unless gateway is running with test auth


def print_results(name: str, results: BenchmarkResults):
    """Print benchmark results."""
    stats = results.stats()
    
    if "error" in stats:
        print(f"❌ {name}: {stats['error']}")
        return
        
    print(f"\n✅ {name} Results:")
    print(f"   Requests: {stats['count']} ({stats['errors']} errors)")
    print(f"   Duration: {stats['duration_sec']:.2f}s")
    print(f"   Throughput: {stats['throughput_rps']:.2f} req/sec")
    print(f"   Latency (ms):")
    print(f"      Min:    {stats['latency_ms']['min']:.2f}")
    print(f"      Median: {stats['latency_ms']['median']:.2f}")
    print(f"      Mean:   {stats['latency_ms']['mean']:.2f}")
    print(f"      p50:    {stats['latency_ms']['p50']:.2f}")
    print(f"      p95:    {stats['latency_ms']['p95']:.2f}")
    print(f"      p99:    {stats['latency_ms']['p99']:.2f}")
    print(f"      Max:    {stats['latency_ms']['max']:.2f}")


def main():
    parser = argparse.ArgumentParser(description="Gateway Performance Benchmark")
    parser.add_argument("--url", default="http://localhost:8000", help="Gateway URL")
    parser.add_argument("--duration", type=int, default=30, help="Duration per benchmark (seconds)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent requests")
    
    args = parser.parse_args()
    
    print("="*60)
    print("Gateway Performance Benchmark Suite")
    print("="*60)
    print(f"Target: {args.url}")
    print(f"Started: {datetime.now()}")
    
    asyncio.run(run_benchmark(args.url, args.duration, args.concurrency))
    
    print("\n" + "="*60)
    print("Benchmark Complete")
    print("="*60)


if __name__ == "__main__":
    main()
