from unsloth import FastLanguageModel
import torch

# 1. Configuration
model_name = "unsloth/gemma-4-E4B-it-unsloth-bnb-4bit" # Pre-quantized 4-bit version
max_seq_length = 2048 # Supports up to 128k, but lower saves RAM
load_in_4bit = True    # Use 4-bit quantization to reduce memory usage

# 2. Load model and tokenizer
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = model_name,
    max_seq_length = max_seq_length,
    load_in_4bit = load_in_4bit,
    device_map="cpu", # Uncomment if you have no GPU at all
)

# 3. Enable faster inference
FastLanguageModel.for_inference(model) 

# 4. Simple Inference Example
inputs = tokenizer(
    ["Explain how per-layer embeddings (PLE) work in Gemma 4."],
    return_tensors = "pt"
).to("cuda" if torch.cuda.is_available() else "cpu")

outputs = model.generate(**inputs, max_new_tokens = 128)
print(tokenizer.batch_decode(outputs))