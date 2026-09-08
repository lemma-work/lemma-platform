import Darwin
import Dispatch
import Foundation

/// Bounded duplex transfer. DispatchIO owns the descriptors, including on cancellation.
final class StreamRelay {
    private let queue: DispatchQueue
    private let left: DispatchIO
    private let right: DispatchIO
    private let leftFD: Int32
    private let rightFD: Int32
    private var endedDirections = 0
    private var stopped = false
    private let finished: () -> Void
    private static let chunkSize = 64 * 1024

    init(leftFD: Int32, rightFD: Int32, queue: DispatchQueue, finished: @escaping () -> Void) {
        self.queue = queue
        self.leftFD = leftFD
        self.rightFD = rightFD
        self.finished = finished
        left = DispatchIO(type: .stream, fileDescriptor: leftFD, queue: queue) { _ in
            Darwin.close(leftFD)
        }
        right = DispatchIO(type: .stream, fileDescriptor: rightFD, queue: queue) { _ in
            Darwin.close(rightFD)
        }
        left.setLimit(lowWater: 1)
        right.setLimit(lowWater: 1)
    }

    func start() {
        read(from: left, into: right, destinationFD: rightFD)
        read(from: right, into: left, destinationFD: leftFD)
    }

    func stop() {
        guard !stopped else { return }
        stopped = true
        // Wake both pending reads and backpressured writes before closing channels.
        Darwin.shutdown(leftFD, SHUT_RDWR)
        Darwin.shutdown(rightFD, SHUT_RDWR)
        left.close(flags: .stop)
        right.close(flags: .stop)
        finished()
    }

    private func read(from source: DispatchIO, into destination: DispatchIO, destinationFD: Int32) {
        guard !stopped else { return }
        var received = 0
        var pendingWrites = 0
        var readDone = false
        var advanced = false
        func advance() {
            guard !stopped, readDone, pendingWrites == 0, !advanced else { return }
            advanced = true
            if received < Self.chunkSize {
                Darwin.shutdown(destinationFD, SHUT_WR)
                endedDirections += 1
                if endedDirections == 2 { stop() }
            } else {
                read(from: source, into: destination, destinationFD: destinationFD)
            }
        }
        source.read(offset: 0, length: Self.chunkSize, queue: queue) { [self] done, data, error in
            guard !stopped else { return }
            guard error == 0 else { stop(); return }
            if let data, !data.isEmpty {
                received += data.count
                pendingWrites += 1
                destination.write(offset: 0, data: data, queue: queue) { [self] done, _, error in
                    guard !stopped else { return }
                    guard error == 0 else { stop(); return }
                    if done {
                        pendingWrites -= 1
                        advance()
                    }
                }
            }
            readDone = done
            advance()
        }
    }
}
