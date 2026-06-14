import time

import ollama

# Same prompts as vllm_quickstart.py for an apples-to-apples comparison.
prompts = [
    "Hello, my name is",
    "The president of the United States is",
    "The capital of France is",
    "The future of AI is",
]

# Ollama model to use. "tinyllama" is a small model comparable in size to
# facebook/opt-125m used in the vLLM version.  Run `ollama pull tinyllama`
# before executing this script.
MODEL = "tinyllama"


def main():
    # Ollama loads the model on the first request, so time everything from
    # the very first generate call to stay comparable to the vLLM numbers.
    first_start = time.perf_counter()
    outputs = []

    for i, prompt in enumerate(prompts):
        call_start = time.perf_counter()
        response = ollama.generate(model=MODEL, prompt=prompt)
        call_time = time.perf_counter() - call_start

        outputs.append((prompt, response["response"], call_time))

        # First call includes model load; mark it so timing is transparent.
        if i == 0:
            load_plus_first = call_time

    total_time = time.perf_counter() - first_start

    print("\nGenerated Outputs:\n" + "-" * 60)
    for prompt, generated_text, _ in outputs:
        print(f"Prompt:    {prompt!r}")
        print(f"Output:    {generated_text!r}")
        print("-" * 60)

    per_prompt_times = [t for _, _, t in outputs]
    print(f"\n[Ollama Timing]")
    print(f"  Call 1 (incl. model load) : {load_plus_first:.2f}s")
    for i, t in enumerate(per_prompt_times[1:], start=2):
        print(f"  Call {i}                   : {t:.3f}s")
    print(f"  Total ({len(prompts)} prompts, sequential) : {total_time:.2f}s")
    print()
    print("Note: Ollama processes prompts one at a time (sequential).")
    print("      vLLM batches all prompts in a single forward pass.")


if __name__ == "__main__":
    main()
