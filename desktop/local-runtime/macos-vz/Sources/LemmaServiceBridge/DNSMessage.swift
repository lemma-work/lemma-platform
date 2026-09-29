import Foundation

/// One DNS question, as the guest asked it.
///
/// Only what answering needs: the header fields the reply must echo, the name
/// in the presentation form macOS's resolver takes, and the question section's
/// own bytes, which go back unchanged so the guest's resolver can match the
/// reply to what it sent.
public struct DNSQuery: Equatable {
    public let id: UInt16
    public let flags: UInt16
    public let name: String
    public let type: UInt16
    public let recordClass: UInt16
    let question: Data
    /// Whether the query carried an EDNS OPT record. A reply without one tells
    /// systemd-resolved the server cannot do EDNS, and it then falls back to
    /// 512-byte UDP for every later query.
    let hasOPT: Bool
}

/// One answer record, with its data exactly as the resolver returned it.
public struct DNSRecord: Equatable {
    public let type: UInt16
    public let recordClass: UInt16
    public let ttl: UInt32
    public let data: Data

    public init(type: UInt16, recordClass: UInt16, ttl: UInt32, data: Data) {
        self.type = type
        self.recordClass = recordClass
        self.ttl = ttl
        self.data = data
    }
}

/// What the Mac's resolver said about a question.
public enum DNSResolution: Equatable {
    case answers([DNSRecord])
    /// The name exists but has no record of that type, or the resolver cannot
    /// tell that apart from the name not existing.
    case noData
    case noSuchName
    /// Anything else, a timeout included. Answered as SERVFAIL, which is what
    /// makes the guest's resolver ask the gateway instead.
    case failed
}

enum DNSQueryError: Error, Equatable {
    /// Not even a header: there is no id to answer, so nothing is sent.
    case unreadable
    case malformed(id: UInt16, flags: UInt16)
    case unsupported(id: UInt16, flags: UInt16)
}

/// Reading one query and writing its reply, in the RFC 1035 wire format.
///
/// Deliberately small: one question per message, standard queries only, no
/// name compression in the question (no resolver sends it there). Everything
/// else is refused with the code that says so rather than guessed at.
enum DNSMessage {
    static let headerLength = 12
    static let maximumNameLength = 255
    static let typeOPT: UInt16 = 41
    static let typeCNAME: UInt16 = 5
    static let typeANY: UInt16 = 255
    /// The UDP payload size advertised back. 1232 bytes is the size the DNS
    /// flag day settled on: it fits an IPv6 packet without fragmenting.
    static let advertisedPayload: UInt16 = 1232

    static let rcodeFormatError: UInt8 = 1
    static let rcodeServerFailure: UInt8 = 2
    static let rcodeNameError: UInt8 = 3
    static let rcodeNotImplemented: UInt8 = 4

    static func parseQuery(_ message: Data) -> Result<DNSQuery, DNSQueryError> {
        let bytes = [UInt8](message)
        guard bytes.count >= headerLength else { return .failure(.unreadable) }
        let id = read16(bytes, 0)
        let flags = read16(bytes, 2)
        if flags & 0x8000 != 0 { return .failure(.malformed(id: id, flags: flags)) }
        if (flags >> 11) & 0xF != 0 { return .failure(.unsupported(id: id, flags: flags)) }
        guard read16(bytes, 4) == 1 else { return .failure(.malformed(id: id, flags: flags)) }
        guard let (name, end) = readName(bytes, from: headerLength), end + 4 <= bytes.count else {
            return .failure(.malformed(id: id, flags: flags))
        }
        let questionEnd = end + 4
        let additional = read16(bytes, 10)
        let hasOPT = additional > 0
            && read16(bytes, 6) == 0 && read16(bytes, 8) == 0
            && questionEnd + 11 <= bytes.count
            && bytes[questionEnd] == 0
            && read16(bytes, questionEnd + 1) == typeOPT
        return .success(DNSQuery(
            id: id,
            flags: flags,
            name: name,
            type: read16(bytes, end),
            recordClass: read16(bytes, end + 2),
            question: Data(bytes[headerLength..<questionEnd]),
            hasOPT: hasOPT
        ))
    }

    /// The reply to `query`. Larger than `limit` and the answers are dropped
    /// and TC set, which sends the resolver back over TCP.
    static func response(to query: DNSQuery, _ resolution: DNSResolution, limit: Int = 65_535) -> Data {
        var records: [DNSRecord] = []
        var rcode: UInt8 = 0
        switch resolution {
        case .answers(let found): records = found.filter { answers(query, $0) }
        case .noData: break
        case .noSuchName: rcode = rcodeNameError
        case .failed: rcode = rcodeServerFailure
        }
        let full = build(query, records: records, rcode: rcode, truncated: false)
        guard full.count > limit else { return full }
        return build(query, records: [], rcode: rcode, truncated: true)
    }

    /// A reply with no question section, for a query that could not be read.
    static func errorResponse(id: UInt16, flags: UInt16, rcode: UInt8) -> Data {
        var out = Data()
        append16(&out, id)
        append16(&out, replyFlags(flags, rcode: rcode, truncated: false))
        out.append(contentsOf: [0, 0, 0, 0, 0, 0, 0, 0])
        return out
    }

    /// Records are returned under the question's own name.
    ///
    /// macOS follows a CNAME chain itself and hands back the records at the
    /// end of it, named for where the chain ended. A stub resolver discards an
    /// answer whose owner is not the name it asked about, so the chain is
    /// flattened: the address is answered for the name that was asked.
    private static func answers(_ query: DNSQuery, _ record: DNSRecord) -> Bool {
        query.type == typeANY || record.type == query.type
    }

    private static func build(_ query: DNSQuery, records: [DNSRecord], rcode: UInt8, truncated: Bool) -> Data {
        let kept = records.filter { $0.data.count <= Int(UInt16.max) }
        var out = Data()
        append16(&out, query.id)
        append16(&out, replyFlags(query.flags, rcode: rcode, truncated: truncated))
        append16(&out, 1)
        append16(&out, UInt16(kept.count))
        append16(&out, 0)
        append16(&out, query.hasOPT ? 1 : 0)
        out.append(query.question)
        for record in kept {
            // A pointer to the question's name, which always starts at offset 12.
            append16(&out, 0xC00C)
            append16(&out, record.type)
            append16(&out, record.recordClass)
            append16(&out, UInt16(truncatingIfNeeded: record.ttl >> 16))
            append16(&out, UInt16(truncatingIfNeeded: record.ttl))
            append16(&out, UInt16(record.data.count))
            out.append(record.data)
        }
        if query.hasOPT {
            out.append(0)
            append16(&out, typeOPT)
            append16(&out, advertisedPayload)
            out.append(contentsOf: [0, 0, 0, 0, 0, 0])
        }
        return out
    }

    /// QR set, the opcode and RD echoed, RA set: this answers recursively.
    private static func replyFlags(_ flags: UInt16, rcode: UInt8, truncated: Bool) -> UInt16 {
        var reply: UInt16 = 0x8000 | (flags & 0x7800) | (flags & 0x0100) | 0x0080
        if truncated { reply |= 0x0200 }
        return reply | UInt16(rcode & 0x0F)
    }

    /// The name at `start`, in presentation form, and the offset after it.
    ///
    /// Escaped the way `DNSServiceQueryRecord` reads a name: a dot or a
    /// backslash inside a label is backslashed, and anything unprintable is
    /// `\DDD`, so a label is never split or joined on the way through.
    static func readName(_ bytes: [UInt8], from start: Int) -> (String, Int)? {
        var offset = start
        var labels: [String] = []
        var wireLength = 1
        while true {
            guard offset < bytes.count else { return nil }
            let length = Int(bytes[offset])
            offset += 1
            if length == 0 { break }
            guard length <= 63, offset + length <= bytes.count else { return nil }
            wireLength += length + 1
            guard wireLength <= maximumNameLength else { return nil }
            var label = ""
            for byte in bytes[offset..<(offset + length)] {
                switch byte {
                case UInt8(ascii: "."), UInt8(ascii: "\\"):
                    label += "\\" + String(UnicodeScalar(byte))
                case 0x21...0x7E:
                    label += String(UnicodeScalar(byte))
                default:
                    label += String(format: "\\%03d", byte)
                }
            }
            labels.append(label)
            offset += length
        }
        return (labels.isEmpty ? "." : labels.joined(separator: ".") + ".", offset)
    }

    static func read16(_ bytes: [UInt8], _ offset: Int) -> UInt16 {
        UInt16(bytes[offset]) << 8 | UInt16(bytes[offset + 1])
    }

    static func append16(_ data: inout Data, _ value: UInt16) {
        data.append(UInt8(value >> 8))
        data.append(UInt8(value & 0xFF))
    }
}
