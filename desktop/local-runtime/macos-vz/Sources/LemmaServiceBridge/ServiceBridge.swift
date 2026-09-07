import Darwin
import Dispatch
import Foundation

/// Retains the VM framework connection for the entire byte stream.
public struct GuestStream {
    public let descriptor: Int32
    public let close: () -> Void

    public init(descriptor: Int32, close: @escaping () -> Void) {
        self.descriptor = descriptor
        self.close = close
    }
}

/// A private Unix endpoint for one fixed guest service, independent of guest IP routing.
public final class ServiceBridge {
    private let queue: DispatchQueue
    private let path: String
    private let listener: DispatchSourceRead
    private let connect: (@escaping (Result<GuestStream, Error>) -> Void) -> Void
    private var connections: [UUID: Client] = [:]
    private var stopped = false
    private let maximumConnections: Int
    private let connectTimeout: TimeInterval

    private final class Client {
        let descriptor: Int32
        var deadline: DispatchWorkItem?
        var relay: StreamRelay?
        var guest: GuestStream?
        init(_ descriptor: Int32) { self.descriptor = descriptor }

        func stop() {
            deadline?.cancel()
            if let relay { relay.stop() }
            else { Darwin.close(descriptor) }
            guest?.close()
        }
    }

    public init(
        path: String, queue: DispatchQueue = .main,
        maximumConnections: Int = 128, connectTimeout: TimeInterval = 5,
        connect: @escaping (@escaping (Result<GuestStream, Error>) -> Void) -> Void
    ) throws {
        self.path = path
        self.queue = queue
        self.connect = connect
        self.maximumConnections = maximumConnections
        self.connectTimeout = connectTimeout
        guard maximumConnections > 0, connectTimeout > 0 else {
            throw POSIXError(.EINVAL)
        }
        let fd = try Self.listen(path: path)
        listener = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        listener.setCancelHandler { Darwin.close(fd); unlink(path) }
        listener.setEventHandler { [weak self] in self?.acceptConnections(fd) }
        listener.resume()
    }

    deinit {
        listener.cancel()
        let clients = Array(connections.values)
        queue.async { for client in clients { client.stop() } }
    }

    /// Call on the queue supplied to init, after consumers have stopped using the service.
    public func stop() {
        guard !stopped else { return }
        stopped = true
        listener.cancel()
        for id in Array(connections.keys) { finish(id) }
    }

    private func acceptConnections(_ fd: Int32) {
        while !stopped {
            let accepted = Darwin.accept(fd, nil, nil)
            if accepted < 0 {
                if errno == EINTR { continue }
                return
            }
            _ = fcntl(accepted, F_SETFD, FD_CLOEXEC)
            _ = fcntl(accepted, F_SETFL, O_NONBLOCK)
            var enabled: Int32 = 1
            _ = setsockopt(accepted, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
            guard connections.count < maximumConnections else { Darwin.close(accepted); continue }
            let id = UUID()
            let client = Client(accepted)
            connections[id] = client
            let deadline = DispatchWorkItem { [weak self] in self?.finish(id) }
            client.deadline = deadline
            queue.asyncAfter(deadline: .now() + connectTimeout, execute: deadline)
            connect { [weak self] result in
                guard let self else {
                    if case .success(let guest) = result { guest.close() }
                    return
                }
                self.queue.async { self.connected(id, result) }
            }
        }
    }

    private func connected(_ id: UUID, _ result: Result<GuestStream, Error>) {
        guard let client = connections[id] else {
            if case .success(let guest) = result { guest.close() }
            return
        }
        client.deadline?.cancel()
        guard case .success(let guest) = result else { finish(id); return }
        client.guest = guest
        let duplicate = dup(guest.descriptor)
        guard duplicate >= 0 else { finish(id); return }
        _ = fcntl(duplicate, F_SETFD, FD_CLOEXEC)
        // The local forwarder waits for this byte: a listening Unix socket alone
        // does not establish that the guest accepted a virtual-socket connection.
        var ready: UInt8 = 0
        guard Darwin.write(client.descriptor, &ready, 1) == 1 else {
            Darwin.close(duplicate)
            finish(id)
            return
        }
        let relay = StreamRelay(leftFD: client.descriptor, rightFD: duplicate, queue: queue) { [weak self] in
            self?.finish(id)
        }
        client.relay = relay
        relay.start()
    }

    private func finish(_ id: UUID) {
        guard let client = connections.removeValue(forKey: id) else { return }
        client.stop()
    }

    private static func listen(path: String) throws -> Int32 {
        guard path.utf8.count < 104 else { throw POSIXError(.ENAMETOOLONG) }
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
        do {
            _ = fcntl(fd, F_SETFD, FD_CLOEXEC)
            _ = fcntl(fd, F_SETFL, O_NONBLOCK)
            var address = sockaddr_un()
            address.sun_family = sa_family_t(AF_UNIX)
            address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
            withUnsafeMutablePointer(to: &address.sun_path) {
                $0.withMemoryRebound(to: CChar.self, capacity: 104) { _ = strlcpy($0, path, 104) }
            }
            // The owning runtime removes stale sockets before launch. Never unlink
            // an endpoint here: another live helper may still own it.
            let bound = withUnsafePointer(to: &address) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
                }
            }
            guard bound == 0 else { throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO) }
            guard chmod(path, S_IRUSR | S_IWUSR) == 0, Darwin.listen(fd, 128) == 0 else {
                unlink(path)
                throw POSIXError(POSIXErrorCode(rawValue: errno) ?? .EIO)
            }
            return fd
        } catch { Darwin.close(fd); throw error }
    }
}
