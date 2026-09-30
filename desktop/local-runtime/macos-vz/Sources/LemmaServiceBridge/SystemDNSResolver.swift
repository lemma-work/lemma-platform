import Dispatch
import Foundation
import dnssd

/// This Mac's resolver, through `DNSServiceQueryRecord`.
///
/// The same path every Mac app's lookups take, so whatever the Mac resolves --
/// through a VPN's per-domain resolvers, a local DNS proxy, a captive portal --
/// the guest resolves the same way.
public final class SystemDNSResolver: HostDNSResolving {
    private let queue = DispatchQueue(label: "lemma-host-dns")

    public init() {}

    public func resolve(_ query: DNSQuery, completion: @escaping (DNSResolution) -> Void) -> () -> Void {
        let lookup = Lookup(query: query, completion: completion)
        queue.async { lookup.start(on: self.queue) }
        return { [queue] in queue.async { lookup.finish(.failed) } }
    }
}

/// One outstanding `DNSServiceQueryRecord`. Touched only on the resolver's
/// queue, which is also where dnssd delivers its replies.
private final class Lookup {
    private let query: DNSQuery
    private var completion: ((DNSResolution) -> Void)?
    private var reference: DNSServiceRef?
    private var records: [DNSRecord] = []
    /// Held for as long as dnssd may call back with it as context.
    private var retained: Unmanaged<Lookup>?

    init(query: DNSQuery, completion: @escaping (DNSResolution) -> Void) {
        self.query = query
        self.completion = completion
    }

    func start(on queue: DispatchQueue) {
        guard completion != nil else { return }
        let context = Unmanaged.passRetained(self)
        retained = context
        // Intermediates, so a name that does not exist is reported rather than
        // waited on until the timeout.
        let flags = DNSServiceFlags(kDNSServiceFlagsReturnIntermediates)
        var created: DNSServiceRef?
        let status = DNSServiceQueryRecord(
            &created, flags, 0, query.name, query.type, query.recordClass,
            lookupReply, context.toOpaque()
        )
        guard status == DNSServiceErrorType(kDNSServiceErr_NoError), let created else {
            return finish(.failed)
        }
        reference = created
        guard DNSServiceSetDispatchQueue(created, queue) == DNSServiceErrorType(kDNSServiceErr_NoError) else {
            return finish(.failed)
        }
    }

    func receive(flags: DNSServiceFlags, error: DNSServiceErrorType, record: DNSRecord) {
        guard completion != nil else { return }
        switch Int(error) {
        case kDNSServiceErr_NoError:
            if flags & DNSServiceFlags(kDNSServiceFlagsAdd) != 0 { records.append(record) }
        case kDNSServiceErr_NoSuchName:
            return finish(.noSuchName)
        case kDNSServiceErr_NoSuchRecord:
            // Negative for this name, or for the end of a CNAME chain.
            return finish(matching.isEmpty ? .noData : .answers(matching))
        default:
            return finish(.failed)
        }
        // A batch is complete once nothing more is coming. A batch holding
        // only the CNAME is not the answer yet: the records it points to
        // follow in a batch of their own.
        if flags & DNSServiceFlags(kDNSServiceFlagsMoreComing) == 0, !matching.isEmpty {
            finish(.answers(matching))
        }
    }

    private var matching: [DNSRecord] {
        records.filter { query.type == DNSMessage.typeANY || $0.type == query.type }
    }

    func finish(_ resolution: DNSResolution) {
        guard let done = completion else { return }
        completion = nil
        if let reference { DNSServiceRefDeallocate(reference) }
        reference = nil
        let context = retained
        retained = nil
        done(resolution)
        // Last: releasing may free this object.
        context?.release()
    }
}

private let lookupReply: DNSServiceQueryRecordReply = {
    _, flags, _, error, _, type, recordClass, length, data, ttl, context in
    guard let context else { return }
    let lookup = Unmanaged<Lookup>.fromOpaque(context).takeUnretainedValue()
    let bytes = data.map { Data(bytes: $0, count: Int(length)) } ?? Data()
    lookup.receive(
        flags: flags,
        error: error,
        record: DNSRecord(type: type, recordClass: recordClass, ttl: ttl, data: bytes)
    )
}
