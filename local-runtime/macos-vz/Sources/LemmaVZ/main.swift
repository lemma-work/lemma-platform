import Darwin
import Foundation
import Virtualization

private let version = "0.1.0"
private let guestPort: UInt32 = 42_411
private let maxRequestBytes = 1_048_576
private let maxResponseBytes = 4_194_304

private struct RuntimePaths {
    let root: URL
    let kernel: URL
    let initialRamdisk: URL
    let disk: URL
    let machineIdentifier: URL
    let consoleLog: URL

    init(root: String) throws {
        let expanded = NSString(string: root).expandingTildeInPath
        self.root = URL(fileURLWithPath: expanded, isDirectory: true).standardizedFileURL
        kernel = self.root.appendingPathComponent("vmlinuz")
        initialRamdisk = self.root.appendingPathComponent("initrd")
        disk = self.root.appendingPathComponent("disk.raw")
        machineIdentifier = self.root.appendingPathComponent("machine-id")
        consoleLog = self.root.appendingPathComponent("console.log")
        for (label, url) in [
            ("kernel", kernel),
            ("initial RAM disk", initialRamdisk),
            ("guest disk", disk),
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

private func configuration(
    paths: RuntimePaths,
    controlShare: URL? = nil
) throws -> VZVirtualMachineConfiguration {
    let configuration = VZVirtualMachineConfiguration()
    let processors = ProcessInfo.processInfo.activeProcessorCount
    configuration.cpuCount = min(4, max(2, processors / 2))
    let physical = ProcessInfo.processInfo.physicalMemory
    let adaptiveMemory = max(UInt64(2 * 1_024 * 1_024 * 1_024), physical / 3)
    configuration.memorySize = min(UInt64(4 * 1_024 * 1_024 * 1_024), adaptiveMemory)

    let platform = VZGenericPlatformConfiguration()
    platform.machineIdentifier = try machineIdentifier(at: paths.machineIdentifier)
    configuration.platform = platform

    let bootLoader = VZLinuxBootLoader(kernelURL: paths.kernel)
    bootLoader.initialRamdiskURL = paths.initialRamdisk
    bootLoader.commandLine = [
        "root=/dev/vda",
        "rw",
        "console=hvc0",
        "panic=1",
        "systemd.unit=lemma-runtime.target",
    ].joined(separator: " ")
    configuration.bootLoader = bootLoader

    let diskAttachment = try VZDiskImageStorageDeviceAttachment(
        url: paths.disk,
        readOnly: false,
        cachingMode: .automatic,
        synchronizationMode: .full
    )
    configuration.storageDevices = [VZVirtioBlockDeviceConfiguration(attachment: diskAttachment)]

    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    configuration.networkDevices = [network]
    configuration.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    configuration.memoryBalloonDevices = [VZVirtioTraditionalMemoryBalloonDeviceConfiguration()]
    configuration.socketDevices = [VZVirtioSocketDeviceConfiguration()]
    if let controlShare {
        let directory = VZSharedDirectory(url: controlShare, readOnly: true)
        let share = VZSingleDirectoryShare(directory: directory)
        let fileSystem = VZVirtioFileSystemDeviceConfiguration(tag: "lemma-control")
        fileSystem.share = share
        configuration.directorySharingDevices = [fileSystem]
    }

    let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
    serial.attachment = VZFileHandleSerialPortAttachment(
        fileHandleForReading: FileHandle.nullDevice,
        fileHandleForWriting: try privateFileHandle(paths.consoleLog)
    )
    configuration.serialPorts = [serial]
    try configuration.validate()
    return configuration
}

private final class VirtualMachineDelegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) {
        fputs("lemma-vz: guest stopped\n", stderr)
        exit(EXIT_SUCCESS)
    }

    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        fputs("lemma-vz: guest stopped with error: \(error.localizedDescription)\n", stderr)
        exit(EXIT_FAILURE)
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

    init(socketDevice: VZVirtioSocketDevice, socketPath: String) throws {
        self.socketDevice = socketDevice
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
                socketDevice.connect(toPort: guestPort) { result in
                    switch result {
                    case .failure(let error):
                        let payload = "{\"ok\":false,\"error\":{\"code\":\"guest_unavailable\",\"message\":\"\(error.localizedDescription)\",\"retryable\":true,\"status_code\":503}}\n"
                        _ = try? writeAll(client, Data(payload.utf8))
                        close(client)
                    case .success(let connection):
                        DispatchQueue.global(qos: .userInitiated).async {
                            defer {
                                connection.close()
                                close(client)
                            }
                            do {
                                let request = try readLine(client, limit: maxRequestBytes)
                                try writeAll(connection.fileDescriptor, request)
                                let response = try readLine(
                                    connection.fileDescriptor,
                                    limit: maxResponseBytes
                                )
                                try writeAll(client, response)
                            } catch {
                                fputs("lemma-vz: bridge error: \(error.localizedDescription)\n", stderr)
                            }
                        }
                    }
                }
            }
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
    let runtimePaths = try RuntimePaths(root: argument("--runtime", in: arguments))
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
    let vm = VZVirtualMachine(
        configuration: try configuration(paths: runtimePaths, controlShare: controlShare)
    )
    let delegate = VirtualMachineDelegate()
    vm.delegate = delegate
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
                bridge = try GuestBridge(socketDevice: socketDevice, socketPath: socketPath)
                bridge?.serve()
            } catch {
                fputs("lemma-vz: control bridge failed: \(error.localizedDescription)\n", stderr)
                exit(EXIT_FAILURE)
            }
        }
    }
    withExtendedLifetime((vm, delegate, bridge)) {
        RunLoop.main.run(until: Date.distantFuture)
    }
    fatalError("unreachable")
}

private func main() throws {
    let arguments = Array(CommandLine.arguments.dropFirst())
    switch arguments.first {
    case "serve":
        try serve(arguments: Array(arguments.dropFirst()))
    case "validate":
        let paths = try RuntimePaths(root: argument("--runtime", in: arguments))
        _ = try configuration(paths: paths)
        print("valid")
    case "--version", "-V":
        print("lemma-vz \(version)")
    case "--help", "-h", nil:
        print("lemma-vz \(version)\n\nUSAGE:\n  lemma-vz serve --runtime <dir> --control-socket <path> --control-share <dir>\n  lemma-vz validate --runtime <dir>")
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
