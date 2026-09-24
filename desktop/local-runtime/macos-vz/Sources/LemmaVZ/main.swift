import Darwin
import Foundation
import LemmaServiceBridge
import Virtualization

private let version = "0.1.0"

/// One line of `vz.log`, stamped.
///
/// Stamped so a transport reset in `vz.log` can be lined up against the
/// backend's own logs; an undated line cannot be placed before, during or after
/// the failure it might explain.
private func vzLog(_ message: String) {
    var now = timeval()
    gettimeofday(&now, nil)
    var seconds = time_t(now.tv_sec)
    var parts = tm()
    gmtime_r(&seconds, &parts)
    var stamp = [CChar](repeating: 0, count: 32)
    _ = strftime(&stamp, stamp.count, "%Y-%m-%dT%H:%M:%S", &parts)
    let instant = String(cString: stamp)
    let millis = Int(now.tv_usec) / 1000
    fputs(String(format: "%@.%03dZ lemma-vz: %@\n", instant, millis, message), stderr)
}

private let guestPort: UInt32 = 42_411
/// guestd's sandbox tunnel. See `sandbox_tunnel` in lemma-guestd.
private let sandboxTunnelPort: UInt32 = 42_412
/// Where guestd opens loopback relay streams *to* this host. See
/// `host_loopback` in lemma-guestd and `loopback_relay` in locald.
private let hostLoopbackPort: UInt32 = 42_413
private let maxRequestBytes = 1_048_576
private let maxResponseBytes = 4_194_304

private struct RuntimePaths {
    let release: URL
    let state: URL
    let kernel: URL
    let initialRamdisk: URL
    let disk: URL
    let dataDisk: URL
    let machineIdentifier: URL
    let networkMACAddress: URL
    let consoleLog: URL

    init(release: String, state: String) throws {
        self.release = URL(
            fileURLWithPath: NSString(string: release).expandingTildeInPath,
            isDirectory: true
        ).standardizedFileURL
        self.state = URL(
            fileURLWithPath: NSString(string: state).expandingTildeInPath,
            isDirectory: true
        ).standardizedFileURL
        kernel = self.release.appendingPathComponent("vmlinuz")
        initialRamdisk = self.release.appendingPathComponent("initrd")
        disk = self.release.appendingPathComponent("disk.raw")
        dataDisk = self.state.appendingPathComponent("data.raw")
        machineIdentifier = self.state.appendingPathComponent("machine-id")
        networkMACAddress = self.state.appendingPathComponent("network-mac")
        consoleLog = self.state.appendingPathComponent("console.log")
        for (label, url) in [
            ("kernel", kernel),
            ("initial RAM disk", initialRamdisk),
            ("guest disk", disk),
            ("guest data disk", dataDisk),
        ] where !FileManager.default.fileExists(atPath: url.path) {
            throw RuntimeError.invalid("Managed runtime is missing \(label): \(url.path)")
        }
    }
}

private enum RuntimeError: LocalizedError {
    case invalid(String)
    case system(String, Int32)

    var errorDescription: String? {
        switch self {
        case .invalid(let message): return message
        case .system(let operation, let code):
            return "\(operation) failed: \(String(cString: strerror(code)))"
        }
    }
}

private func privateFileHandle(_ url: URL, truncate: Bool = false) throws -> FileHandle {
    let flags = O_WRONLY | O_CREAT | (truncate ? O_TRUNC : O_APPEND) | O_CLOEXEC
    let descriptor = open(url.path, flags, S_IRUSR | S_IWUSR)
    guard descriptor >= 0 else { throw RuntimeError.system("open \(url.path)", errno) }
    return FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
}

private func machineIdentifier(at url: URL) throws -> VZGenericMachineIdentifier {
    if let data = try? Data(contentsOf: url),
       let identifier = VZGenericMachineIdentifier(dataRepresentation: data) {
        return identifier
    }
    let identifier = VZGenericMachineIdentifier()
    try identifier.dataRepresentation.write(to: url, options: .atomic)
    try FileManager.default.setAttributes(
        [.posixPermissions: NSNumber(value: 0o600)],
        ofItemAtPath: url.path
    )
    return identifier
}

private func networkMACAddress(at url: URL) throws -> VZMACAddress {
    if let value = try? String(contentsOf: url, encoding: .utf8)
        .trimmingCharacters(in: .whitespacesAndNewlines),
       let address = VZMACAddress(string: value) {
        return address
    }
    let address = VZMACAddress.randomLocallyAdministered()
    try address.string.write(to: url, atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes(
        [.posixPermissions: NSNumber(value: 0o600)],
        ofItemAtPath: url.path
    )
    return address
}

private func configuration(
    paths: RuntimePaths,
    controlShare: URL? = nil
) throws -> VZVirtualMachineConfiguration {
    let configuration = VZVirtualMachineConfiguration()
    let processors = ProcessInfo.processInfo.activeProcessorCount
    configuration.cpuCount = min(4, max(2, processors / 2))
    // Keep one bounded allocation throughout the guest's lifetime. Changing
    // guest memory requires lifecycle and workload qualification, not a guess
    // that zero running sandboxes means the kernel has spare pages to reclaim.
    configuration.memorySize = 4 * 1_024 * 1_024 * 1_024

    let platform = VZGenericPlatformConfiguration()
    platform.machineIdentifier = try machineIdentifier(at: paths.machineIdentifier)
    configuration.platform = platform

    let bootLoader = VZLinuxBootLoader(kernelURL: paths.kernel)
    bootLoader.initialRamdiskURL = paths.initialRamdisk
    bootLoader.commandLine = [
        "root=/dev/vda",
        "ro",
        "console=hvc0",
        "panic=1",
        "systemd.volatile=state",
        "systemd.unit=multi-user.target",
    ].joined(separator: " ")
    configuration.bootLoader = bootLoader

    // Explicit host caching avoids the automatic disk path's Apple Silicon
    // corruption risk. Full synchronization still honors guest flushes.
    let diskAttachment = try VZDiskImageStorageDeviceAttachment(
        url: paths.disk,
        readOnly: true,
        cachingMode: .cached,
        synchronizationMode: .full
    )
    let dataAttachment = try VZDiskImageStorageDeviceAttachment(
        url: paths.dataDisk,
        readOnly: false,
        cachingMode: .cached,
        synchronizationMode: .full
    )
    configuration.storageDevices = [
        VZVirtioBlockDeviceConfiguration(attachment: diskAttachment),
        VZNVMExpressControllerDeviceConfiguration(attachment: dataAttachment),
    ]

    let network = VZVirtioNetworkDeviceConfiguration()
    // Virtualization.framework otherwise chooses a different address on every
    // process launch. A persistent MAC gives macOS NAT/DHCP one stable lease
    // and prevents the host from chasing a stale guest address after restart.
    network.macAddress = try networkMACAddress(at: paths.networkMACAddress)
    network.attachment = VZNATNetworkDeviceAttachment()
    configuration.networkDevices = [network]
    configuration.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    configuration.socketDevices = [VZVirtioSocketDeviceConfiguration()]
    if let controlShare {
        let directory = VZSharedDirectory(url: controlShare, readOnly: true)
        let share = VZSingleDirectoryShare(directory: directory)
        let fileSystem = VZVirtioFileSystemDeviceConfiguration(tag: "lemma-control")
        fileSystem.share = share
        configuration.directorySharingDevices = [fileSystem]
    }

    let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
    guard let serialInput = FileHandle(forReadingAtPath: "/dev/null") else {
        throw RuntimeError.invalid("Could not open /dev/null for guest serial input")
    }
    serial.attachment = VZFileHandleSerialPortAttachment(
        fileHandleForReading: serialInput,
        fileHandleForWriting: try privateFileHandle(paths.consoleLog)
    )
    configuration.serialPorts = [serial]
    try configuration.validate()
    return configuration
}

private final class VirtualMachineDelegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        vzLog("guest stopped")
        fflush(stderr)
        exit(EXIT_SUCCESS)
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        vzLog("guest stopped with error: \(error.localizedDescription)")
        fflush(stderr)
        exit(EXIT_FAILURE)
    }
}

private final class StopCoordinator {
    private let virtualMachine: VZVirtualMachine
    private var requested = false

    init(virtualMachine: VZVirtualMachine) {
        self.virtualMachine = virtualMachine
    }

    func request() {
        guard !requested else { return }
        requested = true
        vzLog("graceful stop requested")
        fflush(stderr)
        if virtualMachine.canRequestStop {
            do {
                try virtualMachine.requestStop()
                return
            } catch {
                vzLog("graceful guest stop failed: \(error.localizedDescription)")
            }
        }
        guard virtualMachine.canStop else {
            vzLog("guest cannot be stopped in its current state")
            exit(EXIT_FAILURE)
        }
        // Last-resort VZ stop is destructive, but is still preferable to the
        // host killing the helper while disk writes are in flight.
        virtualMachine.stop { error in
            if let error {
                vzLog("forced guest stop failed: \(error.localizedDescription)")
                exit(EXIT_FAILURE)
            }
        }
    }
}

private func unixListener(path: String) throws -> Int32 {
    guard path.utf8.count < MemoryLayout<sockaddr_un>.size - 2 else {
        throw RuntimeError.invalid("Guest control socket path is too long")
    }
    _ = unlink(path)
    let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw RuntimeError.system("socket", errno) }
    _ = fcntl(descriptor, F_SETFD, FD_CLOEXEC)
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    address.sun_len = UInt8(MemoryLayout<sockaddr_un>.size)
    withUnsafeMutablePointer(to: &address.sun_path) { pointer in
        pointer.withMemoryRebound(to: CChar.self, capacity: 104) { destination in
            _ = strlcpy(destination, path, 104)
        }
    }
    let result = withUnsafePointer(to: &address) { pointer in
        pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { socketAddress in
            bind(descriptor, socketAddress, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    guard result == 0 else {
        let code = errno
        close(descriptor)
        throw RuntimeError.system("bind", code)
    }
    guard chmod(path, S_IRUSR | S_IWUSR) == 0 else {
        let code = errno
        close(descriptor)
        throw RuntimeError.system("chmod", code)
    }
    guard listen(descriptor, 16) == 0 else {
        let code = errno
        close(descriptor)
        throw RuntimeError.system("listen", code)
    }
    return descriptor
}

private final class GuestBridge {
    private let socketDevice: VZVirtioSocketDevice
    private let listener: Int32
    // A guest connection per client, not one channel served one request at a
    // time.
    //
    // Sharing a channel is why this used to serialise: two requests on one
    // connection can hand each caller the other's reply, and a caller that
    // abandons a slow request leaves an unread response for whoever is next.
    // Serialising fixed that and introduced a worse one. `core.images` is
    // allowed seventy-five minutes because a first install really can take
    // that long on a slow line, and while it held the channel every later
    // request waited behind it -- including the health probe, which has five
    // seconds. So the host concluded its own guest had died in the middle of
    // the pull it had asked for. The guest has served concurrent connections
    // since it stopped answering them from its accept loop; this was the last
    // place that funnelled them back into one.
    //
    // Bounded, and well under the guest's own limit of 32: a client that
    // cannot be served yet waits rather than opening a connection nothing will
    // read. That policy lives in `RequestGate`, in the library target, because
    // it is the whole of the defect and none of it needs a virtual machine to
    // exercise.
    private let gate = RequestGate<Int32>(limit: 8)

    init(
        socketDevice: VZVirtioSocketDevice,
        socketPath: String
    ) throws {
        self.socketDevice = socketDevice
        listener = try unixListener(path: socketPath)
    }

    func serve() {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            while true {
                let client = accept(listener, nil, nil)
                if client < 0 {
                    if errno == EINTR { continue }
                    vzLog("accept failed: \(String(cString: strerror(errno)))")
                    continue
                }
                _ = fcntl(client, F_SETFD, FD_CLOEXEC)
                DispatchQueue.main.async { [self] in
                    start(gate.admit(client))
                }
            }
        }
    }

    private func start(_ clients: [Int32]) {
        dispatchPrecondition(condition: .onQueue(.main))
        for client in clients {
            socketDevice.connect(toPort: guestPort) { [self] result in
                switch result {
                case .failure(let error):
                    vzLog("guest connect failed on port \(guestPort): \(error.localizedDescription)")
                    fail(client: client)
                case .success(let connection):
                    transfer(client: client, connection: connection)
                }
            }
        }
    }

    private func transfer(client: Int32, connection: VZVirtioSocketConnection) {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let outcome = relayControlRequest(
                client: client,
                guest: connection.fileDescriptor,
                requestLimit: maxRequestBytes,
                responseLimit: maxResponseBytes
            )
            switch outcome {
            case .answered, .clientSentNothing:
                break
            case .clientWentAway:
                // Closing the guest connection below is what gives the slot
                // back; the guest sees the close and abandons the reply.
                vzLog("client on port \(guestPort) went away before the guest answered; released its request slot")
            case .clientReadFailed(let error):
                vzLog("client read failed on port \(guestPort): \(error)")
            case .clientWriteFailed(let error):
                vzLog("client write failed on port \(guestPort): \(error)")
            case .guestUnavailable(let error):
                vzLog("guest bridge failed: \(error)")
            }
            close(client)
            finishRequest(connection)
        }
    }

    private func fail(client: Int32) {
        _ = try? writeAll(client, guestUnavailableReply("Private guest is unavailable"))
        close(client)
        finishRequest(nil)
    }

    /// One request is over: close its guest connection and admit the next.
    ///
    /// Closing unconditionally is the difference this rework buys. The shared
    /// channel had to be kept alive across a caller that gave up -- discarding
    /// it would have cost every other caller too -- so a failure had to decide
    /// whether the channel was still good. A connection owned by one request
    /// is simply finished with when that request is.
    private func finishRequest(_ connection: VZVirtioSocketConnection?) {
        // On the main queue, which is the VM's: every other Virtualization
        // object here is touched there, and closing a connection from the
        // worker that just used it would be the one exception.
        DispatchQueue.main.async { [self] in
            connection?.close()
            start(gate.finish())
        }
    }
}

private func argument(_ name: String, in arguments: [String]) throws -> String {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        throw RuntimeError.invalid("Missing required argument \(name)")
    }
    return arguments[index + 1]
}

/// Accepts the guest's loopback relay streams and hands them to locald.
///
/// Called by the framework on the VM's queue, which is also the bridge's.
private final class HostLoopbackListener: NSObject, VZVirtioSocketListenerDelegate {
    let bridge: HostLoopbackBridge

    init(bridge: HostLoopbackBridge) {
        self.bridge = bridge
    }

    func listener(
        _ listener: VZVirtioSocketListener,
        shouldAcceptNewConnection connection: VZVirtioSocketConnection,
        from socketDevice: VZVirtioSocketDevice
    ) -> Bool {
        let accepted = bridge.accept(GuestStream(descriptor: connection.fileDescriptor) {
            connection.close()
        })
        if !accepted {
            vzLog("loopback relay stream refused: locald's relay is not reachable or is at capacity")
        }
        return accepted
    }
}

private final class RuntimeBridges {
    var control: GuestBridge?
    var services: [ServiceBridge] = []
    var hostLoopback: (VZVirtioSocketListener, HostLoopbackListener)?
}

/// An optional argument's value, or nil when it was not given.
private func optionalArgument(_ name: String, in arguments: [String]) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        return nil
    }
    return arguments[index + 1]
}

private func serve(arguments: [String]) throws -> Never {
    let runtimePaths = try RuntimePaths(
        release: argument("--release", in: arguments),
        state: argument("--runtime", in: arguments)
    )
    let socketPath = NSString(
        string: try argument("--control-socket", in: arguments)
    ).expandingTildeInPath
    let controlShare = URL(
        fileURLWithPath: NSString(
            string: try argument("--control-share", in: arguments)
        ).expandingTildeInPath,
        isDirectory: true
    ).standardizedFileURL
    guard FileManager.default.fileExists(atPath: controlShare.path) else {
        throw RuntimeError.invalid("Control share is missing: \(controlShare.path)")
    }
    // Optional, so a runtime manager that predates the relay still starts the
    // guest; without it guestd's relay streams are simply refused.
    let hostLoopbackSocket = optionalArgument("--host-loopback-socket", in: arguments)
        .map { NSString(string: $0).expandingTildeInPath }
    let socketParent = URL(fileURLWithPath: socketPath).deletingLastPathComponent()
    try FileManager.default.createDirectory(
        at: socketParent,
        withIntermediateDirectories: true,
        attributes: [.posixPermissions: NSNumber(value: 0o700)]
    )
    let vmConfiguration = try configuration(
        paths: runtimePaths,
        controlShare: controlShare
    )
    let vm = VZVirtualMachine(configuration: vmConfiguration)
    let delegate = VirtualMachineDelegate()
    vm.delegate = delegate
    let stopCoordinator = StopCoordinator(virtualMachine: vm)
    var signalSources: [DispatchSourceSignal] = []
    for signalNumber in [SIGTERM, SIGINT] {
        signal(signalNumber, SIG_IGN)
        let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
        source.setEventHandler {
            stopCoordinator.request()
        }
        source.resume()
        signalSources.append(source)
    }
    let bridges = RuntimeBridges()
    vm.start { result in
        switch result {
        case .failure(let error):
            vzLog("could not start guest: \(error.localizedDescription)")
            exit(EXIT_FAILURE)
        case .success:
            guard let socketDevice = vm.socketDevices.first as? VZVirtioSocketDevice else {
                vzLog("guest socket device is unavailable")
                exit(EXIT_FAILURE)
            }
            do {
                // Postgres, Redis and SuperTokens, and the sandbox tunnel: every
                // stream the host opens into the guest arrives this way rather
                // than over the guest's network address, which macOS gates
                // behind a Local Network permission a background process
                // cannot be prompted for.
                for port: UInt32 in [5432, 6379, 3567, sandboxTunnelPort] {
                    let service = try ServiceBridge(
                        path: socketParent.appendingPathComponent("service-\(port).sock").path
                    ) { completed in
                        socketDevice.connect(toPort: port) { result in
                            completed(result.map { connection in
                                GuestStream(descriptor: connection.fileDescriptor) { connection.close() }
                            })
                        }
                    }
                    bridges.services.append(service)
                }
                if let hostLoopbackSocket {
                    let listener = VZVirtioSocketListener()
                    let delegate = HostLoopbackListener(
                        bridge: try HostLoopbackBridge(path: hostLoopbackSocket)
                    )
                    listener.delegate = delegate
                    socketDevice.setSocketListener(listener, forPort: hostLoopbackPort)
                    bridges.hostLoopback = (listener, delegate)
                }
                bridges.control = try GuestBridge(
                    socketDevice: socketDevice,
                    socketPath: socketPath
                )
                bridges.control?.serve()
            } catch {
                vzLog("control bridge failed: \(error.localizedDescription)")
                exit(EXIT_FAILURE)
            }
        }
    }
    withExtendedLifetime((vm, delegate, bridges, stopCoordinator, signalSources)) {
        RunLoop.main.run(until: Date.distantFuture)
    }
    fatalError("unreachable")
}

private func main() throws {
    // Runtime bridge clients have their own bounded request timeouts. A late
    // guest response must close only that client connection; the default
    // SIGPIPE disposition would otherwise terminate the VM helper and take
    // PostgreSQL, Redis, auth, and every other sandbox down with it.
    signal(SIGPIPE, SIG_IGN)
    let arguments = Array(CommandLine.arguments.dropFirst())
    switch arguments.first {
    case "serve":
        try serve(arguments: Array(arguments.dropFirst()))
    case "validate":
        let paths = try RuntimePaths(
            release: argument("--release", in: arguments),
            state: argument("--runtime", in: arguments)
        )
        _ = try configuration(paths: paths)
        print("valid")
    case "--version", "-V":
        print("lemma-vz \(version)")
    case "--help", "-h", nil:
        print("lemma-vz \(version)\n\nUSAGE:\n  lemma-vz serve --release <dir> --runtime <state-dir> --control-socket <path> --control-share <dir> [--host-loopback-socket <path>]\n  lemma-vz validate --release <dir> --runtime <state-dir>")
    default:
        throw RuntimeError.invalid("Unknown command \(arguments[0])")
    }
}

do {
    try main()
} catch {
    vzLog("\(error.localizedDescription)")
    exit(EXIT_FAILURE)
}
