use std::io;
use std::net::{SocketAddr, TcpListener};
use std::thread::{self, JoinHandle};
use std::time::Duration;
use tokio::sync::oneshot;
use tokio::task::JoinSet;

const MAX_CONNECTIONS: usize = 128;

#[derive(Clone, Debug)]
enum Upstream {
    Tcp(SocketAddr),
    #[cfg(target_os = "macos")]
    Private(std::path::PathBuf),
}

impl Upstream {
    async fn relay(&self, inbound: &mut tokio::net::TcpStream) -> io::Result<()> {
        match self {
            Self::Tcp(address) => {
                let mut outbound = tokio::time::timeout(
                    Duration::from_secs(5),
                    tokio::net::TcpStream::connect(address),
                )
                .await??;
                tokio::io::copy_bidirectional(inbound, &mut outbound).await?;
            }
            #[cfg(target_os = "macos")]
            Self::Private(path) => {
                let mut outbound =
                    tokio::time::timeout(Duration::from_secs(5), connect_private_service(path))
                        .await??;
                tokio::io::copy_bidirectional(inbound, &mut outbound).await?;
            }
        }
        Ok(())
    }
}

#[cfg(target_os = "macos")]
pub(crate) async fn connect_private_service(
    path: &std::path::Path,
) -> io::Result<tokio::net::UnixStream> {
    use tokio::io::AsyncReadExt;
    let mut stream = tokio::net::UnixStream::connect(path).await?;
    let mut ready = [0xff];
    stream.read_exact(&mut ready).await?;
    if ready != [0] {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid private service acknowledgement",
        ));
    }
    Ok(stream)
}

pub(crate) struct TcpForwarder {
    shutdown: Option<oneshot::Sender<()>>,
    pub(crate) local_address: SocketAddr,
    thread: Option<JoinHandle<()>>,
}

impl TcpForwarder {
    pub(crate) fn start(
        label: &'static str,
        bind: SocketAddr,
        target: SocketAddr,
    ) -> io::Result<Self> {
        Self::start_upstream(label, bind, Upstream::Tcp(target))
    }

    #[cfg(target_os = "macos")]
    pub(crate) fn start_private(
        label: &'static str,
        bind: SocketAddr,
        path: std::path::PathBuf,
    ) -> io::Result<Self> {
        Self::start_upstream(label, bind, Upstream::Private(path))
    }

    fn start_upstream(label: &'static str, bind: SocketAddr, target: Upstream) -> io::Result<Self> {
        let listener = bind_listener(bind).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("could not bind managed {label} route at {bind}: {error}"),
            )
        })?;
        let local_address = listener.local_addr()?;
        listener.set_nonblocking(true)?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let listener = {
            let _entered = runtime.enter();
            tokio::net::TcpListener::from_std(listener)?
        };
        let (shutdown, mut stopped) = oneshot::channel();
        let worker = thread::Builder::new().name(format!("lemma-{label}")).spawn(move || {
            runtime.block_on(async move {
                let mut connections = JoinSet::new();
                loop {
                    tokio::select! {
                        biased;
                        _ = &mut stopped => break,
                        result = connections.join_next(), if !connections.is_empty() => {
                            if let Some(Err(error)) = result {
                                eprintln!("managed {label} route worker failed: {error}");
                            }
                        }
                        accepted = listener.accept(), if connections.len() < MAX_CONNECTIONS => {
                            let (mut inbound, _) = match accepted {
                                Ok(connection) => connection,
                                Err(error) => {
                                    eprintln!("managed {label} route accept failed: {error}");
                                    break;
                                }
                            };
                            let target = target.clone();
                            connections.spawn(async move {
                                if let Err(error) = target.relay(&mut inbound).await {
                                    eprintln!("managed {label} route to {target:?} failed: {error}");
                                }
                            });
                        }
                    }
                }
                // Cancelling also closes idle and backpressured sockets. Joining
                // proves no connection task survives the owning runtime.
                connections.abort_all();
                while connections.join_next().await.is_some() {}
            });
        })?;
        Ok(Self {
            shutdown: Some(shutdown),
            local_address,
            thread: Some(worker),
        })
    }
}

#[cfg(unix)]
fn bind_listener(address: SocketAddr) -> io::Result<TcpListener> {
    use socket2::{Domain, Protocol, Socket, Type};
    let socket = Socket::new(
        Domain::for_address(address),
        Type::STREAM,
        Some(Protocol::TCP),
    )?;
    // Callback routes reuse their port after controlled shutdown and TCP TIME_WAIT.
    socket.set_reuse_address(true)?;
    socket.bind(&address.into())?;
    socket.listen(128)?;
    Ok(socket.into())
}

#[cfg(not(unix))]
fn bind_listener(address: SocketAddr) -> io::Result<TcpListener> {
    TcpListener::bind(address)
}

impl Drop for TcpForwarder {
    fn drop(&mut self) {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        if let Some(worker) = self.thread.take() {
            if worker.join().is_err() {
                eprintln!(
                    "managed callback forwarder at {} stopped unexpectedly",
                    self.local_address
                );
            }
        }
    }
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::UnixListener;

    #[tokio::test]
    async fn private_service_requires_a_valid_guest_acknowledgement() {
        let root = tempfile::Builder::new()
            .prefix("lemma-route-")
            .tempdir_in("/tmp")
            .unwrap();
        let path = root.path().join("service.sock");
        let listener = UnixListener::bind(&path).unwrap();
        for byte in [0xff, 0] {
            let server = async {
                let (mut stream, _) = listener.accept().await.unwrap();
                stream.write_all(&[byte]).await.unwrap();
                stream
            };
            let (upstream, result) = tokio::join!(server, connect_private_service(&path));
            if byte == 0 {
                assert!(result.is_ok());
            } else {
                assert_eq!(result.unwrap_err().kind(), io::ErrorKind::InvalidData);
            }
            drop(upstream);
        }
    }

    #[tokio::test]
    async fn accepting_a_private_socket_without_guest_ack_is_not_ready() {
        let root = tempfile::Builder::new()
            .prefix("lemma-route-")
            .tempdir_in("/tmp")
            .unwrap();
        let path = root.path().join("service.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let server = async { listener.accept().await.unwrap().0 };
        let (upstream, result) = tokio::join!(
            server,
            tokio::time::timeout(Duration::from_millis(50), connect_private_service(&path))
        );
        assert!(
            result.is_err(),
            "a listening helper is not proof of a guest connection"
        );
        drop(upstream);
    }

    #[tokio::test]
    async fn private_forwarder_strips_ack_relays_half_close_and_releases_idle_connections() {
        let root = tempfile::Builder::new()
            .prefix("lemma-route-")
            .tempdir_in("/tmp")
            .unwrap();
        let path = root.path().join("service.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let forwarder =
            TcpForwarder::start_private("test-service", "127.0.0.1:0".parse().unwrap(), path)
                .unwrap();
        let exchange = async {
            let mut client = tokio::net::TcpStream::connect(forwarder.local_address)
                .await
                .unwrap();
            let (mut guest, _) = listener.accept().await.unwrap();
            guest.write_all(&[0]).await.unwrap();
            client.write_all(b"query").await.unwrap();
            client.shutdown().await.unwrap();
            let mut request = Vec::new();
            guest.read_to_end(&mut request).await.unwrap();
            assert_eq!(request, b"query");
            guest.write_all(b"answer").await.unwrap();
            guest.shutdown().await.unwrap();
            let mut response = Vec::new();
            client.read_to_end(&mut response).await.unwrap();
            assert_eq!(
                response, b"answer",
                "transport acknowledgement must not reach the database client"
            );
            let client = tokio::net::TcpStream::connect(forwarder.local_address)
                .await
                .unwrap();
            let (mut guest, _) = listener.accept().await.unwrap();
            guest.write_all(&[0]).await.unwrap();
            (client, guest)
        };
        let (mut client, mut guest) = tokio::time::timeout(Duration::from_secs(3), exchange)
            .await
            .unwrap();
        drop(forwarder);
        for result in [
            tokio::time::timeout(Duration::from_secs(1), client.read_u8())
                .await
                .unwrap(),
            tokio::time::timeout(Duration::from_secs(1), guest.read_u8())
                .await
                .unwrap(),
        ] {
            assert!(matches!(
                result.unwrap_err().kind(),
                io::ErrorKind::UnexpectedEof | io::ErrorKind::ConnectionReset
            ));
        }
    }

    #[tokio::test]
    async fn unavailable_guest_closes_the_client_without_a_false_success_byte() {
        let root = tempfile::Builder::new()
            .prefix("lemma-route-")
            .tempdir_in("/tmp")
            .unwrap();
        let path = root.path().join("missing.sock");
        let forwarder =
            TcpForwarder::start_private("missing-service", "127.0.0.1:0".parse().unwrap(), path)
                .unwrap();
        let mut client = tokio::net::TcpStream::connect(forwarder.local_address)
            .await
            .unwrap();
        let result = tokio::time::timeout(Duration::from_secs(1), client.read_u8())
            .await
            .unwrap();
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::UnexpectedEof);
    }
}
