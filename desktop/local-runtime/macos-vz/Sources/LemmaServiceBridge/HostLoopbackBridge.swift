import Darwin
import Dispatch
import Foundation

/// Streams the guest opens *to* the host, handed to one private Unix endpoint.
///
/// The other direction from `ServiceBridge`: there the host connects and the
/// bridge dials the guest; here the guest connects over vsock -- guestd's
/// loopback relay, on behalf of the paired user's browser sandbox -- and the bridge
/// dials locald's `loopback_relay` socket. It carries bytes and nothing else.
/// Which ports may be reached is locald's decision, made on the first line of
/// each stream; keeping the VM helper out of it means the policy lives in one
/// place and is tested there.
public final class HostLoopbackBridge {
    private let path: String
    private let queue: DispatchQueue
    private let maximumConnections: Int
    private var connections: [UUID: Connection] = [:]
    private var stopped = false

    private final class Connection {
        let guest: GuestStream
        var relay: StreamRelay?
        init(_ guest: GuestStream) { self.guest = guest }

        func stop() {
            relay?.stop()
            guest.close()
        }
    }

    public init(path: String, queue: DispatchQueue = .main, maximumConnections: Int = 128) throws {
        guard maximumConnections > 0, path.utf8.count < 104 else { throw POSIXError(.EINVAL) }
        self.path = path
        self.queue = queue
        self.maximumConnections = maximumConnections
    }

    deinit {
        let open = Array(connections.values)
        queue.async { for connection in open { connection.stop() } }
    }

    /// Take a stream the guest opened. Call on the queue given to init.
    ///
    /// Returns false, and leaves the stream alone, when it cannot be carried:
    /// at capacity, stopped, or nothing listening at the endpoint. The caller
    /// then refuses the connection, and guestd tells the sandbox the host is
    /// not reachable rather than leaving it waiting.
    ///
    /// Not asserted with `dispatchPrecondition`: a wrong guess about which
    /// queue the framework calls its delegate on would crash the VM helper,
    /// and every guest service with it, for a convenience feature.
    public func accept(_ guest: GuestStream) -> Bool {
        guard !stopped, connections.count < maximumConnections else { return false }
        guard let host = Self.connect(path: path) else { return false }
        let duplicate = dup(guest.descriptor)
        guard duplicate >= 0 else {
            Darwin.close(host)
            return false
        }
        _ = fcntl(duplicate, F_SETFD, FD_CLOEXEC)
        let id = UUID()
        let connection = Connection(guest)
        connections[id] = connection
        let relay = StreamRelay(leftFD: duplicate, rightFD: host, queue: queue) { [weak self] in
            self?.finish(id)
        }
        connection.relay = relay
        relay.start()
        return true
    }

    /// Close every stream. Call on the queue given to init.
    public func stop() {
        guard !stopped else { return }
        stopped = true
        for id in Array(connections.keys) { finish(id) }
    }

    var openConnections: Int { connections.count }

    private func finish(_ id: UUID) {
        guard let connection = connections.removeValue(forKey: id) else { return }
        connection.stop()
    }

    private static func connect(path: String) -> Int32? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        _ = fcntl(fd, F_SETFD, FD_CLOEXEC)
        var enabled: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutablePointer(to: &address.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: 104) { _ = strlcpy($0, path, 104) }
        }
        // A local socket connects or refuses at once, so this blocking connect
        // does not hold the VM's queue.
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0 else {
            Darwin.close(fd)
            return nil
        }
        _ = fcntl(fd, F_SETFL, O_NONBLOCK)
        return fd
    }
}
