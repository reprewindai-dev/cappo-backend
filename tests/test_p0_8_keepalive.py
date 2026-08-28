"""Adversarial tests for P0-8 (Warm model + persistent transport/resource lifecycle)."""

from __future__ import annotations

import time

import httpx

from cappo_backend.config import Settings
from cappo_backend.services.providers import OllamaExecutor


class MockState:
    def __init__(self, keep_alive=300):
        self.settings = Settings(_env_file=None, ollama_keep_alive=keep_alive)
        self.drain_active = False
        self.last_ollama_request_time = None

class MockApp:
    def __init__(self, keep_alive=300):
        self.state = MockState(keep_alive)

def test_local_keep_alive_under_normal_conditions() -> None:
    app = MockApp(keep_alive=300)
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3",
            "message": {"role": "assistant", "content": "ok"}
        })

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    
    exec_inst = OllamaExecutor(
        base_url="http://ollama.test",
        model="llama3",
        app=app,
        is_local=True,
        local_ollama_enabled=True,
        client=client,
    )
    exec_inst._bypass_local_raise = True
    
    # 1. Under normal conditions, memory is healthy, no recent demand -> default keep_alive (300)
    import psutil
    original_vm = psutil.virtual_memory
    class DummyMemory:
        percent = 50.0
    psutil.virtual_memory = lambda: DummyMemory()

    try:
        exec_inst.execute({"prompt": "hello"})
        assert captured["body"]["keep_alive"] == 300
    finally:
        psutil.virtual_memory = original_vm

def test_local_keep_alive_under_memory_pressure() -> None:
    app = MockApp(keep_alive=300)
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3",
            "message": {"role": "assistant", "content": "ok"}
        })

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    
    exec_inst = OllamaExecutor(
        base_url="http://ollama.test",
        model="llama3",
        app=app,
        is_local=True,
        local_ollama_enabled=True,
        client=client,
    )
    exec_inst._bypass_local_raise = True
    
    # Under memory pressure (>85%), keep_alive must be 0 (unload)
    import psutil
    original_vm = psutil.virtual_memory
    class DummyMemory:
        percent = 90.0
    psutil.virtual_memory = lambda: DummyMemory()

    try:
        exec_inst.execute({"prompt": "hello"})
        assert captured["body"]["keep_alive"] == 0
    finally:
        psutil.virtual_memory = original_vm

def test_local_keep_alive_under_idle_conditions() -> None:
    app = MockApp(keep_alive=300)
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3",
            "message": {"role": "assistant", "content": "ok"}
        })

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    
    exec_inst = OllamaExecutor(
        base_url="http://ollama.test",
        model="llama3",
        app=app,
        is_local=True,
        local_ollama_enabled=True,
        client=client,
    )
    exec_inst._bypass_local_raise = True
    
    # Inject a last request time that is older than keep_alive (e.g. 400s ago)
    app.state.last_ollama_request_time = time.time() - 400
    
    import psutil
    original_vm = psutil.virtual_memory
    class DummyMemory:
        percent = 50.0
    psutil.virtual_memory = lambda: DummyMemory()

    try:
        exec_inst.execute({"prompt": "hello"})
        assert captured["body"]["keep_alive"] == 0
    finally:
        psutil.virtual_memory = original_vm

def test_local_keep_alive_under_drain_conditions() -> None:
    app = MockApp(keep_alive=300)
    captured = {}

    def handler(request):
        import json
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "model": "llama3",
            "message": {"role": "assistant", "content": "ok"}
        })

    client = httpx.Client(base_url="http://ollama.test", transport=httpx.MockTransport(handler))
    
    exec_inst = OllamaExecutor(
        base_url="http://ollama.test",
        model="llama3",
        app=app,
        is_local=True,
        local_ollama_enabled=True,
        client=client,
    )
    exec_inst._bypass_local_raise = True
    
    # Active drain/kill switch
    app.state.drain_active = True
    
    import psutil
    original_vm = psutil.virtual_memory
    class DummyMemory:
        percent = 50.0
    psutil.virtual_memory = lambda: DummyMemory()

    try:
        exec_inst.execute({"prompt": "hello"})
        assert captured["body"]["keep_alive"] == 0
    finally:
        psutil.virtual_memory = original_vm
