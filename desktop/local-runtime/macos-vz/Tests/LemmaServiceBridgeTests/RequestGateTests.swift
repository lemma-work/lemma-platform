import XCTest
@testable import LemmaServiceBridge

final class RequestGateTests: XCTestCase {
    /// The defect this exists to prevent: one slow request stopping the rest.
    ///
    /// The guest bridge served one request at a time over one shared channel.
    /// `core.images` is allowed seventy-five minutes, and while it held that
    /// channel the health probe -- five seconds -- waited behind it, so the
    /// host decided its guest had died during the pull it had asked for.
    func testASlowRequestDoesNotHoldUpTheOnesBehindIt() {
        let gate = RequestGate<String>(limit: 8)

        let slow = gate.admit("core.images")
        XCTAssertEqual(slow, ["core.images"])

        // The probe starts immediately, with the pull still in flight.
        XCTAssertEqual(gate.admit("health"), ["health"])
        XCTAssertEqual(gate.inFlight, 2)
        XCTAssertEqual(gate.queued, 0)
    }

    /// Concurrency without a bound is the other way to lose a guest.
    func testBeyondTheLimitClientsWaitRatherThanConnect() {
        let gate = RequestGate<Int>(limit: 2)

        XCTAssertEqual(gate.admit(1), [1])
        XCTAssertEqual(gate.admit(2), [2])
        XCTAssertEqual(gate.admit(3), [], "the third has to wait")
        XCTAssertEqual(gate.inFlight, 2)
        XCTAssertEqual(gate.queued, 1)

        XCTAssertEqual(gate.finish(), [3], "and start as soon as there is room")
        XCTAssertEqual(gate.inFlight, 2)
        XCTAssertEqual(gate.queued, 0)
    }

    /// Order matters: the request that has waited longest goes first.
    func testWaitingClientsAreAdmittedInTheOrderTheyArrived() {
        let gate = RequestGate<Int>(limit: 1)
        _ = gate.admit(1)
        _ = gate.admit(2)
        _ = gate.admit(3)

        XCTAssertEqual(gate.finish(), [2])
        XCTAssertEqual(gate.finish(), [3])
        XCTAssertEqual(gate.finish(), [])
        XCTAssertEqual(gate.inFlight, 0)
    }
}
