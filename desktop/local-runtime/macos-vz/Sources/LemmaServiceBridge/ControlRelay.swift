import Darwin
import Foundation

/// How one control request's trip through the bridge ended.
///
/// Extracted from `lemma-vz` for the same reason as `RequestGate`: this is
/// where the bridge's defects were, and none of it needs a virtual machine.
public enum ControlRelayOutcome: Equatable {
    /// The guest answered and the answer was handed to the client.
    case answered
    /// The client connected and closed without sending a request.
    case clientSentNothing
    /// The client gave up while the guest was still working.
    ///
    /// `lemma-runtime` closes its socket when its own deadline passes. Waiting
    /// on the guest past that point holds one of `RequestGate`'s few slots for
    /// nobody, and enough abandoned slow operations queue every later request
    /// -- the health probe included -- behind them.
    case clientWentAway
    /// The guest's answer arrived after the client could take it.
    case clientWriteFailed(String)
    /// The request could not be read from the client.
    case clientReadFailed(String)
    /// The guest connection failed; the client was told it is unavailable.
    case guestUnavailable(String)
}

public enum LineError: Error, Equatable, CustomStringConvertible {
    case tooLong(limit: Int)
    case system(String, Int32)
    /// The descriptor being watched hung up before a line arrived.
    case abandoned

    public var description: String {
        switch self {
        case .tooLong(let limit): return "message exceeded \(limit) bytes"
        case .system(let operation, let code):
            return "\(operation) failed: \(String(cString: strerror(code)))"
        case .abandoned: return "the other side went away"
        }
    }
}

/// What the client is sent when the guest cannot answer.
public func guestUnavailableReply(_ message: String) -> Data {
    let body: [String: Any] = [
        "ok": false,
        "error": [
            "code": "guest_unavailable",
            "message": message,
            "retryable": true,
            "status_code": 503,
        ] as [String: Any],
    ]
    var encoded = (try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])) ?? Data()
    encoded.append(0x0A)
    return encoded
}

/// Carry one request line from `client` to `guest`, and one reply line back.
///
/// Does not close either descriptor: both belong to the caller, which has a
/// request slot to release whatever happened here.
public func relayControlRequest(
    client: Int32,
    guest: Int32,
    requestLimit: Int,
    responseLimit: Int
) -> ControlRelayOutcome {
    let request: Data
    do {
        request = try readLine(client, limit: requestLimit)
    } catch {
        return .clientReadFailed("\(error)")
    }
    guard !request.isEmpty else { return .clientSentNothing }
    let response: Data
    do {
        try writeAll(guest, request)
        response = try readLine(guest, limit: responseLimit, abandonIfClosed: client)
        guard !response.isEmpty else { throw LineError.system("read", ECONNRESET) }
    } catch LineError.abandoned {
        return .clientWentAway
    } catch {
        _ = try? writeAll(client, guestUnavailableReply("Guest control channel is unavailable"))
        return .guestUnavailable("\(error)")
    }
    do {
        try writeAll(client, response)
    } catch {
        // A caller may close its socket just as the answer arrives. The
        // guest did the work; the answer has nowhere to go, and that is not
        // worth failing over.
        return .clientWriteFailed("\(error)")
    }
    return .answered
}

/// Write every byte, retrying short writes and interrupts.
public func writeAll(_ descriptor: Int32, _ data: Data) throws {
    try data.withUnsafeBytes { raw in
        guard let base = raw.baseAddress else { return }
        var offset = 0
        while offset < raw.count {
            let count = Darwin.write(descriptor, base.advanced(by: offset), raw.count - offset)
            if count < 0 {
                if errno == EINTR { continue }
                throw LineError.system("write", errno)
            }
            offset += count
        }
    }
}

/// Read one newline-terminated line, or everything up to end of stream.
///
/// In chunks rather than a byte per `read(2)`: replies run to megabytes, on the
/// path of every workspace operation. The protocol is one line per connection
/// in each direction, so nothing after the newline is lost by reading past it.
///
/// With `abandonIfClosed`, the wait also watches that descriptor, and gives
/// up with `LineError.abandoned` if it hangs up first.
public func readLine(_ descriptor: Int32, limit: Int, abandonIfClosed watched: Int32? = nil) throws -> Data {
    var line = Data()
    var chunk = [UInt8](repeating: 0, count: 64 * 1024)
    var watching = watched
    while true {
        if let peer = watching {
            switch try waitForInput(descriptor, orHangUpOf: peer) {
            case .ready: break
            case .peerGone: throw LineError.abandoned
            case .peerSentData: watching = nil
            }
        }
        let count = Darwin.read(descriptor, &chunk, chunk.count)
        if count < 0 {
            if errno == EINTR { continue }
            throw LineError.system("read", errno)
        }
        if count == 0 { return line }
        if let newline = chunk[..<count].firstIndex(of: 0x0A) {
            line.append(contentsOf: chunk[...newline])
            guard line.count <= limit else { throw LineError.tooLong(limit: limit) }
            return line
        }
        line.append(contentsOf: chunk[..<count])
        guard line.count <= limit else { throw LineError.tooLong(limit: limit) }
    }
}

private enum Readiness {
    case ready
    case peerGone
    /// The watched side sent bytes, which a well-behaved client never does
    /// mid-request. Stop watching it rather than spin on a readable socket.
    case peerSentData
}

private func waitForInput(_ descriptor: Int32, orHangUpOf peer: Int32) throws -> Readiness {
    var descriptors = [
        pollfd(fd: descriptor, events: Int16(POLLIN), revents: 0),
        pollfd(fd: peer, events: Int16(POLLIN), revents: 0),
    ]
    while true {
        let ready = poll(&descriptors, 2, -1)
        if ready < 0 {
            if errno == EINTR { continue }
            throw LineError.system("poll", errno)
        }
        // Checked first: if the guest's answer and the client's departure
        // arrive together, the answer is still read.
        if descriptors[0].revents != 0 { return .ready }
        let peerEvents = Int32(descriptors[1].revents)
        if peerEvents & (POLLHUP | POLLERR | POLLNVAL) != 0 { return .peerGone }
        if peerEvents & POLLIN != 0 {
            var byte: UInt8 = 0
            let peeked = recv(peer, &byte, 1, MSG_PEEK | MSG_DONTWAIT)
            if peeked == 0 { return .peerGone }
            if peeked < 0 && errno != EAGAIN && errno != EINTR { return .peerGone }
            return .peerSentData
        }
    }
}
