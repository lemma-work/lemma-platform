import Dispatch

/// How many guest requests may be in flight, and what happens to the rest.
///
/// Extracted from the guest bridge because it is the whole of a defect and
/// none of it needs a virtual machine to exercise.
///
/// The bridge used to hold one guest connection and serve one request at a
/// time. Sharing a channel is why: two requests on one connection can hand
/// each caller the other's reply. Serialising fixed that and bought something
/// worse -- `core.images` may run for seventy-five minutes, because a first
/// install really can take that long on a slow line, and every later request
/// waited behind it. Including the health probe, which has five seconds. The
/// host concluded its own guest had died in the middle of the pull it had
/// asked for, tore down the database forwarders, and restarted everything.
///
/// A connection per request removes the reply-mixing without the queue. This
/// is what keeps that from becoming unbounded: a bound well under the guest's
/// own limit of 32 connections, and a queue for the rest, so a client waits
/// rather than opening a connection nothing will read.
public final class RequestGate<Client> {
    private let limit: Int
    private var waiting: [Client] = []
    private var active = 0

    public init(limit: Int) {
        precondition(limit > 0, "a gate that admits nobody is a closed door")
        self.limit = limit
    }

    /// Queue a client, and hand back everything that may start now.
    ///
    /// Returns rather than calls, so the caller decides which queue the work
    /// runs on and this stays free of one.
    public func admit(_ client: Client) -> [Client] {
        waiting.append(client)
        return drain()
    }

    /// One request finished; hand back whatever that lets in.
    public func finish() -> [Client] {
        precondition(active > 0, "finished a request that was never admitted")
        active -= 1
        return drain()
    }

    public var inFlight: Int { active }
    public var queued: Int { waiting.count }

    private func drain() -> [Client] {
        var starting: [Client] = []
        while active < limit, !waiting.isEmpty {
            starting.append(waiting.removeFirst())
            active += 1
        }
        return starting
    }
}
