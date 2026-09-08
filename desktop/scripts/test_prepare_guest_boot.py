import gzip
from pathlib import Path
import tempfile
import unittest

from prepare_guest_boot import prepare


class PrepareGuestBootTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "root"
        self.output = Path(directory.name) / "output"
        self.release = "6.8.0-138-generic"
        config = self.root / "etc/lemma"
        config.mkdir(parents=True)
        (config / "kernel-release").write_text(self.release + "\n")
        (config / "packages.txt").write_text("linux-image-6.8.0-138-generic\t6.8.0-138.138\n")
        self.modules = self.root / "usr/lib/modules" / self.release
        self.modules.mkdir(parents=True)
        (self.modules / "modules.dep").write_text("kernel/drivers/rtc/rtc-pl031.ko.zst:\n")
        boot = self.root / "boot"
        boot.mkdir()
        self.kernel = boot / f"vmlinuz-{self.release}"
        self.image = bytes(56) + b"ARM\x64" + bytes(4)
        self.kernel.write_bytes(gzip.compress(self.image))
        self.initrd = boot / f"initrd.img-{self.release}"
        self.initrd.write_bytes(b"matching initramfs")

    def test_stages_the_exact_kernel_and_its_provenance(self) -> None:
        # A second ABI must never win by filename ordering.
        (self.root / "boot/vmlinuz-9.9.9-other").write_bytes(b"wrong kernel")
        (self.root / "boot/initrd.img-9.9.9-other").write_bytes(b"wrong initramfs")
        self.assertEqual(prepare(self.root, self.output), self.release)
        self.assertEqual((self.output / "vmlinuz").read_bytes(), self.image)
        self.assertEqual((self.output / "initrd").read_bytes(), b"matching initramfs")
        self.assertEqual((self.output / "kernel-release").read_text(), self.release + "\n")
        self.assertEqual((self.output / "packages.txt").read_bytes(),
                         (self.root / "etc/lemma/packages.txt").read_bytes())

    def test_accepts_an_uncompressed_arm64_image(self) -> None:
        self.kernel.write_bytes(self.image)
        prepare(self.root, self.output)
        self.assertEqual((self.output / "vmlinuz").read_bytes(), self.image)

    def test_rejects_a_missing_module_tree(self) -> None:
        (self.modules / "modules.dep").unlink()
        with self.assertRaisesRegex(ValueError, "missing modules"):
            prepare(self.root, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_a_missing_or_empty_matching_initramfs(self) -> None:
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.initrd.unlink() if missing else self.initrd.write_bytes(b"")
                with self.assertRaisesRegex(ValueError, "matching initramfs"):
                    prepare(self.root, self.output)
                self.assertFalse(self.output.exists())

    def test_rejects_a_wrong_architecture_and_corrupt_compression(self) -> None:
        for kernel in (b"MZ wrong architecture", b"\x1f\x8btruncated"):
            with self.subTest(kernel=kernel):
                self.kernel.write_bytes(kernel)
                with self.assertRaises((ValueError, OSError, EOFError)):
                    prepare(self.root, self.output)
                self.assertFalse(self.output.exists())

    def test_rejects_a_release_that_escapes_the_package_tree(self) -> None:
        (self.root / "etc/lemma/kernel-release").write_text("../../other")
        with self.assertRaisesRegex(ValueError, "Invalid guest kernel release"):
            prepare(self.root, self.output)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
