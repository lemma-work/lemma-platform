import Darwin
import Dispatch
import Foundation
import XCTest
@testable import LemmaServiceBridge

final class ServiceBridgeTests: XCTestCase {
    private let queue = DispatchQueue(label: "lemma-service-bridge-tests")

    private func pair() throws -> [Int32] {
        var descriptors: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors) == 0 else { throw POSIXError(.EIO) }
        for fd in descriptors { boundIO(fd) }
        return descriptors
    }

    private func boundIO(_ fd: Int32) {
        var timeout = timeval(tv_sec: 2, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        var enabled: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
    }

    private func path() -> String {
        let directory = "/tmp/lemma-service-\(UUID().uuidString)"
        try! FileManager.default.createDirectory(atPath: directory, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try? FileManager.default.removeItem(atPath: directory) }
        return directory + "/service.sock"
    }

    private func connect(_ path: String) throws -> Int32 {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw POSIXError(.EIO) }
        boundIO(fd)
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
        withUnsafeMutablePointer(to: &address.sun_path) {
            $0.withMemoryRebound(to: CChar.self, capacity: 104) { _ = strlcpy($0, path, 104) }
        }
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard result == 0 else { Darwin.close(fd); throw POSIXError(.ECONNREFUSED) }
        addTeardownBlock { Darwin.close(fd) }
        return fd
    }

    private func read(_ fd: Int32, count: Int) -> Data {
        var bytes = [UInt8](repeating: 0, count: count)
        var result = Data()
        while result.count < count {
            let length = Darwin.read(fd, &bytes, count - result.count)
            if length < 0 && errno == EINTR { continue }
            XCTAssertGreaterThanOrEqual(length, 0, "read failed: \(errno)")
            if length <= 0 { break }
            result.append(contentsOf: bytes.prefix(length))
        }
        return result
    }

    private func keep(_ bridge: ServiceBridge) {
        addTeardownBlock { self.queue.sync { bridge.stop() } }
    }

    func testSmallDuplexMessagesAndHalfClose() throws {
        let sockets = try pair()
        defer { Darwin.close(sockets[1]) }
        let closed = expectation(description: "VM connection released")
        let path = path()
        let bridge = try ServiceBridge(path: path, queue: queue) { done in
            done(.success(GuestStream(descriptor: sockets[0]) {
                Darwin.close(sockets[0]); closed.fulfill()
            }))
        }
        keep(bridge)
        let client = try connect(path)
        XCTAssertEqual(read(client, count: 1), Data([0]))
        XCTAssertEqual(Darwin.write(client, "query", 5), 5)
        XCTAssertEqual(read(sockets[1], count: 5), Data("query".utf8))
        XCTAssertEqual(Darwin.write(sockets[1], "answer", 6), 6)
        XCTAssertEqual(read(client, count: 6), Data("answer".utf8))
        Darwin.shutdown(client, SHUT_WR)
        XCTAssertEqual(read(sockets[1], count: 1), Data())
        XCTAssertEqual(Darwin.write(sockets[1], "tail", 4), 4)
        Darwin.shutdown(sockets[1], SHUT_WR)
        XCTAssertEqual(read(client, count: 4), Data("tail".utf8))
        XCTAssertEqual(read(client, count: 1), Data())
        wait(for: [closed], timeout: 2)
    }

    func testStopClosesIdleConnections() throws {
        let sockets = try pair()
        defer { Darwin.close(sockets[1]) }
        let path = path()
        let bridge = try ServiceBridge(path: path, queue: queue) { done in
            done(.success(GuestStream(descriptor: sockets[0]) { Darwin.close(sockets[0]) }))
        }
        keep(bridge)
        let client = try connect(path)
        XCTAssertEqual(read(client, count: 1), Data([0]))
        queue.sync { bridge.stop() }
        XCTAssertEqual(read(client, count: 1), Data())
        XCTAssertEqual(read(sockets[1], count: 1), Data())
    }

    func testFailedGuestConnectionDoesNotReportReady() throws {
        let path = path()
        let bridge = try ServiceBridge(path: path, queue: queue) { $0(.failure(POSIXError(.ECONNREFUSED))) }
        keep(bridge)
        XCTAssertEqual(read(try connect(path), count: 1), Data())
    }

    func testConnectionDeadlineClosesClientAndRejectsLateSuccess() throws {
        let path = path()
        var completion: ((Result<GuestStream, Error>) -> Void)?
        let bridge = try ServiceBridge(path: path, queue: queue, connectTimeout: 0.05) { completion = $0 }
        keep(bridge)
        XCTAssertEqual(read(try connect(path), count: 1), Data())
        let sockets = try pair()
        defer { Darwin.close(sockets[1]) }
        let closed = expectation(description: "late connection closed")
        queue.sync {
            XCTAssertNotNil(completion)
            completion?(.success(GuestStream(descriptor: sockets[0]) {
                Darwin.close(sockets[0]); closed.fulfill()
            }))
        }
        wait(for: [closed], timeout: 2)
        XCTAssertEqual(read(sockets[1], count: 1), Data())
    }

    func testExistingListenerIsNeverUnlinked() throws {
        let path = path()
        let bridge = try ServiceBridge(path: path, queue: queue) { $0(.failure(POSIXError(.ECONNREFUSED))) }
        keep(bridge)
        XCTAssertThrowsError(try ServiceBridge(path: path, queue: queue) { _ in })
        XCTAssertEqual(read(try connect(path), count: 1), Data())
        let attributes = try FileManager.default.attributesOfItem(atPath: path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
    }

    func testPendingConnectionsCountTowardsCapacity() throws {
        let path = path()
        let started = expectation(description: "first connection admitted")
        let bridge = try ServiceBridge(path: path, queue: queue, maximumConnections: 1) { _ in started.fulfill() }
        keep(bridge)
        let first = try connect(path)
        wait(for: [started], timeout: 2)
        XCTAssertEqual(read(try connect(path), count: 1), Data())
        queue.sync { bridge.stop() }
        XCTAssertEqual(read(first, count: 1), Data())
    }

    func testDroppingOwnerClosesActiveConnections() throws {
        let sockets = try pair()
        defer { Darwin.close(sockets[1]) }
        let path = path()
        var bridge: ServiceBridge? = try ServiceBridge(path: path, queue: queue) { done in
            done(.success(GuestStream(descriptor: sockets[0]) { Darwin.close(sockets[0]) }))
        }
        let client = try connect(path)
        XCTAssertEqual(read(client, count: 1), Data([0]))
        XCTAssertNotNil(bridge)
        bridge = nil
        XCTAssertEqual(read(client, count: 1), Data())
        XCTAssertEqual(read(sockets[1], count: 1), Data())
        queue.sync {}
        XCTAssertFalse(FileManager.default.fileExists(atPath: path))
    }

    func testTransferAcrossManyChunksWithSlowReader() throws {
        let sockets = try pair()
        defer { Darwin.close(sockets[1]) }
        let path = path()
        let bridge = try ServiceBridge(path: path, queue: queue) { done in
            done(.success(GuestStream(descriptor: sockets[0]) { Darwin.close(sockets[0]) }))
        }
        keep(bridge)
        let client = try connect(path)
        XCTAssertEqual(read(client, count: 1), Data([0]))
        let payload = Data((0..<(2 * 1024 * 1024)).map { UInt8($0 % 251) })
        let writer = expectation(description: "large write and half-close completed")
        DispatchQueue.global().async {
            payload.withUnsafeBytes { buffer in
                var offset = 0
                while offset < buffer.count {
                    let count = Darwin.write(client, buffer.baseAddress!.advanced(by: offset), buffer.count - offset)
                    if count < 0 && errno == EINTR { continue }
                    XCTAssertGreaterThan(count, 0)
                    if count <= 0 { break }
                    offset += count
                }
            }
            Darwin.shutdown(client, SHUT_WR)
            writer.fulfill()
        }
        var received = Data()
        while received.count < payload.count {
            let chunk = read(sockets[1], count: min(4096, payload.count - received.count))
            if chunk.isEmpty { break }
            received.append(chunk)
            usleep(500)
        }
        XCTAssertEqual(received, payload)
        XCTAssertEqual(read(sockets[1], count: 1), Data())
        wait(for: [writer], timeout: 3)
    }
}
