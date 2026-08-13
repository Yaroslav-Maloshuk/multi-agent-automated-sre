//! Qdrant vector memory: hybrid retrieval (dense embeddings when an
//! OpenAI-compatible `/embeddings` endpoint is configured, full-text otherwise)
//! plus the snapshot engine. Replaces pgvector + BM25 with a Python-free,
//! Arrow-embedded vector store.

use anyhow::{anyhow, Context, Result};
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

/// Retrieval contract shared by the Qdrant memory and the AI-gateway RAG
/// (Qdrant + LanceDB + vectorless PageIndex with RRF fusion). Agents only
/// depend on this trait, so the backing store is a deployment detail.
#[async_trait::async_trait]
pub trait Memory: Send + Sync {
    fn name(&self) -> &'static str;
    async fn hybrid_search(&self, query: &str, top_k: usize) -> Result<Vec<MemoryHit>>;
    async fn index_document(
        &self,
        doc_id: &str,
        text: &str,
        metadata: Map<String, Value>,
    ) -> Result<()>;
    #[allow(clippy::too_many_arguments)]
    async fn snapshot(
        &self,
        snap_ref: &str,
        incident_id: &str,
        run_id: &str,
        step_index: usize,
        node: &str,
        summary: &str,
        status: &str,
        state: Value,
    ) -> Result<()>;
    async fn get_snapshot(&self, snap_ref: &str) -> Result<Option<Value>>;
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

#[async_trait::async_trait]
impl Memory for QdrantMemory {
    fn name(&self) -> &'static str {
        "qdrant"
    }

    async fn hybrid_search(&self, query: &str, top_k: usize) -> Result<Vec<MemoryHit>> {
        QdrantMemory::hybrid_search(self, query, top_k).await
    }

    async fn index_document(
        &self,
        doc_id: &str,
        text: &str,
        metadata: Map<String, Value>,
    ) -> Result<()> {
        QdrantMemory::index_document(self, doc_id, text, metadata).await
    }

    async fn snapshot(
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
        QdrantMemory::snapshot(
            self, snap_ref, incident_id, run_id, step_index, node, summary, status, state,
        )
        .await
    }

    async fn get_snapshot(&self, snap_ref: &str) -> Result<Option<Value>> {
        QdrantMemory::get_snapshot(self, snap_ref).await
    }
}

/// Retrieval backed by the FastAPI AI gateway's hybrid RAG — Qdrant, LanceDB
/// and the vectorless SQLite PageIndex fused with RRF. Used when the worker is
/// configured with `LLM_PROVIDER=ai` so agents reason over the full corpus.
/// Snapshots and evidence indexing still land in Qdrant via the inner memory.
pub struct GatewayMemory {
    http: reqwest::Client,
    base: String,
    qdrant: QdrantMemory,
}

impl GatewayMemory {
    pub async fn connect(cfg: &WorkerConfig, embed: Option<EmbeddingFn>) -> Result<GatewayMemory> {
        Ok(GatewayMemory {
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(30))
                .build()
                .context("http client")?,
            base: cfg.ai_url.trim_end_matches('/').to_string(),
            qdrant: QdrantMemory::connect(cfg, embed).await?,
        })
    }
}

#[async_trait::async_trait]
impl Memory for GatewayMemory {
    fn name(&self) -> &'static str {
        "ai-gateway-rag"
    }

    async fn hybrid_search(&self, query: &str, top_k: usize) -> Result<Vec<MemoryHit>> {
        let resp = self
            .http
            .post(format!("{}/rag/search", self.base))
            .json(&json!({"query": query, "top_k": top_k}))
            .send()
            .await?;
        if !resp.status().is_success() {
            return Err(anyhow!("rag search error {}", resp.status()));
        }
        let data: Value = resp.json().await?;
        let mut out = Vec::new();
        for (i, hit) in data["hits"].as_array().unwrap_or(&vec![]).iter().enumerate() {
            let text = hit["text"].as_str().unwrap_or_default().to_string();
            let mut payload = Map::new();
            if let Some(source) = hit["source"].as_str() {
                payload.insert("source".into(), Value::String(source.to_string()));
            }
            out.push(MemoryHit {
                id: hit["source"]
                    .as_str()
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("hit-{i}")),
                text,
                score: hit["score"].as_f64().unwrap_or(0.0),
                payload,
            });
        }
        Ok(out)
    }

    async fn index_document(
        &self,
        doc_id: &str,
        text: &str,
        metadata: Map<String, Value>,
    ) -> Result<()> {
        self.qdrant.index_document(doc_id, text, metadata).await
    }

    async fn snapshot(
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
        self.qdrant
            .snapshot(snap_ref, incident_id, run_id, step_index, node, summary, status, state)
            .await
    }

    async fn get_snapshot(&self, snap_ref: &str) -> Result<Option<Value>> {
        self.qdrant.get_snapshot(snap_ref).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn gateway_memory_parses_ai_rag_hits() {
        use std::io::{Read, Write};
        use std::sync::{Arc, Mutex};

        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let request_line = Arc::new(Mutex::new(String::new()));
        let captured = Arc::clone(&request_line);

        std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buf = [0u8; 8192];
            let n = stream.read(&mut buf).unwrap();
            let head = String::from_utf8_lossy(&buf[..n]);
            *captured.lock().unwrap() = head.lines().next().unwrap_or_default().to_string();
            let body = concat!(
                r#"{"query":"disk full","hits":["#,
                r#"{"text":"clean /var/log","score":0.91,"source":"runbook-1"},"#,
                r#"{"text":"rotate old logs","score":0.72,"source":"runbook-2"}]}"#
            );
            let resp = format!(
                "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(resp.as_bytes()).unwrap();
        });

        let inner = QdrantMemory {
            http: reqwest::Client::new(),
            base: "http://127.0.0.1:9".into(),
            memory_collection: "m".into(),
            snapshot_collection: "s".into(),
            embed: None,
        };
        let g = GatewayMemory {
            http: reqwest::Client::new(),
            base: format!("http://{addr}/v1"),
            qdrant: inner,
        };

        assert_eq!(g.name(), "ai-gateway-rag");
        let hits = g.hybrid_search("disk full", 3).await.expect("rag search");
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].text, "clean /var/log");
        assert_eq!(hits[0].score, 0.91);
        assert_eq!(hits[0].payload["source"], json!("runbook-1"));

        let line = request_line.lock().unwrap().clone();
        assert!(line.starts_with("POST /v1/rag/search"), "got {line}");
    }
}
