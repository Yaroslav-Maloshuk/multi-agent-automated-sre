//! Pingora reverse proxy routing the SRE workspace services:
//!
//! - `/api/*` and `/ws/*`   -> SRE gateway (Go, :8000)
//! - `/v1/*` (chat/RAG/SSE) -> AI gateway (FastAPI, :8002)
//! - candle/* chat calls    -> Candle inference (:11434)
//! - anything else          -> SRE gateway

use std::net::SocketAddr;
use std::sync::Arc;

use async_trait::async_trait;
use log::info;
use pingora::prelude::*;
use pingora::proxy::{Session, http_proxy_service};

/// Upstream backends resolved from the environment at boot.
#[derive(Clone)]
struct RouterConfig {
    gateway: Arc<HttpPeer>,
    ai: Arc<HttpPeer>,
    candle: Arc<HttpPeer>,
}

fn resolve(host: &str, port: u16) -> SocketAddr {
    use std::net::ToSocketAddrs;
    (host, port)
        .to_socket_addrs()
        .ok()
        .and_then(|mut it| it.next())
        .unwrap_or_else(|| panic!("cannot resolve {host}:{port}"))
}

fn peer(host: &str, port: u16) -> HttpPeer {
    HttpPeer::new(resolve(host, port), false, String::new())
}

fn load_config() -> RouterConfig {
    let gateway_host = std::env::var("GATEWAY_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let gateway_port = std::env::var("GATEWAY_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8000);
    let ai_host = std::env::var("AI_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let ai_port = std::env::var("AI_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(8002);
    let candle_host = std::env::var("CANDLE_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let candle_port = std::env::var("CANDLE_PORT").ok().and_then(|p| p.parse().ok()).unwrap_or(11434);
    RouterConfig {
        gateway: Arc::new(peer(&gateway_host, gateway_port)),
        ai: Arc::new(peer(&ai_host, ai_port)),
        candle: Arc::new(peer(&candle_host, candle_port)),
    }
}

/// Which upstream a request path maps to. Pure function so it is unit-testable.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Upstream {
    Gateway,
    Ai,
    Candle,
}

pub(crate) fn upstream_for(path: &str) -> Upstream {
    if path.starts_with("/api/") || path.starts_with("/ws/") {
        Upstream::Gateway
    } else if path.starts_with("/v1/chat") && path.contains("candle") {
        Upstream::Candle
    } else if path.starts_with("/v1/") {
        Upstream::Ai
    } else {
        Upstream::Gateway
    }
}

fn peer_for(upstream: Upstream, config: &RouterConfig) -> Arc<HttpPeer> {
    match upstream {
        Upstream::Gateway => Arc::clone(&config.gateway),
        Upstream::Ai => Arc::clone(&config.ai),
        Upstream::Candle => Arc::clone(&config.candle),
    }
}

struct SreProxy {
    config: RouterConfig,
}

#[async_trait]
impl ProxyHttp for SreProxy {
    type CTX = ();

    fn new_ctx(&self) -> Self::CTX {}

    async fn upstream_peer(
        &self,
        session: &mut Session,
        _ctx: &mut Self::CTX,
    ) -> pingora::Result<Box<HttpPeer>> {
        let path = session.req_header().uri.path().to_string();
        Ok(Box::new((*peer_for(upstream_for(&path), &self.config)).clone()))
    }
}

fn main() {
    env_logger::init();
    let mut server = Server::new(None).unwrap();
    server.bootstrap();

    let config = load_config();
    let mut proxy = http_proxy_service(&server.configuration, SreProxy { config });
    proxy.add_tcp("0.0.0.0:8080");
    server.add_service(proxy);

    info!("auto-sre proxy listening on :8080");
    server.run_forever();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn api_and_ws_go_to_gateway() {
        assert_eq!(upstream_for("/api/v1/incidents"), Upstream::Gateway);
        assert_eq!(upstream_for("/ws/stream"), Upstream::Gateway);
        assert_eq!(upstream_for("/health"), Upstream::Gateway);
    }

    #[test]
    fn v1_goes_to_ai_gateway() {
        assert_eq!(upstream_for("/v1/chat/completions"), Upstream::Ai);
        assert_eq!(upstream_for("/v1/rag/search"), Upstream::Ai);
        assert_eq!(upstream_for("/v1/events/stream"), Upstream::Ai);
    }

    #[test]
    fn candle_requests_go_to_candle() {
        assert_eq!(upstream_for("/v1/chat/completions/candle"), Upstream::Candle);
    }
}
