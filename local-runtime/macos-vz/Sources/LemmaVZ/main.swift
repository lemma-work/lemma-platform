import Darwin
import Foundation
import Virtualization

private let version = "0.1.0"
private let guestPort: UInt32 = 42_411
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
    let physical = ProcessInfo.processInfo.physicalMemory
    // VZ allocates guest memory on demand, so expose enough headroom for the
    // core services and one bounded AgentBox without asking users to manage a
    // Podman-style reservation. Keep at least half of an 8 GiB Mac for macOS,
    // then scale automatically on larger machines.
    let adaptiveMemory = max(UInt64(4 * 1_024 * 1_024 * 1_024), physical / 3)
    configuration.memorySize = min(UInt64(8 * 1_024 * 1_024 * 1_024), adaptiveMemory)
    configuration.memoryBalloonDevices = [
        VZVirtioTraditionalMemoryBalloonDeviceConfiguration()
    ]

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
        "systemd.unit=lemma-runtime.target",
    ].joined(separator: " ")
    configuration.bootLoader = bootLoader

    let diskAttachment = try VZDiskImageStorageDeviceAttachment(
        url: paths.disk,
        readOnly: true,
        cachingMode: .automatic,
        synchronizationMode: .full
    )
    let dataAttachment = try VZDiskImageStorageDeviceAttachment(
        url: paths.dataDisk,
        readOnly: false,
        cachingMode: .automatic,
        synchronizationMode: .full
    )
    configuration.storageDevices = [
        VZVirtioBlockDeviceConfiguration(attachment: diskAttachment),
        VZVirtioBlockDeviceConfiguration(attachment: dataAttachment),
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

private final class MemoryController {
    private let device: VZVirtioTraditionalMemoryBalloonDevice?
    private let ceiling: UInt64
    private let idleTarget = UInt64(1_536 * 1_024 * 1_024)
    private var idleGeneration = 0
    private(set) var state = "active"

    init(virtualMachine: VZVirtualMachine, ceiling: UInt64) {
        device = virtualMachine.memoryBalloonDevices.first
            as? VZVirtioTraditionalMemoryBalloonDevice
        self.ceiling = ceiling
        if device == nil {
            state = "unsupported"
        }
    }

    func requireCapacity() {
        dispatchPrecondition(condition: .onQueue(.main))
        idleGeneration += 1
        guard let device else {
            state = "unsupported"
            return
        }
        device.targetVirtualMachineMemorySize = ceiling
        state = "active"
    }

    func observe(activeSandboxes: Int) {
        dispatchPrecondition(condition: .onQueue(.main))
        if activeSandboxes > 0 {
            requireCapacity()
            return
        }
        idleGeneration += 1
        let generation = idleGeneration
        DispatchQueue.main.asyncAfter(deadline: .now() + 60) { [weak self] in
            guard let self, self.idleGeneration == generation else { return }
            guard let device = self.device else {
                self.state = "unsupported"
                return
            }
            device.targetVirtualMachineMemorySize = min(self.idleTarget, self.ceiling)
            self.state = "idle-requested"
        }
    }

    func annotate(_ response: Data) -> Data {
        dispatchPrecondition(condition: .onQueue(.main))
        guard var object = try? JSONSerialization.jsonObject(with: response) as? [String: Any],
              var result = object["result"] as? [String: Any] else {
            return response
        }
        let active = result["active_sandboxes"] as? Int ?? 0
        observe(activeSandboxes: active)
        result["balloon_state"] = state
        result["balloon_target_bytes"] =
            device?.targetVirtualMachineMemorySize ?? ceiling
        object["result"] = result
        return (try? JSONSerialization.data(withJSONObject: object)) ?? response
    }
}

private final class VirtualMachineDelegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        fputs("lemma-vz: guest stopped\n", stderr)
        fflush(stderr)
        exit(EXIT_SUCCESS)
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        fputs("lemma-vz: guest stopped with error: \(error.localizedDescription)\n", stderr)
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
        fputs("lemma-vz: graceful stop requested\n", stderr)
        fflush(stderr)
        if virtualMachine.canRequestStop {
            do {
                try virtualMachine.requestStop()
                return
            } catch {
                fputs("lemma-vz: graceful guest stop failed: \(error.localizedDescription)\n", stderr)
            }
        }
        guard virtualMachine.canStop else {
            fputs("lemma-vz: guest cannot be stopped in its current state\n", stderr)
            exit(EXIT_FAILURE)
        }
        // Last-resort VZ stop is destructive, but is still preferable to the
        // host killing the helper while disk writes are in flight.
        virtualMachine.stop { error in
            if let error {
                fputs("lemma-vz: forced guest stop failed: \(error.localizedDescription)\n", stderr)
                exit(EXIT_FAILURE)
            }
        }
    }
}

private func writeAll(_ descriptor: Int32, _ data: Data) throws {
    try data.withUnsafeBytes { rawBuffer in
        guard let base = rawBuffer.baseAddress else { return }
        var offset = 0
        while offset < rawBuffer.count {
            let count = Darwin.write(descriptor, base.advanced(by: offset), rawBuffer.count - offset)
            if count < 0 {
                if errno == EINTR { continue }
                throw RuntimeError.system("write", errno)
            }
            offset += count
        }
    }
}

private func readLine(_ descriptor: Int32, limit: Int) throws -> Data {
    var result = Data()
    var byte: UInt8 = 0
    while result.count <= limit {
        let count = Darwin.read(descriptor, &byte, 1)
        if count == 0 { break }
        if count < 0 {
            if errno == EINTR { continue }
            throw RuntimeError.system("read", errno)
        }
        result.append(byte)
        if byte == 0x0A { break }
    }
    guard result.count <= limit else { throw RuntimeError.invalid("Message exceeded size limit") }
    return result
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
    // Virtualization.framework's virtio-vsock transport is designed for a
    // long-lived RPC channel. Reconnecting for every readiness poll caused
    // connection churn severe enough to corrupt multiple Linux kernel lines.
    // Keep one guest connection and serialize the small control-plane calls.
    private var guestConnection: VZVirtioSocketConnection?
    private var pendingClients: [Int32] = []
    private var requestActive = false
    private let memory: MemoryController

    init(
        socketDevice: VZVirtioSocketDevice,
        socketPath: String,
        memory: MemoryController
    ) throws {
        self.socketDevice = socketDevice
        self.memory = memory
        listener = try unixListener(path: socketPath)
    }

    func serve() {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            while true {
                let client = accept(listener, nil, nil)
                if client < 0 {
                    if errno == EINTR { continue }
                    fputs("lemma-vz: accept failed: \(String(cString: strerror(errno)))\n", stderr)
                    continue
                }
                _ = fcntl(client, F_SETFD, FD_CLOEXEC)
                DispatchQueue.main.async { [self] in
                    pendingClients.append(client)
                    processNext()
                }
            }
        }
    }

    private func processNext() {
        dispatchPrecondition(condition: .onQueue(.main))
        guard !requestActive, !pendingClients.isEmpty else { return }
        requestActive = true
        let client = pendingClients.removeFirst()
        if let connection = guestConnection {
            transfer(client: client, connection: connection)
            return
        }
        socketDevice.connect(toPort: guestPort) { [self] result in
            switch result {
            case .failure(let error):
                fputs("lemma-vz: guest connect failed: \(error.localizedDescription)\n", stderr)
                fail(client: client)
            case .success(let connection):
                guestConnection = connection
                transfer(client: client, connection: connection)
            }
        }
    }

    private func transfer(client: Int32, connection: VZVirtioSocketConnection) {
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            let request: Data
            do {
                request = try readLine(client, limit: maxRequestBytes)
            } catch {
                fputs("lemma-vz: client read failed: \(error.localizedDescription)\n", stderr)
                close(client)
                finishRequest(keepGuestConnection: true)
                return
            }
            guard !request.isEmpty else {
                close(client)
                finishRequest(keepGuestConnection: true)
                return
            }
            if let object = try? JSONSerialization.jsonObject(with: request) as? [String: Any],
               object["operation"] as? String == "sandbox.ensure" {
                DispatchQueue.main.async { [memory] in memory.requireCapacity() }
            }
            do {
                try writeAll(connection.fileDescriptor, request)
                let response = try readLine(
                    connection.fileDescriptor,
                    limit: maxResponseBytes
                )
                guard !response.isEmpty else {
                    throw RuntimeError.invalid("Guest control channel closed")
                }
                let delivered: Data
                if let object = try? JSONSerialization.jsonObject(with: request)
                    as? [String: Any],
                   object["operation"] as? String == "health" {
                    delivered = DispatchQueue.main.sync {
                        memory.annotate(response)
                    }
                } else {
                    delivered = response
                }
                do {
                    try writeAll(client, delivered)
                } catch {
                    // A timed-out bridge caller may close its Unix socket while
                    // the guest operation finishes. The persistent guest
                    // channel remains valid and must not be discarded.
                    fputs("lemma-vz: client write failed: \(error.localizedDescription)\n", stderr)
                }
                close(client)
                finishRequest(keepGuestConnection: true)
            } catch {
                fputs("lemma-vz: guest bridge failed: \(error.localizedDescription)\n", stderr)
                let payload = "{\"ok\":false,\"error\":{\"code\":\"guest_unavailable\",\"message\":\"Guest control channel is unavailable\",\"retryable\":true,\"status_code\":503}}\n"
                _ = try? writeAll(client, Data(payload.utf8))
                close(client)
                finishRequest(keepGuestConnection: false)
            }
        }
    }

    private func fail(client: Int32) {
        let payload = "{\"ok\":false,\"error\":{\"code\":\"guest_unavailable\",\"message\":\"Private guest is unavailable\",\"retryable\":true,\"status_code\":503}}\n"
        _ = try? writeAll(client, Data(payload.utf8))
        close(client)
        requestActive = false
        processNext()
    }

    private func finishRequest(keepGuestConnection: Bool) {
        DispatchQueue.main.async { [self] in
            if !keepGuestConnection {
                guestConnection?.close()
                guestConnection = nil
            }
            requestActive = false
            processNext()
        }
    }
}

private func argument(_ name: String, in arguments: [String]) throws -> String {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
        throw RuntimeError.invalid("Missing required argument \(name)")
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
    let memoryCeiling = vmConfiguration.memorySize
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
    var bridge: GuestBridge?
    vm.start { result in
        switch result {
        case .failure(let error):
            fputs("lemma-vz: could not start guest: \(error.localizedDescription)\n", stderr)
            exit(EXIT_FAILURE)
        case .success:
            guard let socketDevice = vm.socketDevices.first as? VZVirtioSocketDevice else {
                fputs("lemma-vz: guest socket device is unavailable\n", stderr)
                exit(EXIT_FAILURE)
            }
            do {
                let memory = MemoryController(
                    virtualMachine: vm,
                    ceiling: memoryCeiling
                )
                bridge = try GuestBridge(
                    socketDevice: socketDevice,
                    socketPath: socketPath,
                    memory: memory
                )
                bridge?.serve()
            } catch {
                fputs("lemma-vz: control bridge failed: \(error.localizedDescription)\n", stderr)
                exit(EXIT_FAILURE)
            }
        }
    }
    withExtendedLifetime((vm, delegate, bridge, stopCoordinator, signalSources)) {
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
        print("lemma-vz \(version)\n\nUSAGE:\n  lemma-vz serve --release <dir> --runtime <state-dir> --control-socket <path> --control-share <dir>\n  lemma-vz validate --release <dir> --runtime <state-dir>")
    default:
        throw RuntimeError.invalid("Unknown command \(arguments[0])")
    }
}

do {
    try main()
} catch {
    fputs("lemma-vz: \(error.localizedDescription)\n", stderr)
    exit(EXIT_FAILURE)
}
