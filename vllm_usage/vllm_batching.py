from vllm import LLM, SamplingParams

class VLLMBatching:
    def __init__(self, prompt_template: str, model_name: str = "mlx-community/Qwen2.5-35B-A3B-Instruct-4bit"):
        self.prompt_template = prompt_template
        self.model_name = model_name
        self.llm = LLM(model=self.model_name)
        self.sampling_params = SamplingParams(temperature=0.8, top_p=0.95)

    def generate(self, prompts: list[str]) -> list[str]:
        # for our use case, the prompt template will add the code in the brackets 
        prompts = [self.prompt_template.format(prompt) for prompt in prompts]
        outputs = self.llm.generate(prompts, self.sampling_params)
        return [output.outputs[0].text for output in outputs]