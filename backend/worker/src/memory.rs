//! Qdrant vector memory: hybrid retrieval (dense embeddings when an
//! OpenAI-compatible `/embeddings` endpoint is configured, full-text otherwise)
//! plus the snapshot engine. Replaces pgvector + BM25 with a Python-free,
//! Arrow-embedded vector store.

use anyhow::{Context, Result};
use serde_json::{json, Map, Value};
use std::time::Duration;

use crate::config::WorkerConfig;
use crate::llm::EmbeddingFn;

#[derive(Debug, Clone)]
pub struct MemoryHit {
    pub id: String,
    pub text: String,
    pub score: f64,
    pub payload: Map<String, Value>,
}

pub struct QdrantMemory {
    http: reqwest::Client,
    base: String,
    memory_collection: String,
    snapshot_collection: String,
    embed: Option<EmbeddingFn>,
}

impl QdrantMemory {
    pub async fn connect(cfg: &WorkerConfig, embed: Option<EmbeddingFn>) -> Result<QdrantMemory> {
        let m = QdrantMemory {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .build()
                .context("http client")?,
            base: cfg.qdrant_url.trim_end_matches('/').to_string(),
            memory_collection: cfg.qdrant_memory_collection.clone(),
            snapshot_collection: cfg.qdrant_snapshot_collection.clone(),
            embed,
        };
        m.ensure_collections().await?;
        Ok(m)
    }

    async fn ensure_collections(&self) -> Result<()> {
        let dims = 384usize;
        let body = json!({"vectors": {"size": dims, "distance": "Cosine"}});
        self.put_collection(&self.memory_collection, body)
            .await
            .ok();
        self.put_collection(&self.snapshot_collection, json!({"vectors": {}}))
            .await
            .ok();
        // Full-text index over the `text` payload field for lexical (BM25-style) fallback.
        let _ = self
            .http
            .put(format!(
                "{}/collections/{}/index",
                self.base, self.memory_collection
            ))
            .json(&json!({"field_name": "text", "field_schema": "text"}))
            .send()
            .await;
        Ok(())
    }

    async fn put_collection(&self, name: &str, body: Value) -> Result<()> {
        let resp = self
            .http
            .put(format!("{}/collections/{}", self.base, name))
            .json(&body)
            .send()
            .await?;
        let _ = resp.status(); // 200 created, 409 already exists are both fine
        Ok(())
    }

    /// Hybrid retrieval: dense vector search when an embedding endpoint exists,
    /// full-text filter scroll otherwise. Returns up to `top_k` evidence spans.
    pub async fn hybrid_search(&self, query: &str, top_k: usize) -> Result<Vec<MemoryHit>> {
        if let Some(embed) = &self.embed {
            let vectors = embed(vec![query.to_string()]).await?;
            if let Some(vector) = vectors.into_iter().next() {
                if !vector.is_empty() {
                    let resp = self
                        .http
                        .post(format!(
                            "{}/collections/{}/points/search",
                            self.base, self.memory_collection
                        ))
                        .json(&json!({"vector": vector, "limit": top_k, "with_payload": true}))
                        .send()
                        .await?;
                    return self.parse_hits(resp).await;
                }
            }
        }
        self.lexical_search(query, top_k).await
    }

    /// Full-text (BM25-style) fallback over the payload `text` field.
    async fn lexical_search(&self, query: &str, top_k: usize) -> Result<Vec<MemoryHit>> {
        let resp = self
            .http
            .post(format!(
                "{}/collections/{}/points/scroll",
                self.base, self.memory_collection
            ))
            .json(&json!({
                "filter": {"must": [{"key": "text", "match": {"text": query}}]},
                "limit": top_k,
                "with_payload": true,
            }))
            .send()
            .await?;
        let data: Value = resp.json().await?;
        let mut out = Vec::new();
        for point in data["result"]["points"].as_array().unwrap_or(&vec![]) {
            out.push(MemoryHit {
                id: point["id"].as_str().unwrap_or_default().to_string(),
                text: point["payload"]["text"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string(),
                score: 1.0,
                payload: point["payload"].as_object().cloned().unwrap_or_default(),
            });
        }
        Ok(out)
    }

    async fn parse_hits(&self, resp: reqwest::Response) -> Result<Vec<MemoryHit>> {
        let data: Value = resp.json().await?;
        let mut out = Vec::new();
        for point in data["result"].as_array().unwrap_or(&vec![]) {
            out.push(MemoryHit {
                id: point["id"].as_str().unwrap_or_default().to_string(),
                text: point["payload"]["text"]
                    .as_str()
                    .unwrap_or_default()
                    .to_string(),
                score: point["score"].as_f64().unwrap_or(0.0),
                payload: point["payload"].as_object().cloned().unwrap_or_default(),
            });
        }
        Ok(out)
    }

    /// Index a root-cause document back into long-term memory so future
    /// incidents resolve faster.
    pub async fn index_document(
        &self,
        doc_id: &str,
        text: &str,
        metadata: Map<String, Value>,
    ) -> Result<()> {
        let mut payload = metadata;
        payload.insert("text".into(), json!(text));
        let mut point = json!({"id": doc_id, "payload": payload});
        if let Some(embed) = &self.embed {
            let vectors = embed(vec![text.to_string()]).await?;
            if let Some(v) = vectors.into_iter().next() {
                if !v.is_empty() {
                    point["vector"] = json!(v);
                }
            }
        }
        self.http
            .put(format!(
                "{}/collections/{}/points",
                self.base, self.memory_collection
            ))
            .json(&json!({"points": [point]}))
            .send()
            .await?;
        Ok(())
    }

    /// Persist a full trajectory snapshot into the snapshot collection (the
    /// gateway mirrors it into PostgreSQL for durable time-travel reads).
    #[allow(clippy::too_many_arguments)]
    pub async fn snapshot(
        &self,
        snap_ref: &str,
        incident_id: &str,
        run_id: &str,
        step_index: usize,
        node: &str,
        summary: &str,
        status: &str,
        state: Value,
    ) -> Result<()> {
        let payload = json!({
            "incident_id": incident_id,
            "run_id": run_id,
            "step_index": step_index,
            "node": node,
            "summary": summary,
            "status": status,
            "state": state,
        });
        self.http
            .put(format!(
                "{}/collections/{}/points",
                self.base, self.snapshot_collection
            ))
            .json(&json!({"points": [{"id": snap_ref, "payload": payload}]}))
            .send()
            .await?;
        Ok(())
    }

    pub async fn get_snapshot(&self, snap_ref: &str) -> Result<Option<Value>> {
        let resp = self
            .http
            .post(format!(
                "{}/collections/{}/points",
                self.base, self.snapshot_collection
            ))
            .json(&json!({"ids": [snap_ref], "with_payload": true}))
            .send()
            .await?;
        let data: Value = resp.json().await?;
        Ok(data["result"]
            .as_array()
            .and_then(|arr| arr.first())
            .map(|p| p["payload"].clone()))
    }
}
