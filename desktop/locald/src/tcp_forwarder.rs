use std::io;
use std::net::{SocketAddr, TcpListener};
use std::thread::{self, JoinHandle};
use std::time::Duration;
use tokio::sync::oneshot;
use tokio::task::JoinSet;

const MAX_CONNECTIONS: usize = 128;

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
                            connections.spawn(async move {
                                let result = async {
                                    let mut outbound = tokio::time::timeout(
                                        Duration::from_secs(5),
                                        tokio::net::TcpStream::connect(target),
                                    ).await??;
                                    tokio::io::copy_bidirectional(&mut inbound, &mut outbound).await?;
                                    Ok::<(), io::Error>(())
                                }.await;
                                if let Err(error) = result {
                                    eprintln!("managed {label} route to {target} failed: {error}");
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
