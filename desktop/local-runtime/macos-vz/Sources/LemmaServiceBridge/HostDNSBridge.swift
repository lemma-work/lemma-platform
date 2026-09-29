import Darwin
import Dispatch
import Foundation

/// The guest's DNS, answered by this Mac's own resolver, over vsock.
///
/// The guest is on VZ NAT, and DHCP gives it vmnet's gateway as its only name
/// server. vmnet forwards those queries itself, and when the Mac's DNS is a
/// local proxy -- Cloudflare WARP, Tailscale MagicDNS, Zscaler, a VPN client
/// with split DNS -- that forwarding fails while every app on the Mac resolves
/// the same name without trouble. So the guest asks here as well: guestd's
/// `host_dns` listens on the guest's `127.0.0.2:53`, and each query it
/// receives arrives on this bridge as one vsock stream.
///
/// ```text
/// resolved --udp/tcp--> 127.0.0.2:53 (guestd host_dns) --vsock 42414-->
///     this bridge --> DNSServiceQueryRecord --> the Mac's resolver
/// ```
///
/// Answering through `DNSServiceQueryRecord` rather than `getaddrinfo` is what
/// makes split DNS work: it is the resolver every Mac app uses, with the
/// per-domain resolvers a VPN installs, and it answers any record type.
///
/// **Wire format.** One query per stream, framed the way DNS over TCP is
/// (RFC 1035 4.2.2): a two-byte big-endian length, then the message. The reply
/// comes back framed the same way and the stream is closed. A query that cannot
/// be read is answered FORMERR or NOTIMP when it has an id to answer, and
/// otherwise the stream is just closed; anything the resolver cannot answer in
/// time is SERVFAIL, which is what makes the guest's resolver fall back to the
/// gateway.
///
/// **Bounds.** A query is at most `maximumQueryBytes`; a stream is given
/// `ioTimeout` to deliver it and to take the reply; the resolver has
/// `queryTimeout`; and at most `maximumConcurrentQueries` streams are served at
/// once, beyond which a stream is refused and the guest answers SERVFAIL.
public final class HostDNSBridge {
    public static let maximumQueryBytes = 4096

    private let resolver: HostDNSResolving
    private let queue: DispatchQueue
    private let maximumConcurrentQueries: Int
    private let queryTimeout: TimeInterval
    private let ioTimeout: TimeInterval
    private var connections: [UUID: Connection] = [:]
    private var stopped = false

    private struct Connection {
        let guest: GuestStream
        let descriptor: Int32
    }

    public init(
        resolver: HostDNSResolving = SystemDNSResolver(),
        queue: DispatchQueue = .main,
        maximumConcurrentQueries: Int = 64,
        queryTimeout: TimeInterval = 4,
        ioTimeout: TimeInterval = 2
    ) {
        self.resolver = resolver
        self.queue = queue
        self.maximumConcurrentQueries = maximumConcurrentQueries
        self.queryTimeout = queryTimeout
        self.ioTimeout = ioTimeout
    }

    /// Take a stream the guest opened. Call on the queue given to init.
    ///
    /// Returns false, and leaves the stream alone, when stopped or at
    /// capacity; the caller then refuses the connection.
    public func accept(_ guest: GuestStream) -> Bool {
        guard !stopped, connections.count < maximumConcurrentQueries else { return false }
        let descriptor = dup(guest.descriptor)
        guard descriptor >= 0 else { return false }
        _ = fcntl(descriptor, F_SETFD, FD_CLOEXEC)
        var enabled: Int32 = 1
        _ = setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        let id = UUID()
        connections[id] = Connection(guest: guest, descriptor: descriptor)
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            serve(descriptor) { [self] in queue.async { self.finish(id) } }
        }
        return true
    }

    /// Stop taking streams and wake the ones in flight. Call on the queue
    /// given to init. Each stream is still closed by its own worker, so a
    /// descriptor is never closed while that worker can reuse its number.
    public func stop() {
        guard !stopped else { return }
        stopped = true
        for connection in connections.values { Darwin.shutdown(connection.descriptor, SHUT_RDWR) }
    }

    var openConnections: Int { connections.count }

    private func finish(_ id: UUID) {
        guard let connection = connections.removeValue(forKey: id) else { return }
        Darwin.close(connection.descriptor)
        connection.guest.close()
    }

    private func serve(_ descriptor: Int32, done: @escaping () -> Void) {
        let deadline = Date().addingTimeInterval(ioTimeout)
        guard let length = readExactly(descriptor, 2, deadline: deadline).map({ DNSMessage.read16([UInt8]($0), 0) }),
              length > 0, Int(length) <= Self.maximumQueryBytes,
              let message = readExactly(descriptor, Int(length), deadline: deadline)
        else { return done() }
        let query: DNSQuery
        switch DNSMessage.parseQuery(message) {
        case .success(let parsed): query = parsed
        case .failure(.unreadable): return done()
        case .failure(.malformed(let id, let flags)):
            reply(descriptor, DNSMessage.errorResponse(id: id, flags: flags, rcode: DNSMessage.rcodeFormatError))
            return done()
        case .failure(.unsupported(let id, let flags)):
            reply(descriptor, DNSMessage.errorResponse(id: id, flags: flags, rcode: DNSMessage.rcodeNotImplemented))
            return done()
        }
        let once = Once()
        let answer: (DNSResolution) -> Void = { [self] resolution in
            guard once.claim() else { return }
            DispatchQueue.global(qos: .userInitiated).async { [self] in
                reply(descriptor, DNSMessage.response(to: query, resolution))
                done()
            }
        }
        let cancel = resolver.resolve(query, completion: answer)
        // The deadline is enforced here rather than trusted to the resolver,
        // so a query that never returns still costs one slot for a bounded time.
        DispatchQueue.global().asyncAfter(deadline: .now() + queryTimeout) {
            guard !once.isClaimed else { return }
            cancel()
            answer(.failed)
        }
    }

    private func reply(_ descriptor: Int32, _ message: Data) {
        var framed = Data()
        DNSMessage.append16(&framed, UInt16(clamping: message.count))
        framed.append(message.prefix(Int(UInt16.max)))
        _ = writeFully(descriptor, framed, deadline: Date().addingTimeInterval(ioTimeout))
    }
}

/// Answers a DNS question. `completion` is called at most once, from any
/// queue; the returned closure cancels the lookup, and may be called after
/// it has already completed.
public protocol HostDNSResolving: AnyObject {
    func resolve(_ query: DNSQuery, completion: @escaping (DNSResolution) -> Void) -> () -> Void
}

/// First caller wins, across threads.
private final class Once {
    private let lock = NSLock()
    private var claimed = false

    func claim() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if claimed { return false }
        claimed = true
        return true
    }

    var isClaimed: Bool {
        lock.lock()
        defer { lock.unlock() }
        return claimed
    }
}

/// Wait for `descriptor` to be ready, until `deadline`.
private func ready(_ descriptor: Int32, _ events: Int32, deadline: Date) -> Bool {
    while true {
        let remaining = Int32(max(0, deadline.timeIntervalSinceNow * 1000))
        guard remaining > 0 else { return false }
        var poller = pollfd(fd: descriptor, events: Int16(events), revents: 0)
        let result = poll(&poller, 1, remaining)
        if result < 0 && errno == EINTR { continue }
        return result > 0
    }
}

func readExactly(_ descriptor: Int32, _ count: Int, deadline: Date) -> Data? {
    var result = Data()
    var buffer = [UInt8](repeating: 0, count: count)
    while result.count < count {
        guard ready(descriptor, POLLIN, deadline: deadline) else { return nil }
        let length = Darwin.read(descriptor, &buffer, count - result.count)
        if length < 0 && (errno == EINTR || errno == EAGAIN) { continue }
        guard length > 0 else { return nil }
        result.append(contentsOf: buffer.prefix(length))
    }
    return result
}

func writeFully(_ descriptor: Int32, _ data: Data, deadline: Date) -> Bool {
    let bytes = [UInt8](data)
    var written = 0
    while written < bytes.count {
        guard ready(descriptor, POLLOUT, deadline: deadline) else { return false }
        let length = bytes[written...].withUnsafeBytes { Darwin.write(descriptor, $0.baseAddress, $0.count) }
        if length < 0 && (errno == EINTR || errno == EAGAIN) { continue }
        guard length > 0 else { return false }
        written += length
    }
    return true
}
