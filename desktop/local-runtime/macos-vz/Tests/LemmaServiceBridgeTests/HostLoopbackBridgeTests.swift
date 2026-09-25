import Darwin
import Dispatch
import Foundation
import XCTest
@testable import LemmaServiceBridge

final class HostLoopbackBridgeTests: XCTestCase {
    private let queue = DispatchQueue(label: "lemma-host-loopback-tests")

    private func bounded(_ fd: Int32) {
        var timeout = timeval(tv_sec: 2, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var enabled: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
    }

    /// A guest stream: the end handed to the bridge, and the guest's own end.
    private func guest(closed: XCTestExpectation? = nil) throws -> (GuestStream, Int32) {
        var descriptors: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors) == 0 else { throw POSIXError(.EIO) }
        for fd in descriptors { bounded(fd) }
        let handed = descriptors[0]
        addTeardownBlock { Darwin.close(descriptors[1]) }
        return (GuestStream(descriptor: handed) { Darwin.close(handed); closed?.fulfill() }, descriptors[1])
    }

    /// A listening stand-in for locald's relay socket.
    private func endpoint() throws -> (String, Int32) {
        let directory = "/tmp/lemma-loopback-\(UUID().uuidString.prefix(8))"
        try FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try? FileManager.default.removeItem(atPath: directory) }
        let path = directory + "/relay.sock"
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutablePointer(to: &address.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: 104) { _ = strlcpy($0, path, 104) }
        }
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard bound == 0, Darwin.listen(fd, 8) == 0 else { throw POSIXError(.EADDRINUSE) }
        addTeardownBlock { Darwin.close(fd) }
        return (path, fd)
    }

    private func acceptOne(_ listener: Int32) -> Int32 {
        let fd = Darwin.accept(listener, nil, nil)
        XCTAssertGreaterThanOrEqual(fd, 0)
        bounded(fd)
        addTeardownBlock { Darwin.close(fd) }
        return fd
    }

    private func read(_ fd: Int32, count: Int) -> Data {
        var bytes = [UInt8](repeating: 0, count: count)
        var result = Data()
        while result.count < count {
            let length = Darwin.read(fd, &bytes, count - result.count)
            if length < 0 && errno == EINTR { continue }
            if length <= 0 { break }
            result.append(contentsOf: bytes.prefix(length))
        }
        return result
    }

    private func keep(_ bridge: HostLoopbackBridge) {
        addTeardownBlock { self.queue.sync { bridge.stop() } }
    }

    func testBytesBothWaysAndHalfCloseFromTheGuest() throws {
        let (path, listener) = try endpoint()
        let bridge = try HostLoopbackBridge(path: path, queue: queue)
        keep(bridge)
        let closed = expectation(description: "VM connection released")
        let (stream, guestEnd) = try guest(closed: closed)
        XCTAssertTrue(queue.sync { bridge.accept(stream) })
        let relay = acceptOne(listener)

        XCTAssertEqual(Darwin.write(guestEnd, "3000\n", 5), 5)
        XCTAssertEqual(read(relay, count: 5), Data("3000\n".utf8))
        XCTAssertEqual(Darwin.write(relay, "ok\n", 3), 3)
        XCTAssertEqual(read(guestEnd, count: 3), Data("ok\n".utf8))
        // The guest finishes sending; the response still comes back.
        Darwin.shutdown(guestEnd, SHUT_WR)
        XCTAssertEqual(read(relay, count: 1), Data())
        XCTAssertEqual(Darwin.write(relay, "body", 4), 4)
        Darwin.shutdown(relay, SHUT_WR)
        XCTAssertEqual(read(guestEnd, count: 4), Data("body".utf8))
        XCTAssertEqual(read(guestEnd, count: 1), Data())
        wait(for: [closed], timeout: 2)
        XCTAssertEqual(queue.sync { bridge.openConnections }, 0)
    }

    func testNothingListeningRefusesWithoutClosingTheGuestStream() throws {
        let bridge = try HostLoopbackBridge(path: "/tmp/lemma-loopback-missing-\(UUID().uuidString.prefix(8)).sock",
                                            queue: queue)
        keep(bridge)
        let (stream, _) = try guest()
        // Refused, and the stream left for the VM framework to reject.
        XCTAssertFalse(queue.sync { bridge.accept(stream) })
        XCTAssertGreaterThanOrEqual(fcntl(stream.descriptor, F_GETFD), 0)
        Darwin.close(stream.descriptor)
    }

    func testCapacityIsBounded() throws {
        let (path, listener) = try endpoint()
        let bridge = try HostLoopbackBridge(path: path, queue: queue, maximumConnections: 1)
        keep(bridge)
        let (first, _) = try guest()
        XCTAssertTrue(queue.sync { bridge.accept(first) })
        _ = acceptOne(listener)
        let (second, _) = try guest()
        XCTAssertFalse(queue.sync { bridge.accept(second) })
        Darwin.close(second.descriptor)
    }

    func testStopClosesOpenStreams() throws {
        let (path, listener) = try endpoint()
        let bridge = try HostLoopbackBridge(path: path, queue: queue)
        let (stream, guestEnd) = try guest()
        XCTAssertTrue(queue.sync { bridge.accept(stream) })
        let relay = acceptOne(listener)
        queue.sync { bridge.stop() }
        XCTAssertEqual(read(guestEnd, count: 1), Data())
        XCTAssertEqual(read(relay, count: 1), Data())
        let (late, _) = try guest()
        XCTAssertFalse(queue.sync { bridge.accept(late) })
        Darwin.close(late.descriptor)
    }
}
