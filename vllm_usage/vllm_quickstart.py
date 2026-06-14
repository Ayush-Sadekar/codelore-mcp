# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project

import time

from vllm import LLM, SamplingParams

# Sample prompts.
prompts = [
    "Hello, my name is",
    "The president of the United States is",
    "The capital of France is",
    "The future of AI is",
]
# Create a sampling params object.
sampling_params = SamplingParams(temperature=0.8, top_p=0.95)


def main():
    load_start = time.perf_counter()
    llm = LLM(model="mlx-community/Qwen2.5-0.5B-Instruct-4bit")
    load_time = time.perf_counter() - load_start

    gen_start = time.perf_counter()
    outputs = llm.generate(prompts, sampling_params)
    gen_time = time.perf_counter() - gen_start

    print("\nGenerated Outputs:\n" + "-" * 60)
    for output in outputs:
        prompt = output.prompt
        generated_text = output.outputs[0].text
        print(f"Prompt:    {prompt!r}")
        print(f"Output:    {generated_text!r}")
        print("-" * 60)

    print(f"\n[vLLM Timing]")
    print(f"  Model load : {load_time:.2f}s")
    print(f"  Generation : {gen_time:.3f}s  ({len(prompts)} prompts, batched)")
    print(f"  Total      : {load_time + gen_time:.2f}s")


if __name__ == "__main__":
    main()