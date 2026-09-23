import Darwin
import Dispatch
import Foundation
import XCTest
@testable import LemmaServiceBridge

/// The control bridge's one-request relay, over socketpairs standing in for
/// the `lemma-runtime` caller and the guest's vsock connection.
final class ControlRelayTests: XCTestCase {
    /// `[ours, theirs]`: the relay gets `ours`, the test plays `theirs`.
    private func pair() throws -> (Int32, Int32) {
        var descriptors: [Int32] = [-1, -1]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors) == 0 else { throw POSIXError(.EIO) }
        for fd in descriptors {
            var timeout = timeval(tv_sec: 5, tv_usec: 0)
            setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
            var enabled: Int32 = 1
            setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &enabled, socklen_t(MemoryLayout<Int32>.size))
        }
        return (descriptors[0], descriptors[1])
    }

    private func relay(client: Int32, guest: Int32) -> (XCTestExpectation, () -> ControlRelayOutcome?) {
        let done = expectation(description: "relay finished")
        var outcome: ControlRelayOutcome?
        DispatchQueue.global().async {
            outcome = relayControlRequest(client: client, guest: guest, requestLimit: 1024, responseLimit: 64 * 1024)
            done.fulfill()
        }
        return (done, { outcome })
    }

    func testARequestAndItsAnswerAreCarriedWhole() throws {
        let (client, caller) = try pair()
        let (guest, guestd) = try pair()
        defer { [client, caller, guest, guestd].forEach { Darwin.close($0) } }

        let (done, outcome) = relay(client: client, guest: guest)
        try writeAll(caller, Data("{\"operation\":\"health\"}\n".utf8))
        XCTAssertEqual(try readLine(guestd, limit: 1024), Data("{\"operation\":\"health\"}\n".utf8))
        // Larger than one read, so the answer arrives in pieces.
        let answer = Data(("{\"ok\":true,\"pad\":\"" + String(repeating: "x", count: 40_000) + "\"}\n").utf8)
        try writeAll(guestd, answer)
        // Read before waiting: the answer is larger than a socket buffer, so
        // the relay cannot finish until the caller drains it.
        XCTAssertEqual(try readLine(caller, limit: 64 * 1024), answer)

        wait(for: [done], timeout: 5)
        XCTAssertEqual(outcome(), .answered)
    }

    /// A caller whose own deadline passed must not hold a request slot until
    /// the guest finishes.
    func testACallerThatLeavesReleasesTheRequestWithoutWaitingForTheGuest() throws {
        let (client, caller) = try pair()
        let (guest, guestd) = try pair()
        defer { [client, guest, guestd].forEach { Darwin.close($0) } }

        let (done, outcome) = relay(client: client, guest: guest)
        try writeAll(caller, Data("{\"operation\":\"core.images\"}\n".utf8))
        _ = try readLine(guestd, limit: 1024)
        // The guest is still working. The caller gives up.
        Darwin.close(caller)

        wait(for: [done], timeout: 5)
        XCTAssertEqual(outcome(), .clientWentAway)
    }

    func testAGuestThatDropsTheConnectionIsReportedToTheCallerAsUnavailable() throws {
        let (client, caller) = try pair()
        let (guest, guestd) = try pair()
        defer { [client, caller, guest].forEach { Darwin.close($0) } }

        let (done, outcome) = relay(client: client, guest: guest)
        try writeAll(caller, Data("{\"operation\":\"health\"}\n".utf8))
        _ = try readLine(guestd, limit: 1024)
        Darwin.close(guestd)

        wait(for: [done], timeout: 5)
        guard case .guestUnavailable = outcome() else {
            return XCTFail("expected guestUnavailable, got \(String(describing: outcome()))")
        }
        let reply = try JSONSerialization.jsonObject(with: readLine(caller, limit: 4096)) as? [String: Any]
        XCTAssertEqual(reply?["ok"] as? Bool, false)
        let error = reply?["error"] as? [String: Any]
        XCTAssertEqual(error?["code"] as? String, "guest_unavailable")
        XCTAssertEqual(error?["retryable"] as? Bool, true)
        XCTAssertEqual(error?["status_code"] as? Int, 503)
    }

    func testACallerThatSendsNothingNeverReachesTheGuest() throws {
        let (client, caller) = try pair()
        let (guest, guestd) = try pair()
        defer { [client, guest, guestd].forEach { Darwin.close($0) } }

        let (done, outcome) = relay(client: client, guest: guest)
        Darwin.close(caller)

        wait(for: [done], timeout: 5)
        XCTAssertEqual(outcome(), .clientSentNothing)
        var byte: UInt8 = 0
        XCTAssertEqual(recv(guestd, &byte, 1, MSG_DONTWAIT), -1, "nothing was forwarded")
    }

    func testAnOversizedAnswerIsRefusedRatherThanRelayed() throws {
        let (client, caller) = try pair()
        let (guest, guestd) = try pair()
        defer { [client, caller, guest, guestd].forEach { Darwin.close($0) } }

        let done = expectation(description: "relay finished")
        var outcome: ControlRelayOutcome?
        DispatchQueue.global().async {
            outcome = relayControlRequest(client: client, guest: guest, requestLimit: 1024, responseLimit: 16)
            done.fulfill()
        }
        try writeAll(caller, Data("{}\n".utf8))
        _ = try readLine(guestd, limit: 1024)
        try writeAll(guestd, Data((String(repeating: "x", count: 64) + "\n").utf8))

        wait(for: [done], timeout: 5)
        XCTAssertEqual(outcome, .guestUnavailable("message exceeded 16 bytes"))
    }

    func testAnUnterminatedLineEndsAtEndOfStream() throws {
        let (ours, theirs) = try pair()
        defer { Darwin.close(ours) }
        try writeAll(theirs, Data("partial".utf8))
        Darwin.close(theirs)
        XCTAssertEqual(try readLine(ours, limit: 1024), Data("partial".utf8))
    }

    func testTheUnavailableReplyIsOneLineOfJSON() throws {
        let reply = guestUnavailableReply("Private guest is unavailable")
        XCTAssertEqual(reply.last, 0x0A)
        XCTAssertEqual(reply.filter { $0 == 0x0A }.count, 1)
        let decoded = try JSONSerialization.jsonObject(with: reply) as? [String: Any]
        XCTAssertEqual((decoded?["error"] as? [String: Any])?["message"] as? String, "Private guest is unavailable")
    }
}
