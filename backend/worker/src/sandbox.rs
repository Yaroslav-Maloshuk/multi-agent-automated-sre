//! Docker sandbox pool: pre-warmed, network-restricted (`--network none`)
//! containers where the Code Fixer reproduces bugs and the Verifier runs
//! pytest + Ruff. The pool replaces the legacy Python Docker SDK client with a
//! native Rust implementation (bollard) and never touches the host filesystem.

use anyhow::{Context, Result};
use bollard::container::{Config, CreateContainerOptions, LogOutput};
use bollard::exec::{CreateExecOptions, StartExecOptions, StartExecResults};
use bollard::image::{CreateImageOptions, ListImagesOptions};
use bollard::models::*;
use bollard::Docker;
use futures::TryStreamExt;
use serde_json::{json, Map, Value};
use std::time::Duration;
use tokio::time::timeout as tokio_timeout;

use crate::config::WorkerConfig;
use crate::state::hex16;

#[derive(Debug, Clone)]
pub struct ExecResult {
    pub exit_code: i64,
    pub stdout: String,
    pub stderr: String,
    pub ok: bool,
}

#[derive(Clone)]
pub struct SandboxPool {
    docker: Docker,
    image: String,
    min_warm: i64,
    max_size: i64,
    cpu_quota: f64,
    mem_limit: i64,
    network_mode: String,
    workdir: String,
    ready: Vec<String>,
}

impl SandboxPool {
    pub async fn connect(cfg: &WorkerConfig) -> Result<SandboxPool> {
        let docker = Docker::connect_with_local_defaults().context("docker socket")?;
        let mut pool = SandboxPool {
            docker,
            image: cfg.docker_sandbox_image.clone(),
            min_warm: cfg.sandbox_pool_min,
            max_size: cfg.sandbox_pool_max.max(1),
            cpu_quota: cfg.sandbox_cpu_quota,
            mem_limit: cfg.sandbox_mem_limit,
            network_mode: cfg.sandbox_network_mode.clone(),
            workdir: cfg.sandbox_workdir.clone(),
            ready: Vec::new(),
        };
        pool.warm().await?;
        Ok(pool)
    }

    fn container_name() -> String {
        format!("auto-sre-sb-{}", hex16())
    }

    async fn image_present(&self) -> Result<bool> {
        let images = self
            .docker
            .list_images(Some(ListImagesOptions::<String> {
                all: false,
                ..Default::default()
            }))
            .await
            .context("list images")?;
        Ok(images
            .iter()
            .any(|img| img.repo_tags.iter().any(|t| t.contains(&self.image))))
    }

    pub async fn ensure_image(&self) -> Result<()> {
        if self.image_present().await? {
            return Ok(());
        }
        let (repo, tag) = match self.image.rsplit_once(':') {
            Some((repo, tag)) if !tag.contains('/') => (repo.to_string(), tag.to_string()),
            _ => (self.image.clone(), "latest".to_string()),
        };
        tracing::info!(image = %self.image, "pulling sandbox image");
        let stream = self.docker.create_image(
            Some(CreateImageOptions {
                from_image: repo,
                from_src: String::new(),
                repo: String::new(),
                tag,
                platform: String::new(),
                changes: vec![],
            }),
            None,
            None,
        );
        futures::pin_mut!(stream);
        while let Some(item) = stream.try_next().await? {
            let _ = item;
        }
        Ok(())
    }

    async fn spawn(&self) -> Result<String> {
        self.ensure_image().await?;
        let name = Self::container_name();
        let cfg = Config {
            image: Some(self.image.clone()),
            cmd: Some(vec!["sleep".to_string(), "infinity".to_string()]),
            working_dir: Some(self.workdir.clone()),
            network_disabled: Some(self.network_mode == "none"),
            host_config: Some(HostConfig {
                network_mode: Some(self.network_mode.clone()),
                nano_cpus: Some((self.cpu_quota * 1e9) as i64),
                memory: Some(self.mem_limit),
                ..Default::default()
            }),
            ..Default::default()
        };
        let created = self
            .docker
            .create_container(
                Some(CreateContainerOptions {
                    name,
                    platform: None,
                }),
                cfg,
            )
            .await
            .context("create sandbox container")?;
        let id = created.id;
        self.docker
            .start_container(
                &id,
                None::<bollard::container::StartContainerOptions<String>>,
            )
            .await
            .context("start sandbox")?;
        Ok(id)
    }

    /// Warm the pool to `min_warm` ready containers.
    pub async fn warm(&mut self) -> Result<usize> {
        self.ensure_image().await?;
        let mut created = 0usize;
        while (self.ready.len() as i64) < self.min_warm {
            match self.spawn().await {
                Ok(id) => {
                    self.ready.push(id);
                    created += 1;
                }
                Err(err) => {
                    tracing::warn!(%err, "sandbox warm failed");
                    break;
                }
            }
        }
        tracing::info!(ready = self.ready.len(), "sandbox pool warm");
        Ok(created)
    }

    /// Warm the pool up to an explicit target count (from the gateway).
    pub async fn warm_count(&mut self, count: i64) -> Result<usize> {
        if count <= 0 {
            return self.warm().await;
        }
        let mut created = 0usize;
        while (self.ready.len() as i64) < count && (self.ready.len() as i64) < self.max_size {
            match self.spawn().await {
                Ok(id) => {
                    self.ready.push(id);
                    created += 1;
                }
                Err(err) => {
                    tracing::warn!(%err, "sandbox warm failed");
                    break;
                }
            }
        }
        Ok(created)
    }

    pub async fn acquire(&mut self) -> Result<SandboxHandle> {
        let id = if let Some(id) = self.ready.pop() {
            id
        } else if (self.ready.len() as i64) < self.max_size {
            self.spawn().await?
        } else {
            return Err(anyhow::anyhow!("sandbox pool exhausted"));
        };
        Ok(SandboxHandle {
            id,
            pool: self.clone(),
        })
    }

    pub async fn release(&mut self, handle: SandboxHandle) {
        self.ready.push(handle.id);
    }

    pub fn stats(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("backend".into(), json!("docker"));
        m.insert("warm".into(), json!(self.ready.len()));
        m.insert("in_use".into(), json!(0));
        m.insert("max_size".into(), json!(self.max_size));
        m
    }
}

pub struct SandboxHandle {
    pub id: String,
    pool: SandboxPool,
}

impl SandboxHandle {
    pub fn name(&self) -> &str {
        &self.id
    }

    /// Write a file into the sandbox via `sh -c` heredoc over docker exec.
    pub async fn write_file(&self, path: &str, content: &str) -> Result<()> {
        let dir = path.rsplit_once('/').map(|(d, _)| d).unwrap_or(".");
        let script = format!(
            "mkdir -p {dir:?} && cat > {path:?}",
            dir = shell_quote(dir),
            path = shell_quote(path)
        );
        self.exec(
            &script,
            Some(content.as_bytes().to_vec()),
            Duration::from_secs(30),
        )
        .await?;
        Ok(())
    }

    pub async fn read_file(&self, path: &str) -> Result<String> {
        let result = self
            .exec(
                &format!("cat {path:?}", path = shell_quote(path)),
                None,
                Duration::from_secs(30),
            )
            .await?;
        Ok(result.stdout)
    }

    pub async fn run(&self, command: &str, timeout_s: u64) -> Result<ExecResult> {
        self.exec(command, None, Duration::from_secs(timeout_s))
            .await
    }

    async fn exec(
        &self,
        script: &str,
        input: Option<Vec<u8>>,
        timeout: Duration,
    ) -> Result<ExecResult> {
        let exec = self
            .pool
            .docker
            .create_exec(
                &self.id,
                CreateExecOptions {
                    cmd: Some(vec!["sh".to_string(), "-c".to_string(), script.to_string()]),
                    attach_stdin: Some(input.is_some()),
                    attach_stdout: Some(true),
                    attach_stderr: Some(true),
                    tty: Some(false),
                    ..Default::default()
                },
            )
            .await
            .context("create exec")?;
        let exec_id = exec.id;

        let attached = self
            .pool
            .docker
            .start_exec(
                &exec_id,
                Some(StartExecOptions {
                    ..Default::default()
                }),
            )
            .await
            .context("start exec")?;

        let (mut output, mut stdin) = match attached {
            StartExecResults::Attached { output, input } => (output, input),
            StartExecResults::Detached => {
                return Err(anyhow::anyhow!("exec detached unexpectedly"))
            }
        };

        let collected = tokio_timeout(timeout, async {
            use tokio::io::AsyncWriteExt;
            if let Some(bytes) = input {
                stdin.write_all(&bytes).await?;
                stdin.shutdown().await?;
            }
            drop(stdin);

            let mut stdout = String::new();
            let mut stderr = String::new();
            while let Some(output) = output.try_next().await? {
                match output {
                    LogOutput::StdOut { message } => {
                        stdout.push_str(&String::from_utf8_lossy(&message))
                    }
                    LogOutput::StdErr { message } => {
                        stderr.push_str(&String::from_utf8_lossy(&message))
                    }
                    _ => {}
                }
            }
            Ok::<(String, String), anyhow::Error>((stdout, stderr))
        })
        .await;

        let (stdout, stderr) = match collected {
            Ok(Ok(pair)) => pair,
            Ok(Err(err)) => {
                tracing::warn!(%err, "sandbox exec stream error");
                (String::new(), format!("[exec error] {err}"))
            }
            Err(_) => {
                return Ok(ExecResult {
                    exit_code: 124,
                    stdout: String::new(),
                    stderr: format!("[exec timed out after {}s]", timeout.as_secs()),
                    ok: false,
                });
            }
        };

        let info: ExecInspectResponse = self
            .pool
            .docker
            .inspect_exec(&exec_id)
            .await
            .context("inspect exec")?;
        let exit_code = info.exit_code.unwrap_or(-1) as i64;
        Ok(ExecResult {
            exit_code,
            stdout,
            stderr,
            ok: exit_code == 0,
        })
    }
}

fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

pub fn parse_failures(pytest_output: &str) -> Vec<String> {
    let mut lines = Vec::new();
    for line in pytest_output.lines() {
        let l = line.trim();
        if l.starts_with("FAILED") || l.starts_with("ERROR") || (l.starts_with("E ") && l.len() > 1)
        {
            lines.push(l.to_string());
        }
        if lines.len() >= 10 {
            break;
        }
    }
    lines
}

pub fn linter_unavailable(exit_code: i64, output: &str) -> bool {
    exit_code == 127 || (exit_code != 0 && output.contains("command not found"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_pytest_failures() {
        let out = "=== FAILURES ===\nFAILED tests/test_api.py::test_x - AssertionError\nERROR tests/test_b.py - teardown\nE   assert 1 == 2\n";
        let failures = parse_failures(out);
        assert!(failures.len() >= 2);
        assert!(failures[0].contains("FAILED"));
    }

    #[test]
    fn quotes_shell_paths() {
        assert_eq!(shell_quote("/workspace/app.py"), "'/workspace/app.py'");
    }
}
