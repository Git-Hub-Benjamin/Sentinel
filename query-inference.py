#!/usr/bin/env python3
"""Quick test script to query Ollama inference."""

import json
import sys
import urllib.request
import urllib.error

OLLAMA_BASE = "http://localhost:11434"


def get_models():
    req = urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=5)
    data = json.loads(req.read())
    return [m["name"] for m in data.get("models", [])]


def query(model, prompt):
    payload = json.dumps({"model": model, "prompt": prompt, "stream": True}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        for line in resp:
            chunk = json.loads(line)
            print(chunk.get("response", ""), end="", flush=True)
            if chunk.get("done"):
                break
    print()


def main():
    # Check Ollama is up
    try:
        models = get_models()
    except urllib.error.URLError:
        print("ERROR: Ollama is not running (is inference paused by Sentinel?)")
        sys.exit(1)

    if not models:
        print("No models loaded in Ollama.")
        sys.exit(1)

    # Pick model
    print("Available models:")
    for i, m in enumerate(models):
        print(f"  [{i}] {m}")
    choice = input(f"Select model [0-{len(models)-1}] (default 0): ").strip()
    model = models[int(choice)] if choice.isdigit() else models[0]
    print(f"Using: {model}\n")

    # Query loop
    while True:
        try:
            prompt = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt in ("exit", "quit"):
            break
        try:
            query(model, prompt)
        except urllib.error.URLError as e:
            print(f"ERROR: {e}")


if __name__ == "__main__":
    main()
