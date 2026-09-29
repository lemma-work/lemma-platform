import Darwin
import Dispatch
import Foundation
import XCTest
@testable import LemmaServiceBridge

/// A resolver that answers what it is told to, or never.
private final class ScriptedResolver: HostDNSResolving {
    var answer: DNSResolution?
    private(set) var asked: [DNSQuery] = []
    private(set) var cancelled = 0
    private let lock = NSLock()

    init(_ answer: DNSResolution?) { self.answer = answer }

    func resolve(_ query: DNSQuery, completion: @escaping (DNSResolution) -> Void) -> () -> Void {
        lock.lock()
        asked.append(query)
        lock.unlock()
        if let answer { DispatchQueue.global().async { completion(answer) } }
        return { [self] in
            lock.lock()
            cancelled += 1
            lock.unlock()
        }
    }
}

/// A standard query for `name`, as a stub resolver writes one.
func dnsQuery(_ name: String, type: UInt16 = 1, id: UInt16 = 0x1234, opt: Bool = false) -> Data {
    var message = Data()
    DNSMessage.append16(&message, id)
    DNSMessage.append16(&message, 0x0100)
    DNSMessage.append16(&message, 1)
    DNSMessage.append16(&message, 0)
    DNSMessage.append16(&message, 0)
    DNSMessage.append16(&message, opt ? 1 : 0)
    for label in name.split(separator: ".") {
        message.append(UInt8(label.utf8.count))
        message.append(contentsOf: Array(label.utf8))
    }
    message.append(0)
    DNSMessage.append16(&message, type)
    DNSMessage.append16(&message, 1)
    if opt {
        message.append(0)
        DNSMessage.append16(&message, DNSMessage.typeOPT)
        DNSMessage.append16(&message, 4096)
        message.append(contentsOf: [0, 0, 0, 0, 0, 0])
    }
    return message
}

final class HostDNSBridgeTests: XCTestCase {
    private let queue = DispatchQueue(label: "lemma-host-dns-tests")
    private let address = DNSRecord(type: 1, recordClass: 1, ttl: 300, data: Data([192, 0, 2, 7]))

    private func guest(closed: XCTestExpectation? = nil) throws -> (GuestStream, Int32) {
        var descriptors: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors) == 0 else { throw POSIXError(.EIO) }
        for fd in descriptors {
            var timeout = timeval(tv_sec: 3, tv_usec: 0)
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            var enabled: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        }
        let handed = descriptors[0]
        addTeardownBlock { Darwin.close(descriptors[1]) }
        return (GuestStream(descriptor: handed) { Darwin.close(handed); closed?.fulfill() }, descriptors[1])
    }

    private func send(_ fd: Int32, _ message: Data) {
        var framed = Data()
        DNSMessage.append16(&framed, UInt16(message.count))
        framed.append(message)
        XCTAssertTrue(writeFully(fd, framed, deadline: Date().addingTimeInterval(2)))
    }

    private func receive(_ fd: Int32) -> [UInt8]? {
        let deadline = Date().addingTimeInterval(3)
        guard let length = readExactly(fd, 2, deadline: deadline) else { return nil }
        return readExactly(fd, Int(DNSMessage.read16([UInt8](length), 0)), deadline: deadline).map { [UInt8]($0) }
    }

    private func bridge(_ resolver: HostDNSResolving, queryTimeout: TimeInterval = 2,
                        maximum: Int = 64) -> HostDNSBridge {
        let bridge = HostDNSBridge(resolver: resolver, queue: queue,
                                   maximumConcurrentQueries: maximum, queryTimeout: queryTimeout, ioTimeout: 1)
        addTeardownBlock { self.queue.sync { bridge.stop() } }
        return bridge
    }

    func testAnswersUnderTheQuestionsNameAndClosesTheStream() throws {
        let resolver = ScriptedResolver(.answers([
            DNSRecord(type: 5, recordClass: 1, ttl: 60, data: Data([0])),
            address,
        ]))
        let dns = bridge(resolver)
        let closed = expectation(description: "stream released")
        let (stream, guestEnd) = try guest(closed: closed)
        XCTAssertTrue(queue.sync { dns.accept(stream) })
        send(guestEnd, dnsQuery("registry-1.docker.io", opt: true))

        let reply = try XCTUnwrap(receive(guestEnd))
        XCTAssertEqual(DNSMessage.read16(reply, 0), 0x1234)
        XCTAssertEqual(DNSMessage.read16(reply, 2), 0x8180, "QR, RD echoed, RA, NOERROR")
        XCTAssertEqual(DNSMessage.read16(reply, 6), 1, "the CNAME is flattened away")
        XCTAssertEqual(DNSMessage.read16(reply, 10), 1, "EDNS is answered with EDNS")
        XCTAssertEqual(resolver.asked.first?.name, "registry-1.docker.io.")
        let answer = 12 + 22 + 4
        XCTAssertEqual(DNSMessage.read16(reply, answer), 0xC00C)
        XCTAssertEqual(Array(reply[(answer + 12)..<(answer + 16)]), [192, 0, 2, 7])
        wait(for: [closed], timeout: 2)
        XCTAssertEqual(queue.sync { dns.openConnections }, 0)
    }

    func testAResolverThatNeverAnswersIsServerFailureAndCancelled() throws {
        let resolver = ScriptedResolver(nil)
        let dns = bridge(resolver, queryTimeout: 0.2)
        let (stream, guestEnd) = try guest()
        XCTAssertTrue(queue.sync { dns.accept(stream) })
        send(guestEnd, dnsQuery("example.com"))
        let reply = try XCTUnwrap(receive(guestEnd))
        XCTAssertEqual(reply[3] & 0x0F, DNSMessage.rcodeServerFailure)
        XCTAssertEqual(resolver.cancelled, 1)
    }

    func testNegativeAnswersKeepTheirMeaning() {
        let query = try! DNSMessage.parseQuery(dnsQuery("missing.example")).get()
        let missing = [UInt8](DNSMessage.response(to: query, .noSuchName))
        XCTAssertEqual(missing[3] & 0x0F, DNSMessage.rcodeNameError)
        let empty = [UInt8](DNSMessage.response(to: query, .noData))
        XCTAssertEqual(empty[3] & 0x0F, 0)
        XCTAssertEqual(DNSMessage.read16(empty, 6), 0)
    }

    func testAnAnyAnswerNeverPairsACnameWithOtherData() {
        let cname = DNSRecord(type: DNSMessage.typeCNAME, recordClass: 1, ttl: 60, data: Data([3, 0x77, 0x77, 0x77, 0]))
        let address = DNSRecord(type: 1, recordClass: 1, ttl: 60, data: Data([192, 0, 2, 1]))
        let any = try! DNSMessage.parseQuery(dnsQuery("alias.example", type: DNSMessage.typeANY)).get()
        let both = [UInt8](DNSMessage.response(to: any, .answers([cname, address])))
        XCTAssertEqual(DNSMessage.read16(both, 6), 1, "the CNAME is dropped beside data")
        let alone = [UInt8](DNSMessage.response(to: any, .answers([cname])))
        XCTAssertEqual(DNSMessage.read16(alone, 6), 1, "a CNAME on its own is the answer")
    }

    func testAnOversizedQueryIsNotRead() throws {
        let resolver = ScriptedResolver(.noData)
        let dns = bridge(resolver)
        let (stream, guestEnd) = try guest()
        XCTAssertTrue(queue.sync { dns.accept(stream) })
        var framed = Data()
        DNSMessage.append16(&framed, UInt16(HostDNSBridge.maximumQueryBytes + 1))
        XCTAssertTrue(writeFully(guestEnd, framed, deadline: Date().addingTimeInterval(1)))
        XCTAssertNil(receive(guestEnd))
        XCTAssertTrue(resolver.asked.isEmpty)
    }

    func testMalformedAndUnsupportedQueriesSayWhich() throws {
        let dns = bridge(ScriptedResolver(.noData))
        var truncated = dnsQuery("example.com")
        truncated.removeLast(3)
        var notify = dnsQuery("example.com")
        notify[2] = 0x20
        for (message, rcode) in [(truncated, DNSMessage.rcodeFormatError),
                                 (notify, DNSMessage.rcodeNotImplemented)] {
            let (stream, guestEnd) = try guest()
            XCTAssertTrue(queue.sync { dns.accept(stream) })
            send(guestEnd, message)
            let reply = try XCTUnwrap(receive(guestEnd))
            XCTAssertEqual(reply[3] & 0x0F, rcode)
            XCTAssertEqual(DNSMessage.read16(reply, 0), 0x1234)
        }
    }

    func testALargeAnswerIsTruncatedSoTheResolverRetriesOverTCP() {
        let query = try! DNSMessage.parseQuery(dnsQuery("example.com", type: 16)).get()
        let text = DNSRecord(type: 16, recordClass: 1, ttl: 1, data: Data(repeating: 1, count: 400))
        let reply = [UInt8](DNSMessage.response(to: query, .answers([text, text]), limit: 512))
        XCTAssertNotEqual(DNSMessage.read16(reply, 2) & 0x0200, 0)
        XCTAssertEqual(DNSMessage.read16(reply, 6), 0)
    }

    func testNamesAreEscapedForTheResolver() {
        var message = dnsQuery("a")
        // Replace the one-byte label "a" with "a.b\u{1}" so it must be escaped.
        message.replaceSubrange(12..<14, with: [4, 0x61, 0x2E, 0x62, 0x01])
        XCTAssertEqual(try DNSMessage.parseQuery(message).get().name, "a\\.b\\001.")
        XCTAssertEqual(try DNSMessage.parseQuery(dnsQuery("")).get().name, ".")
    }

    func testCapacityIsBoundedAndStopRefusesMore() throws {
        let dns = bridge(ScriptedResolver(nil), queryTimeout: 5, maximum: 1)
        let (first, _) = try guest()
        XCTAssertTrue(queue.sync { dns.accept(first) })
        let (second, _) = try guest()
        XCTAssertFalse(queue.sync { dns.accept(second) })
        Darwin.close(second.descriptor)
        queue.sync { dns.stop() }
        let (late, _) = try guest()
        XCTAssertFalse(queue.sync { dns.accept(late) })
        Darwin.close(late.descriptor)
    }

    /// The real resolver, on a name every Mac answers locally.
    func testTheSystemResolverAnswersLocalhost() throws {
        let query = try DNSMessage.parseQuery(dnsQuery("localhost")).get()
        let answered = expectation(description: "answered")
        var result: DNSResolution?
        _ = SystemDNSResolver().resolve(query) { resolution in
            result = resolution
            answered.fulfill()
        }
        wait(for: [answered], timeout: 5)
        guard case .answers(let records) = result else { return XCTFail("got \(String(describing: result))") }
        XCTAssertTrue(records.contains { $0.data == Data([127, 0, 0, 1]) })
    }
}
