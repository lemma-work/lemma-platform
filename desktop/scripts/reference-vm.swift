// A minimal Virtualization.framework runner for isolating appliance failures.
// The caller must supply disposable writable disks and enforce a deadline.
import Darwin
import Foundation
import Virtualization

final class Delegate: NSObject, VZVirtualMachineDelegate {
    func guestDidStop(_ virtualMachine: VZVirtualMachine) { exit(0) }
    func virtualMachine(_ virtualMachine: VZVirtualMachine, didStopWithError error: Error) {
        fputs("Guest stopped: \(error)\n", stderr)
        exit(1)
    }
}

func run() throws {
    let args = CommandLine.arguments
    guard args.count == 7 else {
        fputs("usage: reference-vm <kernel> <initrd> <disposable-root> <seed-or-dash> <console> <command-line>\n", stderr)
        exit(2)
    }
    let config = VZVirtualMachineConfiguration()
    config.cpuCount = 4
    config.memorySize = 4 * 1024 * 1024 * 1024
    config.platform = VZGenericPlatformConfiguration()
    let boot = VZLinuxBootLoader(kernelURL: URL(fileURLWithPath: args[1]))
    boot.initialRamdiskURL = URL(fileURLWithPath: args[2])
    boot.commandLine = args[6]
    config.bootLoader = boot
    let root = try VZDiskImageStorageDeviceAttachment(
        url: URL(fileURLWithPath: args[3]), readOnly: false)
    config.storageDevices = [VZVirtioBlockDeviceConfiguration(attachment: root)]
    if args[4] != "-" {
        let seed = try VZDiskImageStorageDeviceAttachment(
            url: URL(fileURLWithPath: args[4]), readOnly: true)
        config.storageDevices.append(VZVirtioBlockDeviceConfiguration(attachment: seed))
    }
    let network = VZVirtioNetworkDeviceConfiguration()
    network.attachment = VZNATNetworkDeviceAttachment()
    config.networkDevices = [network]
    config.entropyDevices = [VZVirtioEntropyDeviceConfiguration()]
    let serial = VZVirtioConsoleDeviceSerialPortConfiguration()
    let fd = open(args[5], O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0o600)
    guard fd >= 0 else { throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno)) }
    serial.attachment = VZFileHandleSerialPortAttachment(
        fileHandleForReading: FileHandle(forReadingAtPath: "/dev/null"),
        fileHandleForWriting: FileHandle(fileDescriptor: fd, closeOnDealloc: true))
    config.serialPorts = [serial]
    try config.validate()
    let vm = VZVirtualMachine(configuration: config)
    let delegate = Delegate()
    vm.delegate = delegate
    signal(SIGTERM, SIG_IGN)
    let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    termination.setEventHandler {
        vm.stop { error in
            if let error { fputs("Stop failed: \(error)\n", stderr) }
            exit(1)
        }
    }
    termination.resume()
    vm.start { result in
        if case .failure(let error) = result {
            fputs("Start failed: \(error)\n", stderr)
            exit(1)
        }
    }
    withExtendedLifetime((vm, delegate, termination)) { RunLoop.main.run() }
}

do { try run() } catch {
    fputs("Reference VM: \(error)\n", stderr)
    exit(1)
}
