//! InferenceEngine: a greedy Qwen2 text-generation + sentence-embedding engine
//! backed entirely by Candle on CPU. No Python, no CUDA, no external LLM.

use anyhow::{Context, Result};
use candle_core::{DType, Device, Tensor};
use candle_nn::VarBuilder;
use candle_transformers::models::qwen2::{Config, Model, ModelForCausalLM};
use tokenizers::tokenizer::{EncodeInput, Tokenizer};

pub struct InferenceEngine {
    model: ModelForCausalLM,
    base: Model,
    tokenizer: Tokenizer,
    device: Device,
    eos_ids: Vec<u32>,
    max_seq_len: usize,
    temperature: f32,
}

impl InferenceEngine {
    pub fn load(
        model_id: &str,
        revision: &str,
        device: &Device,
        max_seq_len: usize,
        temperature: f32,
    ) -> Result<Self> {
        let api = hf_hub::api::sync::Api::new().context("hf-hub api")?;
        let repo = api.repo(hf_hub::Repo::with_revision(
            model_id.to_string(),
            hf_hub::RepoType::Model,
            revision.to_string(),
        ));

        let config_path = repo.get("config.json").context("config.json download")?;
        let config: Config =
            serde_json::from_reader(std::io::BufReader::new(std::fs::File::open(&config_path)?))
                .context("config parse")?;
        let tokenizer_path = repo
            .get("tokenizer.json")
            .context("tokenizer.json download")?;
        let tokenizer = Tokenizer::from_file(&tokenizer_path)
            .map_err(|e| anyhow::anyhow!("tokenizer: {e:?}"))?;

        let dirs = config_path
            .parent()
            .map(|p| p.to_path_buf())
            .unwrap_or_default();
        // Ensure the weight files are materialised on disk before mmap.
        let _ = repo
            .get("model.safetensors")
            .or_else(|_| repo.get("model.safetensors.index.json"));
        let vb = load_vb(&dirs, DType::F32, device).context("weights load")?;

        let model = ModelForCausalLM::new(&config, vb.clone()).context("build LM")?;
        let base = Model::new(&config, vb).context("build base model")?;

        let mut eos_ids = vec![];
        for token in ["<|endoftext|>", "<|im_end|>"] {
            if let Some(id) = tokenizer.token_to_id(token) {
                eos_ids.push(id);
            }
        }

        Ok(InferenceEngine {
            model,
            base,
            tokenizer,
            device: device.clone(),
            eos_ids,
            max_seq_len,
            temperature,
        })
    }

    /// Greedy chat completion. Returns (text, prompt_tokens, completion_tokens).
    pub fn generate(
        &mut self,
        messages: &[ChatMessage],
        max_tokens: usize,
    ) -> Result<(String, usize, usize)> {
        let prompt = qwen_chat_template(messages);
        let prompt_tokens = self
            .tokenizer
            .encode(EncodeInput::Single(prompt.as_str().into()), true)
            .map_err(|e| anyhow::anyhow!("prompt encode: {e:?}"))?
            .get_ids()
            .to_vec();

        let max_gen = max_tokens.min(self.max_seq_len.saturating_sub(prompt_tokens.len()).max(1));
        let mut all_tokens = prompt_tokens.clone();
        let mut offset = 0usize;
        let mut generated = Vec::new();

        for _ in 0..max_gen {
            let input_ids: Vec<u32> = if offset == 0 {
                all_tokens.clone()
            } else {
                vec![*all_tokens.last().unwrap()]
            };
            let ctx_len = input_ids.len();
            let input = Tensor::new(input_ids, &self.device)?.unsqueeze(0)?;
            let logits = self.model.forward(&input, offset)?;
            let logits = logits.squeeze(0)?.to_dtype(DType::F32)?;
            let next = sample(&logits, self.temperature)?;
            offset += ctx_len;
            all_tokens.push(next);
            generated.push(next);
            if self.eos_ids.contains(&next) {
                break;
            }
        }

        let text = self
            .tokenizer
            .decode(&generated, true)
            .map_err(|e| anyhow::anyhow!("decode: {e:?}"))?
            .trim()
            .to_string();
        Ok((text, prompt_tokens.len(), generated.len()))
    }

    /// Mean-pooled, L2-normalised sentence embeddings. Returns (vectors, total_tokens).
    pub fn embed(&mut self, texts: &[String]) -> Result<(Vec<Vec<f32>>, usize)> {
        let mut vectors = Vec::with_capacity(texts.len());
        let mut total = 0usize;
        for text in texts {
            let ids = self
                .tokenizer
                .encode(EncodeInput::Single(text.clone().into()), true)
                .map_err(|e| anyhow::anyhow!("embed encode: {e:?}"))?
                .get_ids()
                .to_vec();
            total += ids.len();
            if ids.is_empty() {
                vectors.push(vec![0.0; 1024]);
                continue;
            }
            let ids: Vec<u32> = ids.iter().take(self.max_seq_len).copied().collect();
            let input = Tensor::new(ids, &self.device)?.unsqueeze(0)?;
            let hidden = self.base.forward(&input, 0, None)?;
            let hidden = hidden.squeeze(0)?; // [seq, dim]
            let mean = hidden.mean(0)?;
            let sq = (&mean * &mean)?.sum_all()?;
            let norm = sq.to_scalar::<f32>()?.sqrt().max(1e-9);
            let normalized = mean.affine(1.0 / norm as f64, 0.0)?.to_vec1::<f32>()?;
            vectors.push(normalized);
        }
        Ok((vectors, total))
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

/// Minimal Qwen2 ChatML template (no Jinja runtime required):
/// `<|im_start|>system ... <|im_end|> ... <|im_start|>assistant`
fn qwen_chat_template(messages: &[ChatMessage]) -> String {
    let mut out = String::new();
    for m in messages {
        let role = match m.role.as_str() {
            "system" => "system",
            "assistant" => "assistant",
            _ => "user",
        };
        out.push_str(&format!(
            "<|im_start|>{role}\n{}\n<|im_end|>\n",
            m.content.trim()
        ));
    }
    out.push_str("<|im_start|>assistant\n");
    out
}

fn sample(logits: &Tensor, temperature: f32) -> Result<u32> {
    let logits = if temperature > 1e-6 {
        logits.affine(1.0 / temperature as f64, 0.0)?
    } else {
        logits.clone()
    };
    let probs = candle_nn::ops::softmax(&logits, 0)?;
    Ok(probs.argmax(0)?.to_scalar::<u32>()?)
}

fn load_vb(dirs: &std::path::Path, dtype: DType, device: &Device) -> Result<VarBuilder<'static>> {
    let single = dirs.join("model.safetensors");
    let backend: candle_core::safetensors::MmapedSafetensors = if single.exists() {
        unsafe { candle_core::safetensors::MmapedSafetensors::new(&single) }
            .context("mmap model.safetensors")?
    } else {
        let index: Value = serde_json::from_reader(std::io::BufReader::new(std::fs::File::open(
            dirs.join("model.safetensors.index.json"),
        )?))
        .context("read safetensors index")?;
        let mut shards: Vec<String> = index["weight_map"]
            .as_object()
            .map(|m| {
                m.values()
                    .filter_map(|v| v.as_str().map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        shards.sort();
        shards.dedup();
        let paths: Vec<std::path::PathBuf> = shards.iter().map(|s| dirs.join(s)).collect();
        unsafe { candle_core::safetensors::MmapedSafetensors::multi(&paths) }
            .context("mmap sharded safetensors")?
    };
    Ok(VarBuilder::from_backend(
        Box::new(backend),
        dtype,
        device.clone(),
    ))
}

use serde_json::Value;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chat_template_renders_qwen_markup() {
        let out = qwen_chat_template(&[
            ChatMessage {
                role: "system".into(),
                content: "be brief".into(),
            },
            ChatMessage {
                role: "user".into(),
                content: "hi".into(),
            },
        ]);
        assert!(out.contains("<|im_start|>system\nbe brief\n<|im_end|>"));
        assert!(out.contains("<|im_start|>user\nhi\n<|im_end|>"));
        assert!(out.ends_with("<|im_start|>assistant\n"));
    }
}
