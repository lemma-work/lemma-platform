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
    // core services and one bounded the sandbox runtime without asking users to manage a
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

/// Hands idle guest memory back to macOS, without asking the guest to do it all
/// at once and without mistaking "not started yet" for "finished".
///
/// This crashed a guest. `active_sandboxes == 0` was read as idle, which is true
/// once the stack has run something and false during first setup, when there are
/// no sandboxes because nothing has started. Sixty seconds into the very first
/// boot — with Postgres mid-`initdb` and migrations running — it asked the guest
/// to give back 4.5 of its 6 GiB in a single step. The kernel began mass page
/// migration to comply and took an Oops in `migrate_pages`:
///
///     BUG: Bad rss-counter state mm:… type:MM_ANONPAGES val:9      (t+4.5s)
///     Unable to handle kernel paging request at … kcompactd0       (t+65s)
///
/// Setup then failed with every vsock connect reset and migrations timing out
/// after 300s, none of which named memory.
///
/// ## Why it fired then, of all times
///
/// The balloon was driven by the *arrival* of health responses: `observe` is
/// called from `annotate`, which only runs on a `health` reply, and each idle
/// reply restarted the countdown. locald polls health every 5 seconds — but it
/// skips the poll entirely while a long local operation is running, which first
/// setup is. So the only way the countdown ever completed was for the polling to
/// stop, and the thing that stops it is the guest being busy with work the
/// sandbox count cannot see. The balloon was not merely wrong about setup; it
/// was anti-correlated with idleness, and could never have fired on a genuinely
/// idle machine.
///
/// So the clock is its own now. `observe` records what it saw and when;
/// a repeating timer decides. Silence is read as *unknown*, which is the honest
/// reading — nobody has told us anything — and unknown is never grounds to
/// reclaim.
///
/// The other two changes: it steps down instead of jumping, so the guest is
/// never asked to migrate gigabytes in one go; and it holds off for
/// `bootGraceSeconds` after start rather than until the first sandbox ever runs.
/// A grace period covers first setup, which is what the crash needed, without
/// also covering forever — "has never run a sandbox" is a state a machine can
/// legitimately sit in for its whole life, and such a machine used to keep its
/// full ceiling permanently while reporting `starting`.
///
/// A correct kernel should not Oops however rudely it is ballooned. We can only
/// stop provoking it.
private final class MemoryController {
    private let device: VZVirtioTraditionalMemoryBalloonDevice?
    private let ceiling: UInt64
    private let idleTarget = UInt64(1_536 * 1_024 * 1_024)
    /// The most memory to reclaim in one step, and how long to settle between
    /// steps. Reclaiming is page migration in the guest, and the cost of it is
    /// superlinear in how much is asked for at once.
    private let stepBytes = UInt64(1_024 * 1_024 * 1_024)
    private let stepSeconds = 20.0
    /// How long the guest must have been idle before any of it is reclaimed.
    private let idleSeconds = 60.0
    /// How long after boot the balloon stays out of the way entirely.
    ///
    /// First setup is minutes of real work with nothing running that
    /// `active_sandboxes` can count: `initdb`, migrations, image pulls. Ten
    /// minutes clears it comfortably. This replaces "has ever run a sandbox",
    /// which covered the same case and never expired.
    private let bootGraceSeconds = 600.0
    /// How stale an observation may be before it stops meaning anything.
    ///
    /// locald polls health every 5 seconds and suppresses the poll while a long
    /// local operation is in flight. A gap is therefore evidence of work, not of
    /// quiet, and reclaiming into one is exactly the mistake that crashed a
    /// guest.
    private let observationValidSeconds = 30.0
    /// How often to reconsider. Short relative to `idleSeconds`, so the decision
    /// is made from what is true now rather than from whenever a reply landed.
    private let tickSeconds = 5.0
    private let startedAt = Date()
    /// When the guest was last observed doing something, or nil if never.
    private var lastBusyAt: Date?
    /// When we last heard anything at all about the guest.
    private var lastObservedAt: Date?
    /// Whether a walk down to the idle target is already scheduled.
    private var shrinking = false
    private(set) var state = "active"

    init(virtualMachine: VZVirtualMachine, ceiling: UInt64) {
        device = virtualMachine.memoryBalloonDevices.first
            as? VZVirtioTraditionalMemoryBalloonDevice
        self.ceiling = ceiling
        if device == nil {
            state = "unsupported"
            return
        }
        scheduleTick()
    }

    /// Reconsider on our own clock, forever.
    ///
    /// The whole point of the rewrite: the decision must not be driven by the
    /// arrival of a health reply, because those stop arriving exactly when the
    /// guest is busiest.
    private func scheduleTick() {
        DispatchQueue.main.asyncAfter(deadline: .now() + tickSeconds) { [weak self] in
            guard let self else { return }
            self.reconsider()
            self.scheduleTick()
        }
    }

    func requireCapacity() {
        dispatchPrecondition(condition: .onQueue(.main))
        lastBusyAt = Date()
        lastObservedAt = lastBusyAt
        shrinking = false
        guard let device else {
            state = "unsupported"
            return
        }
        device.targetVirtualMachineMemorySize = ceiling
        state = "active"
    }

    /// Record what the guest last said. Decides nothing.
    func observe(activeSandboxes: Int) {
        dispatchPrecondition(condition: .onQueue(.main))
        let now = Date()
        lastObservedAt = now
        if activeSandboxes > 0 {
            requireCapacity()
            return
        }
        guard device != nil else {
            state = "unsupported"
            return
        }
        // Deliberately does not touch `lastBusyAt`, and deliberately does not
        // restart anything. Restarting on every idle reply is what made a
        // completed countdown impossible at 5-second polling: the timer was
        // reset twelve times for every minute it was asked to wait.
    }

    /// Decide, from what is true now rather than from when something last
    /// arrived.
    private func reconsider() {
        dispatchPrecondition(condition: .onQueue(.main))
        guard device != nil else { return }
        let now = Date()

        // Nothing is reclaimed during first setup. Minutes of `initdb`,
        // migrations and image pulls, none of it visible as a sandbox.
        guard now.timeIntervalSince(startedAt) >= bootGraceSeconds else {
            state = "starting"
            return
        }
        // Silence means a health poll is being suppressed, which locald does
        // while a long local operation runs. Unknown is not idle.
        guard let lastObservedAt, now.timeIntervalSince(lastObservedAt) < observationValidSeconds
        else {
            state = "unknown"
            shrinking = false
            return
        }
        // Busy recently enough that reclaiming would only be undone.
        if let lastBusyAt, now.timeIntervalSince(lastBusyAt) < idleSeconds {
            return
        }
        // A guest that has never been busy still qualifies once the grace period
        // is behind it: `lastBusyAt == nil` means nothing has run, and after ten
        // minutes of a live stack that is a fact about the machine rather than a
        // gap in what we know.
        guard !shrinking else { return }
        shrinking = true
        stepDown()
    }

    /// Walk the target down one step at a time, rescheduling until it lands.
    ///
    /// Abandoned by anything that clears `shrinking` — `requireCapacity` when
    /// work arrives, and `reconsider` when the guest goes quiet on us — so a
    /// reclaim nobody wants any more stops rather than finishing.
    private func stepDown() {
        dispatchPrecondition(condition: .onQueue(.main))
        guard shrinking, let device else { return }
        let floor = min(idleTarget, ceiling)
        let current = device.targetVirtualMachineMemorySize
        guard current > floor else {
            state = "idle"
            shrinking = false
            return
        }
        let next = current - min(stepBytes, current - floor)
        device.targetVirtualMachineMemorySize = next
        state = next > floor ? "idle-shrinking" : "idle-requested"
        DispatchQueue.main.asyncAfter(deadline: .now() + stepSeconds) { [weak self] in
            self?.stepDown()
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
