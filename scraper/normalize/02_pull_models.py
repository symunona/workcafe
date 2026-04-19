#!/usr/bin/env python3
"""
02_pull_models.py — Download required ollama models.

Models:
  nomic-embed-text  274MB  768-dim embeddings
  qwen2.5:1.5b      ~1GB   multilingual (Korean), translation + chain detection

Total VRAM: ~1.3GB of ~3GB available. Safe.
"""
import subprocess
import sys


MODELS = [
    ("nomic-embed-text", "embeddings (768-dim)"),
    ("qwen2.5:1.5b", "multilingual LLM (Korean/English)"),
]


def pull(model, desc):
    print(f"\nPulling {model} — {desc}")
    result = subprocess.run(["ollama", "pull", model], capture_output=False)
    if result.returncode != 0:
        print(f"ERROR pulling {model}", file=sys.stderr)
        return False
    print(f"OK: {model}")
    return True


if __name__ == "__main__":
    for model, desc in MODELS:
        pull(model, desc)
    print("\nDone. Verify with: ollama list")
